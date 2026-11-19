"""Let the Curators group add, change and delete mixture components. Migration 0013 made the
group before mixtures existed. The admin leaves out a section of rows whose model the user
has no permission on, so a curator saw no components on a mixture's page, and the
composition rules, which are checked in that section, didn't run."""

from django.db import migrations

ACTIONS = ["add", "change", "delete"]


def add_permissions(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    MixtureComponent = apps.get_model("repo", "MixtureComponent")
    curators, _ = Group.objects.get_or_create(name="Curators")
    # As in 0013: on a new database Django makes these permissions only after migrate has
    # finished, so make them here, with the codename and name Django would give them.
    content_type, _ = ContentType.objects.get_or_create(app_label="repo", model="mixturecomponent")
    for action in ACTIONS:
        permission, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=f"{action}_mixturecomponent",
            defaults={"name": f"Can {action} {MixtureComponent._meta.verbose_name_raw}"},
        )
        curators.permissions.add(permission)


def remove_permissions(apps, schema_editor):
    """Undo add_permissions. The permissions themselves stay: Django would make them anyway."""
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    permissions = Permission.objects.filter(
        content_type__app_label="repo", codename__in=[f"{action}_mixturecomponent" for action in ACTIONS]
    )
    for curators in Group.objects.filter(name="Curators"):
        curators.permissions.remove(*permissions)


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0019_mixtures"),
    ]

    operations = [
        migrations.RunPython(add_permissions, remove_permissions),
    ]
