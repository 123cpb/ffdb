"""Adding a curve from a reference's Add data page. A first form says what the curve is of and
what it is against; a second asks for its method, conditions and where it is from, and has a
box for its points, pasted as x, value and optionally ±. Check reads them and draws the chart;
Save stores the curve through the rules for values, once every line is a point."""

from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse

from repo.forms import MAX_CURVE_POINTS, NAMED_PROBLEM_LINES, read_points
from repo.models import Material, Measurement, PhaseSequence, Series, Variable
from repo.tests.test_add_data import AddDataTestCase
from repo.tests.test_values import RM734

EXACT, UPPER, LOWER = Measurement.Qualifier.EXACT, Measurement.Qualifier.UPPER, Measurement.Qualifier.LOWER

# A digitised Ps curve of RM734 on cooling, as a digitiser gives it: 20 lines of T (°C), Ps (µC/cm²).
PS_CURVE = [f"{60 + 3.5 * i:g}, {6.0 - 0.28 * i:.2f}" for i in range(20)]
# The same with line 12 mistyped.
WITH_A_BAD_LINE = PS_CURVE[:11] + ["98.5, 2..92"] + PS_CURVE[12:]
# The second form's boxes for that curve.
BOXES = {"method": "current reversal", "condition_frequency_Hz": "50", "condition_cell_gap_um": "8",
         "taken_from": "figure", "location": "Fig. 3a", "digitised": "on", "direction": "cooling"}
# How a screen reader names the curve's chart, on Check and on the material page.
CHART = 'aria-label="Ps against temperature, measured values: 1 series, from Mandle 2017, Chem. Eur. J."'


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


