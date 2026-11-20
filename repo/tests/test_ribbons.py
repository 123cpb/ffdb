import re

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from repo import ribbons
from repo.models import Material, PhaseSequence, Reference
from repo.phases import parse_run, parse_summary
from repo.ribbons import RibbonData, bands
from repo.templatetags.repo_tags import phase_html
from repo.views import ribbon_sequence

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
SIX_CB = "CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
SEVEN_CB = "CCCCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
NF_IN_SVG = 'N<tspan baseline-shift="sub" font-size="75%">F</tspan>'

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


class OpenEndTests(SimpleTestCase):
    """How a phase is drawn where the data stops: on a material's page and in the Materials list."""

    def opacities(self, svg):
        return re.findall(r'fill-opacity="([\d.]+)"', svg)

    def test_a_material_page_fades_an_open_end_out_in_three_steps_with_no_block(self):
        svg = ribbons.ribbon_svg(run("Iso 187.9 N 132.7 NF"), 100, 200, label="")

        self.assertIn('viewBox="0 0 640 42"', svg)  # 26 high, and 16 more for the temperatures
        self.assertEqual(self.opacities(svg), ["0.7", "0.45", "0.2"])
        self.assertNotIn(NF_IN_SVG, svg)  # no code in the steps: the colour, key and tooltip name it

    def test_the_list_draws_an_open_end_as_a_block_with_its_code_then_two_fades(self):
        svg = ribbons.ribbon_svg(
            run("Iso 187.9 N 132.7 NF"), 100, 200, label="", width=600, height=22, temperatures=False, block_ends=True
        )

        self.assertIn('viewBox="0 0 600 22"', svg)
        self.assertEqual(self.opacities(svg), ["0.5", "0.2"])
        self.assertIn(NF_IN_SVG, svg)
        self.assertNotIn("ribbon-temp", svg)

    def test_an_open_upper_end_fades_out_upwards(self):
        # N has no clearing point. On an axis from 0 to 200 °C, 88 °C is at 1 + 88 × 638/200 = 281.7 px.
        svg = ribbons.ribbon_svg(summary("K 58 SmA 88 N", melting=58), 0, 200, label="")

        steps = re.findall(r'<rect x="([\d.]+)"[^>]*fill-opacity', svg)
        self.assertEqual(len(steps), 3)
        self.assertTrue(all(float(x) > 281.7 for x in steps))
        self.assertIn("N: above 88 °C; upper limit not reported", svg)


class SubscriptTests(SimpleTestCase):
    def test_a_sequence_is_written_with_its_subscripts(self):
        self.assertEqual(phase_html("Cr 139.8 NF 132.7 N 187.9 Iso"), "Cr 139.8 N<sub>F</sub> 132.7 N 187.9 Iso")
        self.assertEqual(phase_html("Cr 60 SmCPH 90 NTB 95 Iso"), "Cr 60 SmC<sub>PH</sub> 90 N<sub>TB</sub> 95 Iso")

    def test_everything_else_is_escaped(self):
        self.assertEqual(phase_html("Cr 50 N' 100 <b>"), "Cr 50 N&#x27; 100 &lt;b&gt;")

    def test_a_ribbon_writes_the_subscript_in_svg_and_its_tooltip_keeps_the_code(self):
        svg = ribbons.ribbon_svg(summary("K 61.4 NF 100 I", melting=61.4), 0, 200, label="")

        self.assertIn(f">{NF_IN_SVG}</text>", svg)
        self.assertIn("<title>NF: 61.4 to 100 °C</title>", svg)  # a tooltip can't hold markup


