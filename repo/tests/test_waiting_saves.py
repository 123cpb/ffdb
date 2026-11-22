"""Curators' saves wait for an admin's check. A curator's Save adds every card's rows unapproved, in
one WaitingSave, which records who saved it, when and from which reference, and holds the labels for
materials already in FFDB instead of adding them as aliases. An admin's Save is approved at once, and
keeps a label only under a public reference. Nothing waiting appears on a public page."""

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Reference, RetiredID, Series, WaitingSave, is_admin
from repo.tests.test_add_data import FIVE_CB, WAITING
from repo.tests.test_compound_card import FIVE_CB_HEATING, RM734_COOLING, run_fields
from repo.tests.test_sequences import RM734, RM734_SUMMARY
from repo.tests.test_several_cards import THREE_CARDS, CardsTestCase
from repo.tests.test_switches import UNPUBLISHED_WORK

MARKER = "WAITINGMARKER"


class CuratorTests(CardsTestCase):
    def test_a_curators_save_waits_unapproved_in_one_waiting_save_which_holds_the_label(self):
        response = self.send_cards(*THREE_CARDS, button="save", follow=True)

        saved = WaitingSave.objects.get()
        self.assertEqual((saved.reference, saved.saved_by), (self.article, self.curator))
        self.assertIsNotNone(saved.saved_at)
        rows = [*PhaseSequence.objects.all(), *Series.objects.all()]
        self.assertEqual(len(rows), 4)  # a sequence on each card, and RM734's value
        for row in rows:
            with self.subTest(row=str(row)):
                self.assertEqual((row.approved, row.waiting_save), (False, saved))
        # DIO, the label for Aya_5B, is held, not added. 5CB names a new compound, so isn't held.
        self.assertEqual(saved.labels, {str(self.aya.pk): "DIO"})
        self.assertEqual(Material.objects.get(pk=self.aya.pk).aliases, "")
        self.assertIn("DIO is held with the save", self.messages(response)[1])
        self.assertEqual(self.messages(response)[-1], WAITING)

    def test_a_label_that_wouldnt_be_kept_isnt_held(self):
        self.send(RM734_COOLING, "save", material="RM734", name="10b")  # a bare compound number

        self.assertEqual(WaitingSave.objects.get().labels, {})


class AdminTests(CardsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_an_admins_save_is_approved_at_once_and_keeps_the_label_under_a_public_reference(self):
        response = self.send(RM734_COOLING, "save", follow=True, material="RM734", name="MB-1")

        self.assertFalse(WaitingSave.objects.exists())
        run = PhaseSequence.objects.get()
        self.assertEqual((run.approved, run.waiting_save), (True, None))
        self.assertEqual(Material.objects.get(pk=self.rm734.pk).aliases, "MB-1")
        self.assertEqual(self.messages(response), [f"Saved a phase sequence for {self.rm734}. MB-1 is kept as one of its aliases."])

    def test_an_admins_label_isnt_kept_under_a_reference_that_isnt_public(self):
        unpublished = Reference.objects.create(kind=Reference.Kind.UNPUBLISHED, identifier=UNPUBLISHED_WORK)
        self.page = reverse("repo:add_compound", args=[unpublished.pk])

        response = self.send(RM734_COOLING, "save", follow=True, material="RM734", name="MB-1")

        self.assertTrue(PhaseSequence.objects.get().approved)
        self.assertEqual(Material.objects.get(pk=self.rm734.pk).aliases, "")
        self.assertEqual(
            self.messages(response),
            [f"Saved a phase sequence for {self.rm734}. MB-1 isn't kept as one of its aliases, because the reference isn't public."],
        )


class PublicPageTests(CardsTestCase):
    def test_nothing_a_curator_saves_appears_on_a_public_page(self):
        # RM734 is public, with a sequence from another paper.
        other = Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", published=True)
        PhaseSequence.objects.create(material=self.rm734, reference=other, sequence=RM734_SUMMARY, approved=True)
        marked_run = run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="70", method=MARKER)

        # Followed, so the Add data page shows the curator their own messages, which name the marker.
        self.send_cards(
            {"run": marked_run, "material": "RM734", "name": f"{MARKER}-1"},
            {"run": FIVE_CB_HEATING, "smiles": FIVE_CB, "name": f"{MARKER} compound"},
            button="save", follow=True,
        )

        self.assertEqual(WaitingSave.objects.get().labels, {str(self.rm734.pk): f"{MARKER}-1"})
        new = Material.objects.get(name=f"{MARKER} compound")
        pages = [
            reverse("repo:material", args=[self.rm734.pk]), reverse("repo:materials"), reverse("repo:materials_csv"),
            reverse("repo:sequences_csv"), reverse("repo:references"), reverse("repo:reference", args=[other.pk]),
        ]
        for page in pages:  # as the curator, still logged in: public pages show nothing hidden to anyone
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(MARKER, response.content.decode())
        for page in [reverse("repo:material", args=[new.pk]), reverse("repo:reference", args=[self.article.pk])]:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(page).status_code, 404)
        self.assertEqual(self.client.get(reverse("repo:materials"), {"q": MARKER}).context["page"].paginator.count, 0)


