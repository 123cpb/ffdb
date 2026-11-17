from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from repo.models import Phase
from repo.phases import PhaseSequenceError, normalise_label, parse_summary, phase_labels, tokenise

# The test database is built by running the migrations, so the 26 phases from
# 0005_seed_phases are already in it.


class SynonymMapTests(TestCase):
    def setUp(self):
        self.synonyms = Phase.synonym_map()

    def standard(self, label):
        return self.synonyms[normalise_label(label)]

    def test_k_i_and_nx_become_cr_iso_and_ns(self):
        self.assertEqual(self.standard("K"), "Cr")
        self.assertEqual(self.standard("I"), "Iso")
        self.assertEqual(self.standard("NX"), "NS")

    def test_case_and_underscores_are_ignored(self):
        self.assertEqual(self.standard("n_f"), "NF")

    def test_ntbf_and_hcnf_are_one_phase(self):
        self.assertEqual(self.standard("HCNF"), "NTBF")
        self.assertEqual(Phase.objects.count(), 26)


class ClashTests(TestCase):
    def test_a_synonym_another_phase_has_is_refused(self):
        phase = Phase(code="NX2", name="Test phase", synonyms="M3, N_X")
        with self.assertRaisesMessage(ValidationError, "N_X (NS)"):
            phase.full_clean()

    def test_a_code_differing_only_in_case_is_refused(self):
        phase = Phase(code="nf", name="Test phase")
        with self.assertRaisesMessage(ValidationError, "Phase NF already has this label."):
            phase.full_clean()

    def test_a_saved_phase_does_not_clash_with_itself(self):
        Phase.objects.get(code="NS").full_clean()  # raises if it does


# The parser needs only a dict of normalised label -> code, so these tests write a small
# one instead of reading the Phase table. SimpleTestCase refuses any database query.
SYNONYMS = {"k": "Cr", "cr": "Cr", "i": "Iso", "iso": "Iso", "n": "N", "nf": "NF"}


class SummaryTests(SimpleTestCase):
    def read(self, text):
        return parse_summary(text, SYNONYMS)

    def transitions(self, parsed):
        return [(t.upper, t.lower, t.temperature_c) for t in parsed.transitions]

    def test_plain_summary(self):
        parsed = self.read("K 73.2 Nf 96.8 N 127.2 I")
        self.assertEqual(parsed.phases, ["Cr", "NF", "N", "Iso"])
        self.assertEqual(
            self.transitions(parsed), [("Iso", "N", 127.2), ("N", "NF", 96.8), ("NF", "Cr", 73.2)]
        )
        self.assertEqual(parsed.standard_text(), "Cr 73.2 NF 96.8 N 127.2 Iso")

    def test_monotropic_nf_below_the_melting_point(self):
        # The NF forms at 132.7, below the melting point: it is seen only on cooling. The
        # melting point stays in the text but isn't a transition.
        parsed = self.read("K 139.8 NF 132.7 N 187.9 I")
        self.assertEqual(parsed.phases, ["Cr", "NF", "N", "Iso"])
        self.assertEqual(self.transitions(parsed), [("Iso", "N", 187.9), ("N", "NF", 132.7)])
        self.assertEqual(parsed.standard_text(), "Cr 139.8 NF 132.7 N 187.9 Iso")

    def test_no_melting_point_given(self):
        for text in ("K ?? N 24 I", "K N 24 I"):
            with self.subTest(text):
                parsed = self.read(text)
                self.assertEqual(parsed.phases, ["Cr", "N", "Iso"])
                self.assertEqual(self.transitions(parsed), [("Iso", "N", 24.0)])
                self.assertEqual(parsed.standard_text(), "Cr ? N 24 Iso")

    def test_an_unknown_label_is_named(self):
        with self.assertRaisesMessage(PhaseSequenceError, "Unknown phase label: Q."):
            self.read("K 100 Q 50 I")

    def test_temperatures_out_of_order(self):
        with self.assertRaisesMessage(PhaseSequenceError, "must rise from left to right"):
            self.read("K 50 NF 120 N 100 I")

    def test_iso_not_last(self):
        with self.assertRaisesMessage(PhaseSequenceError, "the isotropic liquid must be the last phase"):
            self.read("K 50 I 100 N")

    def test_minus_signs_are_kept_and_pasted_separators_are_not(self):
        self.assertEqual(self.transitions(self.read("K -14.9 I")), [("Iso", "Cr", -14.9)])
        self.assertEqual(tokenise("Cr–61.4 °C–I"), ["Cr", "61.4", "I"])

    def test_phase_labels_leave_out_temperatures_question_marks_and_separators(self):
        self.assertEqual(phase_labels("K 61.4 °C → I"), ["K", "I"])
        self.assertEqual(phase_labels("Cr ? N 24 Iso"), ["Cr", "N", "Iso"])
