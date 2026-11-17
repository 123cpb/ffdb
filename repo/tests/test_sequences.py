from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Reference
from repo.phases import PhaseSequenceError

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
RM734_SUMMARY = "K 139.8 NF 132.7 N 187.9 I"


class SequenceTestCase(TestCase):
    """RM734 and one reference. The phases are already in the test database, from the
    0005_seed_phases migration."""

    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017
        )

    def add(self, sequence):
        return PhaseSequence.objects.create(material=self.material, reference=self.reference, sequence=sequence)


def codes(sequence):
    return set(sequence.phases.values_list("code", flat=True))


class StoredFieldTests(SequenceTestCase):
    def test_saving_fills_in_the_worked_out_fields(self):
        sequence = self.add(RM734_SUMMARY)
        sequence.refresh_from_db()

        self.assertEqual(sequence.standard_sequence, "Cr 139.8 NF 132.7 N 187.9 Iso")
        self.assertEqual(codes(sequence), {"Cr", "NF", "N", "Iso"})
        self.assertEqual(sequence.t_clearing_c, 187.9)
        # Above the monotropic NF, so not one of the transitions, but it is the melting point.
        self.assertEqual(sequence.t_melting_c, 139.8)

    def test_saving_a_corrected_sequence_replaces_its_phases(self):
        sequence = self.add(RM734_SUMMARY)
        sequence.sequence = "K 139.8 N 187.9 I"
        sequence.save()

        self.assertEqual(codes(sequence), {"Cr", "N", "Iso"})

    def test_a_melting_point_not_given_is_left_empty(self):
        sequence = self.add("K ?? N 24 I")

        self.assertIsNone(sequence.t_melting_c)
        self.assertEqual(sequence.t_clearing_c, 24)


class RefusedSequenceTests(SequenceTestCase):
    def test_an_unknown_label_is_refused_naming_it(self):
        sequence = PhaseSequence(material=self.material, reference=self.reference, sequence="K 100 Q 50 I")
        with self.assertRaisesMessage(ValidationError, "Unknown phase label: Q."):
            sequence.full_clean()

    def test_saving_without_full_clean_is_refused_too(self):
        with self.assertRaisesMessage(PhaseSequenceError, "Unknown phase label: Q."):
            self.add("K 100 Q 50 I")
        self.assertFalse(PhaseSequence.objects.exists())

    def test_the_database_requires_a_reference(self):
        # atomic() keeps the failed insert from spoiling the rest of the test's transaction.
        with transaction.atomic(), self.assertRaises(IntegrityError):
            PhaseSequence.objects.create(material=self.material, sequence=RM734_SUMMARY)


class DeletionTests(SequenceTestCase):
    def test_a_reference_with_sequences_cannot_be_deleted(self):
        self.add(RM734_SUMMARY)
        with self.assertRaises(ProtectedError):
            self.reference.delete()

    def test_deleting_a_material_deletes_its_sequences(self):
        self.add(RM734_SUMMARY)
        self.material.delete()
        self.assertFalse(PhaseSequence.objects.exists())


class AdminTests(SequenceTestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))

    def save_on_material_page(self, sequence):
        """Fill in the empty sequence row on RM734's admin page and press Save. The
        management form tells Django how many rows the page sent."""
        url = reverse("admin:repo_material_change", args=[self.material.pk])
        data = {
            "name": "RM734", "aliases": "", "smiles": RM734, "notes": "",
            "phase_sequences-TOTAL_FORMS": "1", "phase_sequences-INITIAL_FORMS": "0",
            "phase_sequences-0-reference": str(self.reference.pk),
            "phase_sequences-0-sequence": sequence,
            "phase_sequences-0-notes": "",
        }
        return self.client.post(url, data)

    def test_a_sequence_added_on_the_material_page_is_saved_with_its_phases(self):
        response = self.save_on_material_page(RM734_SUMMARY)

        self.assertEqual(response.status_code, 302)  # saved, and sent back to the list
        sequence = self.material.phase_sequences.get()
        self.assertEqual(codes(sequence), {"Cr", "NF", "N", "Iso"})
        self.assertEqual(sequence.t_clearing_c, 187.9)

    def test_an_unreadable_sequence_is_refused_with_the_parsers_message(self):
        response = self.save_on_material_page("K 100 Q 50 I")

        self.assertContains(response, "Unknown phase label: Q.")
        self.assertFalse(PhaseSequence.objects.exists())

    def test_the_sequence_is_listed_on_the_material_and_reference_pages(self):
        self.add(RM734_SUMMARY)
        for url in (
            reverse("admin:repo_material_change", args=[self.material.pk]),
            reverse("admin:repo_reference_change", args=[self.reference.pk]),
        ):
            with self.subTest(url):
                self.assertContains(self.client.get(url), "Cr 139.8 NF 132.7 N 187.9 Iso")


