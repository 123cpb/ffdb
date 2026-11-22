import base64

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
        # Listed: with no data, only a listed material is public, and so is its drawing.
        cls.material = Material.objects.create(name="RM734", smiles=RM734, listed=True)

    def test_structure_address_returns_an_svg_browsers_may_keep(self):
        response = self.client.get(reverse("repo:structure", args=[self.material.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", response.content)
        self.assertEqual(response["Cache-Control"], "max-age=86400")

    def test_thumbnail_is_drawn_200_by_128(self):
        response = self.client.get(self.material.thumbnail_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"width='200px' height='128px'", response.content)

    def test_unknown_number_has_no_structure(self):
        response = self.client.get(reverse("repo:structure", args=[9999]))

        self.assertEqual(response.status_code, 404)

    def test_page_shows_drawing_formula_and_molar_mass(self):
        response = self.client.get(reverse("repo:material", args=[self.material.pk]))

        self.assertContains(response, f'<img src="{self.material.structure_url}"')
        self.assertContains(response, "C<sub>22</sub>H<sub>17</sub>NO<sub>8</sub>")
        self.assertContains(response, "423.4 g/mol")

    def test_the_admin_draws_even_a_hidden_structure_in_its_own_page(self):
        Material.objects.filter(pk=self.material.pk).update(listed=False)
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))

        response = self.client.get(reverse("admin:repo_material_change", args=[self.material.pk]))

        drawing = base64.b64encode(chem.structure_svg(self.material.canonical_smiles).encode()).decode()
        self.assertContains(response, f'<img src="data:image/svg+xml;base64,{drawing}"')
        self.assertNotContains(response, "structure.svg")  # the public address, which now gives 404

    def test_a_changed_structure_gets_a_new_address(self):
        old_url = self.material.structure_url

        self.material.smiles = RM734.replace("[N+](=O)[O-]", "C#N")  # nitro to cyano
        self.material.save()

        self.assertIn(self.material.inchikey, self.material.structure_url)
        self.assertNotEqual(self.material.structure_url, old_url)
