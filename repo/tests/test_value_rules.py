"""The rules for values: what a series and each of its points must state, so values from
different references can be compared (Series.clean() and Measurement.clean())."""

from django.contrib.auth.models import User
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.urls import reverse

from repo.models import Measurement, Property, Series, Variable
from repo.tests.test_series import SeriesTestCase

# What a measured Ps series must state besides its method (see the 0011 migration).
PS_CONDITIONS = {"frequency_Hz": 100, "cell_gap_um": 5}
# What a measured ε∥ series must state besides temperature and frequency.
EPS_CONDITIONS = {"cell_gap_um": 5, "electrode_coating": "polyimide"}
DELTA_N_CONDITIONS = {"wavelength_nm": 589}


class RulesTestCase(SeriesTestCase):
    def series(self, property_code, variable_code="temperature", **fields):
        """A series that isn't saved, for full_clean() to check. Measured unless kind is given."""
        fields.setdefault("kind", Series.Kind.MEASURED)
        return Series(
            material=self.material,
            reference=self.reference,
            property=Property.objects.get(code=property_code),
            variable=Variable.objects.get(code=variable_code),
            **fields,
        )

    def point(self, series, **fields):
        """A point of series, for full_clean() to check. The series is saved first, because
        full_clean() refuses a point whose series has no primary key."""
        series.save()
        return Measurement(series=series, **fields)

    def assertRefused(self, obj, field, *words):
        """full_clean() refuses obj with a message on field (NON_FIELD_ERRORS for one that
        belongs to no one field) containing each of words. Returns the message."""
        with self.assertRaises(ValidationError) as caught:
            obj.full_clean()
        errors = caught.exception.message_dict
        self.assertIn(field, errors)
        message = " ".join(errors[field])
        for word in words:
            self.assertIn(word, message)
        return message


class SeriesConditionTests(RulesTestCase):
    def test_a_ps_series_without_a_frequency_is_refused_naming_it(self):
        series = self.series("ps", method="current reversal", conditions={"cell_gap_um": 5})
        self.assertRefused(
            series, NON_FIELD_ERRORS, 'Ps values must state: frequency ("frequency_Hz" in Conditions).'
        )

    def test_a_ps_series_without_a_method_is_refused_naming_it(self):
        series = self.series("ps", method="  ", conditions=PS_CONDITIONS)
        self.assertRefused(series, NON_FIELD_ERRORS, "Ps values must state: method.")

    def test_a_ps_series_that_states_everything_passes(self):
        self.series("ps", method="current reversal", conditions=PS_CONDITIONS).full_clean()

    def test_a_condition_with_an_empty_value_is_missing(self):
        series = self.series("ps", method="current reversal", conditions={"frequency_Hz": "", "cell_gap_um": 5})
        self.assertRefused(series, NON_FIELD_ERRORS, "frequency")

    def test_a_computed_value_states_the_computed_list(self):
        # A computed d33 names no reference crystal: it states its local-field model instead.
        series = self.series("d33", "none", kind=Series.Kind.COMPUTED, method="DFT")
        message = self.assertRefused(series, NON_FIELD_ERRORS, "local-field model")
        self.assertNotIn("reference crystal", message)

    def test_the_variables_own_condition_is_stated_by_the_points(self):
        # ε∥ must state a frequency, and a frequency sweep gives it as each point's x.
        self.series("eps-parallel", "frequency", conditions=EPS_CONDITIONS).full_clean()

    def test_the_variable_cannot_also_be_a_fixed_condition(self):
        series = self.series("eps-parallel", "frequency", conditions={"frequency_Hz": 1000, **EPS_CONDITIONS})
        self.assertRefused(series, "conditions", "Frequency is what this series varies")

    def test_conditions_must_be_key_value_pairs(self):
        series = self.series("ps", method="current reversal", conditions=[100, 5])
        self.assertRefused(series, "conditions", "key: value pairs")


class DigitisedTests(RulesTestCase):
    def test_only_values_taken_from_a_figure_can_be_digitised(self):
        series = self.series(
            "birefringence", conditions=DELTA_N_CONDITIONS, digitised=True, taken_from=Series.TakenFrom.TABLE
        )
        self.assertRefused(series, "digitised", "choose Figure")

        series.taken_from = Series.TakenFrom.FIGURE
        series.full_clean()


class LimitTests(RulesTestCase):
    def test_a_limit_has_no_uncertainty(self):
        series = self.series("birefringence", conditions=DELTA_N_CONDITIONS)
        point = self.point(
            series, qualifier=Measurement.Qualifier.UPPER, value=0.01, uncertainty=0.002, temperature_c=25
        )
        self.assertRefused(point, "uncertainty", "A limit has no ±")

    def test_an_exact_value_can_have_an_uncertainty(self):
        series = self.series("birefringence", conditions=DELTA_N_CONDITIONS)
        self.point(series, value=0.21, uncertainty=0.01, temperature_c=25).full_clean()


