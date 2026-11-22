"""Blocks of values on a compound card, for the card's material: one line is a single value and
several are a curve, pasted as x, value and optionally ±. A block's choices add the boxes they
need, so without JavaScript those appear after Check. A block is refused by the rules for values,
with their messages, and Save adds the card's blocks with the rest of it, or nothing."""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.urls import reverse

from repo.forms import MAX_CURVE_POINTS, NAMED_PROBLEM_LINES, ValueBlockForm, condition_value, read_points
from repo.models import Measurement, PhaseSequence, Property, Series, must_state
from repo.tests.test_add_data import WAITING, AddDataTestCase
from repo.tests.test_compound_card import RM734_COOLING, CardTestCase

EXACT, UPPER, LOWER = Measurement.Qualifier.EXACT, Measurement.Qualifier.UPPER, Measurement.Qualifier.LOWER

# A digitised Ps curve of RM734 on cooling, as a digitiser gives it: 20 lines of T (°C), Ps (µC/cm²).
PS_CURVE = [f"{60 + 3.5 * i:g}, {6.0 - 0.28 * i:.2f}" for i in range(20)]
# The same with line 12 mistyped.
WITH_A_BAD_LINE = PS_CURVE[:11] + ["98.5, 2..92"] + PS_CURVE[12:]
# The boxes besides the points and the choice for that curve.
BOXES = {"method": "current reversal", "condition_frequency_Hz": "50", "condition_cell_gap_um": "8",
         "taken_from": "figure", "location": "Fig. 3a", "digitised": "on", "direction": "cooling"}
# How a screen reader names the curve's chart, on Check and on the material page.
CHART = 'aria-label="Ps against temperature, measured values: 1 series, from Mandle 2017, Chem. Eur. J."'

# A block for an extrapolated Δε at 25 °C and 1 kHz, as the check adds it: one line, so a single value.
DELTA_EPSILON = {
    "property": "dielectric-anisotropy", "kind": "extrapolated", "variable": "temperature",
    "condition_frequency_Hz": "1000", "taken_from": "table", "location": "Table 2", "points": "25, 5.2",
}
# A block for RM734's digitised Ps curve on cooling: 20 lines.
PS_BLOCK = {"property": "ps", "kind": "measured", "variable": "temperature", **BOXES, "points": "\n".join(PS_CURVE)}
# Ps against frequency: each point's x is its frequency, and every point has the T box's temperature.
FREQUENCY_SWEEP = {
    "property": "ps", "kind": "measured", "variable": "frequency", "method": "current reversal",
    "condition_cell_gap_um": "8", "taken_from": "table", "location": "Table S2", "temperature_c": "70",
}
# A computed molecular length, which has no temperature: a value against nothing, with no x.
LENGTH = {
    "property": "molecular-length", "kind": "computed", "variable": "none", "method": "DFT",
    "condition_conformer_protocol": "lowest energy", "condition_solvent": "gas phase",
    "taken_from": "table", "location": "Table S1", "points": "2.4",
}


class MethodsTests(AddDataTestCase):
    def test_the_migration_gives_each_property_its_usual_methods(self):
        self.assertEqual(Property.objects.get(code="ps").methods, ["current reversal", "P–E hysteresis loop"])
        self.assertEqual(Property.objects.get(code="molecular-length").methods, ["DFT"])

    def test_methods_must_be_a_list_of_names(self):
        prop = Property.objects.get(code="ps")
        for methods in ["current reversal", ["current reversal", ""], {"ps": "current reversal"}]:
            with self.subTest(methods):
                prop.methods = methods
                with self.assertRaises(ValidationError) as caught:
                    prop.full_clean()
                self.assertIn("methods", caught.exception.message_dict)

    def test_condition_values_are_numbers_when_they_read_as_numbers(self):
        cases = {"1000": 1000, "1000.0": 1000, "5.5": 5.5, "polyimide": "polyimide", "1 kHz": "1 kHz", "nan": "nan"}
        for text, stored in cases.items():
            with self.subTest(text):
                self.assertEqual(condition_value(text), stored)
                self.assertIs(type(condition_value(text)), type(stored))