class WaitingCountTests(CardsTestCase):
    def test_the_add_data_page_counts_the_curators_own_waiting_saves_from_the_reference(self):
        self.send(RM734_COOLING, "save", material="RM734")
        self.send(FIVE_CB_HEATING, "save", smiles=FIVE_CB, name="5CB")
        # Another curator's save from the article, and this curator's from the patent.
        WaitingSave.objects.create(reference=self.article, saved_by=User.objects.create_user("other"))
        WaitingSave.objects.create(reference=self.patent, saved_by=self.curator)

        page = self.client.get(self.add_page(self.article))
        self.assertContains(page, "2 of your saves from this reference are waiting for an admin's check.")
        page = self.client.get(self.add_page(self.patent))
        self.assertContains(page, "1 of your saves from this reference is waiting for an admin's check.")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(self.add_page(self.article)), "waiting for an admin's check")


class RestoreTests(CardsTestCase):
    """RM734 retired by an admin while a curator's sequence of it waits."""

    def setUp(self):
        super().setUp()
        self.send(RM734_COOLING, "save", material="RM734")
        self.saved = WaitingSave.objects.get()
        Material.objects.get(pk=self.rm734.pk).retire(self.admin)
        self.record = RetiredID.objects.get(number=self.rm734.pk)

    def test_restoring_a_material_whose_waiting_save_has_gone_blanks_the_link(self):
        self.saved.delete()  # approved or rejected since

        self.record.restore()

        run = PhaseSequence.objects.get()
        self.assertEqual((run.material_id, run.approved, run.waiting_save), (self.rm734.pk, False, None))

    def test_restoring_while_its_save_still_waits_keeps_the_link(self):
        self.record.restore()

        self.assertEqual(PhaseSequence.objects.get().waiting_save, self.saved)


class IsAdminTests(TestCase):
    def test_an_admin_is_a_user_who_may_delete_materials(self):
        curator = User.objects.create_user("curator")
        curator.groups.add(Group.objects.get(name="Curators"))
        deleter = User.objects.create_user("deleter")
        deleter.user_permissions.add(Permission.objects.get(content_type__app_label="repo", codename="delete_material"))

        self.assertFalse(is_admin(curator))
        # Read again: Django keeps a user's permissions once it has looked them up.
        self.assertTrue(is_admin(User.objects.get(pk=deleter.pk)))
        self.assertTrue(is_admin(User.objects.create_superuser("admin")))

    def test_the_admins_pages_ask_the_is_admin_filter(self):
        admin = User.objects.create_superuser("admin")
        # Curators can't open a material's admin page (migration 0032). Someone an admin gave change
        # material to, but not delete material, can, and isn't an admin.
        editor = User.objects.create_user("editor", is_staff=True)
        editor.user_permissions.add(Permission.objects.get(content_type__app_label="repo", codename="change_material"))
        rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cb5 = Material.objects.create(name="5CB", smiles=FIVE_CB)
        number = cb5.pk  # before retiring, which takes its pk
        cb5.retire(admin)
        merge = reverse("admin:repo_material_merge", args=[rm734.pk])

        self.client.force_login(editor)
        self.assertNotContains(self.client.get(reverse("admin:repo_material_change", args=[rm734.pk])), merge)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("admin:repo_material_change", args=[rm734.pk])), merge)
        self.assertContains(self.client.get(reverse("admin:repo_retiredid_change", args=[number])), 'value="Restore"')