class PointPositionTests(RulesTestCase):
    """What each point must give, by what its series is against."""

    def computed_dipole(self, variable_code):
        """A computed dipole moment's conditions don't include temperature."""
        return self.series(
            "dipole-moment", variable_code, kind=Series.Kind.COMPUTED, method="DFT",
            conditions={"conformer_protocol": "lowest energy", "solvent": "gas phase"},
        )

    def test_a_value_against_nothing_has_no_x(self):
        point = self.point(self.computed_dipole("none"), value=11.4, x=1)
        self.assertRefused(point, "x", "A value against nothing has no x.")

    def test_a_value_against_nothing_has_no_temperature(self):
        point = self.point(self.computed_dipole("none"), value=11.4, temperature_c=25)
        self.assertRefused(point, "temperature_c", "choose Temperature")

    def test_against_temperature_a_point_gives_t_not_x(self):
        series = self.series("birefringence", conditions=DELTA_N_CONDITIONS)
        point = self.point(series, value=0.21, temperature_c=25, x=25)
        self.assertRefused(point, "x", "as T, not x")

    def test_against_temperature_a_point_needs_its_temperature(self):
        point = self.point(self.computed_dipole("temperature"), value=11.4)
        self.assertRefused(point, "temperature_c", "Give each point's temperature")

    def test_against_another_variable_a_point_needs_its_x(self):
        series = self.series("eps-parallel", "frequency", conditions=EPS_CONDITIONS)
        point = self.point(series, value=9000, temperature_c=80)
        self.assertRefused(point, "x", "Give the frequency (Hz) of each point.")

    def test_an_x_on_a_log_axis_must_be_above_zero(self):
        series = self.series("eps-parallel", "frequency", conditions=EPS_CONDITIONS)
        point = self.point(series, value=9000, temperature_c=80, x=0)
        self.assertRefused(point, "x", "log axis")

    def test_a_property_that_requires_temperature_needs_it_on_every_point(self):
        series = self.series("eps-parallel", "frequency", conditions=EPS_CONDITIONS)
        point = self.point(series, value=9000, x=1000)
        self.assertRefused(point, "temperature_c", "ε∥ values must state: temperature.")

    def test_a_birefringence_value_at_25_c_with_a_wavelength_passes(self):
        series = self.series("birefringence", conditions=DELTA_N_CONDITIONS)
        series.full_clean()
        self.point(series, value=0.21, temperature_c=25).full_clean()


class AdminTests(RulesTestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))

    def add_on_admin(self, property_code, conditions, method="", points=()):
        """Fill in the series Add page, against temperature, and press Save. Each point is
        (value is, value, ±, T). The management form tells Django how many rows were sent."""
        data = {
            "material": self.material.pk,
            "reference": self.reference.pk,
            "property": Property.objects.get(code=property_code).pk,
            "variable": Variable.objects.get(code="temperature").pk,
            "kind": "measured", "method": method, "conditions": conditions,
            "direction": "unspecified", "taken_from": "", "location": "", "notes": "",
            "points-TOTAL_FORMS": str(len(points)), "points-INITIAL_FORMS": "0",
        }
        for i, (qualifier, value, uncertainty, temperature) in enumerate(points):
            data.update({
                f"points-{i}-qualifier": qualifier, f"points-{i}-value": value,
                f"points-{i}-uncertainty": uncertainty, f"points-{i}-temperature_c": temperature,
                f"points-{i}-x": "",
            })
        return self.client.post(reverse("admin:repo_series_add"), data)

    def test_a_ps_series_without_a_frequency_is_refused_on_its_page(self):
        response = self.add_on_admin(
            "ps", '{"cell_gap_um": 5}', method="current reversal", points=[("exact", "6.0", "", "80")]
        )
        self.assertContains(response, "Ps values must state: frequency")
        self.assertFalse(Series.objects.exists())

    def test_a_birefringence_value_at_25_c_with_a_wavelength_saves(self):
        response = self.add_on_admin("birefringence", '{"wavelength_nm": 589}', points=[("exact", "0.21", "", "25")])
        self.assertEqual(response.status_code, 302)  # saved, and sent back to the list
        self.assertEqual(Series.objects.get().points.get().value, 0.21)

    def test_a_limit_with_an_uncertainty_is_refused_on_its_page(self):
        response = self.add_on_admin(
            "birefringence", '{"wavelength_nm": 589}', points=[("upper", "0.01", "0.002", "25")]
        )
        self.assertContains(response, "A limit has no ±")
        self.assertFalse(Series.objects.exists())

    def test_points_are_checked_against_a_series_that_is_not_saved_yet(self):
        # On the Add page each point is checked against the series being added.
        response = self.add_on_admin("birefringence", '{"wavelength_nm": 589}', points=[("exact", "0.21", "", "")])
        self.assertContains(response, "Δn values must state: temperature.")
        self.assertFalse(Series.objects.exists())

    def test_a_series_with_no_points_is_refused(self):
        response = self.add_on_admin("birefringence", '{"wavelength_nm": 589}')
        self.assertContains(response, "A series needs at least one point.")
        self.assertFalse(Series.objects.exists())
