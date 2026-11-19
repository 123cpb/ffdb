import json

from django.apps import apps
from django.contrib.auth.models import User
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, MixtureComponent, PhaseSequence, Property, Reference, RetiredID, Series, Variable
from repo.tests.test_mixtures import CB5
from repo.tests.test_sequences import RM734, RM734_SUMMARY


def all_rows():
    """Every row of every FFDB model, as (model, primary key) pairs, named as the snapshot
    names them, e.g. ("repo.phasesequence", 3)."""
    return {
        (model._meta.label_lower, pk)
        for model in apps.get_app_config("repo").get_models()
        for pk in model.objects.values_list("pk", flat=True)
    }


def snapshot_rows(record):
    return {(row["model"], row["pk"]) for row in json.loads(record.snapshot)}


class RetiredIDTests(TestCase):
    """RM734, with a sequence and a series of two points, and F7, a mixture with 5CB as a
    component. An admin is logged in: only admins delete materials."""

    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.cb5 = Material.objects.create(name="5CB", smiles=CB5)
        cls.f7 = Material.objects.create(kind=Material.Kind.MIXTURE, name="F7")
        cls.component = MixtureComponent.objects.create(
            mixture=cls.f7, component=cls.cb5, fraction=30, unit=MixtureComponent.Unit.MOL
        )
        reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017
        )
        PhaseSequence.objects.create(material=cls.rm734, reference=reference, sequence=RM734_SUMMARY)
        series = Series.objects.create(
            material=cls.rm734, reference=reference, property=Property.objects.get(code="ps"),
            variable=Variable.objects.get(code="temperature"), kind=Series.Kind.MEASURED,
        )
        series.points.create(value=6.0, temperature_c=80)
        series.points.create(value=4.4, temperature_c=100)
        cls.admin = User.objects.create_superuser("admin", password="pw")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_deleting_a_material_saves_a_record_of_its_id_and_everything_deleted(self):
        before = all_rows()
        self.client.post(reverse("admin:repo_material_delete", args=[self.rm734.pk]), {"post": "yes"})
        gone = before - all_rows()

        record = RetiredID.objects.get()
        self.assertEqual(
            (record.number, record.kind, record.name, record.inchikey),
            (self.rm734.pk, Material.Kind.COMPOUND, "RM734", self.rm734.inchikey),
        )
        self.assertEqual((record.retired_by, record.reason), (self.admin, ""))
        # Every row that went is in the snapshot, and nothing else is.
        self.assertEqual(snapshot_rows(record), gone)
        self.assertEqual(
            {model for model, _ in gone},
            {"repo.material", "repo.phasesequence", "repo.phasetransition", "repo.series", "repo.measurement"},
        )

    def test_delete_selected_saves_a_record_for_each_material(self):
        before = all_rows()
        self.client.post(
            reverse("admin:repo_material_changelist"),
            {"action": "delete_selected", "_selected_action": [self.rm734.pk, self.f7.pk], "post": "yes"},
        )
        gone = before - all_rows()

        records = RetiredID.objects.in_bulk()  # {number: record}
        self.assertEqual(set(records), {self.rm734.pk, self.f7.pk})
        self.assertEqual(snapshot_rows(records[self.rm734.pk]) | snapshot_rows(records[self.f7.pk]), gone)
        self.assertIn(("repo.mixturecomponent", self.component.pk), snapshot_rows(records[self.f7.pk]))

    def test_a_delete_that_fails_saves_no_record(self):
        with self.assertRaises(ProtectedError):  # 5CB is a component of F7
            self.cb5.retire(self.admin)

        self.assertFalse(RetiredID.objects.exists())
        self.assertTrue(Material.objects.filter(pk=self.cb5.pk).exists())

    def test_a_record_can_be_given_a_reason_but_not_added_deleted_or_otherwise_changed(self):
        self.rm734.retire(self.admin)
        record = RetiredID.objects.get()
        change_url = reverse("admin:repo_retiredid_change", args=[record.pk])

        self.assertContains(self.client.get(reverse("admin:repo_retiredid_changelist")), record.accession)
        self.assertContains(self.client.get(change_url), "repo.phasesequence")  # the snapshot is shown
        self.assertEqual(self.client.get(reverse("admin:repo_retiredid_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:repo_retiredid_delete", args=[record.pk])).status_code, 403)

        self.client.post(change_url, {"reason": "Entered twice", "name": "Changed"})
        record.refresh_from_db()
        self.assertEqual((record.reason, record.name), ("Entered twice", "RM734"))


class RetiredPageTests(TestCase):
    """A retired ID's public page. A number never given out gives a 404: see
    test_materials.MaterialPageTests.test_unknown_number_is_not_found."""

    def retire(self, material, reason=""):
        number = material.pk  # read first: deleting an object sets its pk to None
        material.retire(None)  # no user: the page doesn't show who retired it
        RetiredID.objects.filter(number=number).update(reason=reason)
        return self.client.get(reverse("repo:material", args=[number]))

    def test_a_retired_compound_gives_410_with_what_it_was(self):
        material = Material.objects.create(name="RM734", smiles=RM734)
        accession = material.accession  # before retiring, which sets its pk to None
        response = self.retire(material, "Entered twice")

        for text in [accession, "RM734", "compound", material.inchikey, "Entered twice", "will never name anything else"]:
            with self.subTest(text=text):
                self.assertContains(response, text, status_code=410)
        self.assertNotContains(response, '"fields"', status_code=410)  # nothing from the snapshot

    def test_a_retired_mixture_has_no_inchikey_and_no_reason_until_given_one(self):
        response = self.retire(Material.objects.create(kind=Material.Kind.MIXTURE, name="F7"))

        self.assertContains(response, "the mixture is no longer in FFDB", status_code=410)
        self.assertNotContains(response, "InChIKey", status_code=410)
        self.assertNotContains(response, "Reason", status_code=410)
