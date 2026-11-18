from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, Measurement, Property, Reference, Series, Variable

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"


class SeriesTestCase(TestCase):
    """RM734 and one reference. The properties and variables are already in the test
    database, from the 0011_seed_property_vocabulary migration."""

    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.reference = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1080/02678292.2022.2058101", first_author="Chen", year=2022
        )

    def add(self, property_code="ps", variable_code="temperature"):
        return Series.objects.create(
            material=self.material,
            reference=self.reference,
            property=Property.objects.get(code=property_code),
            variable=Variable.objects.get(code=variable_code),
            kind=Series.Kind.MEASURED,
        )


class PointOrderTests(SeriesTestCase):
    def test_points_come_back_in_temperature_order(self):
        series = self.add()
        for temperature, value in [(120, 2.1), (80, 6.0), (100, 4.4)]:
            series.points.create(value=value, temperature_c=temperature)

        self.assertEqual([p.temperature_c for p in series.points.all()], [80, 100, 120])


class XValueTests(SeriesTestCase):
    def test_against_temperature_x_is_the_temperature(self):
        point = self.add("ps", "temperature").points.create(value=6.0, temperature_c=80)
        self.assertEqual(point.x_value(), 80)

    def test_against_frequency_x_is_the_frequency_not_the_temperature(self):
        point = self.add("eps-parallel", "frequency").points.create(value=9000, temperature_c=80, x=1000)
        self.assertEqual(point.x_value(), 1000)

    def test_against_nothing_there_is_no_x(self):
        point = self.add("dipole-moment", "none").points.create(value=11.4)
        self.assertIsNone(point.x_value())


class DeletionTests(SeriesTestCase):
    def test_a_reference_with_series_cannot_be_deleted(self):
        self.add()
        with self.assertRaises(ProtectedError):
            self.reference.delete()

    def test_deleting_a_material_deletes_its_series_and_their_points(self):
        self.add().points.create(value=6.0, temperature_c=80)
        self.material.delete()
        self.assertFalse(Series.objects.exists())
        self.assertFalse(Measurement.objects.exists())

    def test_a_property_or_variable_in_use_cannot_be_deleted(self):
        series = self.add()
        with self.assertRaises(ProtectedError):
            series.property.delete()
        with self.assertRaises(ProtectedError):
            series.variable.delete()


class FieldTests(SeriesTestCase):
    def test_kind_must_be_chosen(self):
        series = self.add()
        series.kind = ""
        with self.assertRaises(ValidationError) as caught:
            series.full_clean()
        self.assertIn("kind", caught.exception.message_dict)

    def test_an_emptied_conditions_box_means_no_conditions(self):
        # Density needs only a method and temperature, so no conditions is allowed.
        series = self.add("density")
        series.method = "pycnometer"
        series.conditions = None  # what the admin sends for an emptied box
        series.full_clean()
        self.assertEqual(series.conditions, {})


class AdminTests(SeriesTestCase):
    def test_the_series_page_lists_its_points(self):
        series = self.add()
        series.points.create(value=6.25, temperature_c=80)
        series.points.create(value=2.75, temperature_c=120)
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))

        response = self.client.get(reverse("admin:repo_series_change", args=[series.pk]))

        self.assertContains(response, "6.25")
        self.assertContains(response, "2.75")
