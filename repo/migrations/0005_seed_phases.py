"""Fill the Phase table with the starting vocabulary: the prototype's 27 phases, with
NTBF and HCNF merged into one (decided 17 November 2026). This runs once. After that the
admin is where phases are added or edited, so this list is not kept up to date."""

from django.db import migrations

# code, name, polar, synonyms, sort order
PHASES = [
    ("Iso", "Isotropic liquid", False, "I, IL", 10),
    ("N", "Nematic", False, "", 20),
    ("N'", "N' (as reported)", False, "", 22),
    ("NTB", "Twist-bend nematic", False, "N_TB, Ntb", 25),
    ("NTBF", "Ferroelectric twist-bend nematic", True, "N_TBF, HCNF", 27),
    ("NS", "Splay nematic", True, "N_S, SmZA, SmZ_A, N_X, NX, M2", 30),
    ("NF", "Ferroelectric nematic", True, "N_F", 40),
    ("SmAF", "Polar smectic A", True, "SmA_F", 50),
    ("SmAAF", "Antiferroelectric smectic A", True, "SmA_AF", 52),
    ("SmApx", "SmApx (as reported)", True, "", 55),
    ("SmA", "Smectic A", False, "", 60),
    ("TGBA", "Twist grain boundary A", False, "", 62),
    ("SmC", "Smectic C", False, "", 70),
    ("TGBC", "Twist grain boundary C", False, "", 72),
    ("SmCP", "Polar smectic C", True, "SmC_P", 73),
    ("SmCF", "Ferroelectric smectic C", True, "SmC_F", 74),
    ("SmCPH", "Heliconical polar smectic C", True, "SmC_PH", 75),
    ("SmChp", "SmChp (as reported)", True, "", 76),
    ("SmCH", "SmCH (as reported)", False, "", 77),
    ("SmCX", "Unidentified smectic C variant", False, "", 78),
    ("SmB", "Smectic B", False, "", 80),
    ("SmE", "Smectic E", False, "", 82),
    ("SmX", "Unidentified smectic", False, "", 84),
    ("SmXF", "Unidentified polar smectic", True, "", 85),
    ("Cr", "Crystal", False, "K, X, Cry", 90),
    ("g", "Glass", False, "glass, Tg", 95),
]


def add_phases(apps, schema_editor):
    # The model as it was at this migration, not repo.models.Phase, which may change later.
    Phase = apps.get_model("repo", "Phase")
    Phase.objects.bulk_create(
        Phase(code=code, name=name, polar=polar, synonyms=synonyms, sort_order=sort_order)
        for code, name, polar, synonyms, sort_order in PHASES
    )


def remove_phases(apps, schema_editor):
    """Undo add_phases, so `migrate repo 0004` can step back past this migration."""
    Phase = apps.get_model("repo", "Phase")
    Phase.objects.filter(code__in=[row[0] for row in PHASES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0004_phase"),
    ]

    operations = [
        migrations.RunPython(add_phases, remove_phases),
    ]
