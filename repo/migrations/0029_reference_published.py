"""Rename the reference's switch from public to published (0027), so that "public" means
only the rule in models.py (ReferenceQuerySet.public()): published, with public data.

Written by hand: makemigrations asks whether a field was renamed, and without an answer
it would drop the column, losing every reference's switch, and add a new one, all off.
RenameField keeps the column and its values. The constraint names the field, so it is
removed first and added again under the new name."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("repo", "0028_switch_on_existing_data"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="reference", name="unpublished_is_not_public"),
        migrations.RenameField(model_name="reference", old_name="public", new_name="published"),
        migrations.AlterField(
            model_name="reference",
            name="published",
            field=models.BooleanField(
                default=False,
                help_text="The work is published, so its approved data can be public. An unpublished group result can't be.",
            ),
        ),
        migrations.AddConstraint(
            model_name="reference",
            constraint=models.CheckConstraint(
                condition=~models.Q(kind="unpublished") | models.Q(published=False),
                name="unpublished_is_not_published",
                violation_error_message="An unpublished group result can't be marked published.",
            ),
        ),
    ]
