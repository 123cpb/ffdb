"""The switches: approved on sequences and series, published on references and listed on
materials. Each is off unless something turns it on, so every way data is added turns them on:
the imports, Add data and the admin. The database refuses published unpublished work."""

import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Property, Reference, Series, Variable
from repo.tests.test_compound_card import RM734_COOLING, CardTestCase
from repo.tests.test_database_checks import DatabaseChecks
from repo.tests.test_entered import EnteredTestCase
from repo.tests.test_import import (
    ARTICLE, DIMETHYLCYCLOHEXANE, FIVE_CB, PATENT, RM734, RM734_SUMMARY, TRANS_DIMETHYLCYCLOHEXANE,
)
from repo.tests.test_import_series import POINTS, SAMPLE as SERIES_SAMPLE
from repo.tests.test_reference_details import PCCP
from repo.tests.test_value_blocks import DELTA_EPSILON

UNPUBLISHED_WORK = "Mandle group 2026, Computed dipole moments"

# A merged list in which each material's outcome is in its row's comment.
MERGED = [
    ["origin", "line", "name", "smiles", "sequence", "reference", "reference_kind"],
    ["NF", "2", "RM734", RM734, RM734_SUMMARY, ARTICLE, "doi"],  # a sequence: not listed
    ["LCPROP", "3", "5CB", FIVE_CB, "", PATENT, "patent"],  # no sequence: listed
    ["NF", "4", "DMCH", DIMETHYLCYCLOHEXANE, "K 100 Q 50 I", ARTICLE, "doi"],  # its sequence is skipped: listed
    ["LCPROP", "5", "trans-DMCH", TRANS_DIMETHYLCYCLOHEXANE, "", PATENT, "patent"],  # given one by line 6:
    ["NF", "6", "trans-DMCH", TRANS_DIMETHYLCYCLOHEXANE, "K 5 N 15 I", ARTICLE, "doi"],  # not listed
]