class ReadingTests(SimpleTestCase):
    """read_points(), on its own: no database."""

    def read(self, text):
        return read_points(text)[1]

    def test_x_value_and_an_optional_plus_or_minus_split_on_tabs_commas_or_spaces(self):
        lines = self.read("60\t6.08\n75, 4.5, 0.2\n89.86   0.198")

        self.assertEqual([(line.x, line.value, line.uncertainty) for line in lines],
                         [(60, 6.08, None), (75, 4.5, 0.2), (89.86, 0.198, None)])
        self.assertEqual([line.problem for line in lines], ["", "", ""])

    def test_a_value_after_less_than_or_greater_than_is_a_limit(self):
        lines = self.read("80 <0.1\n85 < 0.1\n90\t>\t5\n95 0.05")

        self.assertEqual([(line.qualifier, line.value) for line in lines],
                         [(UPPER, 0.1), (UPPER, 0.1), (LOWER, 5), (EXACT, 0.05)])

    def test_the_minus_sign_papers_print_is_a_minus(self):
        self.assertEqual(self.read("−5 1.2")[0].x, -5)

    def test_a_first_line_with_no_number_is_a_header_and_blank_lines_keep_their_numbers(self):
        header, lines = read_points("T (°C), Ps (µC/cm²)\n\n60, 6.08\n\n75, 4.5\nT, Ps")

        self.assertEqual(header, 1)
        self.assertEqual([line.number for line in lines], [3, 5, 6])
        self.assertEqual(lines[2].problem, "“T” isn't a number.")  # only a first line is a header

    def test_a_line_that_cant_be_read_says_why(self):
        cases = {
            "60": "Expected two or three cells (x, value and optionally ±), but found 1.",
            "60 6.08 0.2 9": "Expected two or three cells (x, value and optionally ±), but found 4.",
            "60 6..08": "“6..08” isn't a number.",
            "60 nan": "“nan” isn't a number.",
            "60\t6,08": "“6,08” isn't a number. Use a decimal point.",
            "60\t\t0.2": "A cell is empty.",
            "60 <": "A cell is empty.",
        }
        for text, problem in cases.items():
            with self.subTest(text):
                self.assertEqual(self.read(text)[0].problem, problem)


class ReadingWithoutXTests(SimpleTestCase):
    """read_points() for values against nothing: value and optionally ±, with no x."""

    def test_a_line_is_a_value_and_optionally_its_plus_or_minus(self):
        lines = read_points("5.2\n<0.1\n4.8, 0.2\n1, 2, 3", has_x=False)[1]

        self.assertEqual(
            [(line.x, line.qualifier, line.value, line.uncertainty) for line in lines[:3]],
            [(None, "exact", 5.2, None), (None, "upper", 0.1, None), (None, "exact", 4.8, 0.2)],
        )
        self.assertEqual(lines[3].problem, "Expected one or two cells (value and optionally ±), but found 3.")


class BlockBoxesTests(CardTestCase):
    def block(self, response, number=0):
        return self.card(response).block_forms[number]

    def test_an_empty_card_has_one_block_with_no_boxes_for_a_choice_yet(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, 'name="card-0-block-TOTAL_FORMS" value="1"')
        self.assertContains(response, "<legend>Values 1</legend>", html=True)
        self.assertNotContains(response, "<legend>Values 2</legend>", html=True)
        block = self.block(response)
        self.assertEqual(block.condition_fields(), [])
        self.assertNotIn("direction", block.fields)
        self.assertNotIn("temperature_c", block.fields)

    def test_check_adds_the_boxes_the_property_kind_and_variable_need(self):
        chosen = {"property": "dielectric-anisotropy", "kind": "extrapolated"}
        cases = {
            # Against temperature: the direction, which a curve needs.
            "temperature": (["Frequency (Hz)"], "direction"),
            # A frequency sweep gives its frequencies, so it asks the temperature of every point.
            "frequency": ([], "temperature_c"),
        }
        for variable, (labels, box) in cases.items():
            with self.subTest(variable):
                block = self.block(self.send(blocks=[{**chosen, "variable": variable}], material="RM734"))

                self.assertEqual([field.label for field in block.condition_fields()], labels)
                self.assertIn(box, block.fields)
                self.assertEqual(len({"direction", "temperature_c"} & set(block.fields)), 1)

    def test_against_nothing_asks_neither_the_direction_nor_a_temperature(self):
        block = self.block(self.send(blocks=[{**LENGTH, "points": ""}], material="RM734"))

        self.assertEqual([field.label for field in block.condition_fields()], ["Conformer protocol", "Solvent or gas phase"])
        self.assertNotIn("direction", block.fields)
        self.assertNotIn("temperature_c", block.fields)
        self.assertEqual(block.errors["points"], ["Paste at least one line: the value."])

    def test_each_block_has_its_own_method_suggestions(self):
        response = self.send(blocks=[DELTA_EPSILON, PS_BLOCK], material="RM734")

        self.assertContains(response, 'list="card-0-block-0-method-suggestions"')
        self.assertContains(response, '<datalist id="card-0-block-1-method-suggestions">')
        self.assertContains(response, '<option value="current reversal">')


