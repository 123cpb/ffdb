"""The CSV downloads: what the Materials page and a property page show, as files for a
spreadsheet or for code. Nothing is combined: there is one row per material, per sequence
or per point, so whoever uses the data combines it and knows how. Lists inside a cell,
such as aliases, are separated by "; ", so a cell never looks like two to anyone who
splits a line at its commas."""

import csv
import json
from collections import defaultdict

from django.http import HttpResponse
from django.utils import timezone

from . import chem
from .models import Phase, PhaseSequence, Series
from .phases import NOT_LIQUID_CRYSTAL
from .templatetags.repo_tags import conditions_text


def csv_response(filename, header, rows):
    """A CSV file to download, named filename: header, then rows, each with the time it
    was exported (UTC) added as its last column, so the file itself says when it was made."""
    # attachment tells the browser to save the file rather than show it.
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    # The byte-order mark. Without it Excel guesses an older encoding and shows µ as Âµ.
    response.write("﻿")
    exported_at = timezone.now().isoformat(timespec="seconds")  # 2026-11-18T14:03:12+00:00
    # A response has write(), as a file does, so the csv module writes straight into it.
    # It quotes any cell that holds a comma, a quote or a line break, and writes None as "".
    writer = csv.writer(response)
    writer.writerow([*header, "exported_at"])
    for row in rows:
        writer.writerow([*row, exported_at])
    return response


MATERIAL_COLUMNS = [
    "material_id", "name", "aliases", "smiles", "inchikey", "formula", "molar_mass_g_mol",
    "lc_phases", "n_references",
]


def material_rows(materials):
    """One row per material, in the order given. lc_phases are the liquid-crystal phases in
    any of its sequences, in the vocabulary's order; n_references counts each reference that
    gives it a sequence or a value once. Both are found for all the materials at once, in
    three queries, rather than three for each material."""
    phases = defaultdict(list)
    # One row per material and phase. Phase's own ordering, by sort order, keeps each
    # material's phases in the vocabulary's order.
    in_sequences = (
        Phase.objects.filter(phase_sequences__material__in=materials)
        .exclude(code__in=NOT_LIQUID_CRYSTAL)
        .values_list("phase_sequences__material", "code")
        .distinct()
    )
    for material_id, code in in_sequences:
        phases[material_id].append(code)
    references = defaultdict(set)  # a set keeps a reference once, however often it is added
    for model in (PhaseSequence, Series):
        # order_by() with nothing in it drops the model's ordering, which needs joins.
        pairs = model.objects.filter(material__in=materials).order_by().values_list("material_id", "reference_id")
        for material_id, reference_id in pairs:
            references[material_id].add(reference_id)
    for material in materials:
        formula, mass = chem.formula_and_mass(material.canonical_smiles)
        yield [
            material.accession, material.name, "; ".join(material.alias_list()),
            material.canonical_smiles, material.inchikey, formula, round(mass, 3),
            "; ".join(phases[material.pk]), len(references[material.pk]),
        ]


REFERENCE_COLUMNS = ["reference", "reference_kind", "reference_identifier", "reference_link"]


def reference_cells(reference):
    """A row's reference: its label, kind (article, preprint, patent or thesis), identifier
    and link. A thesis has no link."""
    return [reference.label, reference.kind, reference.identifier, reference.link]


SEQUENCE_COLUMNS = [
    "material_id", "material", "sequence_reported", "sequence_standard",
    "clearing_point_c", "melting_point_c", *REFERENCE_COLUMNS,
]


def sequence_rows(materials):
    """One row per phase sequence of the materials, by LCR ID, then as the material page
    lists them. Every sequence is given, as reported and under the vocabulary's codes."""
    sequences = (
        PhaseSequence.objects.filter(material__in=materials)
        .select_related("material", "reference")
        .order_by("material_id", "reference__identifier", "id")
    )
    for sequence in sequences:
        yield [
            sequence.material.accession, sequence.material.name, sequence.sequence,
            sequence.standard_sequence, sequence.t_clearing_c, sequence.t_melting_c,
            *reference_cells(sequence.reference),
        ]


VALUE_COLUMNS = [
    "material_id", "material", "smiles", "property", "symbol", "series_id", "qualifier",
    "value", "unit", "uncertainty", "temperature_c", "variable", "x", "x_unit", "kind",
    "method", "direction", "conditions", "conditions_json", "taken_from", "location",
    "digitised", *REFERENCE_COLUMNS,
]


def value_rows(points):
    """One row per point, given as a Measurement queryset. Rows with the same series_id are
    one curve, and a single value is a series of one. series_id is the database's number,
    not a permanent ID. x is where the point sits on its chart: its temperature for a series
    against temperature, and blank for a value against nothing. The conditions are given
    twice, in words and as JSON for code. Choices are given as their stored codes:
    measured, cooling, figure."""
    points = points.select_related(
        "series__material", "series__property", "series__variable", "series__reference"
    ).order_by(
        "series__material_id", "series__property__sort_order", "series__reference__identifier",
        "series_id", "temperature_c", "x", "id",
    )
    for point in points:
        series = point.series
        prop, variable = series.property, series.variable
        yield [
            series.material.accession, series.material.name, series.material.canonical_smiles,
            prop.name, prop.symbol, series.pk, point.qualifier, point.value, prop.unit,
            point.uncertainty, point.temperature_c, variable.code, point.x_value(), variable.unit,
            series.kind, series.method, series.direction, conditions_text(series.conditions),
            # sort_keys, as the words are sorted; ensure_ascii=False writes µ, not µ.
            json.dumps(series.conditions or {}, sort_keys=True, ensure_ascii=False),
            series.taken_from, series.location, "yes" if series.digitised else "no",
            *reference_cells(series.reference),
        ]
