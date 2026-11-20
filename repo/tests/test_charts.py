from django.urls import reverse

from repo.charts import DENSE_POINTS, LABELLED_LINES, chart_groups, figure, plotted_points, to_svg
from repo.models import Material, Measurement, Reference, Series, Variable
from repo.tests.test_property_pages import PS_CONDITIONS, PropertyPagesTestCase
from repo.tests.test_values import ValuesTestCase, add


def with_points(series_list):
    """Saved series as figure() takes them: each with its points from the database."""
    return [(one, one.points.all()) for one in series_list]


class ChartTests(ValuesTestCase):
    """Charts drawn from the DIO fixture in test_values, with series that tests add to RM734.
    figure() returns the matplotlib Figure, so a test looks at what was drawn on its one
    set of axes, not at the SVG text."""

    def draw(self, series):
        return figure(series.property, series.variable, with_points([series])).axes[0]

    def test_one_chart_per_kind_and_variable(self):
        measured = self.dio.series.get(property__code="ps")
        against_frequency = add(self.dio, self.chen, "ps", "frequency")
        computed = add(self.dio, self.chen, "ps", kind=Series.Kind.COMPUTED)
        against_nothing = add(self.dio, self.chen, "ps", "none", kind=Series.Kind.COMPUTED)

        groups = chart_groups([against_nothing, computed, against_frequency, measured])

        self.assertEqual(
            [(kind, variable.code, series) for kind, variable, series in groups],
            [
                ("measured", "temperature", [measured]),
                ("measured", "frequency", [against_frequency]),
                ("computed", "temperature", [computed]),
            ],
        )

    def test_a_limit_is_not_drawn(self):
        series = add(self.rm734, self.chen, "ps")
        series.points.create(value=5.0, temperature_c=60)
        series.points.create(value=0.1, temperature_c=80, qualifier=Measurement.Qualifier.UPPER)
        series.points.create(value=4.0, temperature_c=70)

        self.assertEqual([point.value for point in plotted_points(series.points.all())], [5.0, 4.0])
        self.assertEqual(list(self.draw(series).lines[0].get_ydata()), [5.0, 4.0])
        self.assertContains(self.page(self.rm734), "1 limit not drawn")

    def test_a_single_value_is_one_marker(self):
        series = add(self.rm734, self.chen, "ps")
        series.points.create(value=4.4, temperature_c=100)

        line = self.draw(series).lines[0]

        self.assertEqual(list(line.get_xdata()), [100])
        self.assertEqual(line.get_marker(), "o")

    def test_frequency_is_drawn_on_a_log_axis(self):
        series = add(self.rm734, self.chen, "eps-parallel", "frequency")
        series.points.create(value=9000, temperature_c=25, x=1000)

        self.assertEqual(Variable.objects.get(code="frequency").scale, Variable.Scale.LOG)
        self.assertEqual(self.draw(series).get_xscale(), "log")
        self.assertEqual(self.draw(self.dio.series.get(property__code="ps")).get_xscale(), "linear")

    def test_a_dense_series_is_a_line_without_markers(self):
        def series_of(n):
            series = add(self.rm734, self.chen, "ps")
            Measurement.objects.bulk_create(
                Measurement(series=series, value=1 + i / n, temperature_c=i) for i in range(n)
            )
            return series

        self.assertEqual(self.draw(series_of(DENSE_POINTS)).lines[0].get_marker(), "o")
        self.assertEqual(self.draw(series_of(DENSE_POINTS + 1)).lines[0].get_marker(), "None")

    def test_an_uncertainty_is_an_error_bar(self):
        with_error = add(self.rm734, self.chen, "ps")
        with_error.points.create(value=4.4, uncertainty=0.2, temperature_c=100)

        self.assertTrue(self.draw(with_error).containers[0].has_yerr)
        self.assertFalse(self.draw(self.dio.series.get(property__code="ps")).containers[0].has_yerr)

    def test_the_legend_names_the_reference(self):
        series = self.dio.series.get(property__code="ps")

        legend = figure(series.property, series.variable, with_points([series])).legends[0]

        self.assertEqual([text.get_text() for text in legend.get_texts()], ["Chen 2022, Liq. Cryst."])

    def test_a_dollar_sign_in_a_label_is_drawn_as_typed(self):
        # Unescaped, matplotlib would read "$\frac{$" as a formula and stop with an error.
        reference = Reference.objects.create(kind=Reference.Kind.THESIS, identifier=r"Smith, 2020, $\frac{$")
        series = add(self.rm734, reference, "ps")
        series.points.create(value=4.4, temperature_c=100)

        self.assertIn(r"$\frac{$", to_svg(figure(series.property, series.variable, with_points([series]))))


