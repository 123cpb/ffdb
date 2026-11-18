from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

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

    def test_curators_can_add_change_and_delete_the_data(self):
        codenames = set(self.curators.permissions.values_list("codename", flat=True))
        expected = {
            f"{action}_{model}"
            for action in ["add", "change", "delete"]
            for model in ["material", "reference", "phasesequence", "series", "measurement"]
        }
        self.assertEqual(codenames, expected)

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

        self.assertRedirects(self.client.post(LOGOUT), HOME)
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

    def test_logged_in_user_who_is_not_a_curator_is_refused(self):
        self.client.force_login(self.other)

        self.assertEqual(self.client.get(ADD).status_code, 403)
        page = self.client.get(reverse("repo:materials"))
        self.assertNotContains(page, f'href="{ADD}"')
        self.assertContains(page, f'action="{LOGOUT}"')
