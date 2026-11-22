from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from repo.models import Material, Measurement, Property, Reference, Series, Variable
from repo.templatetags.repo_tags import conditions_text, num, with_unit

DIO = "c1c(c(c(cc1C1OCC(CO1)CCC)F)C(=O)Oc1cc(c(cc1)c1cc(c(c(c1)F)F)F)F)F"
RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"


def add(
    material, reference, property_code, variable_code="temperature", kind=Series.Kind.MEASURED, approved=True, **fields
):
    """A series without points: the test adds them. Approved unless the test says not, so it is
    public under a published reference."""
    return Series.objects.create(
        material=material,
        reference=reference,
        property=Property.objects.get(code=property_code),
        variable=Variable.objects.get(code=variable_code),
        kind=kind,
        approved=approved,
        **fields,
    )


class ValuesTestCase(TestCase):
    """DIO with a Ps curve from Chen 2022 and a Δn curve from Yadav 2023, three points each,
    against temperature, all public. RM734 has no values until a test gives it some, and is
    listed, so its page is public without them."""

    @classmethod
    def setUpTestData(cls):
        cls.dio = Material.objects.create(name="DIO", smiles=DIO)
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734, listed=True)
        cls.chen = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1080/02678292.2022.2058101",
            first_author="Chen", year=2022, venue="Liq. Cryst.", published=True,
        )
        cls.yadav = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/d3cp00357d", first_author="Yadav", year=2023, venue="PCCP",
            published=True,
        )
        ps = add(
            cls.dio, cls.chen, "ps", method="current reversal", conditions={"frequency_Hz": 50, "cell_gap_um": 8},
            taken_from=Series.TakenFrom.FIGURE, location="Fig. 5(b)", digitised=True,
        )
        for temperature, value in [(60, 6.08), (75, 4.5), (89.86, 0.198)]:
            ps.points.create(value=value, temperature_c=temperature)
        dn = add(cls.dio, cls.yadav, "birefringence", conditions={"wavelength_nm": 550}, direction=Series.Direction.COOLING)
        for temperature, value in [(120, 0.21), (150, 0.15), (172.8, 0.0736)]:
            dn.points.create(value=value, temperature_c=temperature)

    def page(self, material):
        return self.client.get(reverse("repo:material", args=[material.pk]))


