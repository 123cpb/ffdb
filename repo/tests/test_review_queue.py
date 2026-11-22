"""The review queue: curators' waiting saves in the admin, oldest first, for admins only. Approving a
save ticks approved on its rows, keeps its labels as aliases under a public reference, and deletes
it. Rejecting deletes its rows, retires each material it leaves with nothing, and deletes it. Each
is recorded in the reference's history, because the save's own goes with it."""

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from repo.duplicates import ONE_ONLY
from repo.models import (
    Material, Measurement, MixtureComponent, NotDuplicate, PhaseSequence, Reference, RetiredID, Series, WaitingSave,
)
from repo.tests.test_import import ARTICLE, FIVE_CB, RM734, RM734_SUMMARY
from repo.tests.test_several_cards import THREE_CARDS, CardsTestCase
from repo.tests.test_switches import UNPUBLISHED_WORK
from repo.tests.test_values import DIO, add

QUEUE = reverse("admin:repo_waitingsave_changelist")
FIVE_CB_SUMMARY = "K 22.5 N 35 I"
DIO_SUMMARY = "K 68.8 NF 84.5 N 174 I"


class ReviewTestCase(TestCase):
    """An admin, logged in; a curator with staff status; a published article, with nothing from it
    yet, and unpublished work. RM734 is public, with a sequence from another paper; 5CB has nothing
    yet, so it reads as one a save added."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin")
        cls.curator = User.objects.create_user("curator", is_staff=True)
        cls.curator.groups.add(Group.objects.get(name="Curators"))
        cls.article = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier=ARTICLE, first_author="Mandle", year=2017, published=True
        )
        cls.work = Reference.objects.create(kind=Reference.Kind.UNPUBLISHED, identifier=UNPUBLISHED_WORK)
        other = Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier="10.1002/chem.201702742", published=True)
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        PhaseSequence.objects.create(material=cls.rm734, reference=other, sequence=RM734_SUMMARY, approved=True)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)

    def setUp(self):
        self.client.force_login(self.admin)

    def waiting(self, reference, labels=None, sequences=(), values=()):
        """A curator's waiting save from reference, as Save leaves it: a sequence of each (material,
        sequence) in sequences and a Ps value of each material in values, unapproved, and the labels
        held, {material: label}."""
        saved = WaitingSave.objects.create(
            reference=reference, saved_by=self.curator,
            labels={str(material.pk): label for material, label in (labels or {}).items()},
        )
        for material, sequence in sequences:
            PhaseSequence.objects.create(material=material, reference=reference, sequence=sequence, waiting_save=saved)
        for material in values:
            series = add(material, reference, "ps", approved=False, waiting_save=saved)
            Measurement.objects.create(series=series, value=5.2, temperature_c=100)
        return saved

    def act(self, action, saved):
        """Press Approve or Reject on the save's page. The response is the queue, with the messages."""
        return self.client.post(reverse(f"admin:repo_waitingsave_{action}", args=[saved.pk]), follow=True)

    def messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def status(self, material_pk):
        """What a visitor gets at a material's public page."""
        self.client.logout()
        code = self.client.get(reverse("repo:material", args=[material_pk])).status_code
        self.client.force_login(self.admin)
        return code

    def history(self, reference):
        return [(e.user, e.change_message) for e in LogEntry.objects.filter(object_id=str(reference.pk)).order_by("pk")]


