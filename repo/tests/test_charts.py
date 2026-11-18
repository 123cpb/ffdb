from django.urls import reverse

from repo.charts import DENSE_POINTS, chart_groups, figure, plotted_points, to_svg
from repo.models import Measurement, Reference, Series, Variable
from repo.tests.test_values import ValuesTestCase, add


class ChartTests(ValuesTestCase):
    """Charts drawn from the DIO fixture in test_values, with series that tests add to RM734.
    figure() returns the matplotlib Figure, so a test looks at what was drawn on its one
    set of axes, not at the SVG text."""

    def draw(self, series):
        return figure(series.property, series.variable, [series]).axes[0]

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

        self.assertEqual([point.value for point in plotted_points(series)], [5.0, 4.0])
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

        legend = figure(series.property, series.variable, [series]).legends[0]

        self.assertEqual([text.get_text() for text in legend.get_texts()], ["Chen 2022, Liq. Cryst."])

    def test_a_dollar_sign_in_a_label_is_drawn_as_typed(self):
        # Unescaped, matplotlib would read "$\frac{$" as a formula and stop with an error.
        reference = Reference.objects.create(kind=Reference.Kind.THESIS, identifier=r"Smith, 2020, $\frac{$")
        series = add(self.rm734, reference, "ps")
        series.points.create(value=4.4, temperature_c=100)

        self.assertIn(r"$\frac{$", to_svg(figure(series.property, series.variable, [series])))


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
