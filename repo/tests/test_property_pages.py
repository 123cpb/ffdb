from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from repo.models import Material, Reference
from repo.tests.test_import import FIVE_CB
from repo.tests.test_values import DIO, RM734, add

PS_CONDITIONS = {"method": "current reversal", "conditions": {"frequency_Hz": 50, "cell_gap_um": 8}}


class PropertyPagesTestCase(TestCase):
    """Three Ps curves of three points each: DIO's and RM734's from Chen 2022, and a third
    material's from a second reference. 5CB has no NF phase: it stands in for a third
    structure. No other property has values until a test gives it some."""

    @classmethod
    def setUpTestData(cls):
        cls.dio = Material.objects.create(name="DIO", smiles=DIO)
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        cls.chen = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1080/02678292.2022.2058101",
            first_author="Chen", year=2022, venue="Liq. Cryst.", published=True,
        )
        cls.other = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1039/d2sm01298g", published=True
        )
        cls.curves = {}
        for material, reference, points in [
            (cls.dio, cls.chen, [(60, 6.08), (75, 4.5), (89.86, 0.198)]),
            (cls.rm734, cls.chen, [(135, 5.9), (150, 4.2), (165, 1.1)]),
            (cls.five_cb, cls.other, [(100, 3.5), (110, 3.0), (120, 2.0)]),
        ]:
            series = add(material, reference, "ps", **PS_CONDITIONS)
            for temperature, value in points:
                series.points.create(value=value, temperature_c=temperature)
            cls.curves[material.name] = series


class PropertiesListTests(PropertyPagesTestCase):
    def setUp(self):
        self.response = self.client.get(reverse("repo:properties"))

    def test_ps_is_on_its_own_and_the_refractive_index_is_under_one_heading(self):
        entries = [
            (group.name if group else None, [prop.symbol for prop in members])
            for group, members in self.response.context["entries"]
        ]

        self.assertEqual(entries[0], (None, ["Ps"]))
        self.assertIn(("Refractive index", ["Δn", "nₑ", "nₒ"]), entries)
        self.assertContains(
            self.response, '<th colspan="5" scope="rowgroup">Refractive index</th>', count=1, html=True
        )

    def test_counts_materials_and_references_each_once(self):
        props = {prop.code: prop for group, members in self.response.context["entries"] for prop in members}

        # Chen 2022 gives two of the three curves, so Ps has three materials but two references.
        self.assertEqual((props["ps"].n_materials, props["ps"].n_references), (3, 2))
        self.assertEqual((props["d33"].n_materials, props["d33"].n_references), (0, 0))

    def test_the_navigation_links_to_it(self):
        link = reverse("repo:properties")
        self.assertContains(self.response, f'<a href="{link}" aria-current="page">Properties</a>', html=True)


class PropertyPageTests(PropertyPagesTestCase):
    def page(self, code, **params):
        return self.client.get(reverse("repo:property", args=[code]), params)

    def test_ps_lists_its_three_curves_by_material(self):
        response = self.page("ps")

        self.assertEqual(
            list(response.context["series_list"]),
            [self.curves["DIO"], self.curves["RM734"], self.curves["5CB"]],
        )
        self.assertContains(response, "3 points", count=3)
        self.assertContains(response, "0.198 to 6.08 µC/cm²")
        self.assertContains(response, "3 materials from 2 references.")
        for material in [self.dio, self.rm734, self.five_cb]:
            link = reverse("repo:material", args=[material.pk])
            self.assertContains(response, f'<a href="{link}">{material.accession}</a>', html=True)
        link = reverse("repo:reference", args=[self.chen.pk])
        self.assertContains(response, f'<a href="{link}">{self.chen.label}</a>', count=2, html=True)

    def test_it_gives_the_description_and_what_values_must_state(self):
        response = self.page("ps")

        self.assertContains(response, "Saturated polarisation from current reversal or P–E loops.")
        self.assertContains(response, "<dd>temperature, method, frequency, cell gap</dd>", html=True)
        self.assertNotContains(response, "must state instead")

    def test_a_kind_with_its_own_conditions_is_listed_after_the_others(self):
        response = self.page("d33")

        self.assertContains(response, "<dd>temperature, method, wavelength, reference crystal</dd>", html=True)
        self.assertContains(response, "<dt>Computed values must state instead</dt>", html=True)
        self.assertContains(response, "method, conformer protocol, solvent or gas phase, wavelength")

    def test_a_property_with_no_values_says_so(self):
        self.assertContains(self.page("d33"), "No d33 values yet.")

    def test_an_unknown_code_gives_404(self):
        self.assertEqual(self.page("nope").status_code, 404)

    def test_more_series_cost_no_more_queries(self):
        with CaptureQueriesContext(connection) as before:
            self.page("ps")
        series = add(self.dio, self.other, "ps", **PS_CONDITIONS)
        series.points.create(value=5.0, temperature_c=70)

        with CaptureQueriesContext(connection) as after:
            self.page("ps")

        self.assertEqual(len(after), len(before))


class SortTests(PropertyPagesTestCase):
    def page(self, code, **params):
        return self.client.get(reverse("repo:property", args=[code]), params)

    def test_by_value_single_values_go_from_smallest_to_largest(self):
        for material, value in [(self.dio, 1.42), (self.rm734, 1.35), (self.five_cb, 1.01)]:
            add(material, self.other, "density", method="pycnometer").points.create(value=value, temperature_c=25)

        def values(response):
            return [series.points.all()[0].value for series in response.context["series_list"]]

        self.assertEqual(values(self.page("density")), [1.42, 1.35, 1.01])  # by material
        response = self.page("density", sort="value")
        self.assertEqual(values(response), [1.01, 1.35, 1.42])
        self.assertContains(response, '<th scope="col" aria-sort="ascending">Value</th>', html=True)

    def test_a_curve_sits_at_its_lowest_value_and_a_series_without_points_goes_last(self):
        single = add(self.dio, self.other, "ps", **PS_CONDITIONS)
        single.points.create(value=1.5, temperature_c=70)
        empty = add(self.rm734, self.other, "ps", **PS_CONDITIONS)

        response = self.page("ps", sort="value")

        # The curves' lowest values: DIO's 0.198, RM734's 1.1 and 5CB's 2.0.
        self.assertEqual(
            list(response.context["series_list"]),
            [self.curves["DIO"], self.curves["RM734"], single, self.curves["5CB"], empty],
        )

    def test_an_unknown_sort_gives_material_order(self):
        response = self.page("ps", sort="colour")

        self.assertEqual(response.context["sort"], "material")
        self.assertContains(response, '<th scope="col" aria-sort="ascending">Material</th>', html=True)
        self.assertEqual(response.context["series_list"][0], self.curves["DIO"])
