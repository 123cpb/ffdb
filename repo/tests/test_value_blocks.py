"""Blocks of values on a compound card, for the card's material: one line is a single value and
several are a curve. A block's choices add the boxes they need, so without JavaScript those
appear after Check. A block is refused by the rules of the single value and curve forms, with
their messages, and Save adds the card's blocks with the rest of it, or nothing."""

from django.test import SimpleTestCase
from django.urls import reverse

from repo.forms import ValueBlockForm, read_points
from repo.models import Measurement, PhaseSequence, Property, Series, must_state
from repo.tests.test_compound_card import RM734_COOLING, CardTestCase
from repo.tests.test_curves import BOXES, CHART, PS_CURVE

# A block for an extrapolated Δε at 25 °C and 1 kHz, as the check adds it: one line, so a single value.
DELTA_EPSILON = {
    "property": "dielectric-anisotropy", "kind": "extrapolated", "variable": "temperature",
    "condition_frequency_Hz": "1000", "taken_from": "table", "location": "Table 2", "points": "25, 5.2",
}
# A block for RM734's digitised Ps curve on cooling, as the curve form's tests add it: 20 lines.
PS_BLOCK = {"property": "ps", "kind": "measured", "variable": "temperature", **BOXES, "points": "\n".join(PS_CURVE)}
# A computed molecular length, which has no temperature: a value against nothing, with no x.
LENGTH = {
    "property": "molecular-length", "kind": "computed", "variable": "none", "method": "DFT",
    "condition_conformer_protocol": "lowest energy", "condition_solvent": "gas phase",
    "taken_from": "table", "location": "Table S1", "points": "2.4",
}


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

    def test_an_empty_card_has_two_blocks_with_no_boxes_for_a_choice_yet(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, 'name="block-TOTAL_FORMS" value="2"')
        self.assertContains(response, "<legend>Values 2</legend>", html=True)
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

        self.assertContains(response, 'list="block-0-method-suggestions"')
        self.assertContains(response, '<datalist id="block-1-method-suggestions">')
        self.assertContains(response, '<option value="current reversal">')


class BlockSaveTests(CardTestCase):
    def test_save_adds_a_single_value_and_a_curve_and_rm734s_page_shows_them(self):
        response = self.send([], "save", True, blocks=[DELTA_EPSILON, PS_BLOCK], material="RM734")

        self.assertEqual(self.messages(response), [f"Saved 2 blocks of values for {self.rm734.accession} RM734."])
        value = Series.objects.get(property__code="dielectric-anisotropy")
        self.assertEqual(
            (value.material, value.reference, value.kind, value.direction, value.conditions, value.entered_by),
            (self.rm734, self.article, "extrapolated", Series.Direction.NOT_APPLICABLE, {"frequency_Hz": 1000}, self.curator),
        )
        self.assertEqual([(p.temperature_c, p.value, p.x) for p in value.points.all()], [(25, 5.2, None)])
        curve = Series.objects.get(property__code="ps")
        self.assertEqual((curve.material, curve.direction, curve.digitised), (self.rm734, Series.Direction.COOLING, True))
        self.assertEqual(curve.points.count(), 20)
        page = self.client.get(reverse("repo:material", args=[self.rm734.pk]))
        self.assertContains(page, CHART)
        self.assertContains(page, 'aria-label="Δε against temperature, extrapolated values: 1 series')

    def test_a_run_and_a_block_save_together_and_an_empty_block_is_skipped(self):
        response = self.send([RM734_COOLING], "save", True, blocks=[LENGTH], material="RM734")

        self.assertEqual(self.messages(response), [f"Saved 1 run and 1 block of values for {self.rm734.accession} RM734."])
        point = Measurement.objects.get()
        self.assertEqual((point.series.variable.code, point.value, point.temperature_c, point.x), ("none", 2.4, None, None))
        self.assertEqual(PhaseSequence.objects.get().material, self.rm734)

    def test_a_limit_with_a_plus_or_minus_is_refused_and_nothing_is_saved(self):
        limit = {**DELTA_EPSILON, "points": "25, <5, 0.3"}

        response = self.send([RM734_COOLING], "save", blocks=[PS_BLOCK, limit], material="RM734", name="MB-1")

        self.assertContains(response, "Line 1: A limit has no ±: enter the limit itself as the value.")
        self.assertFalse(Series.objects.exists())
        self.assertFalse(PhaseSequence.objects.exists())
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")

    def test_a_block_is_refused_with_the_messages_of_the_single_value_and_curve_forms(self):
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
        }
        for case, (block, box, message) in cases.items():
            with self.subTest(case):
                response = self.send([], "save", blocks=[block], material="RM734")

                self.assertIn(message, self.card(response).block_forms[0].errors[box])
                self.assertFalse(Series.objects.exists())
