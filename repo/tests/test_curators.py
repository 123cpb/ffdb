from django.apps import apps
from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Reference
from repo.tests.test_compound_card import RM734_COOLING
from repo.tests.test_sequences import RM734
from repo.tests.test_several_cards import CardsTestCase

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

    def test_curators_may_only_add_references(self):
        """Adding a reference is what lets them use Add data (migration 0032)."""
        codenames = set(self.curators.permissions.values_list("codename", flat=True))
        self.assertEqual(codenames, {"add_reference"})

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
    """Only admins delete materials: deleting one deletes its sequences and values too. Curators
    can't open the admin at all now (CuratorAdminTests), but the refusal is tested here too."""

    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.curator = User.objects.create_user("curator", password="a-test-password", is_staff=True)
        cls.curator.groups.add(Group.objects.get(name="Curators"))
        cls.admin = User.objects.create_superuser("admin", password="a-test-password")
        cls.change_url = reverse("admin:repo_material_change", args=[cls.material.pk])
        cls.delete_url = reverse("admin:repo_material_delete", args=[cls.material.pk])

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

    def test_an_admin_can_delete_a_material(self):
        self.client.force_login(self.admin)

        self.assertContains(self.client.get(self.change_url), self.delete_url)
        self.client.post(self.delete_url, {"post": "yes"})
        self.assertFalse(Material.objects.filter(pk=self.material.pk).exists())


class CuratorAdminTests(CardsTestCase):
    """Curators are out of the admin: their Group has only add reference (migration 0032), which
    lets them use Add data, where their saves wait for an admin's check. They ask an admin for
    corrections. This curator kept staff status, which lets a user into the admin, and is shown
    nothing there."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.curator.is_staff = True
        cls.curator.save()

    def test_the_admin_shows_a_curator_with_staff_status_nothing(self):
        index = self.client.get(reverse("admin:index"))

        self.assertEqual(index.context["app_list"], [])  # "You don't have permission to view or edit anything."
        for model in apps.get_models():
            if not admin.site.is_registered(model):
                continue
            for page in ["changelist", "add"]:
                name = f"admin:{model._meta.app_label}_{model._meta.model_name}_{page}"
                with self.subTest(name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_a_curator_cant_open_or_save_an_admin_change_page(self):
        url = reverse("admin:repo_material_change", args=[self.rm734.pk])
        references = Reference.objects.count()

        self.assertEqual(self.client.get(url).status_code, 403)
        # Only the fields that would change: the admin refuses before it reads the form.
        self.assertEqual(self.client.post(url, {"name": "RM734 corrected"}).status_code, 403)
        add = self.client.post(reverse("admin:repo_reference_add"), {"kind": "patent", "identifier": "EP0000001A1"})
        self.assertEqual(add.status_code, 403)

        self.assertEqual(Material.objects.get(pk=self.rm734.pk).name, "RM734")
        self.assertEqual(Reference.objects.count(), references)

    def test_a_curator_can_still_save_on_add_data(self):
        response = self.send(RM734_COOLING, "save", material="RM734")

        self.assertRedirects(response, self.add_page(self.article))
        sequence = PhaseSequence.objects.get()
        self.assertEqual((sequence.material, sequence.approved), (self.rm734, False))  # waiting