class MaterialPageChartTests(ValuesTestCase):
    def test_each_property_has_its_chart_with_a_description(self):
        response = self.page(self.dio)

        self.assertContains(response, '<figure class="chart">', count=2)
        self.assertContains(response, "<svg", count=2)
        self.assertContains(
            response, 'aria-label="Ps against temperature, measured values: 1 series, from Chen 2022, Liq. Cryst."'
        )
        self.assertContains(response, 'aria-label="Δn against temperature, measured values: 1 series, from Yadav 2023, PCCP"')
        self.assertContains(response, "Measured values against temperature.", count=2)

    def test_a_value_against_nothing_has_no_chart(self):
        series = add(self.rm734, self.chen, "dipole-moment", "none", kind=Series.Kind.COMPUTED, method="DFT")
        series.points.create(value=11.4)

        response = self.page(self.rm734)

        self.assertContains(response, "11.4 D")
        self.assertNotContains(response, 'class="chart"')

    def test_the_reference_page_has_no_charts(self):
        response = self.client.get(reverse("repo:reference", args=[self.chen.pk]))

        self.assertContains(response, "0.198 to 6.08 µC/cm²")
        self.assertNotContains(response, 'class="chart"')


class PropertyPageChartTests(PropertyPagesTestCase):
    """Charts on the property page, from the three Ps curves in test_property_pages: DIO's
    and RM734's from Chen 2022, and 5CB's from a reference with no details, so its label is
    its DOI."""

    def page(self, **params):
        """The Ps page with all three materials ticked, so every series is drawn. A list
        in params goes in the address once for each item: material=1&material=2."""
        params.setdefault("material", [self.dio.pk, self.rm734.pk, self.five_cb.pk])
        return self.client.get(reverse("repo:property", args=["ps"]), params)

    def ps_figure(self):
        series = list(Series.objects.filter(property__code="ps").order_by("material_id", "id"))
        return figure(series[0].property, series[0].variable, with_points(series), by_material=True)

    def test_one_chart_per_kind_and_variable(self):
        response = self.page()

        self.assertContains(response, '<figure class="chart">', count=1)
        self.assertContains(
            response,
            'aria-label="Ps against temperature, measured values: 3 series of 3 materials, '
            'from Chen 2022, Liq. Cryst.; 10.1039/d2sm01298g"',
        )

        add(self.dio, self.other, "ps", "frequency").points.create(value=5.0, temperature_c=70, x=1000)
        add(self.rm734, self.chen, "ps", kind=Series.Kind.COMPUTED).points.create(value=6.0, temperature_c=25)
        add(self.rm734, self.chen, "ps", "none", kind=Series.Kind.COMPUTED).points.create(value=7.0)

        response = self.page()

        # The value against nothing has no chart: it is only in the table.
        self.assertEqual(
            [(chart["kind"], chart["variable"].code) for chart in response.context["charts"]],
            [("measured", "temperature"), ("measured", "frequency"), ("computed", "temperature")],
        )
        self.assertContains(response, '<figure class="chart">', count=3)

    def test_the_legend_names_material_and_reference(self):
        legend = self.ps_figure().legends[0]

        self.assertEqual(
            [text.get_text() for text in legend.get_texts()],
            [
                f"{self.dio.accession} DIO – Chen 2022, Liq. Cryst.",
                f"{self.rm734.accession} RM734 – Chen 2022, Liq. Cryst.",
                f"{self.five_cb.accession} 5CB – 10.1039/d2sm01298g",
            ],
        )

    def test_more_lines_than_can_be_told_apart_are_not_labelled(self):
        def add_line():
            add(self.dio, self.other, "ps", **PS_CONDITIONS).points.create(value=5.0, temperature_c=70)

        for _ in range(LABELLED_LINES - 3):
            add_line()
        self.assertEqual(len(self.ps_figure().legends), 1)
        self.assertTrue(self.page().context["charts"][0]["labelled"])

        add_line()

        self.assertEqual(self.ps_figure().legends, [])
        self.assertContains(self.page(), f"{LABELLED_LINES + 1} series, too many to label: the table lists them.")

    def test_the_chart_keeps_material_order_whatever_the_sort(self):
        # The lowest Ps of all, so sorting by value puts this series first in the table.
        add(self.five_cb, self.chen, "ps", **PS_CONDITIONS).points.create(value=0.05, temperature_c=100)
        dio = f"{self.dio.accession} DIO – Chen 2022"
        five_cb = f"{self.five_cb.accession} 5CB – Chen 2022"

        for sort in ["material", "value"]:
            response = self.page(sort=sort)
            svg = response.context["charts"][0]["svg"]
            self.assertLess(svg.index(dio), svg.index(five_cb), sort)

        self.assertEqual(response.context["series_list"][0].material, self.five_cb)  # the table did re-sort