class BlockSaveTests(CardTestCase):
    def test_save_adds_a_single_value_and_a_curve_and_rm734s_page_shows_them(self):
        self.client.force_login(self.admin)  # an admin's save is approved at once, so the page shows it
        response = self.send(None, "save", True, blocks=[DELTA_EPSILON, PS_BLOCK], material="RM734")

        self.assertEqual(self.messages(response), [f"Saved 2 blocks of values for {self.rm734.accession} RM734."])
        value = Series.objects.get(property__code="dielectric-anisotropy")
        self.assertEqual(
            (value.material, value.reference, value.kind, value.direction, value.conditions, value.entered_by),
            (self.rm734, self.article, "extrapolated", Series.Direction.NOT_APPLICABLE, {"frequency_Hz": 1000}, self.admin),
        )
        self.assertEqual([(p.temperature_c, p.value, p.x) for p in value.points.all()], [(25, 5.2, None)])
        curve = Series.objects.get(property__code="ps")
        self.assertEqual((curve.material, curve.direction, curve.digitised), (self.rm734, Series.Direction.COOLING, True))
        self.assertEqual(curve.points.count(), 20)
        page = self.client.get(reverse("repo:material", args=[self.rm734.pk]))
        self.assertContains(page, CHART)
        self.assertContains(page, 'aria-label="Δε against temperature, extrapolated values: 1 series')

    def test_a_run_and_a_block_save_together_and_an_empty_block_is_skipped(self):
        response = self.send(RM734_COOLING, "save", True, blocks=[LENGTH], material="RM734")

        self.assertEqual(self.messages(response), [f"Saved a phase sequence and 1 block of values for {self.rm734.accession} RM734.", WAITING])
        point = Measurement.objects.get()
        self.assertEqual((point.series.variable.code, point.value, point.temperature_c, point.x), ("none", 2.4, None, None))
        self.assertEqual(PhaseSequence.objects.get().material, self.rm734)

    def test_a_limit_with_a_plus_or_minus_is_refused_and_nothing_is_saved(self):
        limit = {**DELTA_EPSILON, "points": "25, <5, 0.3"}

        response = self.send(RM734_COOLING, "save", blocks=[PS_BLOCK, limit], material="RM734", name="MB-1")

        self.assertContains(response, "Line 1: A limit has no ±: enter the limit itself as the value.")
        self.assertFalse(Series.objects.exists())
        self.assertFalse(PhaseSequence.objects.exists())
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")

    def test_a_block_is_refused_with_the_rules_messages(self):
        delta_epsilon = Property.objects.get(code="dielectric-anisotropy")
        cases = {
            "a required condition left out": (
                {**DELTA_EPSILON, "condition_frequency_Hz": ""}, "__all__", must_state(delta_epsilon, ["frequency_Hz"])
            ),
            "no temperature for a property that needs one": (
                {**DELTA_EPSILON, "variable": "none", "points": "5.2"}, "__all__", must_state(delta_epsilon, ["temperature"])
            ),
            "a curve against temperature without its direction": (
                {**PS_BLOCK, "direction": ""}, "direction", "This field is required."
            ),
            "a single value with a direction": (
                {**DELTA_EPSILON, "direction": "cooling"}, "direction", ValueBlockForm.ONE_LINE_HAS_NO_DIRECTION
            ),
            "values with no property chosen": ({**DELTA_EPSILON, "property": ""}, "property", "This field is required."),
            "no points": ({**PS_BLOCK, "points": ""}, "points", "Paste at least one line: x, then the value."),
        }
        for case, (block, box, message) in cases.items():
            with self.subTest(case):
                response = self.send(None, "save", blocks=[block], material="RM734")

                self.assertIn(message, self.card(response).block_forms[0].errors[box])
                self.assertFalse(Series.objects.exists())


