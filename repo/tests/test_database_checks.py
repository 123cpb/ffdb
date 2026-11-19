"""The database's own checks: the CheckConstraints in the models' Meta. PostgreSQL holds them
however a row is written, so each test writes a bad row with update(), which skips save()
and full_clean(), as a merge does. Each test is one rule."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from repo.models import Material, Measurement, MixtureComponent, Series
from repo.tests.test_mixtures import MIXTURE, MOL, MixtureTestCase
from repo.tests.test_series import SeriesTestCase

OCTYL_CB = "CCCCCCCCc1ccc(-c2ccc(C#N)cc2)cc1"  # 8CB, a structure not in the test database
MADE_UP_INCHIKEY = "AAAAAAAAAAAAAA-BBBBBBBBBB-N"


class DatabaseChecks:
    """For a test class that also inherits from a TestCase."""

    def assertDatabaseRefuses(self, rows, constraint, **changes):
        """The database refuses rows.update(**changes), naming constraint. assertRaises is
        outside atomic(), so the error passes through atomic() and it undoes the update."""
        with self.assertRaises(IntegrityError) as caught, transaction.atomic():
            rows.update(**changes)
        self.assertIn(constraint, str(caught.exception))


class MaterialCheckTests(DatabaseChecks, MixtureTestCase):
    def test_a_compound_without_an_inchikey_is_refused(self):
        rm734 = Material.objects.filter(pk=self.rm734.pk)
        self.assertDatabaseRefuses(rm734, "compound_has_structure", inchikey="")

    def test_a_compound_without_a_smiles_is_refused(self):
        rm734 = Material.objects.filter(pk=self.rm734.pk)
        self.assertDatabaseRefuses(rm734, "compound_has_structure", smiles="")

    def test_a_mixture_with_an_inchikey_is_refused(self):
        f7 = Material.objects.filter(pk=self.f7.pk)
        self.assertDatabaseRefuses(f7, "mixture_has_no_structure", inchikey=MADE_UP_INCHIKEY)

    def test_a_material_that_is_neither_kind_is_refused(self):
        # RM734 has a structure, so it passes compound_has_structure and fails the other.
        rm734 = Material.objects.filter(pk=self.rm734.pk)
        self.assertDatabaseRefuses(rm734, "mixture_has_no_structure", kind="polymer")

    def test_full_clean_fills_in_a_new_compounds_inchikey_and_passes_it(self):
        """The InChIKey is otherwise worked out only by save(), and full_clean() checks the
        constraints before that: a blank one would fail compound_has_structure."""
        compound = Material(name="8CB", smiles=OCTYL_CB)
        compound.full_clean()  # raises if it doesn't pass

        self.assertEqual(len(compound.inchikey), 27)


class FractionCheckTests(DatabaseChecks, MixtureTestCase):
    def setUp(self):
        self.rm734_in_f7 = MixtureComponent.objects.filter(mixture=self.f7, component=self.rm734)

    def test_a_fraction_over_100_is_refused(self):
        self.assertDatabaseRefuses(self.rm734_in_f7, "fraction_more_than_0_at_most_100", fraction=100.1)

    def test_a_fraction_of_0_is_refused(self):
        self.assertDatabaseRefuses(self.rm734_in_f7, "fraction_more_than_0_at_most_100", fraction=0)

    def test_a_fraction_of_100_is_allowed(self):
        self.rm734_in_f7.update(fraction=100)  # raises if it isn't

        # F7's fractions now add up to 130. That rule is across rows, which a CheckConstraint
        # can't see, so the database allows it: Material.composition_problems() refuses it.
        self.assertEqual(sum(c.fraction for c in self.f7.components.all()), 130)

    def test_full_clean_gives_the_message_once_beside_the_fraction(self):
        f8 = Material.objects.create(kind=MIXTURE, name="F8")
        component = MixtureComponent(mixture=f8, component=self.cb5, fraction=150, unit=MOL)

        with self.assertRaises(ValidationError) as caught:
            component.full_clean()
        # From clean(), on the field. The constraint is skipped because the field already
        # has an error, so there is no second copy above the form.
        self.assertEqual(caught.exception.message_dict, {"fraction": ["A fraction is more than 0 and at most 100."]})


class ValueCheckTests(DatabaseChecks, SeriesTestCase):
    def test_a_limit_with_an_uncertainty_is_refused(self):
        point = self.add().points.create(value=2.1, uncertainty=0.1, temperature_c=100)

        points = Measurement.objects.filter(pk=point.pk)
        self.assertDatabaseRefuses(points, "limit_has_no_uncertainty", qualifier=Measurement.Qualifier.UPPER)

    def test_digitised_values_not_read_off_a_figure_are_refused(self):
        series = Series.objects.filter(pk=self.add().pk)  # taken from: not recorded
        self.assertDatabaseRefuses(series, "digitised_only_from_figure", digitised=True)
