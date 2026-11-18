from django.core.exceptions import ValidationError
from django.test import TestCase

from repo.models import Property, PropertyGroup, Variable, group_properties

# The test database is built by running the migrations, so the vocabulary from
# 0011_seed_property_vocabulary is already in it.


class SeededVocabularyTests(TestCase):
    def test_counts(self):
        self.assertEqual(PropertyGroup.objects.count(), 4)
        self.assertEqual(Property.objects.count(), 15)
        self.assertEqual(Variable.objects.count(), 7)

    def test_refractive_index_group_and_log_frequency(self):
        members = Property.objects.filter(group__name="Refractive index")
        self.assertEqual([prop.symbol for prop in members], ["Δn", "nₑ", "nₒ"])
        self.assertEqual(Variable.objects.get(code="frequency").scale, Variable.Scale.LOG)


class ConditionsForTests(TestCase):
    def setUp(self):
        self.d33 = Property.objects.get(code="d33")

    def test_a_computed_d33_has_its_own_list(self):
        computed = self.d33.conditions_for("computed")
        self.assertIn("conformer_protocol", computed)
        self.assertNotIn("reference_crystal", computed)

    def test_a_measured_d33_has_the_default_list(self):
        self.assertEqual(
            self.d33.conditions_for("measured"), ["temperature", "method", "wavelength_nm", "reference_crystal"]
        )

    def test_the_list_is_a_copy(self):
        self.d33.conditions_for("measured").append("frequency_Hz")
        self.assertNotIn("frequency_Hz", self.d33.required_conditions)


class GroupPropertiesTests(TestCase):
    def entries(self):
        """group_properties() as (group code or the property's code, [member codes])."""
        return [
            (group.code if group else members[0].code, [prop.code for prop in members])
            for group, members in group_properties(Property.objects.select_related("group"))
        ]

    def test_seeded_order(self):
        self.assertEqual(
            self.entries(),
            [
                ("ps", ["ps"]),
                ("shg", ["d33", "d31", "d15"]),
                ("density", ["density"]),
                ("refractive-index", ["birefringence", "n-e", "n-o"]),
                ("permittivity", ["dielectric-anisotropy", "eps-parallel", "eps-perpendicular"]),
                ("dipole", ["dipole-moment", "dipole-parallel", "dipole-long-axis-angle"]),
                ("molecular-length", ["molecular-length"]),
            ],
        )

    def test_a_group_sits_where_its_first_member_would(self):
        # A new dipole property sorted before Ps takes the whole Dipole moment group to the
        # front, and the group's other members keep their order behind it.
        Property.objects.create(
            code="test-dipole", name="Test dipole", symbol="μt", unit="D",
            group=PropertyGroup.objects.get(code="dipole"), sort_order=1,
        )
        self.assertEqual(
            self.entries()[:2],
            [
                ("dipole", ["test-dipole", "dipole-moment", "dipole-parallel", "dipole-long-axis-angle"]),
                ("ps", ["ps"]),
            ],
        )


class ConditionShapeTests(TestCase):
    def setUp(self):
        self.prop = Property.objects.get(code="d33")

    def test_a_seeded_property_is_accepted(self):
        self.prop.full_clean()  # raises if it isn't

    def test_a_name_instead_of_a_list_is_refused(self):
        self.prop.required_conditions = "temperature"
        with self.assertRaisesMessage(ValidationError, "Give a list of names"):
            self.prop.full_clean()

    def test_a_kind_without_a_list_is_refused(self):
        self.prop.required_conditions_by_kind = {"computed": "method"}
        with self.assertRaisesMessage(ValidationError, "Give each kind a list of names"):
            self.prop.full_clean()

    def test_an_emptied_box_means_no_conditions(self):
        self.prop.required_conditions = None
        self.prop.full_clean()
        self.assertEqual(self.prop.required_conditions, [])