class MaterialPageTests(ValuesTestCase):
    def test_each_curve_is_one_row_with_its_ranges_and_reference(self):
        response = self.page(self.dio)

        self.assertContains(response, "3 points", count=2)
        self.assertContains(response, "0.198 to 6.08 µC/cm²")
        self.assertContains(response, "60 to 89.86 °C")
        # Δn is dimensionless, so no unit is written after it.
        self.assertContains(response, "0.0736 to 0.21")
        self.assertNotContains(response, "dimensionless")
        self.assertContains(response, "120 to 172.8 °C, cooling")
        self.assertContains(response, "Fig. 5(b), digitised")
        for reference in [self.chen, self.yadav]:
            link = reverse("repo:reference", args=[reference.pk])
            self.assertContains(response, f'<a href="{link}">{reference.label}</a>', html=True)

    def test_properties_are_in_vocabulary_order_with_a_group_under_its_name(self):
        response = self.page(self.dio)

        sections = [
            (group.name if group else None, [prop.symbol for prop, series, charts in members])
            for group, members in response.context["value_sections"]
        ]
        self.assertEqual(sections, [(None, ["Ps"]), ("Refractive index", ["Δn"])])
        self.assertContains(response, "<h3>Spontaneous polarisation, Ps</h3>", html=True)
        self.assertContains(response, "<h3>Refractive index</h3>", html=True)
        self.assertContains(response, "<h4>Birefringence, Δn</h4>", html=True)

    def test_a_single_value_shows_its_conditions(self):
        series = add(
            self.rm734, self.chen, "ps", method="current reversal", conditions={"frequency_Hz": 50, "cell_gap_um": 8}
        )
        series.points.create(value=4.4, uncertainty=0.2, temperature_c=100)

        response = self.page(self.rm734)

        self.assertContains(response, "4.4 ± 0.2 µC/cm²")
        self.assertContains(response, "<td>100 °C</td>", html=True)
        self.assertContains(response, "<td>cell gap: 8 µm, frequency: 50 Hz</td>", html=True)
        self.assertContains(response, "<td>Measured</td>", html=True)
        self.assertContains(response, "<td>current reversal</td>", html=True)
        self.assertNotContains(response, "1 point")

    def test_a_material_with_no_values_says_so(self):
        response = self.page(self.rm734)

        self.assertContains(response, "No property values yet.")
        self.assertNotContains(response, '<th scope="col">Value</th>', html=True)

    def test_a_limit_shows_its_sign(self):
        series = add(
            self.rm734, self.chen, "d33", method="Maker fringes",
            conditions={"wavelength_nm": 1064, "reference_crystal": "quartz"},
        )
        series.points.create(qualifier=Measurement.Qualifier.UPPER, value=0.1, temperature_c=25)

        self.assertContains(self.page(self.rm734), "&lt; 0.1 pm/V")

    def test_measured_and_extrapolated_values_are_two_rows_each_with_its_kind(self):
        for kind in [Series.Kind.MEASURED, Series.Kind.EXTRAPOLATED]:
            series = add(self.rm734, self.chen, "birefringence", kind=kind, conditions={"wavelength_nm": 589})
            series.points.create(value=0.2, temperature_c=25)

        response = self.page(self.rm734)

        self.assertContains(response, "<td>0.2</td>", count=2, html=True)
        self.assertContains(response, "<td>Measured</td>", html=True)
        self.assertContains(response, "<td>Extrapolated</td>", html=True)

    def test_a_value_against_frequency_gives_its_frequency_and_temperature(self):
        series = add(
            self.rm734, self.chen, "eps-parallel", "frequency", conditions={"cell_gap_um": 10, "electrode_coating": "ITO"}
        )
        series.points.create(value=9000, temperature_c=25, x=1000)

        self.assertContains(self.page(self.rm734), "<td>1000 Hz at 25 °C</td>", html=True)

    def test_a_value_against_nothing_is_measured_at_nothing(self):
        series = add(
            self.rm734, self.chen, "dipole-moment", "none", kind=Series.Kind.COMPUTED, method="DFT",
            conditions={"conformer_protocol": "lowest energy", "solvent": "gas phase"},
        )
        series.points.create(value=11.4)

        response = self.page(self.rm734)

        self.assertContains(response, "11.4 D")
        self.assertContains(response, "<td>–</td>", html=True)
        self.assertContains(response, "<td>Computed</td>", html=True)

    def test_more_series_cost_no_more_queries(self):
        with CaptureQueriesContext(connection) as before:
            self.page(self.dio)
        patent = Reference.objects.create(kind=Reference.Kind.PATENT, identifier="EP3260450A1", published=True)
        series = add(self.dio, patent, "n-e", conditions={"wavelength_nm": 589})
        series.points.create(value=1.7, temperature_c=25)

        with CaptureQueriesContext(connection) as after:
            self.page(self.dio)

        self.assertEqual(len(after), len(before))


class ReferencePageTests(ValuesTestCase):
    def page(self, reference):
        return self.client.get(reverse("repo:reference", args=[reference.pk]))

    def test_the_reference_page_lists_its_values_with_their_materials(self):
        response = self.page(self.chen)

        dio_page = reverse("repo:material", args=[self.dio.pk])
        self.assertContains(response, f'<a href="{dio_page}">{self.dio.accession}</a>', html=True)
        self.assertContains(response, '<th scope="col">Material</th>', html=True)
        self.assertContains(response, "0.198 to 6.08 µC/cm²")
        self.assertNotContains(response, "Birefringence")  # Yadav 2023's, not Chen 2022's

    def test_a_reference_with_no_values_says_so(self):
        patent = Reference.objects.create(kind=Reference.Kind.PATENT, identifier="EP3260450A1")

        self.assertContains(self.page(patent), "No property values from this reference.")


class FilterTests(SimpleTestCase):
    def test_num_writes_a_true_minus_sign(self):
        self.assertEqual(num(-1.23456), "−1.235")

    def test_num_writes_none_as_a_dash(self):
        self.assertEqual(num(None), "–")

    def test_num_writes_ten_thousand_whole(self):
        self.assertEqual(num(10000), "10000")
        self.assertEqual(num(123456.7), "123457")

    def test_with_unit(self):
        self.assertEqual(with_unit("0.21", "dimensionless"), "0.21")
        self.assertEqual(with_unit("45", "°"), "45°")
        self.assertEqual(with_unit("8", "µm"), "8 µm")

    def test_conditions_text_names_each_condition_with_its_unit_in_key_order(self):
        self.assertEqual(
            conditions_text({"frequency_Hz": 50, "field_V_um": 0.069, "alignment": "planar"}),
            "alignment: planar, field: 0.069 V/µm, frequency: 50 Hz",
        )
        self.assertEqual(conditions_text({}), "")
