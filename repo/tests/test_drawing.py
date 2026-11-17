from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from repo import chem
from repo.models import Material
from repo.templatetags.repo_tags import formula

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"


class FormulaTests(SimpleTestCase):
    def test_formula_and_mass_of_rm734(self):
        rm734_formula, mass = chem.formula_and_mass(RM734)

        self.assertEqual(rm734_formula, "C22H17NO8")
        self.assertAlmostEqual(mass, 423.38, places=2)

    def test_counts_are_subscripted_but_a_charge_is_not(self):
        self.assertEqual(formula("C22H17NO8"), "C<sub>22</sub>H<sub>17</sub>NO<sub>8</sub>")
        self.assertEqual(formula("Ca+2"), "Ca+2")


class StructureDrawingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)

    def test_structure_address_returns_an_svg_browsers_may_keep(self):
        response = self.client.get(reverse("repo:structure", args=[self.material.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content)
        self.assertEqual(response["Cache-Control"], "max-age=86400")

    def test_unknown_number_has_no_structure(self):
        response = self.client.get(reverse("repo:structure", args=[9999]))

        self.assertEqual(response.status_code, 404)

    def test_page_shows_drawing_formula_and_molar_mass(self):
        response = self.client.get(reverse("repo:material", args=[self.material.pk]))

        self.assertContains(response, f'<img src="{self.material.structure_url}"')
        self.assertContains(response, "C<sub>22</sub>H<sub>17</sub>NO<sub>8</sub>")
        self.assertContains(response, "423.4 g/mol")

    def test_admin_shows_the_drawing(self):
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))

        response = self.client.get(reverse("admin:repo_material_change", args=[self.material.pk]))

        self.assertContains(response, f'<img src="{self.material.structure_url}"')

    def test_a_changed_structure_gets_a_new_address(self):
        old_url = self.material.structure_url

        self.material.smiles = RM734.replace("[N+](=O)[O-]", "C#N")  # nitro to cyano
        self.material.save()

        self.assertIn(self.material.inchikey, self.material.structure_url)
        self.assertNotEqual(self.material.structure_url, old_url)
