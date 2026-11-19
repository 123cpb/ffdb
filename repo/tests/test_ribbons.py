from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from repo import ribbons
from repo.models import Material, PhaseSequence, Reference
from repo.phases import parse_run, parse_summary
from repo.ribbons import RibbonData, bands

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"

# Enough of the vocabulary for these sequences, as Phase.synonym_map() gives it, so the parser
# runs without the database.
SYNONYMS = {"k": "Cr", "cr": "Cr", "i": "Iso", "iso": "Iso", "n": "N", "nf": "NF", "sma": "SmA", "n'": "N'"}


def summary(text, **extra):
    return RibbonData(parse_summary(text, SYNONYMS).transitions, **extra)


def run(text, **extra):
    return RibbonData(parse_run(text, SYNONYMS).transitions, **extra)


class BandTests(SimpleTestCase):
    """The bands on an axis from 0 to 200 °C, from the top down, with None for an end not reported."""

    def test_a_summary_runs_from_its_solid_to_the_isotropic_liquid(self):
        self.assertEqual(
            bands(summary("K 61.4 N 100 I", melting=61.4), 0, 200),
            [("Iso", 100, 200), ("N", 61.4, 100), ("Cr", 0, 61.4)],
        )

    def test_a_phase_below_the_melting_point_is_open_below_with_no_solid_under_it(self):
        self.assertEqual(
            bands(summary("K 139.8 NF 132.7 N 187.9 I", melting=139.8), 0, 200),
            [("Iso", 187.9, 200), ("N", 132.7, 187.9), ("NF", None, 132.7)],
        )

    def test_a_cooling_run_that_ends_without_a_solid_gets_none(self):
        self.assertEqual(
            bands(run("Iso 187.9 N 132.7 NF"), 0, 200),
            [("Iso", 187.9, 200), ("N", 132.7, 187.9), ("NF", None, 132.7)],
        )

    def test_a_cooling_run_that_crystallises_has_its_solid_down_to_the_bottom_of_the_axis(self):
        self.assertEqual(
            bands(run("Iso 187.9 N 132.7 NF 70 Cr"), 0, 200)[-2:],
            [("NF", 70, 132.7), ("Cr", 0, 70)],
        )

    def test_the_lowest_phase_stops_at_the_lowest_temperature_reached(self):
        self.assertEqual(bands(run("Iso 187.9 N 132.7 NF", lowest=100), 0, 200)[-1], ("NF", 100, 132.7))
        self.assertEqual(bands(run("Iso 187.9 N 132.7 NF 70 Cr", lowest=20), 0, 200)[-1], ("Cr", 20, 70))

    def test_a_top_phase_that_isnt_the_isotropic_liquid_is_open_above(self):
        self.assertEqual(
            bands(summary("K 58 SmA 88 N", melting=58), 0, 200),
            [("N", 88, None), ("SmA", 58, 88), ("Cr", 0, 58)],
        )

    def test_a_sequence_with_no_transitions_has_no_bands(self):
        self.assertEqual(bands(summary("K I"), 0, 200), [])


class DrawingTests(SimpleTestCase):
    def test_the_melting_point_is_marked_and_the_phase_below_it_is_monotropic(self):
        svg = ribbons.ribbon_svg(summary("K 139.8 NF 132.7 N 187.9 I", melting=139.8), 120, 200, label="RM734")

        self.assertIn("Melting point 139.8 °C; phases below it were seen on cooling only (monotropic)", svg)
        self.assertIn("NF: below 132.7 °C; lower limit not reported", svg)
        self.assertIn("N: 132.7 to 187.9 °C", svg)
        self.assertIn("Iso: above 187.9 °C", svg)
        for temperature in ("132.7", "139.8", "187.9"):  # written under the ribbon
            self.assertIn(f'class="ribbon-temp">{temperature}</text>', svg)

    def test_a_melting_point_that_is_a_transition_isnt_called_monotropic(self):
        svg = ribbons.ribbon_svg(summary("K 61.4 N 100 I", melting=61.4), 0, 200, label="")

        self.assertIn("Melting point 61.4 °C</title>", svg)
        self.assertIn("Cr: below 61.4 °C", svg)

    def test_the_sequence_is_the_ribbons_text_for_screen_readers(self):
        svg = ribbons.ribbon_svg(summary("K 61.4 N 100 I"), 0, 200, label="Cr 61.4 N 100 Iso (°C)")

        self.assertIn('role="img" aria-label="Cr 61.4 N 100 Iso (°C)"', svg)

    def test_not_examined_below_the_lowest_temperature_reached(self):
        svg = ribbons.ribbon_svg(run("Iso 187.9 N 132.7 NF", lowest=100), 0, 200, label="")

        self.assertIn("Not examined below 100 °C", svg)

    def test_a_sequence_with_no_transitions_says_so(self):
        svg = ribbons.ribbon_svg(summary("K I"), 0, 200, label="")

        self.assertIn("No transition temperatures reported", svg)

    def test_text_written_into_the_svg_is_escaped(self):
        svg = ribbons.ribbon_svg(summary("K 50 N' 100 I"), 0, 200, label='<script>"A & B"</script>')

        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;&quot;A &amp; B&quot;&lt;/script&gt;", svg)
        self.assertNotIn("N'", svg)  # the code, written in its band and its tooltip
        self.assertIn(">N&#x27;</text>", svg)
        self.assertIn("N&#x27;: 50 to 100 °C", svg)


