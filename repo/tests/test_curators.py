from django.apps import apps
from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Reference
from repo.tests.test_sequences import RM734, RM734_SUMMARY

ACTIONS = ["add", "change", "delete"]
ADD = reverse("repo:add")
LOGIN = reverse("login")
LOGOUT = reverse("logout")
HOME = reverse("repo:home")


class CuratorTests(TestCase):
    """A curator (in the Curators group), and a logged-in user who isn't one."""

    @classmethod
    def setUpTestData(cls):
        # The group comes from migration 0013, which the test database runs.
        cls.curators = Group.objects.get(name="Curators")
        cls.curator = User.objects.create_user("curator", password="a-test-password")
        cls.curator.groups.add(cls.curators)
        cls.other = User.objects.create_user("other", password="a-test-password")

    def test_curators_can_add_change_and_delete_the_data_but_not_delete_materials(self):
        codenames = set(self.curators.permissions.values_list("codename", flat=True))
        expected = {
            f"{action}_{model}"
            for action in ACTIONS
            for model in ["material", "reference", "phasesequence", "series", "measurement", "mixturecomponent"]
        } - {"delete_material"}  # only admins delete materials (migration 0021)
        self.assertEqual(codenames, expected)

    def test_curators_can_work_on_every_section_of_rows_on_their_admin_pages(self):
        """The admin leaves out a section of rows (an inline) whose model the user has no
        permission on, and with it any rules checked in that section. So each section on a
        page curators can change needs the group's permissions too, including a section
        added after migration 0013 made the group."""
        for model in apps.get_app_config("repo").get_models():
            if not admin.site.is_registered(model):
                continue
            if not self.curator.has_perm(f"repo.change_{model._meta.model_name}"):
                continue  # not a curators' page, like the vocabularies
            for inline in admin.site.get_model_admin(model).inlines:
                for action in ACTIONS:
                    codename = f"{action}_{inline.model._meta.model_name}"
                    with self.subTest(page=model.__name__, permission=codename):
                        self.assertTrue(self.curator.has_perm(f"repo.{codename}"))

    def test_curator_with_staff_status_can_add_data_in_the_admin(self):
        self.curator.is_staff = True  # set on the user's admin page; a group can't give it
        self.curator.save()
        self.client.force_login(self.curator)

        for model in ["material", "reference", "series"]:
            with self.subTest(model=model):
                self.assertEqual(self.client.get(reverse(f"admin:repo_{model}_add")).status_code, 200)
        # Not the vocabularies: those stay with admins.
        self.assertEqual(self.client.get(reverse("admin:repo_phase_add")).status_code, 403)

    def test_curator_logs_in_sees_add_data_and_logs_out(self):
        response = self.client.post(
            LOGIN, {"username": "curator", "password": "a-test-password", "next": ADD}
        )
        self.assertRedirects(response, ADD)

        page = self.client.get(reverse("repo:materials"))
        self.assertContains(page, f'href="{ADD}"')
        self.assertContains(page, f'action="{LOGOUT}"')
        self.assertNotContains(page, f'href="{LOGIN}"')  # Log out takes its place

        self.assertRedirects(self.client.post(LOGOUT), HOME)
        self.assertContains(self.client.get(HOME), f'href="{LOGIN}"')
        self.assertRedirects(self.client.get(ADD), f"{LOGIN}?next={ADD}")

    def test_wrong_password_stays_on_the_login_page(self):
        response = self.client.post(LOGIN, {"username": "curator", "password": "wrong"})

        self.assertContains(response, "form-error")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logged_out_visitor_is_sent_to_the_login_page(self):
        self.assertRedirects(self.client.get(ADD), f"{LOGIN}?next={ADD}")

        page = self.client.get(reverse("repo:materials"))
        self.assertNotContains(page, f'href="{ADD}"')
        self.assertNotContains(page, f'action="{LOGOUT}"')
        self.assertContains(page, f'href="{LOGIN}"')  # the way back to Add data

    def test_logged_in_user_who_is_not_a_curator_is_refused(self):
        self.client.force_login(self.other)

        self.assertEqual(self.client.get(ADD).status_code, 403)
        page = self.client.get(reverse("repo:materials"))
        self.assertNotContains(page, f'href="{ADD}"')
        self.assertContains(page, f'action="{LOGOUT}"')
        self.assertNotContains(page, f'href="{LOGIN}"')


class MaterialDeleteTests(TestCase):
    """Only admins delete materials: deleting one deletes its sequences and values too.
    A curator can still delete a single sequence."""

    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017
        )
        cls.sequence = PhaseSequence.objects.create(material=cls.material, reference=cls.reference, sequence=RM734_SUMMARY)
        cls.curator = User.objects.create_user("curator", password="a-test-password", is_staff=True)
        cls.curator.groups.add(Group.objects.get(name="Curators"))
        cls.admin = User.objects.create_superuser("admin", password="a-test-password")
        cls.change_url = reverse("admin:repo_material_change", args=[cls.material.pk])
        cls.delete_url = reverse("admin:repo_material_delete", args=[cls.material.pk])

    def test_a_curator_is_offered_no_way_to_delete_a_material(self):
        self.client.force_login(self.curator)

        self.assertNotContains(self.client.get(self.change_url), self.delete_url)  # no Delete button
        self.assertNotContains(self.client.get(reverse("admin:repo_material_changelist")), "delete_selected")

    def test_a_curator_who_sends_the_delete_form_is_refused(self):
        self.client.force_login(self.curator)

        self.assertEqual(self.client.get(self.delete_url).status_code, 403)
        self.assertEqual(self.client.post(self.delete_url, {"post": "yes"}).status_code, 403)
        # The list's Delete selected, sent as its confirmation page would send it.
        self.client.post(
            reverse("admin:repo_material_changelist"),
            {"action": "delete_selected", "_selected_action": [self.material.pk], "post": "yes"},
        )
        self.assertTrue(Material.objects.filter(pk=self.material.pk).exists())

    def test_a_curator_can_still_delete_a_sequence(self):
        """Tick Delete on the sequence's row on the material's page, and save. Every field is
        sent as the page would send it; the row's other fields aren't checked once it is ticked."""
        self.client.force_login(self.curator)
        data = {
            "kind": "compound", "name": "RM734", "aliases": "", "smiles": RM734, "notes": "",
            "composition_status": "listed", "other_components": "",
            "components-TOTAL_FORMS": "0", "components-INITIAL_FORMS": "0",
            "phase_sequences-TOTAL_FORMS": "1", "phase_sequences-INITIAL_FORMS": "1",
            "phase_sequences-0-id": str(self.sequence.pk),
            "phase_sequences-0-material": str(self.material.pk),
            "phase_sequences-0-reference": str(self.reference.pk),
            "phase_sequences-0-direction": "summary",
            "phase_sequences-0-sequence": RM734_SUMMARY,
            "phase_sequences-0-DELETE": "on",
        }

        response = self.client.post(self.change_url, data)

        self.assertEqual(response.status_code, 302)  # saved, and sent back to the list
        self.assertFalse(PhaseSequence.objects.filter(pk=self.sequence.pk).exists())
        self.assertTrue(Material.objects.filter(pk=self.material.pk).exists())

    def test_an_admin_can_delete_a_material(self):
        self.client.force_login(self.admin)

        self.assertContains(self.client.get(self.change_url), self.delete_url)
        self.client.post(self.delete_url, {"post": "yes"})
        self.assertFalse(Material.objects.filter(pk=self.material.pk).exists())