class ApproveTests(ReviewTestCase):
    def test_approving_under_a_published_reference_makes_its_data_public_and_keeps_its_labels(self):
        dio = Material.objects.create(name="Aya_5B", smiles=DIO)
        saved = self.waiting(
            self.article, labels={self.rm734: "MB-1", dio: "DIO"},
            sequences=[(self.rm734, RM734_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY), (dio, DIO_SUMMARY)],
            values=[self.five_cb],
        )
        dio_id = dio.accession
        dio.retire(self.admin)  # since the save: its label has nowhere to go
        number = saved.pk

        response = self.act("approve", saved)

        self.assertRedirects(response, QUEUE)
        self.assertFalse(WaitingSave.objects.exists())
        for row in [*PhaseSequence.objects.filter(reference=self.article), *Series.objects.all()]:
            with self.subTest(row=str(row)):
                self.assertEqual((row.approved, row.waiting_save), (True, None))
        self.assertEqual(self.status(self.five_cb.pk), 200)
        self.assertEqual(Material.objects.get(pk=self.rm734.pk).aliases, "MB-1")
        self.assertEqual(
            self.messages(response),
            [
                f"Approved save {number} from {self.article}: its data is public.",
                f"MB-1 is kept as one of {self.rm734.accession}'s aliases.",
                f"DIO wasn't kept as one of {dio_id}'s aliases: {dio_id} is no longer in FFDB.",
            ],
        )
        self.assertEqual(self.history(self.article), [(self.admin, f"Approved save {number}.")])

    def test_approving_under_unpublished_work_approves_its_data_but_keeps_it_hidden(self):
        saved = self.waiting(
            self.work, labels={self.rm734: "MB-1"}, sequences=[(self.rm734, RM734_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY)]
        )
        number = saved.pk

        response = self.act("approve", saved)

        self.assertFalse(WaitingSave.objects.exists())
        self.assertFalse(PhaseSequence.objects.filter(approved=False).exists())
        self.assertEqual(self.status(self.five_cb.pk), 404)
        self.assertEqual(Material.objects.get(pk=self.rm734.pk).aliases, "")
        self.assertEqual(
            self.messages(response),
            [
                f"Approved save {number} from {self.work}: its data stays hidden, because {self.work} isn't published. "
                "Marking it published makes it public.",
                f"MB-1 wasn't kept as one of {self.rm734.accession}'s aliases: the reference isn't public.",
            ],
        )


class RejectTests(ReviewTestCase):
    def test_rejecting_deletes_its_rows_and_retires_what_it_leaves_with_nothing(self):
        mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name="M1")
        MixtureComponent.objects.create(mixture=mixture, component=self.rm734, fraction=50, unit="mol%")
        saved = self.waiting(
            self.article, labels={self.rm734: "MB-1"},
            sequences=[(self.rm734, RM734_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY), (mixture, "K 40 N 90 I")],
            values=[self.five_cb],
        )
        number, cb_text, mixture_text = saved.pk, str(self.five_cb), str(mixture)

        response = self.act("reject", saved)

        self.assertFalse(WaitingSave.objects.exists())
        self.assertFalse(PhaseSequence.objects.filter(reference=self.article).exists())
        self.assertFalse(Series.objects.exists())
        self.assertFalse(Measurement.objects.exists())
        # The new compound and mixture are retired, and leave no public trace.
        self.assertEqual(
            set(RetiredID.objects.values_list("number", "was_public")), {(self.five_cb.pk, False), (mixture.pk, False)}
        )
        self.assertEqual(self.status(self.five_cb.pk), 404)
        self.assertEqual(self.status(mixture.pk), 404)
        # RM734 keeps its own sequence, and isn't given the label.
        self.assertEqual(Material.objects.get(pk=self.rm734.pk).aliases, "")
        self.assertEqual(self.rm734.phase_sequences.count(), 1)
        retired = "is retired, because the save left it with no data. It isn't public, so its address answers not found."
        self.assertEqual(
            self.messages(response),
            [
                f"Rejected save {number} from {self.article}: its phase sequences and values are deleted.",
                f"{mixture_text} {retired}",  # mixtures first
                f"{cb_text} {retired}",
            ],
        )
        self.assertEqual(self.history(self.article), [(self.admin, f"Rejected save {number}. Retired {mixture_text}, {cb_text}.")])

    def test_rejecting_keeps_a_hidden_material_with_other_data_and_a_compound_in_a_mixture(self):
        # DIO is hidden, with an approved sequence from unpublished work. 5CB has no data of its own,
        # but is a component of a listed mixture.
        dio = Material.objects.create(name="DIO", smiles=DIO)
        PhaseSequence.objects.create(material=dio, reference=self.work, sequence=DIO_SUMMARY, approved=True)
        host = Material.objects.create(kind=Material.Kind.MIXTURE, name="Host", listed=True)
        MixtureComponent.objects.create(mixture=host, component=self.five_cb, fraction=50, unit="mol%")
        saved = self.waiting(self.article, sequences=[(dio, DIO_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY)])

        self.act("reject", saved)

        self.assertFalse(PhaseSequence.objects.filter(reference=self.article).exists())
        self.assertEqual(dio.phase_sequences.count(), 1)
        self.assertTrue(Material.objects.filter(pk=self.five_cb.pk).exists())
        self.assertFalse(RetiredID.objects.exists())


