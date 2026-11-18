"""Adding a single value from a reference's Add data page: a first form says what the value is
of, and a second asks for what that property needs for that kind, then saves it as a series of
one point, checked by the rules for values."""

from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse

from repo.forms import condition_value
from repo.models import Material, PhaseSequence, Property, Series, Variable
from repo.tests.test_add_data import FIVE_CB, AddDataTestCase
from repo.tests.test_values import RM734

# The second form's boxes for an extrapolated Δε at 25 °C and 1 kHz, as the check adds it.
DELTA_EPSILON = {"qualifier": "exact", "value": "5.2", "temperature_c": "25", "condition_frequency_Hz": "1000",
                 "taken_from": "table", "location": "Table 2"}


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


class SingleValueTests(AddDataTestCase):
    """The article gives a sequence for RM734. 5CB is in FFDB, from no reference."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.article, sequence="K 139.8 NF 132.7 N 187.9 I")

    def value_page(self, prop="dielectric-anisotropy", kind="extrapolated", temperature="yes", **choices):
        """The add-a-value page's address with the first form's choices: RM734 unless choices
        say otherwise."""
        choices.setdefault("material", self.rm734.pk)
        query = urlencode({"property": prop, "kind": kind, "temperature": temperature, **choices})
        return f"{reverse('repo:add_value', args=[self.article.pk])}?{query}"

    def save(self, boxes, follow=False, **choices):
        """Send the second form. With follow, the response is the page it redirects to."""
        return self.client.post(self.value_page(**choices), boxes, follow=follow)

    def form_errors(self, response, form="form"):
        """Every error message on the page's form (choice, or form), each field's together."""
        return " ".join(" ".join(messages) for messages in response.context[form].errors.values())


class ChoosingTests(SingleValueTests):
    def test_the_add_data_page_offers_its_materials_only(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, "Add a single value")
        self.assertEqual(list(response.context["choice"].fields["material"].queryset), [self.rm734])

    def test_without_choices_the_page_shows_only_the_first_form(self):
        response = self.client.get(reverse("repo:add_value", args=[self.article.pk]))

        self.assertFalse(response.context["choice"].errors)
        self.assertIsNone(response.context["form"])

    def test_any_material_can_be_chosen_by_its_lcr_id(self):
        response = self.client.get(self.value_page(material="", lcr_id=self.five_cb.accession.lower()))

        self.assertEqual(response.context["form"].material, self.five_cb)

    def test_the_material_is_refused_unless_it_is_given_once(self):
        cases = {
            "Choose one of this reference's materials": {"material": ""},
            "not both": {"lcr_id": self.five_cb.accession},
            "isn't an LCR ID": {"material": "", "lcr_id": "5CB"},
            "No material has the LCR ID LCR-09999": {"material": "", "lcr_id": "LCR-9999"},
        }
        for message, choices in cases.items():
            with self.subTest(message):
                response = self.client.get(self.value_page(**choices))

                self.assertIn(message, self.form_errors(response, "choice"))
                self.assertIsNone(response.context["form"])

    def test_no_temperature_is_refused_for_a_property_that_needs_one(self):
        response = self.client.get(self.value_page(temperature="no"))

        self.assertIn("Δε values must state: temperature.", self.form_errors(response, "choice"))


class SecondFormTests(SingleValueTests):
    def test_an_extrapolated_delta_epsilon_asks_for_frequency_but_not_cell_gap_or_coating(self):
        form = self.client.get(self.value_page()).context["form"]

        self.assertEqual([field.label for field in form.condition_fields()], ["Frequency (Hz)"])
        self.assertIn("temperature_c", form.fields)

    def test_a_measured_delta_epsilon_asks_for_cell_gap_and_coating_too(self):
        form = self.client.get(self.value_page(kind="measured")).context["form"]

        self.assertEqual(
            [field.label for field in form.condition_fields()], ["Frequency (Hz)", "Cell gap (µm)", "Electrode coating"]
        )

    def test_the_propertys_methods_are_offered_as_suggestions(self):
        response = self.client.get(self.value_page())

        self.assertContains(response, 'list="method-suggestions"')
        self.assertContains(response, '<option value="capacitance (LCR meter)">', html=True)

    def test_a_value_without_a_temperature_has_no_temperature_box(self):
        form = self.client.get(self.value_page(prop="dipole-parallel", kind="computed", temperature="no")).context["form"]

        self.assertNotIn("temperature_c", form.fields)
        self.assertEqual(
            [field.label for field in form.condition_fields()], ["Conformer protocol", "Solvent or gas phase"]
        )


class SavingTests(SingleValueTests):
    def test_the_check_an_extrapolated_delta_epsilon_at_25_c_and_1_khz_is_saved(self):
        response = self.save(DELTA_EPSILON, follow=True)

        self.assertRedirects(response, self.add_page(self.article))
        self.assertIn(f"Saved a Δε value for {self.rm734.accession}.", self.messages(response))
        self.assertContains(response, "Dielectric anisotropy, Δε")  # in the reference's values
        series = Series.objects.get()
        self.assertEqual(
            (series.material, series.reference, series.property.code, series.kind, series.variable.code),
            (self.rm734, self.article, "dielectric-anisotropy", "extrapolated", Variable.TEMPERATURE),
        )
        self.assertEqual(series.conditions, {"frequency_Hz": 1000})
        self.assertEqual(series.direction, Series.Direction.NOT_APPLICABLE)
        self.assertEqual((series.taken_from, series.location), ("table", "Table 2"))
        self.assertEqual(series.entered_by, self.curator)
        point = series.points.get()
        self.assertEqual((point.value, point.temperature_c, point.uncertainty), (5.2, 25, None))

    def test_a_limit_with_a_plus_or_minus_is_refused(self):
        response = self.save({**DELTA_EPSILON, "qualifier": "upper", "uncertainty": "0.3"})

        self.assertEqual(response.context["form"].errors["uncertainty"], ["A limit has no ±: enter the limit itself as the value."])
        self.assertFalse(Series.objects.exists())

    def test_a_missing_required_condition_is_refused_with_the_rules_message(self):
        response = self.save({**DELTA_EPSILON, "condition_frequency_Hz": ""})

        self.assertIn('Δε values must state: frequency ("frequency_Hz" in Conditions).', self.form_errors(response))
        self.assertFalse(Series.objects.exists())

    def test_other_conditions_are_saved_as_key_value_pairs(self):
        self.save({**DELTA_EPSILON, "other_conditions": "alignment: planar\n\nfield_V_um: 2"})

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
                response = self.save({**DELTA_EPSILON, "other_conditions": text})

                self.assertIn(message, " ".join(response.context["form"].errors["other_conditions"]))
        self.assertFalse(Series.objects.exists())

    def test_a_value_from_a_figure_must_say_which(self):
        response = self.save({**DELTA_EPSILON, "taken_from": "figure", "location": ""})

        self.assertIn("Give the figure's label", self.form_errors(response))

    def test_a_value_without_a_temperature_is_saved_against_nothing(self):
        boxes = {"qualifier": "exact", "value": "3.1", "method": "DFT", "condition_conformer_protocol": "lowest-energy conformer",
                 "condition_solvent": "gas phase", "taken_from": "text"}
        self.save(boxes, prop="dipole-parallel", kind="computed", temperature="no")

        series = Series.objects.get()
        self.assertEqual(series.variable.code, Variable.NONE)
        self.assertIsNone(series.points.get().temperature_c)

    def test_a_logged_in_user_who_isnt_a_curator_is_refused(self):
        self.client.force_login(User.objects.create_user("other"))

        self.assertEqual(self.client.get(self.value_page()).status_code, 403)
        self.assertEqual(self.save(DELTA_EPSILON).status_code, 403)
        self.assertFalse(Series.objects.exists())
