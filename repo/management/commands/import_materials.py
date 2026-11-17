"""manage.py import_materials <path>: load names and structures from a merged list."""

import csv
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from repo import chem
from repo.models import Material, name_key

# The columns of the merged list that this command reads. Its other columns (phase
# sequences, references, values) are for later stages.
COLUMNS = ["origin", "line", "name", "smiles"]


class Command(BaseCommand):
    help = "Import names and structures from a merged list, a CSV with origin, line, name and smiles columns."

    def add_arguments(self, parser):
        parser.add_argument("path", help="the CSV file, kept outside the repository")

    def handle(self, *args, **options):
        path = options["path"]
        try:
            # utf-8-sig also reads a file that Excel saved with a byte-order mark.
            with open(path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
                if missing:
                    raise CommandError(f"{path} has no {', '.join(missing)} column. It needs {', '.join(COLUMNS)}.")
                rows = list(reader)
        except OSError as exc:
            raise CommandError(f"Could not read {path}: {exc}")

        # One transaction: if anything goes wrong partway, nothing from this run is kept.
        with transaction.atomic():
            outcomes = Counter(self.import_row(row) for row in rows)

        self.stdout.write(f"Rows read: {len(rows)}")
        self.stdout.write(f"Materials added: {outcomes['added']}")
        self.stdout.write(f"Names added as aliases: {outcomes['alias']}")
        self.stdout.write(f"Rows with nothing new: {outcomes['nothing new']}")
        self.stdout.write(f"Rows skipped: {outcomes['skipped']}")

    def import_row(self, row):
        """Import one row. Returns what happened: "added", "alias", "nothing new" or "skipped"."""
        where = f"{row['origin']} line {row['line']}"  # its place in the original list
        name = (row["name"] or "").strip()
        smiles = (row["smiles"] or "").strip()

        if not smiles:
            return self.skip(where, name, "no SMILES")
        try:
            _, inchikey = chem.standardise(smiles)
        except chem.StructureError as exc:
            return self.skip(where, name, str(exc))

        material = Material.objects.filter(inchikey=inchikey).first()
        if material is None:
            material = Material.objects.create(name=name, smiles=smiles)
            warning = material.stereo_warning()
            if warning:
                self.stdout.write(f"{where}: {warning}")
            return "added"

        # The structure is already in FFDB: keep the row's name as an alias, if it's a new name.
        if name_key(name) and not material.has_name(name):
            material.aliases = ", ".join(material.alias_list() + [name])
            material.save()
            return "alias"
        return "nothing new"

    def skip(self, where, name, reason):
        self.stdout.write(f"{where} ({name or 'no name'}): skipped, {reason}")
        return "skipped"
