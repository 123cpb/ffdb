"""Create the Curators group. A curator can add, change and delete materials, references,
phase sequences, series and their points, in the site's forms and in the admin. Admins put
a user in the group from the user's page in the admin; a curator also needs Staff status
there to get into the admin."""

from django.db import migrations

# The models curators work on, by their names in lower case, as Django's permissions name them.
MODELS = ["material", "reference", "phasesequence", "series", "measurement"]
ACTIONS = ["add", "change", "delete"]


def add_curators(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    curators, _ = Group.objects.get_or_create(name="Curators")
    for model_name in MODELS:
        # Django makes each model's permissions after migrate has finished, so on a new
        # database (the tests' included) they don't exist yet. Make the ones needed here, with
        # the codename and name Django would give them; Django then finds them and skips them.
        model = apps.get_model("repo", model_name)
        content_type, _ = ContentType.objects.get_or_create(app_label="repo", model=model_name)
        for action in ACTIONS:
            permission, _ = Permission.objects.get_or_create(
                content_type=content_type,
                codename=f"{action}_{model_name}",
                defaults={"name": f"Can {action} {model._meta.verbose_name_raw}"},
            )
            curators.permissions.add(permission)


def remove_curators(apps, schema_editor):
    """Undo add_curators. The permissions stay: Django would make them anyway."""
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Curators").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0012_series_measurement"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(add_curators, remove_curators),
    ]
