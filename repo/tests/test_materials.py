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
