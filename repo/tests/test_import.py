import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from repo.models import Material, PhaseSequence, Reference

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
RM734_RETYPED = "c1c(c(ccc1OC)C(=O)Oc1ccc(cc1)C(=O)Oc1ccc(cc1)[N+](=O)[O-])OC"
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
DIMETHYLCYCLOHEXANE = "CC1CCC(C)CC1"  # no stereochemistry given
TRANS_DIMETHYLCYCLOHEXANE = "C[C@H]1CC[C@H](C)CC1"

RM734_SUMMARY = "K 139.8 NF 132.7 N 187.9 I"
ARTICLE = "10.1039/c7cp00456g"
ARXIV = "10.48550/arxiv.2505.03009"
CHEMRXIV = "10.26434/chemrxiv.15004113/v1"
PATENT = "EP3260450A1"
THESIS = "RJM PhD Thesis"

# A small list in the merged list's form. Each row's outcome is: its material; its sequence.
SAMPLE = [
    ["origin", "line", "name", "smiles", "sequence", "reference", "reference_kind"],
    ["NF", "2", "RM734", RM734, RM734_SUMMARY, ARTICLE, "doi"],  # added; added, with its reference
    ["NF", "3", "", FIVE_CB, "", "10.1000/values-only", "doi"],  # added, with no name; none, so no reference
    # already there: RM734's name, written differently; already there, as "Nf" is NF
    ["NF", "4", "rm-734", RM734_RETYPED, "K 139.8 Nf 132.7 N 187.9 I", ARTICLE, "doi"],
    ["NF", "5", "Compound 5", RM734, "K 140 N 188 I", ARTICLE, "doi"],  # alias of RM734; added, a second sequence
    ["NF", "6", "22", FIVE_CB, "K 22.5 N 35 I", ARXIV, "doi"],  # alias of the material with no name; added
    ["NF", "7", "Nonsense", "not a smiles", "K 1 I", "10.1000/nonsense", "doi"],  # skipped; skipped
    ["LCPROP", "8", "NoSmiles", "", "", PATENT, "patent"],  # skipped; none
    ["LCPROP", "9", "DMCH", DIMETHYLCYCLOHEXANE, "K 10 N 20 I", PATENT, "patent"],  # added; added
    ["LCPROP", "10", "trans-DMCH", TRANS_DIMETHYLCYCLOHEXANE, "K 5 N 15 I", CHEMRXIV, "doi"],  # added, with a warning; added
    ["NF", "11", "RM734", RM734, RM734_SUMMARY, THESIS, "thesis"],  # already there; added, from another reference
    ["NF", "12", "RM734", RM734, "K 174 NF 107 N 215 I", "SM-ART-12-2025-001206", "manuscript"],  # already there; skipped
    ["NF", "13", "RM734", RM734, "K 100 Q 50 I", "10.1000/unreadable", "doi"],  # already there; skipped
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

    def references(self):
        return dict(Reference.objects.values_list("identifier", "kind"))

    def sequences(self):
        return list(PhaseSequence.objects.order_by("pk").values_list("material__name", "reference__identifier", "sequence"))

    def test_counts_add_up_to_the_rows_read(self):
        output = self.run_import()

        self.assertIn(
            "Rows read: 12\nMaterials added: 4\nNames added as aliases: 2\n"
            "Materials already there: 4\nRows skipped: 2\n"
            "References added: 5\nSequences added: 6\nSequences already there: 1\nSequences skipped: 3\n",
            output,
        )

    def test_imported_references_and_sequences_record_no_one(self):
        self.run_import()

        for model in (Reference, PhaseSequence):
            with self.subTest(model.__name__):
                self.assertTrue(model.objects.exists())
                # Leave out the rows where both are blank, and nothing is left.
                self.assertFalse(model.objects.exclude(entered_by=None, entered_at=None).exists())

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
        before = (self.materials(), self.references(), self.sequences())

        output = self.run_import()

        self.assertIn("Materials added: 0\nNames added as aliases: 0\nMaterials already there: 10\n", output)
        self.assertIn(
            "References added: 0\nSequences added: 0\nSequences already there: 7\nSequences skipped: 3\n",
            output,
        )
        self.assertEqual((self.materials(), self.references(), self.sequences()), before)

    def test_each_reference_is_added_once_with_its_kind(self):
        self.run_import()

        # Not the references of rows whose sequence was skipped or missing.
        self.assertEqual(
            self.references(),
            {
                ARTICLE: Reference.Kind.ARTICLE,
                ARXIV: Reference.Kind.PREPRINT,
                CHEMRXIV: Reference.Kind.PREPRINT,
                PATENT: Reference.Kind.PATENT,
                THESIS: Reference.Kind.THESIS,
            },
        )

    def test_each_row_with_a_sequence_adds_it_once(self):
        self.run_import()

        rm734 = Material.objects.get(name="RM734")
        self.assertEqual(
            set(rm734.phase_sequences.values_list("reference__identifier", "standard_sequence")),
            {
                (ARTICLE, "Cr 139.8 NF 132.7 N 187.9 Iso"),  # line 2; line 4 is the same sequence
                (ARTICLE, "Cr 140 N 188 Iso"),  # line 5: same structure and reference, both kept
                (THESIS, "Cr 139.8 NF 132.7 N 187.9 Iso"),  # line 11
            },
        )
        self.assertEqual(PhaseSequence.objects.count(), 6)

    def test_skipped_sequences_are_printed_with_line_and_reason(self):
        output = self.run_import()

        self.assertIn(
            "NF line 12 (RM734): sequence skipped, reference kind 'manuscript' isn't one FFDB takes "
            "(SM-ART-12-2025-001206)",
            output,
        )
        self.assertIn("NF line 13 (RM734): sequence skipped, Unknown phase label: Q.", output)
        self.assertNotIn("NF line 7 (Nonsense): sequence skipped", output)  # its skipped row is enough

    def test_same_connectivity_is_printed_naming_the_other(self):
        output = self.run_import()

        dmch = Material.objects.get(name="DMCH")
        self.assertIn("LCPROP line 10: LCR-", output)
        self.assertIn(f"has the same connectivity as {dmch.accession} (DMCH)", output)

    def test_a_file_without_the_columns_is_refused(self):
        self.write([["title", "SMILES"], ["RM734", RM734]])  # the raw NF list's header

        with self.assertRaisesMessage(CommandError, "has no origin, line, name, smiles, sequence, reference, reference_kind column"):
            self.run_import()
        self.assertFalse(Material.objects.exists())


class NameTests(SimpleTestCase):
    def test_names_are_compared_by_letters_and_digits_only(self):
        material = Material(name="RM734", aliases="DIO-1, Compound 5")

        self.assertTrue(material.has_name("rm-734"))
        self.assertTrue(material.has_name("dio1"))
        self.assertTrue(material.has_name("compound5"))
        self.assertFalse(material.has_name("RM735"))