class PointsAndRulesTests(CardTestCase):
    """How a block reads its points, and the rules for values it is checked by, block by block,
    as the forms for a single value and a curve were tested before the cards replaced them."""

    def send_block(self, button="check", block=PS_BLOCK, **boxes):
        """Post RM734's card with one block, block with boxes changed, and no runs."""
        return self.send(None, button, blocks=[{**block, **boxes}], material="RM734")

    def errors(self, response, box="points"):
        return self.card(response).block_forms[0].errors.get(box, [])

    def test_a_header_is_skipped_and_said(self):
        response = self.send_block(points="\n".join(["T (°C), Ps (µC/cm²)", *PS_CURVE]))

        self.assertContains(response, "Line 1 has no number in it, so it was read as a header.")
        self.assertContains(response, "20 points read, 0 lines with a problem.")

    def test_a_limit_is_saved_as_a_limit(self):
        self.send_block("save", points="\n".join([*PS_CURVE, "130, <0.1"]))

        self.assertEqual(Series.objects.get().points.get(temperature_c=130).qualifier, UPPER)

    def test_a_block_can_have_5000_lines_and_no_more(self):
        lines = [f"{i / 100}, 1" for i in range(MAX_CURVE_POINTS + 1)]

        too_many = self.send_block(points="\n".join(lines))
        at_the_limit = self.send_block(points="\n".join(lines[:-1]))

        self.assertEqual(self.errors(too_many), [
            "A curve can have at most 5,000 lines, and this one has 5,001. FFDB never thins data: "
            "thin the curve yourself, and say how in its notes."
        ])
        self.assertEqual(self.errors(at_the_limit), [])
        self.assertContains(at_the_limit, f"{MAX_CURVE_POINTS} points read")

    def test_lines_with_a_problem_are_named_up_to_a_limit(self):
        response = self.send_block(points="\n".join(f"{i}, x" for i in range(1, NAMED_PROBLEM_LINES + 6)))

        errors = self.errors(response)
        self.assertEqual(len(errors), 1 + NAMED_PROBLEM_LINES + 1)
        self.assertEqual(errors[1], "Line 1: “x” isn't a number.")
        self.assertEqual(errors[-1], "…and 5 more lines with a problem.")
        self.assertContains(response, f"0 points read, {NAMED_PROBLEM_LINES + 5} lines with a problem.")

    def test_against_frequency_each_point_has_its_frequency_as_x_and_the_one_temperature(self):
        self.send_block("save", FREQUENCY_SWEEP, points="100, 5.9\n1000, 5.8\n10000, 5.1")

        series = Series.objects.get()
        self.assertEqual((series.variable.code, series.direction), ("frequency", Series.Direction.NOT_APPLICABLE))
        self.assertEqual(series.conditions, {"cell_gap_um": 8})
        self.assertEqual(list(series.points.values_list("x", "temperature_c", "value")),
                         [(100, 70, 5.9), (1000, 70, 5.8), (10000, 70, 5.1)])

    def test_against_frequency_the_temperature_is_asked_for_once_and_a_frequency_must_be_more_than_0(self):
        response = self.send_block(block=FREQUENCY_SWEEP, temperature_c="", points="0, 5.9\n1000, 5.8\n10000, 5.1")

        temperature_errors = self.errors(response, "temperature_c")
        self.assertEqual(len(temperature_errors), 1)
        self.assertIn("Ps values must state: temperature.", temperature_errors[0])
        self.assertIn("Line 1: Frequency is drawn on a log axis, so it must be more than 0.", self.errors(response))
        self.assertContains(response, "2 points read, 1 line with a problem.")

    def test_only_values_from_a_figure_are_digitised(self):
        response = self.send_block("save", taken_from="table", location="Table 2")

        self.assertIn("Only values read off a figure are digitised", " ".join(self.errors(response, "digitised")))
        self.assertFalse(Series.objects.exists())

    def test_values_from_a_figure_must_say_which(self):
        response = self.send_block(block=DELTA_EPSILON, taken_from="figure", location="")

        self.assertEqual(self.errors(response, "location"), ["Give the figure's label, e.g. Fig. 3b."])

    def test_other_conditions_are_saved_as_key_value_pairs(self):
        self.send_block("save", DELTA_EPSILON, other_conditions="alignment: planar\n\nfield_V_um: 2")

        self.assertEqual(Series.objects.get().conditions, {"frequency_Hz": 1000, "alignment": "planar", "field_V_um": 2})

    def test_other_conditions_are_refused_without_a_colon_or_with_their_own_box(self):
        cases = {
            "alignment planar": "Line 1: write each condition as key: value",
            "alignment: planar\nfrequency_Hz: 50": "Line 2: frequency_Hz isn't one of the other conditions",
            "method: DFT": "Line 1: method isn't one of the other conditions",
            "a: 1\na: 2": "Line 2: a is given twice.",
        }
        for text, message in cases.items():
            with self.subTest(text):
                response = self.send_block("save", DELTA_EPSILON, other_conditions=text)

                self.assertIn(message, " ".join(self.errors(response, "other_conditions")))
        self.assertFalse(Series.objects.exists())
