"""What is public, as the query sets near the top of models.py say it: a sequence or series
when it is approved and its reference published, a point with its series, a material when it
has a public sequence or series or is listed, and a reference when it is published and has a
public sequence or series. The admin's materials list shows the rule's answer."""

import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, Measurement, PhaseSequence, Property, Reference, Series, Variable
from repo.tests.test_import import ARTICLE, PATENT
from repo.tests.test_sequences import RM734, RM734_SUMMARY
from repo.tests.test_switches import UNPUBLISHED_WORK


class PublicTestCase(TestCase):
    """RM734 with no data yet, a published article and a piece of unpublished work."""

    @classmethod
    def setUpTestData(cls):
        cls.material = Material.objects.create(name="RM734", smiles=RM734)
        cls.paper = Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier=ARTICLE, published=True)
        cls.unpublished = Reference.objects.create(kind=Reference.Kind.UNPUBLISHED, identifier=UNPUBLISHED_WORK)

    def sequence(self, reference, approved=True):
        return PhaseSequence.objects.create(
            material=self.material, reference=reference, sequence=RM734_SUMMARY, approved=approved
        )

    def series(self, reference, approved=True):
        """A series of one point."""
        series = Series.objects.create(
            material=self.material, reference=reference, property=Property.objects.get(code="birefringence"),
            variable=Variable.objects.get(code="temperature"), kind="measured", approved=approved,
        )
        Measurement.objects.create(series=series, value=0.2, temperature_c=100)
        return series

    def assertPublic(self, row, expected=True):
        """Whether the rule for row's model finds row."""
        self.assertEqual(type(row).objects.public().filter(pk=row.pk).exists(), expected)

    def assertHidden(self, row):
        self.assertPublic(row, expected=False)

    def make_listed(self):
        self.material.listed = True
        self.material.save()


class SequenceAndSeriesTests(PublicTestCase):
    def test_approved_under_a_published_reference_is_public(self):
        for make in (self.sequence, self.series):
            with self.subTest(make.__name__):
                self.assertPublic(make(self.paper))

    def test_waiting_is_hidden(self):
        for make in (self.sequence, self.series):
            with self.subTest(make.__name__):
                self.assertHidden(make(self.paper, approved=False))

    def test_approved_under_an_unpublished_reference_is_hidden(self):
        for make in (self.sequence, self.series):
            with self.subTest(make.__name__):
                self.assertHidden(make(self.unpublished))


class PointTests(PublicTestCase):
    def test_a_point_is_public_with_its_series(self):
        for reference, approved, public in [(self.paper, True, True), (self.paper, False, False), (self.unpublished, True, False)]:
            with self.subTest(reference=reference.kind, approved=approved):
                self.assertPublic(self.series(reference, approved).points.get(), public)


class MaterialTests(PublicTestCase):
    def test_a_public_sequence_makes_it_public(self):
        self.sequence(self.paper)
        self.assertPublic(self.material)

    def test_a_public_series_makes_it_public(self):
        self.series(self.paper)
        self.assertPublic(self.material)

    def test_it_is_hidden_when_its_only_data_is_waiting(self):
        self.sequence(self.paper, approved=False)
        self.series(self.paper, approved=False)
        self.assertHidden(self.material)

    def test_it_is_hidden_when_its_only_data_is_under_an_unpublished_reference(self):
        self.sequence(self.unpublished)
        self.series(self.unpublished)
        self.assertHidden(self.material)

    def test_with_no_data_it_is_public_only_when_listed(self):
        self.assertHidden(self.material)
        self.make_listed()
        self.assertPublic(self.material)

    def test_a_waiting_row_on_a_listed_material_stays_hidden(self):
        self.make_listed()
        waiting = self.sequence(self.paper, approved=False)

        self.assertPublic(self.material)
        self.assertHidden(waiting)

    def test_a_material_with_several_public_rows_is_found_once(self):
        self.sequence(self.paper)
        self.sequence(self.paper)
        self.series(self.paper)
        self.make_listed()

        self.assertEqual(list(Material.objects.public()), [self.material])


class ReferenceTests(PublicTestCase):
    def test_a_published_reference_with_a_public_sequence_or_series_is_public(self):
        patent = Reference.objects.create(kind=Reference.Kind.PATENT, identifier=PATENT, published=True)
        self.sequence(self.paper)
        self.series(patent)

        self.assertPublic(self.paper)
        self.assertPublic(patent)

    def test_a_published_reference_with_no_data_is_hidden(self):
        self.assertHidden(self.paper)

    def test_a_reference_whose_only_data_is_waiting_is_hidden(self):
        self.sequence(self.paper, approved=False)
        self.series(self.paper, approved=False)
        self.assertHidden(self.paper)

    def test_an_unpublished_reference_with_approved_data_is_hidden(self):
        self.sequence(self.unpublished)
        self.series(self.unpublished)
        self.assertHidden(self.unpublished)


class AdminTests(PublicTestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", password="a-test-password"))

    def public_column(self):
        """What the materials list's Public column says for RM734, the only material: Django
        draws a yes or no icon, whose alt text is True or False."""
        html = self.client.get(reverse("admin:repo_material_changelist")).content.decode()
        return re.search(r'class="field-is_public"><img [^>]*alt="(True|False)"', html).group(1) == "True"

    def test_the_materials_list_shows_the_rules_answer(self):
        only = self.sequence(self.paper)
        self.assertTrue(self.public_column())

        only.approved = False
        only.save()
        self.assertFalse(self.public_column())

        self.make_listed()
        self.assertTrue(self.public_column())