class CurveTestCase(AddDataTestCase):
    """The article gives a sequence for RM734, so RM734 is one of its materials."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.article, sequence="K 139.8 NF 132.7 N 187.9 I")

    def curve_page(self, prop="ps", kind="measured", variable="temperature", **choices):
        """The add-a-curve page's address with the first form's choices: RM734 unless choices
        say otherwise."""
        choices.setdefault("material", self.rm734.pk)
        query = urlencode({"property": prop, "kind": kind, "variable": variable, **choices})
        return f"{reverse('repo:add_curve', args=[self.article.pk])}?{query}"

    def send(self, button, lines, boxes=BOXES, follow=False, **choices):
        """Press Check or Save on the second form with lines in the points box."""
        data = {**boxes, "points": "\n".join(lines), button: ""}
        return self.client.post(self.curve_page(**choices), data, follow=follow)

    def errors(self, response, field="points"):
        return response.context["form"].errors.get(field, [])


class ChoosingTests(CurveTestCase):
    def test_the_add_data_page_offers_a_curve_with_boxes_of_its_own(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, "Add a curve")
        self.assertContains(response, f'action="{reverse("repo:add_curve", args=[self.article.pk])}"')
        # Its own ids: the single value's first form has a material box too.
        self.assertContains(response, 'id="id_curve_material"')
        self.assertContains(response, 'id="id_material"')

    def test_nothing_isnt_offered_as_what_a_curve_is_against(self):
        response = self.client.get(self.curve_page(variable="none"))

        self.assertIn("variable", response.context["choice"].errors)
        self.assertIsNone(response.context["form"])

    def test_against_temperature_asks_its_direction_and_against_frequency_its_temperature(self):
        by_temperature = self.client.get(self.curve_page()).context["form"]
        by_frequency = self.client.get(self.curve_page(variable="frequency")).context["form"]

        self.assertIn("direction", by_temperature.fields)
        self.assertNotIn("temperature_c", by_temperature.fields)
        self.assertEqual([field.label for field in by_temperature.condition_fields()], ["Frequency (Hz)", "Cell gap (µm)"])
        self.assertIn("temperature_c", by_frequency.fields)
        self.assertNotIn("direction", by_frequency.fields)
        # A frequency sweep's frequencies are its points' x, so it has no frequency box.
        self.assertEqual([field.label for field in by_frequency.condition_fields()], ["Cell gap (µm)"])

    def test_the_points_box_states_the_units(self):
        response = self.client.get(self.curve_page(variable="frequency"))

        self.assertContains(response, "Points: Frequency (Hz), Ps (µC/cm²) and optionally ±")


class TheCheckTests(CurveTestCase):
    """The block's check: a digitised Ps curve of 20 lines, one of them bad."""

    def test_check_reads_19_points_names_the_bad_line_and_draws_the_curve(self):
        response = self.send("check", WITH_A_BAD_LINE)

        self.assertContains(response, "19 points read, 1 line with a problem. Nothing has been saved.")
        self.assertIn("Line 12: “2..92” isn't a number.", self.errors(response))
        self.assertContains(response, '<figure class="chart">', count=1)
        self.assertContains(response, CHART)
        self.assertFalse(Series.objects.exists())

    def test_save_is_refused_until_every_line_is_a_point(self):
        response = self.send("save", WITH_A_BAD_LINE)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Nothing is saved until every line is a point: fix or delete these lines.", self.errors(response))
        self.assertFalse(Series.objects.exists())

    def test_once_the_bad_line_is_deleted_save_adds_the_curve_and_the_material_page_draws_it(self):
        fixed = [line for line in WITH_A_BAD_LINE if line != "98.5, 2..92"]

        response = self.send("save", fixed, follow=True)

        self.assertRedirects(response, self.add_page(self.article))
        self.assertIn(f"Saved a Ps curve of 19 points for {self.rm734.accession}.", self.messages(response))
        series = Series.objects.get()
        self.assertEqual(
            (series.material, series.reference, series.property.code, series.kind, series.variable.code),
            (self.rm734, self.article, "ps", "measured", Variable.TEMPERATURE),
        )
        self.assertEqual((series.method, series.conditions), ("current reversal", {"frequency_Hz": 50, "cell_gap_um": 8}))
        self.assertEqual((series.direction, series.taken_from, series.location, series.digitised),
                         ("cooling", "figure", "Fig. 3a", True))
        self.assertEqual(series.entered_by, self.curator)
        # Against temperature, each line's x is the point's temperature.
        first = series.points.order_by("temperature_c").first()
        self.assertEqual((first.temperature_c, first.value, first.x), (60, 6.0, None))
        self.assertEqual(series.points.count(), 19)
        self.assertContains(self.client.get(reverse("repo:material", args=[self.rm734.pk])), CHART)


class PointsTests(CurveTestCase):
    def test_a_header_is_skipped_and_said(self):
        response = self.send("check", ["T (°C), Ps (µC/cm²)", *PS_CURVE])

        self.assertContains(response, "Line 1 has no number in it, so it was read as a header.")
        self.assertContains(response, "20 points read, 0 lines with a problem.")

    def test_a_limit_is_saved_as_a_limit(self):
        self.send("save", [*PS_CURVE, "130, <0.1"])

        self.assertEqual(Series.objects.get().points.get(temperature_c=130).qualifier, UPPER)

    def test_a_limit_with_a_plus_or_minus_is_refused_by_the_rules(self):
        response = self.send("save", [*PS_CURVE, "130, <0.1, 0.02"])

        self.assertIn("Line 21: A limit has no ±: enter the limit itself as the value.", self.errors(response))
        self.assertFalse(Series.objects.exists())

    def test_an_empty_box_is_refused(self):
        for lines in [[], ["", "   "]]:
            with self.subTest(lines):
                response = self.send("check", lines)

                self.assertEqual(self.errors(response), ["Paste at least one line: x, then the value."])

    def test_a_curve_can_have_5000_lines_and_no_more(self):
        lines = [f"{i / 100}, 1" for i in range(MAX_CURVE_POINTS + 1)]

        too_many = self.send("check", lines)
        at_the_limit = self.send("check", lines[:-1])

        self.assertEqual(self.errors(too_many), [
            "A curve can have at most 5,000 lines, and this one has 5,001. FFDB never thins data: "
            "thin the curve yourself, and say how in its notes."
        ])
        self.assertEqual(self.errors(at_the_limit), [])
        self.assertContains(at_the_limit, f"{MAX_CURVE_POINTS} points read")

    def test_lines_with_a_problem_are_named_up_to_a_limit(self):
        response = self.send("check", [f"{i}, x" for i in range(1, NAMED_PROBLEM_LINES + 6)])

        errors = self.errors(response)
        self.assertEqual(len(errors), 1 + NAMED_PROBLEM_LINES + 1)
        self.assertEqual(errors[1], "Line 1: “x” isn't a number.")
        self.assertEqual(errors[-1], "…and 5 more lines with a problem.")
        self.assertContains(response, f"0 points read, {NAMED_PROBLEM_LINES + 5} lines with a problem.")


