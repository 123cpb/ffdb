"""Take delete material away from the Curators group: only admins delete materials.
Migration 0013 gave curators it with the rest. Deleting a material also deletes every
sequence and series it has, and nothing records what went, so a curator's slip lost data
for good. Curators can still delete single sequences, values, mixture components, and
references with no data. Superusers have every permission without being given it."""

from django.db import migrations


def curators_and_permission(apps):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    # filter rather than get: a database without the group, or the permission, does nothing.
    permission = Permission.objects.filter(content_type__app_label="repo", codename="delete_material")
    return Group.objects.filter(name="Curators"), permission


def remove_permission(apps, schema_editor):
    groups, permission = curators_and_permission(apps)
    for curators in groups:
        curators.permissions.remove(*permission)


def add_permission(apps, schema_editor):
    """Undo remove_permission."""
    groups, permission = curators_and_permission(apps)
    for curators in groups:
        curators.permissions.add(*permission)


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0020_curators_mixture_components"),
    ]

    operations = [
        migrations.RunPython(remove_permission, add_permission),
    ]
