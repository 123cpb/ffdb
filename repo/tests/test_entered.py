"""Who entered a reference, sequence or series, and when: filled in when a row is added in
the admin, and blank for rows the imports add."""

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from repo.models import Material, PhaseSequence, Property, Reference, Series, Variable
from repo.tests.test_sequences import RM734, RM734_SUMMARY


class EnteredTestCase(TestCase):
    """RM734, with a reference and a sequence added as the imports add them, and a curator
    logged in to the admin: staff, in the Curators group from migration 0013, not a superuser."""

    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017
        )
        cls.imported = PhaseSequence.objects.create(
            material=cls.material, reference=cls.reference, sequence=RM734_SUMMARY
        )
        cls.curator = User.objects.create_user("curator", password="a-test-password", is_staff=True)
        cls.curator.groups.add(Group.objects.get(name="Curators"))

    def setUp(self):
        self.client.force_login(self.curator)
        self.start = timezone.now()

    def assertEnteredByCurator(self, row):
        """The row records the curator, at a time during this test."""
        row.refresh_from_db()
        self.assertEqual(row.entered_by, self.curator)
        self.assertTrue(self.start <= row.entered_at <= timezone.now())

    def assertImported(self, row):
        """The row records no one and no time."""
        row.refresh_from_db()
        self.assertIsNone(row.entered_by)
        self.assertIsNone(row.entered_at)


class AdminTests(EnteredTestCase):
    def test_a_reference_added_in_the_admin_records_who_and_when(self):
        data = {
            "kind": "patent", "identifier": "EP3260450A1", "first_author": "", "year": "",
            "title": "", "venue": "", "notes": "",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        response = self.client.post(reverse("admin:repo_reference_add"), data)

        self.assertEqual(response.status_code, 302)  # saved, and sent back to the list
        self.assertEnteredByCurator(Reference.objects.get(identifier="EP3260450A1"))

    def test_a_sequence_added_on_a_material_page_records_who_and_when(self):
        # Row 0 is the imported sequence, with its notes changed; row 1 is a new one.
        url = reverse("admin:repo_material_change", args=[self.material.pk])
        data = {
            "name": "RM734", "aliases": "", "smiles": RM734, "notes": "",
            "phase_sequences-TOTAL_FORMS": "2", "phase_sequences-INITIAL_FORMS": "1",
            "phase_sequences-0-id": str(self.imported.pk),
            "phase_sequences-0-material": str(self.material.pk),
            "phase_sequences-0-reference": str(self.reference.pk),
            "phase_sequences-0-sequence": RM734_SUMMARY,
            "phase_sequences-0-notes": "Checked against the paper.",
            "phase_sequences-1-reference": str(self.reference.pk),
            "phase_sequences-1-sequence": "K 140 NF 133 N 188 I",
            "phase_sequences-1-notes": "",
        }
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, 302)
        self.assertEnteredByCurator(PhaseSequence.objects.get(sequence="K 140 NF 133 N 188 I"))
        # Changing a row isn't entering it.
        self.assertImported(self.imported)
        self.assertEqual(self.imported.notes, "Checked against the paper.")

    def test_a_series_added_in_the_admin_records_who_and_when(self):
        data = {
            "material": self.material.pk,
            "reference": self.reference.pk,
            "property": Property.objects.get(code="birefringence").pk,
            "variable": Variable.objects.get(code="temperature").pk,
            "kind": "measured", "method": "", "conditions": '{"wavelength_nm": 589}',
            "direction": "unspecified", "taken_from": "", "location": "", "notes": "",
            "points-TOTAL_FORMS": "1", "points-INITIAL_FORMS": "0",
            "points-0-qualifier": "exact", "points-0-value": "0.21", "points-0-uncertainty": "",
            "points-0-temperature_c": "25", "points-0-x": "",
        }
        response = self.client.post(reverse("admin:repo_series_add"), data)

        self.assertEqual(response.status_code, 302)
        self.assertEnteredByCurator(Series.objects.get())

    def test_changing_an_imported_reference_in_the_admin_records_nothing(self):
        data = {
            "kind": "article", "identifier": "10.1039/c7cp00456g", "first_author": "Mandle",
            "year": "2017", "title": "", "venue": "", "notes": "Corrected.",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        url = reverse("admin:repo_reference_change", args=[self.reference.pk])
        response = self.client.post(url, data)

        self.assertEqual(response.status_code, 302)
        self.assertImported(self.reference)
        self.assertEqual(self.reference.notes, "Corrected.")

    def test_the_reference_list_filters_by_who_entered_them_and_when(self):
        entered = Reference(kind=Reference.Kind.PATENT, identifier="EP3260450A1")
        entered.mark_entered(self.curator)
        entered.save()
        url = reverse("admin:repo_reference_changelist")

        for query, expected in [
            ({"entered_by__id__exact": self.curator.pk}, [entered]),
            ({"entered_by__isnull": "True"}, [self.reference]),  # "-": imported
            ({"entered_at__isnull": "True"}, [self.reference]),  # "No date": imported
        ]:
            with self.subTest(query):
                response = self.client.get(url, query)
                self.assertEqual(list(response.context["cl"].result_list), expected)


class DeletedAccountTests(EnteredTestCase):
    def test_deleting_a_users_account_keeps_what_they_entered(self):
        self.reference.mark_entered(self.curator)
        self.reference.save()

        self.curator.delete()

        self.reference.refresh_from_db()
        self.assertIsNone(self.reference.entered_by)
        # The time stays, so the reference doesn't look imported.
        self.assertIsNotNone(self.reference.entered_at)