class AgainstFrequencyTests(CurveTestCase):
    """Ps against frequency: the points' x is the frequency, and every point has the T box's
    temperature."""

    boxes = {"method": "current reversal", "condition_cell_gap_um": "8", "taken_from": "table", "location": "Table S2",
             "temperature_c": "70"}

    def test_each_point_has_its_frequency_as_x_and_the_one_temperature(self):
        self.send("save", ["100, 5.9", "1000, 5.8", "10000, 5.1"], self.boxes, variable="frequency")

        series = Series.objects.get()
        self.assertEqual((series.variable.code, series.direction), ("frequency", Series.Direction.NOT_APPLICABLE))
        self.assertEqual(series.conditions, {"cell_gap_um": 8})
        self.assertEqual(list(series.points.values_list("x", "temperature_c", "value")),
                         [(100, 70, 5.9), (1000, 70, 5.8), (10000, 70, 5.1)])

    def test_the_temperature_is_asked_for_once_and_a_frequency_must_be_more_than_0(self):
        boxes = {**self.boxes, "temperature_c": ""}

        response = self.send("check", ["0, 5.9", "1000, 5.8", "10000, 5.1"], boxes, variable="frequency")

        temperature_errors = self.errors(response, "temperature_c")
        self.assertEqual(len(temperature_errors), 1)
        self.assertIn("Ps values must state: temperature.", temperature_errors[0])
        self.assertIn("Line 1: Frequency is drawn on a log axis, so it must be more than 0.", self.errors(response))
        self.assertContains(response, "2 points read, 1 line with a problem.")


class SeriesRulesTests(CurveTestCase):
    def test_a_curve_against_temperature_must_give_its_direction_once(self):
        response = self.send("save", PS_CURVE, {**BOXES, "direction": ""})

        # Said by the form only: the rules don't check a box that already has an error.
        self.assertEqual(self.errors(response, "direction"), ["This field is required."])
        self.assertFalse(Series.objects.exists())

    def test_only_a_curve_from_a_figure_is_digitised(self):
        response = self.send("save", PS_CURVE, {**BOXES, "taken_from": "table", "location": "Table 2"})

        self.assertIn("Only values read off a figure are digitised", " ".join(self.errors(response, "digitised")))
        self.assertFalse(Series.objects.exists())

    def test_a_missing_required_condition_is_refused_with_the_rules_message(self):
        response = self.send("save", PS_CURVE, {**BOXES, "condition_cell_gap_um": ""})

        self.assertIn('Ps values must state: cell gap ("cell_gap_um" in Conditions).',
                      " ".join(response.context["form"].non_field_errors()))
        self.assertFalse(Series.objects.exists())

    def test_a_logged_in_user_who_isnt_a_curator_is_refused(self):
        self.client.force_login(User.objects.create_user("other"))

        self.assertEqual(self.client.get(self.curve_page()).status_code, 403)
        self.assertEqual(self.send("save", PS_CURVE).status_code, 403)
        self.assertFalse(Series.objects.exists())
