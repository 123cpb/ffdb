import json
from datetime import UTC, datetime

from django.apps import apps
from django.contrib.auth.models import Group, User
from django.contrib.messages import get_messages
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


class RetiredTestCase(TestCase):
    """RM734, with a sequence and a series of two points from one reference, and F7, a
    mixture with 5CB as a component. An admin is logged in: only admins delete materials."""

    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.cb5 = Material.objects.create(name="5CB", smiles=CB5)
        cls.f7 = Material.objects.create(kind=Material.Kind.MIXTURE, name="F7")
        cls.component = MixtureComponent.objects.create(
            mixture=cls.f7, component=cls.cb5, fraction=30, unit=MixtureComponent.Unit.MOL
        )
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017
        )
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.reference, sequence=RM734_SUMMARY)
        series = Series.objects.create(
            material=cls.rm734, reference=cls.reference, property=Property.objects.get(code="ps"),
            variable=Variable.objects.get(code="temperature"), kind=Series.Kind.MEASURED,
        )
        series.points.create(value=6.0, temperature_c=80)
        series.points.create(value=4.4, temperature_c=100)
        cls.admin = User.objects.create_superuser("admin", password="pw")

    def setUp(self):
        self.client.force_login(self.admin)


class RetiredIDTests(RetiredTestCase):
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


class RestoreTests(RetiredTestCase):
    """Restore, on a retired ID's admin page. Each test retires a material first, then posts
    as the page's Restore button would. A restore that is refused must change nothing:
    all_rows() includes the retired ID records, so it shows the record is still there too."""

    def retire(self, material):
        """Retire material and return its number, which deleting it takes off the object."""
        number = material.pk
        material.retire(self.admin)
        return number

    def restore(self, number):
        return self.client.post(reverse("admin:repo_retiredid_restore", args=[number]))

    def messages(self, response):
        return " ".join(str(message) for message in get_messages(response.wsgi_request))

    def test_a_restore_brings_back_the_same_id_and_rows(self):
        before = all_rows()
        number = self.retire(self.rm734)
        record_url = reverse("admin:repo_retiredid_change", args=[number])
        restore_url = reverse("admin:repo_retiredid_restore", args=[number])
        snapshot = RetiredID.objects.get(number=number).snapshot
        self.assertContains(self.client.get(record_url), f'action="{restore_url}"')  # the button

        response = self.restore(number)

        self.assertRedirects(response, reverse("admin:repo_material_change", args=[number]))
        self.assertEqual(all_rows(), before)  # every row back, and the record gone
        self.assertEqual(Material.objects.get(pk=number).snapshot(), snapshot)  # each row as it was
        self.assertContains(self.client.get(reverse("repo:material", args=[number])), "RM734")

    def test_a_restore_whose_reference_has_gone_is_refused_and_changes_nothing(self):
        number = self.retire(self.rm734)
        reference = self.reference.pk
        self.reference.delete()  # possible now: nothing else cites it
        before = all_rows()

        response = self.restore(number)

        self.assertRedirects(response, reverse("admin:repo_retiredid_change", args=[number]))
        self.assertIn(f"reference {reference} is no longer in FFDB.", self.messages(response))
        self.assertEqual(all_rows(), before)

    def test_a_restore_whose_structure_has_been_added_again_is_refused_and_changes_nothing(self):
        number = self.retire(self.rm734)
        again = Material.objects.create(name="RM734 again", smiles=RM734)
        before = all_rows()

        response = self.restore(number)

        self.assertIn(f"its structure is now in FFDB as {again.accession} (RM734 again)", self.messages(response))
        self.assertEqual(all_rows(), before)

    def test_a_mixture_whose_component_has_gone_is_refused(self):
        number = self.retire(self.f7)
        component = self.cb5.accession
        self.retire(self.cb5)  # possible now: F7 no longer uses it
        before = all_rows()

        response = self.restore(number)

        self.assertIn(f"{component} is no longer in FFDB.", self.messages(response))
        self.assertEqual(all_rows(), before)

    def test_a_mixture_whose_component_is_now_a_mixture_is_refused(self):
        number = self.retire(self.f7)
        Material.objects.filter(pk=self.cb5.pk).update(kind=Material.Kind.MIXTURE, smiles="", inchikey="")
        before = all_rows()

        response = self.restore(number)

        self.assertIn(f"its component {self.cb5.accession} is now a mixture", self.messages(response))
        self.assertEqual(all_rows(), before)

    def test_a_snapshot_that_no_longer_fits_the_tables_is_refused(self):
        number = self.retire(self.rm734)
        record = RetiredID.objects.get(number=number)
        # As if a migration had since renamed a material's notes field.
        RetiredID.objects.filter(number=number).update(snapshot=record.snapshot.replace('"notes"', '"remarks"'))
        before = all_rows()

        response = self.restore(number)

        self.assertIn("the snapshot no longer fits FFDB's tables", self.messages(response))
        self.assertEqual(all_rows(), before)

    def test_a_deleted_account_leaves_entered_by_blank(self):
        curator = User.objects.create_user("curator")
        # Whole seconds: a snapshot keeps times to the millisecond.
        entered_at = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        PhaseSequence.objects.filter(material=self.rm734).update(entered_by=curator, entered_at=entered_at)
        number = self.retire(self.rm734)
        curator.delete()

        self.restore(number)

        sequence = PhaseSequence.objects.get(material_id=number)
        self.assertEqual((sequence.entered_by, sequence.entered_at), (None, entered_at))

    def test_a_curator_cannot_restore(self):
        number = self.retire(self.rm734)
        curator = User.objects.create_user("curator", is_staff=True)
        curator.groups.add(Group.objects.get(name="Curators"))
        before = all_rows()
        self.client.force_login(curator)

        self.assertEqual(self.restore(number).status_code, 403)
        self.assertEqual(all_rows(), before)
