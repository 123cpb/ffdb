from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from repo.models import Material, accession


class PublicIdTests(SimpleTestCase):
    def test_public_id_is_made_from_the_primary_key(self):
        self.assertEqual(accession(12), "LCR-00012")
        self.assertEqual(Material(pk=12).accession, "LCR-00012")


class MaterialAdminTests(TestCase):
    def test_admin_list_shows_public_id_and_name(self):
        admin_user = User.objects.create_superuser("admin", password="pw")
        self.client.force_login(admin_user)
        material = Material.objects.create(
            name="RM734", smiles="COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
        )

        response = self.client.get(reverse("admin:repo_material_changelist"))

        self.assertContains(response, material.accession)
        self.assertContains(response, "RM734")


class MaterialPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(
            name="RM734",
            aliases="RM-734",
            smiles="COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1",
        )
        cls.url = reverse("repo:material", args=[cls.material.pk])

    def test_page_shows_id_name_aliases_and_smiles(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.material.accession)
        self.assertContains(response, "RM734")
        self.assertContains(response, "Also called RM-734")
        self.assertContains(response, self.material.smiles)

    def test_unknown_number_is_not_found(self):
        response = self.client.get(reverse("repo:material", args=[9999]))

        self.assertEqual(response.status_code, 404)

    def test_edit_in_admin_link_is_for_staff_only(self):
        edit_url = reverse("admin:repo_material_change", args=[self.material.pk])

        self.assertNotContains(self.client.get(self.url), edit_url)

        self.client.force_login(User.objects.create_user("curator", is_staff=True))
        self.assertContains(self.client.get(self.url), edit_url)