class ImportTestCase(TestCase):
    """A folder for the lists, thrown away after each test."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def write(self, name, rows):
        with open(self.folder / name, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)

    def run_import(self, command, name):
        """Run the command on the list and return what it printed."""
        out = StringIO()
        call_command(command, str(self.folder / name), stdout=out)
        return out.getvalue()


class ImportMaterialsTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.write("merged.csv", MERGED)

    def test_sequences_are_approved_and_references_published(self):
        self.run_import("import_materials", "merged.csv")

        self.assertEqual(PhaseSequence.objects.count(), 2)
        self.assertFalse(PhaseSequence.objects.filter(approved=False).exists())
        # Only the article: a reference is added with a sequence, and no row gives the patent one.
        self.assertTrue(Reference.objects.get(identifier=ARTICLE).published)
        self.assertEqual(Reference.objects.count(), 1)

    def test_a_material_added_with_no_sequence_is_listed(self):
        output = self.run_import("import_materials", "merged.csv")

        listed = Material.objects.filter(listed=True).order_by("name").values_list("name", flat=True)
        self.assertEqual(list(listed), ["5CB", "DMCH"])
        self.assertIn("Materials listed, with no sequence: 2\n", output)

    def test_running_it_again_leaves_switches_an_admin_turned_off(self):
        self.run_import("import_materials", "merged.csv")
        PhaseSequence.objects.update(approved=False)
        Reference.objects.update(published=False)
        Material.objects.update(listed=False)

        output = self.run_import("import_materials", "merged.csv")

        self.assertFalse(PhaseSequence.objects.filter(approved=True).exists())
        self.assertFalse(Reference.objects.filter(published=True).exists())
        # 5CB and DMCH were already there, so this run added nothing to list.
        self.assertFalse(Material.objects.filter(listed=True).exists())
        self.assertIn("Materials listed, with no sequence: 0\n", output)


class ImportSeriesTests(ImportTestCase):
    def test_series_are_approved_and_references_published(self):
        Material.objects.create(name="RM734", smiles=RM734)
        Material.objects.create(name="5CB", smiles=FIVE_CB)
        for name, lines in POINTS.items():
            self.write(name, lines)
        self.write("series.csv", SERIES_SAMPLE)

        self.run_import("import_series", "series.csv")

        self.assertEqual(Series.objects.count(), 3)
        self.assertFalse(Series.objects.filter(approved=False).exists())
        self.assertFalse(Reference.objects.filter(published=False).exists())


class AddDataTests(CardTestCase):
    def test_a_new_reference_is_published_unless_it_is_unpublished_work(self):
        self.give("doi", PCCP)
        self.give("unpublished", "")

        self.assertTrue(Reference.objects.get(identifier=PCCP).published)
        self.assertFalse(Reference.objects.get(kind=Reference.Kind.UNPUBLISHED).published)

    def test_a_saved_cards_sequence_and_values_are_approved(self):
        self.send(RM734_COOLING, "save", blocks=[DELTA_EPSILON], material=self.rm734.accession)

        self.assertTrue(PhaseSequence.objects.get().approved)
        self.assertTrue(Series.objects.get().approved)


class AdminTests(EnteredTestCase):
    """The imported sequence of EnteredTestCase was added with approved left off."""

    def page_form(self, name):
        """The form of an admin page, not sent."""
        return self.client.get(reverse(name)).context["adminform"].form

    def test_new_rows_start_with_approved_and_published_ticked_but_not_listed(self):
        self.assertTrue(self.page_form("admin:repo_series_add")["approved"].value())
        self.assertTrue(self.page_form("admin:repo_reference_add")["published"].value())
        self.assertFalse(self.page_form("admin:repo_material_add")["listed"].value())

    def test_a_saved_sequence_shows_its_own_box_and_the_new_row_starts_ticked(self):
        response = self.client.get(reverse("admin:repo_material_change", args=[self.material.pk]))
        inlines = response.context["inline_admin_formsets"]
        saved, new = next(inline.formset for inline in inlines if inline.formset.model is PhaseSequence).forms

        self.assertEqual((saved.instance, saved["approved"].value()), (self.imported, False))
        self.assertTrue(new["approved"].value())

    def test_an_unpublished_reference_ticked_published_is_refused_with_the_message(self):
        data = {
            "kind": "unpublished", "identifier": UNPUBLISHED_WORK, "first_author": "", "year": "",
            "title": "", "venue": "", "notes": "", "published": "on",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        response = self.client.post(reverse("admin:repo_reference_add"), data)

        form = response.context["adminform"].form
        self.assertEqual(list(form.non_field_errors()), ["An unpublished group result can't be marked published."])
        self.assertFalse(Reference.objects.filter(identifier=UNPUBLISHED_WORK).exists())

    def test_each_list_filters_by_its_switch(self):
        five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB, listed=True)
        published = Reference.objects.create(kind=Reference.Kind.PATENT, identifier=PATENT, published=True)
        waiting = Series.objects.create(
            material=self.material, reference=published, property=Property.objects.get(code="birefringence"),
            variable=Variable.objects.get(code="temperature"), kind="measured",
        )

        for name, query, expected in [
            ("admin:repo_material_changelist", {"listed__exact": "1"}, [five_cb]),
            ("admin:repo_material_changelist", {"listed__exact": "0"}, [self.material]),
            ("admin:repo_reference_changelist", {"published__exact": "1"}, [published]),
            ("admin:repo_reference_changelist", {"published__exact": "0"}, [self.reference]),
            ("admin:repo_series_changelist", {"approved__exact": "0"}, [waiting]),
            ("admin:repo_series_changelist", {"approved__exact": "1"}, []),
        ]:
            with self.subTest(name=name, query=query):
                response = self.client.get(reverse(name), query)
                self.assertEqual(list(response.context["cl"].result_list), expected)


class UnpublishedReferenceTests(DatabaseChecks, TestCase):
    def test_the_database_refuses_a_published_unpublished_reference(self):
        unpublished = Reference.objects.create(kind=Reference.Kind.UNPUBLISHED, identifier=UNPUBLISHED_WORK)

        self.assertDatabaseRefuses(Reference.objects.filter(pk=unpublished.pk), "unpublished_is_not_published", published=True)
