"""Fill the property vocabulary: the prototype's 4 property groups, 15 properties and 7
variables. One change from the prototype (decided 18 November 2026): the SHG condition
"reference" is called "reference_crystal", because in FFDB a reference is the paper a value
comes from. This runs once. After that the admin is where the vocabulary is edited, so
these lists are not kept up to date."""

from django.db import migrations

# code, name
GROUPS = [
    ("shg", "SHG coefficients"),
    ("refractive-index", "Refractive index"),
    ("permittivity", "Dielectric permittivity"),
    ("dipole", "Dipole moment"),
]

# Condition lists that several properties share.
SHG = ["temperature", "method", "wavelength_nm", "reference_crystal"]
SHG_BY_KIND = {
    "computed": [
        "method", "conformer_protocol", "solvent", "wavelength_nm", "local_field_model",
        "order_parameter_P1", "order_parameter_P3", "density_kg_m3",
    ]
}
REFRACTIVE_INDEX = ["temperature", "wavelength_nm"]
PERMITTIVITY = ["temperature", "frequency_Hz", "cell_gap_um", "electrode_coating"]
PERMITTIVITY_BY_KIND = {"extrapolated": ["temperature", "frequency_Hz"]}
CALCULATION_BY_KIND = {"computed": ["method", "conformer_protocol", "solvent"]}

# code, name, symbol, unit, group code, required conditions, by kind, sort order, description
PROPERTIES = [
    ("ps", "Spontaneous polarisation", "Ps", "µC/cm²", None,
     ["temperature", "method", "frequency_Hz", "cell_gap_um"], {}, 10,
     "Saturated polarisation from current reversal or P–E loops."),
    ("d33", "SHG coefficient d33", "d33", "pm/V", "shg", SHG, SHG_BY_KIND, 20,
     "Usually calibrated against a reference crystal, so the reference crystal and the value "
     "assumed for it must be stated. A computed d33 (from the molecular hyperpolarisability) "
     "names no reference crystal; it states the local-field model and the order parameters "
     "and density it assumes instead."),
    ("d31", "SHG coefficient d31", "d31", "pm/V", "shg", SHG, SHG_BY_KIND, 21,
     "For the NF's C∞v symmetry d31 = d32. It equals d15 only under Kleinman symmetry, so d31 "
     "and d15 are each stored as reported. Like d33, a measured value states its reference "
     "crystal and a computed one its local-field model, order parameters and density."),
    ("d15", "SHG coefficient d15", "d15", "pm/V", "shg", SHG, SHG_BY_KIND, 22,
     "For the NF's C∞v symmetry d15 = d24. It equals d31 only under Kleinman symmetry, so d15 "
     "and d31 are each stored as reported. Like d33, a measured value states its reference "
     "crystal and a computed one its local-field model, order parameters and density."),
    ("density", "Density", "ρ", "g/cm³", None, ["temperature", "method"], {}, 30,
     "Mass density. Computed values (e.g. from MD) are stored as computed, never mixed with "
     "measured ones."),
    ("birefringence", "Birefringence", "Δn", "dimensionless", "refractive-index",
     REFRACTIVE_INDEX, {}, 40, ""),
    ("n-e", "Refractive index, extraordinary", "nₑ", "dimensionless", "refractive-index",
     REFRACTIVE_INDEX, {}, 41, "Light polarised along the director."),
    ("n-o", "Refractive index, ordinary", "nₒ", "dimensionless", "refractive-index",
     REFRACTIVE_INDEX, {}, 42, "Light polarised perpendicular to the director."),
    ("dielectric-anisotropy", "Dielectric anisotropy", "Δε", "dimensionless", "permittivity",
     PERMITTIVITY, PERMITTIVITY_BY_KIND, 45,
     "A measured value states its cell thickness and electrode coating, as ε∥ and ε⊥ do: in an "
     "NF, interfacial layers distort the apparent Δε as much as ε∥. A value extrapolated from "
     "host mixtures states temperature and frequency."),
    ("eps-parallel", "Dielectric permittivity, parallel", "ε∥", "dimensionless", "permittivity",
     PERMITTIVITY, PERMITTIVITY_BY_KIND, 50,
     "A measured value states its cell thickness and electrode coating, because interfacial "
     "layers can dominate apparent NF permittivities. A value extrapolated from host mixtures "
     "states temperature and frequency."),
    ("eps-perpendicular", "Dielectric permittivity, perpendicular", "ε⊥", "dimensionless",
     "permittivity", PERMITTIVITY, PERMITTIVITY_BY_KIND, 51,
     "A measured value states its cell thickness and electrode coating, because interfacial "
     "layers can dominate apparent NF permittivities. A value extrapolated from host mixtures "
     "states temperature and frequency."),
    ("dipole-moment", "Dipole moment", "μ", "D", "dipole",
     ["temperature", "method", "solvent"], CALCULATION_BY_KIND, 60,
     "The size of the molecular dipole, |μ|. A computed value is for the geometry or conformers "
     "calculated, in the gas phase unless a solvent model is stated."),
    ("dipole-parallel", "Dipole moment along the long axis", "μ∥", "D", "dipole",
     ["method"], CALCULATION_BY_KIND, 62,
     "|μ| cos θ: the size of the dipole's component along the molecular long axis. With a "
     "number density it gives the fully polar limit of Ps."),
    ("dipole-long-axis-angle", "Dipole angle to the long axis", "θμ", "°", "dipole",
     ["method"], CALCULATION_BY_KIND, 64,
     "The angle θ between the dipole and the molecular long axis, between 0° and 90° because "
     "the axis has no direction."),
    ("molecular-length", "Molecular length", "L", "nm", None,
     ["method"], CALCULATION_BY_KIND, 70,
     "The extent of the molecule along its long axis. How it is measured (e.g. whether van der "
     "Waals radii are added) is given with each set of values."),
]