class QueueTests(ReviewTestCase):
    def test_the_queue_lists_saves_oldest_first_with_what_each_holds(self):
        newer = self.waiting(self.article, sequences=[(self.rm734, RM734_SUMMARY)])
        older = self.waiting(self.work, sequences=[(self.rm734, RM734_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY)], values=[self.five_cb])
        WaitingSave.objects.filter(pk=older.pk).update(saved_at=newer.saved_at.replace(year=2025))

        page = self.client.get(QUEUE)

        self.assertEqual(list(page.context["cl"].result_list), [older, newer])
        for column, count in [("material_count", 2), ("sequence_count", 2), ("series_count", 1)]:
            self.assertContains(page, f'<td class="field-{column}">{count}</td>', html=True)

    def test_a_saves_page_lists_what_it_adds_with_approve_and_reject_and_no_delete(self):
        saved = self.waiting(self.article, labels={self.rm734: "MB-1"}, sequences=[(self.rm734, RM734_SUMMARY)])
        change = reverse("admin:repo_waitingsave_change", args=[saved.pk])

        page = self.client.get(change)

        self.assertContains(page, f"<h2>{self.rm734}</h2>", html=True)
        self.assertContains(page, "Label on the curator's card: MB-1.")
        self.assertContains(page, 'class="ribbon"', count=1)
        self.assertContains(page, 'value="Approve"')
        self.assertContains(page, 'value="Reject"')
        self.assertNotContains(page, reverse("admin:repo_waitingsave_delete", args=[saved.pk]))
        self.assertNotContains(page, 'name="_save"')


class PreviewTests(ReviewTestCase):
    """A save's page shows what it adds as the public pages would, with their templates: each
    material with its status, the label held for it and its drawing, its sequences with their
    ribbons, and its values with their charts. It says whether approving makes the data public,
    and warns of a hidden material that approving would make public."""

    def page(self, saved):
        return self.client.get(reverse("admin:repo_waitingsave_change", args=[saved.pk]))

    def hidden_dio(self):
        """DIO, hidden: its only data is a sequence from unpublished work."""
        dio = Material.objects.create(name="DIO", smiles=DIO)
        PhaseSequence.objects.create(material=dio, reference=self.work, sequence=DIO_SUMMARY, approved=True)
        return dio

    def test_a_saves_page_shows_its_materials_ribbons_values_and_charts(self):
        mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name="M1")
        MixtureComponent.objects.create(mixture=mixture, component=self.rm734, fraction=50, unit="mol%")
        saved = self.waiting(
            self.article, labels={self.rm734: "MB-1"},
            sequences=[(self.rm734, RM734_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY), (mixture, "K 40 N 90 I")],
        )
        curve = add(self.five_cb, self.article, "ps", approved=False, waiting_save=saved)
        for temperature, value in [(25, 4.1), (30, 4.6), (34, 5.2)]:
            Measurement.objects.create(series=curve, value=value, temperature_c=temperature)

        page = self.page(saved)

        self.assertContains(page, "/static/repo/data.css")
        self.assertContains(page, "Approving makes this save's data public.")
        statuses = {entry["material"]: entry["status"] for entry in page.context["entries"]}
        self.assertEqual(statuses, {self.rm734: "public", self.five_cb: "new", mixture: "new"})
        self.assertContains(page, "Public: this save adds to its page.", count=1)
        self.assertContains(page, "New: it has no data but this save's", count=2)
        for material in [self.rm734, self.five_cb, mixture]:
            self.assertContains(page, f"<h2>{material}</h2>", html=True)
        self.assertContains(page, "Label on the curator's card: MB-1.")
        self.assertContains(page, "It will be kept as an alias.")
        # Each compound drawn in the page, as its structure.svg answers 404 while it is hidden, and
        # the mixture's composition in its place.
        self.assertContains(page, 'src="data:image/svg+xml;base64,', count=2)
        self.assertContains(page, f"{self.rm734.accession}</a> RM734, 50 mol%")
        # Each sequence with its ribbon and standard sequence, and 5CB's curve in its table and chart.
        self.assertContains(page, 'class="ribbon"', count=3)
        self.assertContains(page, "Cr 22.5 N 35 Iso")
        self.assertContains(page, "4.1 to 5.2 µC/cm²")
        self.assertContains(page, '<figure class="chart">', count=1)
        self.assertNotContains(page, "Approving makes it public under")
        self.assertNotContains(page, "Check before approving")  # nothing in FFDB is like what it adds

    def test_a_hidden_material_is_warned_of_with_the_curators_label(self):
        # A curator typed DIO's structure: their card showed a new compound named 10a.
        dio = self.hidden_dio()
        saved = self.waiting(
            self.article, labels={dio: "10a"}, sequences=[(dio, DIO_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY)]
        )

        page = self.page(saved)

        self.assertContains(page, f"<h2>{dio}</h2>", html=True)
        self.assertContains(page, "Hidden: it isn't public, and has data that isn't in this save.", count=1)
        # Only DIO's: 5CB is new, so its name is the label the curator gave it.
        self.assertContains(page, "Approving makes it public under its current name, DIO.", count=1)
        self.assertContains(page, "Label on the curator's card: 10a.")
        self.assertContains(page, "It won't be kept as an alias: a bare compound number means something only inside one paper.")

    def test_under_unpublished_work_approving_keeps_it_hidden_so_nothing_is_warned_of(self):
        dio = self.hidden_dio()
        saved = self.waiting(self.work, labels={self.rm734: "MB-1"}, sequences=[(dio, DIO_SUMMARY), (self.rm734, RM734_SUMMARY)])

        page = self.page(saved)

        self.assertContains(
            page,
            f"Approving keeps this save's data hidden, because {self.work} isn't published. Marking it published makes it public.",
        )
        self.assertContains(page, "Hidden: it isn't public")
        self.assertNotContains(page, "Approving makes it public under")
        # The reason is text from the code, so its apostrophe is escaped in the page.
        reason = escape("the reference isn't public")
        self.assertContains(page, f"It won't be kept as an alias: {reason}.")

    def test_a_materials_status_says_whether_it_is_public_new_or_hidden(self):
        dio = self.hidden_dio()
        saved = self.waiting(
            self.article, sequences=[(self.rm734, RM734_SUMMARY), (self.five_cb, FIVE_CB_SUMMARY), (dio, DIO_SUMMARY)]
        )

        self.assertEqual([saved.material_status(m) for m in (self.rm734, self.five_cb, dio)], ["public", "new", "hidden"])
        # Data in another waiting save is outside this one, so 5CB is hidden here now, and Reject
        # would no longer retire it.
        self.waiting(self.article, sequences=[(self.five_cb, FIVE_CB_SUMMARY)])
        self.assertEqual(saved.material_status(self.five_cb), "hidden")

    def test_the_public_pages_link_data_css_before_style_css(self):
        page = self.client.get(reverse("repo:materials")).content.decode()

        self.assertLess(page.index("/static/repo/data.css"), page.index("/static/repo/style.css"))


