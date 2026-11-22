"""The review queue: curators' waiting saves in the admin, oldest first, for admins only. Approving a
save ticks approved on its rows, keeps its labels as aliases under a public reference, and deletes
it. Rejecting deletes its rows, retires each material it leaves with nothing, and deletes it. Each
is recorded in the reference's history, because the save's own goes with it."""

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, Measurement, MixtureComponent, PhaseSequence, Reference, RetiredID, Series, WaitingSave
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

        self.assertContains(page, f"{self.rm734}, label to keep: MB-1")
        self.assertContains(page, f"{self.rm734.accession}: {RM734_SUMMARY} (Summary)")
        self.assertContains(page, 'value="Approve"')
        self.assertContains(page, 'value="Reject"')
        self.assertNotContains(page, reverse("admin:repo_waitingsave_delete", args=[saved.pk]))
        self.assertNotContains(page, 'name="_save"')


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