# code, name, unit, scale, condition, sort order
VARIABLES = [
    ("temperature", "Temperature", "°C", "linear", "temperature", 10),
    ("frequency", "Frequency", "Hz", "log", "frequency_Hz", 20),
    ("time", "Time", "s", "linear", "", 30),
    ("field", "Electric field", "V/µm", "linear", "field_V_um", 40),
    ("wavelength", "Wavelength", "nm", "linear", "wavelength_nm", 50),
    ("q", "Scattering vector q", "Å⁻¹", "linear", "", 60),
    ("none", "Nothing", "", "linear", "", 90),
]


def add_vocabulary(apps, schema_editor):
    # The models as they were at this migration, not repo.models, which may change later.
    PropertyGroup = apps.get_model("repo", "PropertyGroup")
    Property = apps.get_model("repo", "Property")
    Variable = apps.get_model("repo", "Variable")

    # The groups first, saved one by one: each property points to one, so it needs the
    # saved group, with its primary key.
    group_by_code = {code: PropertyGroup.objects.create(code=code, name=name) for code, name in GROUPS}
    Property.objects.bulk_create(
        Property(
            code=code, name=name, symbol=symbol, unit=unit,
            group=group_by_code.get(group_code),  # None for a property in no group
            required_conditions=required, required_conditions_by_kind=by_kind,
            sort_order=sort_order, description=description,
        )
        for code, name, symbol, unit, group_code, required, by_kind, sort_order, description in PROPERTIES
    )
    Variable.objects.bulk_create(
        Variable(code=code, name=name, unit=unit, scale=scale, condition=condition, sort_order=sort_order)
        for code, name, unit, scale, condition, sort_order in VARIABLES
    )


def remove_vocabulary(apps, schema_editor):
    """Undo add_vocabulary, so `migrate repo 0010` can step back past this migration. The
    properties go first: a group can't be deleted while properties are in it."""
    apps.get_model("repo", "Property").objects.filter(code__in=[row[0] for row in PROPERTIES]).delete()
    apps.get_model("repo", "PropertyGroup").objects.filter(code__in=[row[0] for row in GROUPS]).delete()
    apps.get_model("repo", "Variable").objects.filter(code__in=[row[0] for row in VARIABLES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0010_property_vocabulary"),
    ]

    operations = [
        migrations.RunPython(add_vocabulary, remove_vocabulary),
    ]