class FlagTests(ReviewTestCase):
    """A save's page flags, under each of its materials, what may be in FFDB already, hidden data
    included, as a curator's checks see only public data: its possible duplicates not yet decided,
    other mixtures with one of its names, and its data from the save's reference outside the save."""

    def entry(self, saved, material):
        """The page, and the entry for material on it."""
        page = self.client.get(reverse("admin:repo_waitingsave_change", args=[saved.pk]))
        return page, next(entry for entry in page.context["entries"] if entry["material"] == material)

    def test_a_possible_duplicate_not_yet_decided_is_flagged_hidden_ones_included(self):
        rac = Material.objects.create(name="rac-2-octanol", smiles="CC(O)CCCCCC")
        s = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")  # hidden: no data, not listed
        saved = self.waiting(self.article, sequences=[(rac, "K 10 N 40 I")])

        page, entry = self.entry(saved, rac)

        self.assertEqual(entry["pairs"], [{"other": s, "hidden": True, "heading": ONE_ONLY, "sequences": []}])
        self.assertContains(page, "Check before approving")
        self.assertContains(page, f"{s}</a>, hidden:")
        self.assertContains(page, f"same connectivity. {ONE_ONLY}.")
        self.assertContains(page, reverse("admin:repo_material_duplicates"))

        NotDuplicate.mark(rac, s, self.admin)
        page, entry = self.entry(saved, rac)
        self.assertEqual(entry["pairs"], [])
        self.assertNotContains(page, "Check before approving")

    def test_a_compound_with_a_matching_sequence_from_the_same_reference_is_flagged(self):
        pentane = Material.objects.create(name="C5", smiles="CCCCC")
        hexane = Material.objects.create(name="C6", smiles="CCCCCC")  # hidden: its sequence isn't approved
        theirs = PhaseSequence.objects.create(material=hexane, reference=self.article, sequence="K 10.3 N 40 I")
        saved = self.waiting(self.article, sequences=[(pentane, "K 10 N 40 I")])

        page, entry = self.entry(saved, pentane)

        self.assertEqual(entry["pairs"], [{"other": hexane, "hidden": True, "heading": self.article, "sequences": [theirs]}])
        self.assertContains(page, f"a matching sequence from {self.article}:")
        self.assertContains(page, "Cr 10.3 N 40 Iso (Summary).")

    def test_other_mixtures_with_one_of_its_names_are_flagged_hidden_ones_included(self):
        # The curator's own earlier M1, still waiting, so hidden; a public mixture also called M1; and M2.
        earlier = Material.objects.create(kind=Material.Kind.MIXTURE, name="M1")
        self.waiting(self.article, sequences=[(earlier, "K 40 N 90 I")])
        f7 = Material.objects.create(kind=Material.Kind.MIXTURE, name="F7", aliases="M1", listed=True)
        m2 = Material.objects.create(kind=Material.Kind.MIXTURE, name="M2", listed=True)
        mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name="M1")
        saved = self.waiting(self.article, sequences=[(mixture, "K 41 N 92 I")])

        page, entry = self.entry(saved, mixture)

        self.assertEqual(entry["same_name"], [{"other": earlier, "hidden": True}, {"other": f7, "hidden": False}])
        self.assertContains(page, f"{earlier}</a> (hidden), ")
        self.assertContains(page, f"{f7}</a>.")
        self.assertNotContains(page, str(m2))

    def test_data_from_the_same_reference_outside_the_save_is_flagged_waiting_or_not(self):
        first = self.waiting(self.article, sequences=[(self.rm734, RM734_SUMMARY)])
        unticked = PhaseSequence.objects.create(material=self.rm734, reference=self.article, sequence=RM734_SUMMARY)
        series = add(self.rm734, self.article, "ps")  # approved
        second = self.waiting(self.article, sequences=[(self.rm734, RM734_SUMMARY)])

        page, entry = self.entry(second, self.rm734)

        # Not RM734's sequence from the other paper, nor this save's own.
        self.assertEqual(list(entry["same_reference_sequences"]), [*first.phase_sequences.all(), unticked])
        self.assertEqual(list(entry["same_reference_series"]), [series])
        first_page = reverse("admin:repo_waitingsave_change", args=[first.pk])
        self.assertContains(page, f'(Summary): waiting in <a href="{first_page}">save {first.pk}</a>')
        self.assertContains(page, "(Summary): not approved, in no save")
        self.assertContains(page, "Spontaneous polarisation against temperature: approved")
        # The first save's page flags the second's sequence in turn.
        _, entry = self.entry(first, self.rm734)
        self.assertIn(second.phase_sequences.get(), entry["same_reference_sequences"])


