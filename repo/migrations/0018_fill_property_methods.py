"""Fill each property's usual methods, from the prototype's data/vocabulary.json, which the
0011 migration left out. Add data offers them as suggestions. This runs once. After that the
admin is where the lists are edited, so these lists are not kept up to date."""

from django.db import migrations

# property code -> its usual methods, as in the prototype.
METHODS = {
    "ps": ["current reversal", "P–E hysteresis loop"],
    "d33": ["SHG", "Maker fringes"],
    "d31": ["SHG", "Maker fringes"],
    "d15": ["SHG", "Maker fringes"],
    "density": ["dilatometry", "pycnometry", "vibrating-tube densimeter", "MD"],
    "birefringence": ["Abbe refractometer", "Berek compensator", "wedge cell", "transmission spectrum"],
    "n-e": ["Abbe refractometer", "wedge cell", "prism"],
    "n-o": ["Abbe refractometer", "wedge cell", "prism"],
    "dielectric-anisotropy": ["dielectric spectroscopy", "capacitance (LCR meter)"],
    "eps-parallel": ["dielectric spectroscopy"],
    "eps-perpendicular": ["dielectric spectroscopy"],
    "dipole-moment": ["DFT"],
    "dipole-parallel": ["DFT"],
    "dipole-long-axis-angle": ["DFT"],
    "molecular-length": ["DFT"],
}


def fill_methods(apps, schema_editor):
    # The model as it was at this migration, not repo.models, which may change later. A
    # property whose code has been changed in the admin is not found, and is left as it is.
    Property = apps.get_model("repo", "Property")
    for prop in Property.objects.filter(code__in=METHODS):
        prop.methods = METHODS[prop.code]
        prop.save(update_fields=["methods"])


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0017_property_methods"),
    ]

    # Going back needs nothing here: undoing 0017 removes the methods column.
    operations = [
        migrations.RunPython(fill_methods, migrations.RunPython.noop),
    ]
