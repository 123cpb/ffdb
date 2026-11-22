"""manage.py import_series <path>: load property data, one series per row, from the list
that merge.py writes (series.csv)."""

import csv
import json
import unicodedata
from collections import Counter
from pathlib import Path

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from repo import chem
from repo.management.commands.import_materials import reference_kind
from repo.models import Material, Measurement, Property, Reference, Series, Variable, clean_doi

# A point's columns, the same in a row (a single value) and in a points file (a curve).
POINT_COLUMNS = ["qualifier", "value", "uncertainty", "temperature_c", "x"]
COLUMNS = [
    "origin", "line", "smiles", "reference", "reference_kind", "property", "variable", "kind", "method",
    "direction", "taken_from", "location", "digitised", "conditions", "notes", "value_unit", "x_unit",
    "points_file",
] + POINT_COLUMNS
YES_NO = {"yes": True, "no": False}


class RowRefused(Exception):
    """A row that can't be imported. The message is the reason, which is printed."""


def same_unit(a, b):
    """Units compared as written, apart from look-alike characters: µ (micro sign) and μ (mu)
    are one character after NFKC normalisation. Nothing is converted."""
    return unicodedata.normalize("NFKC", a.strip()) == unicodedata.normalize("NFKC", b.strip())


def describe(error):
    """A ValidationError as one line, each message after the field it is about, so a message
    such as "This field cannot be blank." says which column to fix."""
    if not hasattr(error, "error_dict"):
        return " ".join(error.messages)
    return " ".join(
        message if field == NON_FIELD_ERRORS else f"{field}: {message}"
        for field, messages in error.message_dict.items()
        for message in messages
    )