class OnlyAdminsTests(ReviewTestCase):
    def test_only_admins_can_open_the_queue_or_approve_or_reject(self):
        saved = self.waiting(self.article, sequences=[(self.five_cb, FIVE_CB_SUMMARY)])
        # Given every permission on waiting saves, but not delete material, so not an admin.
        editor = User.objects.create_user("editor", is_staff=True)
        editor.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="repo", codename__endswith="_waitingsave")
        )
        pages = [QUEUE, reverse("admin:repo_waitingsave_change", args=[saved.pk])]
        buttons = [reverse(f"admin:repo_waitingsave_{action}", args=[saved.pk]) for action in ["approve", "reject"]]

        for user in [self.curator, editor]:
            self.client.force_login(user)
            for url in pages:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            for url in buttons:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.post(url).status_code, 403)

        self.assertTrue(WaitingSave.objects.filter(pk=saved.pk).exists())
        self.assertFalse(PhaseSequence.objects.get(material=self.five_cb).approved)
        # An admin who isn't a superuser: one who may delete materials.
        deleter = User.objects.create_user("deleter", is_staff=True)
        deleter.user_permissions.add(Permission.objects.get(content_type__app_label="repo", codename="delete_material"))
        self.client.force_login(deleter)
        self.assertEqual(self.client.get(QUEUE).status_code, 200)


class FromAddDataTests(CardsTestCase):
    def test_a_curators_save_on_add_data_is_public_once_approved(self):
        self.send_cards(*THREE_CARDS, button="save")
        saved = WaitingSave.objects.get()
        five_cb = Material.objects.get(name="5CB")
        self.assertEqual(self.client.get(reverse("repo:material", args=[five_cb.pk])).status_code, 404)

        self.client.force_login(self.admin)
        self.client.post(reverse("admin:repo_waitingsave_approve", args=[saved.pk]))

        self.client.logout()
        self.assertEqual(self.client.get(reverse("repo:material", args=[five_cb.pk])).status_code, 200)
        self.assertEqual(Material.objects.get(pk=self.aya.pk).aliases, "DIO")
