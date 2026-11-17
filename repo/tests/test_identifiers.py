from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from repo import chem
from repo.models import Material

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
RM734_RETYPED = "c1c(c(ccc1OC)C(=O)Oc1ccc(cc1)C(=O)Oc1ccc(cc1)[N+](=O)[O-])OC"
DIMETHYLCYCLOHEXANE = "CC1CCC(C)CC1"  # no stereochemistry given
TRANS_DIMETHYLCYCLOHEXANE = "C[C@H]1CC[C@H](C)CC1"


class StandardiseTests(SimpleTestCase):
    def test_two_typings_of_one_structure_give_the_same_identifiers(self):
        self.assertEqual(chem.standardise(RM734), chem.standardise(RM734_RETYPED))

    def test_unreadable_smiles_is_an_error(self):
        with self.assertRaises(chem.StructureError):
            chem.standardise("not a smiles")

    def test_stereochemistry_is_kept_and_changes_only_the_second_block(self):
        plain_smiles, plain_key = chem.standardise(DIMETHYLCYCLOHEXANE)
        trans_smiles, trans_key = chem.standardise(TRANS_DIMETHYLCYCLOHEXANE)

        self.assertNotIn("@", plain_smiles)
        self.assertIn("@", trans_smiles)
        self.assertNotEqual(plain_key, trans_key)
        self.assertEqual(chem.skeleton_key(plain_key), chem.skeleton_key(trans_key))


class StoredIdentifierTests(TestCase):
    def test_saving_stores_the_inchikey_and_canonical_smiles(self):
        material = Material.objects.create(name="RM734", smiles=RM734_RETYPED)
        material.refresh_from_db()

        self.assertEqual(material.inchikey, "GTOBZXOWFFWRDY-UHFFFAOYSA-N")
        self.assertEqual(material.canonical_smiles, RM734)


class AdminStructureTests(TestCase):
    add_url = reverse("admin:repo_material_add")

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))

    def add(self, name, smiles):
        """Fill in the admin's Add material form and press Save, with no phase sequence rows."""
        data = {
            "name": name, "aliases": "", "smiles": smiles, "notes": "",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        return self.client.post(self.add_url, data, follow=True)

    def warnings(self, response):
        return [str(m) for m in response.context["messages"] if m.level_tag == "warning"]

    def test_unreadable_smiles_is_refused(self):
        response = self.add("Nonsense", "not a smiles")

        self.assertContains(response, "RDKit could not read")
        self.assertFalse(Material.objects.exists())

    def test_structure_already_in_ffdb_is_refused_naming_the_material(self):
        first = Material.objects.create(name="RM734", smiles=RM734)

        response = self.add("RM734 again", RM734_RETYPED)

        self.assertContains(response, f"already in FFDB as {first.accession} (RM734)")
        self.assertEqual(Material.objects.count(), 1)

    def test_same_connectivity_is_saved_with_a_warning_naming_the_other(self):
        first_response = self.add("1,4-dimethylcyclohexane", DIMETHYLCYCLOHEXANE)
        second_response = self.add("trans-1,4-dimethylcyclohexane", TRANS_DIMETHYLCYCLOHEXANE)

        self.assertEqual(Material.objects.count(), 2)
        self.assertEqual(self.warnings(first_response), [])
        first = Material.objects.get(smiles=DIMETHYLCYCLOHEXANE)
        [warning] = self.warnings(second_response)
        self.assertIn(f"{first.accession} (1,4-dimethylcyclohexane)", warning)
