import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from repo.models import Material

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
RM734_RETYPED = "c1c(c(ccc1OC)C(=O)Oc1ccc(cc1)C(=O)Oc1ccc(cc1)[N+](=O)[O-])OC"
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
DIMETHYLCYCLOHEXANE = "CC1CCC(C)CC1"  # no stereochemistry given
TRANS_DIMETHYLCYCLOHEXANE = "C[C@H]1CC[C@H](C)CC1"

# A small list in the merged list's form. The sequence column shows that other columns are ignored.
SAMPLE = [
    ["origin", "line", "name", "smiles", "sequence"],
    ["NF", "2", "RM734", RM734, "K 139.8 NF 132.7 N 187.9 I"],  # added
    ["NF", "3", "", FIVE_CB, ""],  # added, with no name
    ["NF", "4", "rm-734", RM734_RETYPED, ""],  # nothing new: RM734's name, written differently
    ["NF", "5", "Compound 5", RM734, ""],  # alias of RM734
    ["NF", "6", "22", FIVE_CB, ""],  # alias of the material with no name
    ["NF", "7", "Nonsense", "not a smiles", ""],  # skipped
    ["LCPROP", "8", "NoSmiles", "", ""],  # skipped
    ["LCPROP", "9", "DMCH", DIMETHYLCYCLOHEXANE, ""],  # added
    ["LCPROP", "10", "trans-DMCH", TRANS_DIMETHYLCYCLOHEXANE, ""],  # added, with a warning
]


class ImportTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "merged.csv"
        self.write(SAMPLE)

    def write(self, rows):
        with open(self.path, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)

    def run_import(self):
        """Run the command on the file and return what it printed."""
        out = StringIO()
        call_command("import_materials", str(self.path), stdout=out)
        return out.getvalue()

    def materials(self):
        return list(Material.objects.order_by("pk").values_list("pk", "name", "aliases"))

    def test_counts_add_up_to_the_rows_read(self):
        output = self.run_import()

        self.assertIn(
            "Rows read: 9\nMaterials added: 4\nNames added as aliases: 2\n"
            "Rows with nothing new: 1\nRows skipped: 2\n",
            output,
        )

    def test_new_structures_are_added_and_new_names_become_aliases(self):
        self.run_import()

        self.assertEqual(Material.objects.count(), 4)
        self.assertEqual(Material.objects.get(name="RM734").aliases, "Compound 5")
        nameless = Material.objects.get(smiles=FIVE_CB)
        self.assertEqual((nameless.name, nameless.aliases), ("", "22"))

    def test_skipped_rows_are_printed_with_line_and_reason(self):
        output = self.run_import()

        self.assertIn("NF line 7 (Nonsense): skipped, RDKit could not read", output)
        self.assertIn("LCPROP line 8 (NoSmiles): skipped, no SMILES", output)

    def test_running_it_twice_adds_nothing(self):
        self.run_import()
        before = self.materials()

        output = self.run_import()

        self.assertIn("Materials added: 0\nNames added as aliases: 0\nRows with nothing new: 7\n", output)
        self.assertEqual(self.materials(), before)

    def test_same_connectivity_is_printed_naming_the_other(self):
        output = self.run_import()

        dmch = Material.objects.get(name="DMCH")
        self.assertIn("LCPROP line 10: LCR-", output)
        self.assertIn(f"has the same connectivity as {dmch.accession} (DMCH)", output)

    def test_a_file_without_the_columns_is_refused(self):
        self.write([["title", "SMILES"], ["RM734", RM734]])  # the raw NF list's header

        with self.assertRaisesMessage(CommandError, "has no origin, line, name, smiles column"):
            self.run_import()
        self.assertFalse(Material.objects.exists())


class NameTests(SimpleTestCase):
    def test_names_are_compared_by_letters_and_digits_only(self):
        material = Material(name="RM734", aliases="DIO-1, Compound 5")

        self.assertTrue(material.has_name("rm-734"))
        self.assertTrue(material.has_name("dio1"))
        self.assertTrue(material.has_name("compound5"))
        self.assertFalse(material.has_name("RM735"))
