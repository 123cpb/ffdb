"""Leave the Curators group only add reference, which is what lets a curator use Add data
(views.py). Their saves there wait for an admin's check (migration 0031), but until now they
could also add, change and delete sequences, values, references and mixture components in
the admin, which got round the check. Curators now ask an admin for corrections, and no
longer need staff status. One who keeps it sees nothing in the admin: ReferenceAdmin offers
adding a reference only to admins."""

from django.db import migrations

# What migrations 0013, 0020 and 0021 left the group: add, change and delete on these models,
# except delete material. The undo puts it back.
MODELS = ["material", "reference", "phasesequence", "series", "measurement", "mixturecomponent"]
EARLIER = [f"{action}_{model}" for model in MODELS for action in ["add", "change", "delete"]]
EARLIER.remove("delete_material")


def repo_permissions(apps, codenames):
    Permission = apps.get_model("auth", "Permission")
    return Permission.objects.filter(content_type__app_label="repo", codename__in=codenames)


def add_reference_only(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    # filter rather than get: a database without the group does nothing. set() rather than
    # remove(), so anything an admin gave the group by hand goes too.
    for curators in Group.objects.filter(name="Curators"):
        curators.permissions.set(repo_permissions(apps, ["add_reference"]))


def give_back(apps, schema_editor):
    """Undo add_reference_only. The permissions exist: migrations 0013 and 0020 made them."""
    Group = apps.get_model("auth", "Group")
    for curators in Group.objects.filter(name="Curators"):
        curators.permissions.add(*repo_permissions(apps, EARLIER))


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0031_waiting_saves"),
    ]

    operations = [
        migrations.RunPython(add_reference_only, give_back),
    ]
