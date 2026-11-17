from django.core.exceptions import ValidationError
from django.test import TestCase

from repo.models import Phase
from repo.phases import normalise_label

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