class RenamedPhaseTests(SimpleTestCase):
    """phase_was_renamed() reads only the two stored texts, so these sequences are never saved."""

    def renamed(self, reported, standard):
        return PhaseSequence(sequence=reported, standard_sequence=standard).phase_was_renamed()

    def test_nx_read_as_ns_and_hcnf_read_as_ntbf_are_renamed(self):
        self.assertTrue(self.renamed("K 100 NX 120 N 150 I", "Cr 100 NS 120 N 150 Iso"))
        self.assertTrue(self.renamed("K 90 HCNF 110 I", "Cr 90 NTBF 110 Iso"))

    def test_k_and_i_and_a_change_of_case_are_not(self):
        self.assertFalse(self.renamed(RM734_SUMMARY, "Cr 139.8 NF 132.7 N 187.9 Iso"))
        self.assertFalse(self.renamed("K 139.8 Nf 132.7 N 187.9 I", "Cr 139.8 NF 132.7 N 187.9 Iso"))


class MaterialPageTests(SequenceTestCase):
    def page(self):
        return self.client.get(reverse("repo:material", args=[self.material.pk]))

    def test_each_sequence_is_listed_with_its_reference(self):
        patent = Reference.objects.create(kind=Reference.Kind.PATENT, identifier="EP3260450A1")
        self.add(RM734_SUMMARY)
        PhaseSequence.objects.create(material=self.material, reference=patent, sequence="K 140 N 188 I")

        response = self.page()

        self.assertContains(response, "Cr 139.8 NF 132.7 N 187.9 Iso")
        self.assertContains(response, "Cr 140 N 188 Iso")
        self.assertContains(response, f'<a href="{self.reference.link}">Mandle 2017</a>', html=True)
        self.assertContains(response, f'<a href="{patent.link}">EP3260450A1</a>', html=True)

    def test_a_renamed_phase_shows_the_sequence_as_reported(self):
        self.add("K 100 NX 120 N 150 I")

        response = self.page()

        self.assertContains(response, "Cr 100 NS 120 N 150 Iso")
        self.assertContains(response, "Reported as K 100 NX 120 N 150 I")

    def test_k_and_i_alone_do_not_show_the_sequence_as_reported(self):
        self.add(RM734_SUMMARY)

        self.assertNotContains(self.page(), "Reported as")

    def test_a_material_without_sequences_says_so(self):
        response = self.page()

        self.assertContains(response, "No phase sequences yet.")
        self.assertNotContains(response, "Liquid-crystal phases")

    def test_liquid_crystal_phases_are_listed_once_in_vocabulary_order(self):
        self.add(RM734_SUMMARY)
        self.add("K 140 N 188 I")  # N again, from a second sequence

        self.assertEqual([phase.code for phase in self.material.liquid_crystal_phases()], ["N", "NF"])
        self.assertContains(self.page(), "Liquid-crystal phases: N, NF")

    def test_a_material_with_no_liquid_crystal_phase_says_none_reported(self):
        self.add("K 61.4 I")

        self.assertContains(self.page(), "Liquid-crystal phases: none reported")