def number(cells, column, where):
    """The number in cells[column], or None when it's blank."""
    text = (cells.get(column) or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise RowRefused(f"{where}: {column} {text!r} isn't a number")


def read_point(cells, where):
    """A Measurement, not yet saved and without its series, from a row or a points-file line."""
    value = number(cells, "value", where)
    if value is None:
        raise RowRefused(f"{where}: no value")
    return Measurement(
        qualifier=(cells.get("qualifier") or "").strip() or Measurement.Qualifier.EXACT,
        value=value,
        uncertainty=number(cells, "uncertainty", where),
        temperature_c=number(cells, "temperature_c", where),
        x=number(cells, "x", where),
    )


class Command(BaseCommand):
    help = f"Import property data from a series list, a CSV with {', '.join(COLUMNS)} columns."

    def add_arguments(self, parser):
        parser.add_argument("path", help="the CSV file, kept outside the repository")

    def handle(self, *args, **options):
        path = Path(options["path"])
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
                if missing:
                    raise CommandError(f"{path} has no {', '.join(missing)} column. It needs {', '.join(COLUMNS)}.")
                rows = list(reader)
        except OSError as exc:
            raise CommandError(f"Could not read {path}: {exc}")

        self.folder = path.parent  # points files are named relative to the list
        self.properties = {p.code: p for p in Property.objects.all()}
        self.variables = {v.code: v for v in Variable.objects.all()}
        self.counts = Counter()
        with transaction.atomic():
            for row in rows:
                self.counts[self.import_row(row)] += 1

        self.stdout.write(f"Series added: {self.counts['added']}")
        self.stdout.write(f"Series already there: {self.counts['already there']}")
        self.stdout.write(f"Series skipped: {self.counts['skipped']}")
        self.stdout.write(f"Points added: {self.counts['points']}")

    def import_row(self, row):
        """Import one row's series and points. Returns "added", "already there" or "skipped"."""
        row = {key: (value or "").strip() for key, value in row.items()}
        where = f"{row['origin']} line {row['line']}"  # its place in its source list
        try:
            series, points, identifier, kind = self.read_row(row)
        except RowRefused as exc:
            return self.skip(where, row["smiles"], str(exc))

        reference = Reference.objects.filter(identifier=identifier).first()
        if reference and self.already_there(series, reference):
            return "already there"

        try:
            # A savepoint: if a rule refuses the series or any point, everything this row
            # saved, its reference included, is undone, and the rest of the import carries on.
            with transaction.atomic():
                # As in import_materials: a new reference is published unless it is unpublished work.
                reference, created = Reference.objects.get_or_create(
                    identifier=identifier, defaults={"kind": kind, "published": kind != Reference.Kind.UNPUBLISHED}
                )
                series.reference = reference
                series.full_clean()
                series.save()
                for label, point in points:
                    point.series = series
                    try:
                        point.full_clean()
                    except ValidationError as exc:
                        raise ValidationError(f"{label}: {describe(exc)}")
                    point.save()
        except ValidationError as exc:
            return self.skip(where, series.material, describe(exc))
        self.counts["points"] += len(points)
        return "added"

    def read_row(self, row):
        """(series, [(label, point), ...], reference identifier, reference kind) from a row, the
        series and points not yet saved. Raises RowRefused for anything the row gets wrong
        that the models' rules don't check."""
        try:
            _, inchikey = chem.standardise(row["smiles"])
        except chem.StructureError as exc:
            raise RowRefused(str(exc))
        material = Material.objects.filter(inchikey=inchikey).first()
        if material is None:
            raise RowRefused("material not in FFDB (import_materials adds materials)")

        identifier = row["reference"]
        kind = reference_kind(row["reference_kind"], identifier)
        if kind is None:
            raise RowRefused(f"reference kind {row['reference_kind']!r} isn't one FFDB takes ({identifier})")
        if kind in (Reference.Kind.ARTICLE, Reference.Kind.PREPRINT):
            identifier = clean_doi(identifier)

        prop, variable = self.properties.get(row["property"]), self.variables.get(row["variable"])
        if prop is None:
            raise RowRefused(f"unknown property {row['property']!r}")
        if variable is None:
            raise RowRefused(f"unknown variable {row['variable']!r}")
        if not same_unit(row["value_unit"], prop.unit):
            raise RowRefused(f"value_unit {row['value_unit']!r}: {prop.symbol} is stored in {prop.unit}")
        if not same_unit(row["x_unit"], variable.unit):
            raise RowRefused(f"x_unit {row['x_unit']!r}: {variable.name.lower()} is stored in {variable.unit or 'no unit'}")
        if row["digitised"] not in YES_NO:
            raise RowRefused(f"digitised is yes or no, not {row['digitised']!r}")
        try:
            conditions = json.loads(row["conditions"]) if row["conditions"] else {}
        except json.JSONDecodeError as exc:
            raise RowRefused(f"conditions aren't JSON: {exc}")

        series = Series(
            material=material, property=prop, variable=variable, kind=row["kind"], method=row["method"],
            conditions=conditions, direction=row["direction"], taken_from=row["taken_from"],
            location=row["location"], digitised=YES_NO[row["digitised"]], notes=row["notes"],
            approved=True,  # as in import_materials: only an admin runs an import
        )
        return series, self.read_points(row), identifier, kind

    def read_points(self, row):
        """[(label, point), ...] from the row's points file, or the row's own point columns for
        a single value. The label says where the point is, for messages."""
        in_row = any(row[column] for column in POINT_COLUMNS)
        if not row["points_file"]:
            if not in_row:
                raise RowRefused("no points: give a points_file or a value")
            return [("the row's value", read_point(row, "the row's value"))]
        if in_row:
            raise RowRefused("give the points in points_file or in the row, not both")
        name = row["points_file"]
        try:
            with open(self.folder / name, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                if "value" not in (reader.fieldnames or []):
                    raise RowRefused(f"{name} has no value column")
                points = [
                    (f"{name} line {n}", read_point(cells, f"{name} line {n}"))
                    for n, cells in enumerate(reader, start=2)
                ]
        except OSError as exc:
            raise RowRefused(f"could not read {name}: {exc}")
        if not points:
            raise RowRefused(f"{name} has no points")
        return points

    def already_there(self, series, reference):
        """Whether the reference already gives this series: the same material, property, kind,
        variable, location, direction and conditions. Conditions are compared in Python, so
        50 and 50.0 match and SQLite and PostgreSQL agree."""
        same = Series.objects.filter(
            material=series.material, reference=reference, property=series.property, kind=series.kind,
            variable=series.variable, location=series.location, direction=series.direction,
        )
        return any(other.conditions == series.conditions for other in same)

    def skip(self, where, material, reason):
        self.stdout.write(f"{where} ({material or 'no SMILES'}): skipped, {reason}")
        return "skipped"
