"""Turn the switches on for what was in FFDB before they existed (0027), so nothing that was
public is hidden: every sequence and series approved, every reference public except an
unpublished group result, and every material with no sequence and no series listed, as
LCPROP's structures without data are. Rows added after this get their switches from the
route that adds them."""

from django.db import migrations


def switch_on(apps, schema_editor):
    # The models as they were at this migration (see 0009). update() changes every row in
    # one query each, without running save().
    Material = apps.get_model("repo", "Material")
    PhaseSequence = apps.get_model("repo", "PhaseSequence")
    Reference = apps.get_model("repo", "Reference")
    Series = apps.get_model("repo", "Series")
    PhaseSequence.objects.update(approved=True)
    Series.objects.update(approved=True)
    Reference.objects.exclude(kind="unpublished").update(public=True)
    # One filter() with both: a material that has neither a sequence nor a series.
    Material.objects.filter(phase_sequences__isnull=True, series__isnull=True).update(listed=True)


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0027_switches"),
    ]

    operations = [
        # Undoing it needs nothing: undoing 0027 drops the switches.
        migrations.RunPython(switch_on, migrations.RunPython.noop),
    ]