class ChartChoiceTests(PropertyPagesTestCase):
    """Choosing which of the three Ps materials the property page's chart draws."""

    def page(self, **params):
        return self.client.get(reverse("repo:property", args=["ps"]), params)

    def test_nothing_is_drawn_until_a_material_is_ticked(self):
        response = self.page()

        self.assertNotContains(response, '<figure class="chart">')
        self.assertContains(response, "Tick materials and select Draw to chart their values.")
        self.assertContains(response, 'type="checkbox" name="material"', count=3)
        self.assertNotContains(response, "checked")

    def test_a_ticked_material_is_drawn_alone(self):
        response = self.page(material=[self.rm734.pk])

        self.assertContains(response, '<figure class="chart">', count=1)
        self.assertContains(response, "1 series of 1 material, from Chen 2022")
        svg = response.context["charts"][0]["svg"]
        self.assertIn(f"{self.rm734.accession} RM734", svg)
        self.assertNotIn(f"{self.dio.accession} DIO", svg)
        self.assertContains(
            response, f'<input type="checkbox" name="material" value="{self.rm734.pk}" checked>', html=True
        )
        # The table still lists every value.
        self.assertEqual(len(response.context["series_list"]), 3)

    def test_a_material_without_this_property_is_ignored(self):
        no_ps = Material.objects.create(name="Ethanol", smiles="CCO")

        response = self.page(material=[no_ps.pk, "abc"])

        self.assertEqual(response.context["chosen"], [])
        self.assertNotContains(response, '<figure class="chart">')
        self.assertEqual(self.page(material=[no_ps.pk, self.dio.pk]).context["chosen"], [self.dio.pk])

    def test_the_sort_and_the_choice_keep_each_other(self):
        ticked = f"material={self.dio.pk}"

        by_material = self.page(material=[self.dio.pk])
        self.assertContains(by_material, f'<a href="?{ticked}&amp;sort=value">Value</a>', html=True)
        self.assertNotContains(by_material, 'name="sort"')

        by_value = self.page(material=[self.dio.pk], sort="value")
        link = reverse("repo:property", args=["ps"])
        self.assertContains(by_value, f'<a href="{link}?{ticked}">Material</a>', html=True)
        self.assertContains(by_value, '<input type="hidden" name="sort" value="value">', html=True)