class SequenceChoiceTests(SimpleTestCase):
    """ribbon_sequence() reads only each sequence's primary key and direction, so they are never saved."""

    def sequence(self, pk, direction="summary"):
        return PhaseSequence(pk=pk, direction=direction)

    def test_a_sequence_the_search_found_comes_first(self):
        found = self.sequence(2, "heating")

        self.assertIs(ribbon_sequence([self.sequence(1), found], preferred={2}), found)

    def test_cooling_comes_before_heating(self):
        cooling = self.sequence(2, "cooling")

        self.assertIs(ribbon_sequence([self.sequence(1, "heating"), cooling]), cooling)

    def test_otherwise_the_first_entered(self):
        first = self.sequence(2, "summary")
        sequences = [self.sequence(3, "cooling"), first, self.sequence(5, "unspecified"), self.sequence(1, "heating")]

        self.assertIs(ribbon_sequence(sequences), first)

    def test_none_for_a_material_with_no_sequence(self):
        self.assertIsNone(ribbon_sequence([]))


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

    def test_each_sequence_is_a_row_640_wide_under_the_key_and_its_axis(self):
        self.add("K 139.8 NF 132.7 N 187.9 I")
        self.add("K 139 NF 131 N 188 I")

        content = self.page().content.decode()

        self.assertEqual(content.count('viewBox="0 0 640 42"'), 2)
        self.assertEqual(content.count("Melting point 139"), 2)
        self.assertLess(content.index('<ul class="phase-key"'), content.index('<svg class="ribbon-axis"'))
        self.assertNotIn("phase-key__block", content)  # the key shows the three steps this page draws
        self.assertIn('<p class="ribbon-row__text">Cr 139.8 N<sub>F</sub> 132.7 N 187.9 Iso', content)

    def test_a_material_with_no_sequence_says_so(self):
        response = self.page()

        self.assertContains(response, "No phase sequences yet.")
        self.assertNotContains(response, '<svg class="ribbon')


class MaterialsListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/c7cp00456g", first_author="Mandle", year=2017
        )
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.reference, sequence="K 139.8 NF 132.7 N 187.9 I")

    def page(self, **search):
        return self.client.get(reverse("repo:materials"), search)

    def test_each_material_has_a_ribbon_on_one_axis_with_the_key(self):
        response = self.page()

        self.assertContains(response, '<svg class="ribbon"', count=1)
        self.assertContains(response, 'viewBox="0 0 600 22"')
        self.assertContains(response, '<svg class="ribbon-axis" viewBox="0 0 600 20"', count=1)
        self.assertContains(response, f">{NF_IN_SVG}</text>")  # RM734's NF, in its block
        self.assertContains(response, 'aria-label="Cr 139.8 NF 132.7 N 187.9 Iso (°C), Summary, Mandle 2017"')
        self.assertContains(response, "N<sub>F</sub></span></li>")  # in the key
        self.assertContains(response, "phase-key__block")  # the key shows the block this list draws
        self.assertContains(response, "A material with several sequences shows one")

    def test_a_material_with_no_sequence_says_so(self):
        self.assertContains(self.page(), '<span class="result__no-ribbon">No phase sequence</span>', count=1)

    def test_a_page_with_no_ribbon_has_no_key_or_axis(self):
        response = self.page(q="5CB")

        self.assertNotContains(response, '<ul class="phase-key"')
        self.assertNotContains(response, "ribbon-axis")

    def test_a_search_draws_the_run_that_matched(self):
        PhaseSequence.objects.create(
            material=self.rm734, reference=self.reference, sequence="Iso 188 N 105 NF", direction="cooling"
        )
        summary_label = 'aria-label="Cr 139.8 NF 132.7 N 187.9 Iso (°C), Summary'
        cooling_label = 'aria-label="Iso 188 N 105 NF (°C), Cooling'

        everything = self.page()
        searched = self.page(phase="NF", tmin="100", tmax="110")

        self.assertContains(everything, summary_label)  # entered first, and not a heating run
        self.assertNotContains(everything, cooling_label)
        self.assertContains(searched, cooling_label)
        self.assertNotContains(searched, summary_label)

    def test_the_downloads_keep_the_plain_codes(self):
        content = self.client.get(reverse("repo:sequences_csv")).content.decode()

        self.assertIn("Cr 139.8 NF 132.7 N 187.9 Iso", content)
        self.assertNotIn("<sub>", content)

    def test_the_list_makes_no_more_queries_for_more_rows(self):
        with CaptureQueriesContext(connection) as one:
            self.page(phase="NF")
        for name, smiles in (("6CB", SIX_CB), ("7CB", SEVEN_CB)):
            material = Material.objects.create(name=name, smiles=smiles)
            PhaseSequence.objects.create(material=material, reference=self.reference, sequence="K 50 NF 60 N 90 I")
            PhaseSequence.objects.create(
                material=material, reference=self.reference, sequence="Iso 90 N 60 NF", direction="cooling"
            )
        with CaptureQueriesContext(connection) as three:
            response = self.page(phase="NF")

        self.assertContains(response, '<svg class="ribbon"', count=3)
        self.assertEqual(len(three), len(one))