class AxisTests(SimpleTestCase):
    def test_the_range_covers_every_temperature_with_room_either_side_in_whole_steps(self):
        data = [summary("K 139.8 NF 132.7 N 187.9 I", melting=139.8), run("Iso 190 N 150 NF", lowest=120)]

        self.assertEqual(ribbons.temperature_range(data), (100, 200))

    def test_with_no_temperatures_the_range_is_0_to_200(self):
        self.assertEqual(ribbons.temperature_range([summary("K I")]), (0, 200))

    def test_ticks_are_round_numbers_in_the_range(self):
        self.assertEqual(ribbons.ticks(120, 200), [120, 140, 160, 180, 200])
        self.assertEqual(ribbons.ticks(-20, 40), [-20, -10, 0, 10, 20, 30, 40])

    def test_the_axis_writes_a_true_minus_sign(self):
        self.assertIn(">−20</text>", ribbons.axis_svg(-20, 40))


class MaterialPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017,
            venue="Phys. & Chem.",
        )

    def add(self, sequence, **fields):
        return PhaseSequence.objects.create(material=self.material, reference=self.reference, sequence=sequence, **fields)

    def page(self):
        return self.client.get(reverse("repo:material", args=[self.material.pk]))

    def test_ribbon_data_reads_the_stored_transitions_and_a_summarys_melting_point(self):
        summary_data = ribbons.ribbon_data(self.add("K 139.8 NF 132.7 N 187.9 I"))
        heating_data = ribbons.ribbon_data(self.add("Cr 70 NF 132.7 N 187.9 Iso", direction="heating"))

        self.assertEqual([t.lower for t in summary_data.transitions], ["N", "NF"])  # highest first
        self.assertEqual(summary_data.melting, 139.8)
        self.assertIsNone(heating_data.melting)  # a heating run's melting point is a transition

    def test_each_sequence_has_a_ribbon_on_one_axis(self):
        self.add("K 139.8 NF 132.7 N 187.9 I")
        self.add("Iso 188 N 131 NF", direction="cooling", lowest_temperature_c=100)

        response = self.page()

        self.assertContains(response, '<svg class="ribbon"', count=2)
        self.assertContains(response, '<svg class="ribbon-axis"', count=1)
        self.assertContains(response, 'aria-label="Cr 139.8 NF 132.7 N 187.9 Iso (°C), Summary, Mandle 2017, Phys. &amp; Chem."')
        self.assertContains(response, "Not examined below 100 °C")

    def test_the_page_makes_no_more_queries_for_more_sequences(self):
        self.add("K 139.8 NF 132.7 N 187.9 I")
        with CaptureQueriesContext(connection) as one:
            self.page()
        self.add("K 139 NF 131 N 188 I")
        self.add("Iso 188 N 131 NF 70 K", direction="cooling")
        with CaptureQueriesContext(connection) as three:
            self.page()

        self.assertEqual(len(three), len(one))

    def test_a_material_with_no_sequence_says_so(self):
        response = self.page()

        self.assertContains(response, "No phase sequences yet.")
        self.assertNotContains(response, '<svg class="ribbon')
