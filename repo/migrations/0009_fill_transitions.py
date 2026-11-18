"""Store the transitions of the sequences saved before the PhaseTransition table existed.
Sequences saved after this get theirs from PhaseSequence.save()."""

from django.db import migrations

from repo.phases import normalise_label, parse_summary


def fill_transitions(apps, schema_editor):
    # The models as they were at this migration. They have none of the models' own methods,
    # so PhaseSequence.save() can't be used: the sequences are read here instead. Each
    # standard_sequence is already under the codes ("Cr 139.8 NF 132.7 N 187.9 Iso"), so each
    # code is its own synonym, and the vocabulary's other labels don't matter.
    Phase = apps.get_model("repo", "Phase")
    PhaseSequence = apps.get_model("repo", "PhaseSequence")
    PhaseTransition = apps.get_model("repo", "PhaseTransition")
    phases = {phase.code: phase for phase in Phase.objects.all()}
    synonyms = {normalise_label(code): code for code in phases}
    PhaseTransition.objects.bulk_create(
        PhaseTransition(sequence=sequence, upper=phases[t.upper], lower=phases[t.lower], temperature_c=t.temperature_c)
        for sequence in PhaseSequence.objects.all()
        for t in parse_summary(sequence.standard_sequence, synonyms).transitions
    )


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0008_phasetransition"),
    ]

    operations = [
        # Undoing it needs nothing: undoing 0008 drops the whole table.
        migrations.RunPython(fill_transitions, migrations.RunPython.noop),
    ]
