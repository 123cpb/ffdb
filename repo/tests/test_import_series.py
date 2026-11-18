"""manage.py import_series: property data from a series list, run on a small sample."""

import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from repo.management.commands.import_series import COLUMNS
from repo.models import Material, Measurement, Reference, Series
from repo.tests.test_import import FIVE_CB, RM734

PS_PAPER = "10.1080/02678292.2022.2058101"
DN_PAPER = "10.1073/pnas.2002290117"
TABLE_PAPER = "10.1039/d2sm01298g"
PS_CONDITIONS = '{"frequency_Hz": 50, "cell_gap_um": 8}'

# Points files, by name. A curve against temperature gives each point's temperature as temperature_c.
POINTS = {
    "ps_cooling.csv": [["temperature_c", "value"], ["130", "4.2"], ["120", "5.0"], ["110", "5.6"]],
    "ps_heating.csv": [["temperature_c", "value"], ["110", "5.5"], ["120", "4.9"], ["130", "4.1"]],
    "dn.csv": [["temperature_c", "value"], ["140", "0.25"], ["150", "0.24"]],
    # Line 3 is a limit with a ±, which the rules refuse.
    "limit.csv": [["temperature_c", "qualifier", "value", "uncertainty"], ["120", "", "5.0", "0.1"],
                  ["110", "upper", "0.5", "0.1"]],
}


def row(line, **fields):
    """One row of the list: a measured Ps curve of RM734 on cooling, unless fields say otherwise.
    Its unit is written with the Greek letter μ, which looks like the vocabulary's µ (micro sign)."""
    values = dict(
        origin="CURVES", line=line, smiles=RM734, reference=PS_PAPER, reference_kind="doi", property="ps",
        variable="temperature", kind="measured", method="current reversal", direction="cooling",
        taken_from="figure", location="Fig. 5b", digitised="yes", conditions=PS_CONDITIONS,
        value_unit="μC/cm²", x_unit="°C", points_file="ps_cooling.csv",
    )
    values.update(fields)
    return [values.get(column, "") for column in COLUMNS]


DELTA_N = dict(property="birefringence", value_unit="dimensionless", method="", conditions="")

# Each row's outcome is in its comment.
SAMPLE = [
    COLUMNS,
    row(2),  # added, 3 points
    row(3, direction="heating", points_file="ps_heating.csv"),  # added: heating is another series
    row(4, reference=DN_PAPER, points_file="dn.csv", **DELTA_N),  # skipped: no wavelength
    row(5, smiles=FIVE_CB, reference=TABLE_PAPER, **dict(DELTA_N, conditions='{"wavelength_nm": 589}'),
        direction="not_applicable", taken_from="table", location="Table 1", digitised="no", points_file="",
        value="0.2", temperature_c="25"),  # added, a single value
    row(6, smiles="CCO"),  # skipped: material not in FFDB
    row(7, value_unit="nC/cm²"),  # skipped: units are never converted
    row(8, property="spontaneous-polarisation"),  # skipped: unknown code
    row(9, location="Fig. 6", points_file="limit.csv"),  # skipped: a point breaks a rule
]


class ImportSeriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        Material.objects.create(name="5CB", smiles=FIVE_CB)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for name, lines in POINTS.items():
            self.write(name, lines)
        self.write("series.csv", SAMPLE)

    def write(self, name, lines):
        with open(self.folder / name, "w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(lines)

    def run_import(self):
        """Run the command on the sample and return what it printed."""
        out = StringIO()
        call_command("import_series", str(self.folder / "series.csv"), stdout=out)
        return out.getvalue()

    def test_it_ends_by_printing_the_counts(self):
        output = self.run_import()

        self.assertIn("Series added: 3\nSeries already there: 0\nSeries skipped: 5\nPoints added: 7\n", output)

    def test_imported_series_and_references_record_no_one(self):
        self.run_import()

        for model in (Series, Reference):
            with self.subTest(model.__name__):
                self.assertTrue(model.objects.exists())
                # Leave out the rows where both are blank, and nothing is left.
                self.assertFalse(model.objects.exclude(entered_by=None, entered_at=None).exists())

    def test_running_it_twice_adds_nothing(self):
        self.run_import()
        before = (list(Series.objects.values_list("pk", flat=True)), Measurement.objects.count(),
                  Reference.objects.count())

        output = self.run_import()

        self.assertIn("Series added: 0\nSeries already there: 3\nSeries skipped: 5\nPoints added: 0\n", output)
        after = (list(Series.objects.values_list("pk", flat=True)), Measurement.objects.count(),
                 Reference.objects.count())
        self.assertEqual(after, before)

    def test_a_curve_against_temperature_stores_each_temperature_as_t(self):
        self.run_import()

        series = Series.objects.get(material=self.rm734, direction="cooling")
        self.assertEqual(series.conditions, {"frequency_Hz": 50, "cell_gap_um": 8})
        self.assertEqual(series.reference.kind, Reference.Kind.ARTICLE)
        self.assertEqual(
            list(series.points.values_list("temperature_c", "x", "value")),
            [(110, None, 5.6), (120, None, 5.0), (130, None, 4.2)],
        )

    def test_heating_and_cooling_from_one_figure_are_two_series(self):
        self.run_import()

        self.assertEqual(Series.objects.filter(material=self.rm734, property__code="ps").count(), 2)

    def test_a_single_value_is_a_series_of_one_point(self):
        self.run_import()

        series = Series.objects.get(material__name="5CB")
        self.assertEqual(series.conditions, {"wavelength_nm": 589})
        self.assertEqual(list(series.points.values_list("value", "temperature_c")), [(0.2, 25)])

    def test_a_series_that_breaks_a_rule_is_skipped_with_the_rules_message(self):
        output = self.run_import()

        self.assertIn(f"CURVES line 4 ({self.rm734}): skipped, Δn values must state: wavelength", output)
        self.assertFalse(Series.objects.filter(property__code="birefringence", material=self.rm734).exists())
        # Its reference was new, and it goes too: nothing would cite it.
        self.assertFalse(Reference.objects.filter(identifier=DN_PAPER).exists())

    def test_a_point_that_breaks_a_rule_skips_the_whole_series(self):
        output = self.run_import()

        self.assertIn("CURVES line 9", output)
        self.assertIn("limit.csv line 3: uncertainty: A limit has no ±", output)
        self.assertFalse(Series.objects.filter(location="Fig. 6").exists())
        self.assertEqual(Measurement.objects.count(), 7)  # none of its points, not even line 2's

    def test_rows_that_cant_be_read_are_printed_with_the_reason(self):
        output = self.run_import()

        self.assertIn("CURVES line 6 (CCO): skipped, material not in FFDB", output)
        self.assertIn("CURVES line 7", output)
        self.assertIn("value_unit 'nC/cm²': Ps is stored in µC/cm²", output)
        self.assertIn("unknown property 'spontaneous-polarisation'", output)

    def test_a_list_without_a_column_stops_the_import(self):
        self.write("series.csv", [[column for column in COLUMNS if column != "points_file"]])

        with self.assertRaisesMessage(CommandError, "has no points_file column"):
            self.run_import()
