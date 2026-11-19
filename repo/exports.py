"""The CSV downloads: what the Materials page and a property page show, as files for a
spreadsheet or for code. Nothing is combined: there is one row per material, per sequence
or per point, so whoever uses the data combines it and knows how. Lists inside a cell,
such as aliases, are separated by "; ", so a cell never looks like two to anyone who
splits a line at its commas. Every file says whether each row's material is a compound
or a mixture (material_kind), so a mixture's rows can be left out, for example by code
that reads structures."""

import csv
import json
from collections import defaultdict

from django.http import HttpResponse
from django.utils import timezone

from . import chem
from .models import Material, MixtureComponent, Phase, PhaseSequence, Series
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
    "material_id", "name", "aliases", "material_kind", "composition_status", "components",
    "other_components", "smiles", "inchikey", "formula", "molar_mass_g_mol", "lc_phases",
    "n_references",
]


def component_text(row):
    """One of a mixture's components, as its material page writes it: "LCR-00012 DIO, 70
    mol%", or "LCR-00099, 30 mol%" for a compound with no name. To split one, the LCR ID
    is before the first space or comma, and the fraction and unit are after the last comma,
    whatever the name holds. The components cell joins them with "; ", so only a name
    containing "; " would split wrongly. None does, but nothing refuses one."""
    compound = row.component
    label = f"{compound.accession} {compound.name}" if compound.name else compound.accession
    return f"{label}, {row.fraction:g} {row.unit}"


def material_rows(materials):
    """One row per material, in the order given. lc_phases are the liquid-crystal phases in
    any of its sequences, in the vocabulary's order; n_references counts each reference that
    gives it a sequence or a value once. A mixture's components are its components in FFDB,
    largest first (component_text()), and its composition status is the stored code. These
    three are found for all the materials at once, in four queries, rather than four for
    each material.

    A mixture has no structure, so no SMILES, InChIKey, formula or molar mass. A compound
    has no composition: every compound stores the default status, listed, so it is left out."""
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
    components = defaultdict(list)
    # MixtureComponent's own ordering puts each mixture's largest component first.
    rows = MixtureComponent.objects.filter(mixture__in=materials).select_related("component")
    for row in rows:
        components[row.mixture_id].append(component_text(row))
    for material in materials:
        if material.kind == Material.Kind.COMPOUND:
            formula, mass = chem.formula_and_mass(material.canonical_smiles)
            mass = round(mass, 3)
            status = other = ""
        else:
            # Not from RDKit, which reads a blank SMILES as a molecule with no atoms: "" and 0.0.
            formula = mass = ""
            status, other = material.composition_status, material.other_components
        yield [
            material.accession, material.name, "; ".join(material.alias_list()), material.kind,
            status, "; ".join(components[material.pk]), other,
            material.canonical_smiles, material.inchikey, formula, mass,
            "; ".join(phases[material.pk]), len(references[material.pk]),
        ]


REFERENCE_COLUMNS = ["reference", "reference_kind", "reference_identifier", "reference_link"]


def reference_cells(reference):
    """A row's reference: its label, kind (article, preprint, patent, thesis or
    unpublished), identifier and link. A thesis or an unpublished result has no link. An
    unpublished result's label ends "(unpublished)" too (Reference.label)."""
    return [reference.label, reference.kind, reference.identifier, reference.link]


SEQUENCE_COLUMNS = [
    "material_id", "material", "material_kind", "sequence_reported", "sequence_standard",
    "direction", "rate_k_per_min", "method", "lowest_temperature_c", "clearing_point_c",
    "melting_point_c", *REFERENCE_COLUMNS,
]


def sequence_rows(materials):
    """One row per phase sequence of the materials, by LCR ID, then as the material page
    lists them. Every sequence is given, as reported and under the vocabulary's codes. The
    direction is its stored code: summary, cooling, heating or unspecified; so is the
    material's kind: compound or mixture. A summary is written from the solid up and a run
    in the order observed, so a cooling run reads from the hottest phase down."""
    sequences = (
        PhaseSequence.objects.filter(material__in=materials)
        .select_related("material", "reference")
        .order_by("material_id", "reference__identifier", "id")
    )
    for sequence in sequences:
        material = sequence.material
        yield [
            material.accession, material.name, material.kind, sequence.sequence,
            sequence.standard_sequence, sequence.direction, sequence.rate_k_per_min, sequence.method,
            sequence.lowest_temperature_c, sequence.t_clearing_c, sequence.t_melting_c,
            *reference_cells(sequence.reference),
        ]


VALUE_COLUMNS = [
    "material_id", "material", "material_kind", "smiles", "property", "symbol", "series_id",
    "qualifier", "value", "unit", "uncertainty", "temperature_c", "variable", "x", "x_unit",
    "kind", "method", "direction", "conditions", "conditions_json", "taken_from", "location",
    "digitised", *REFERENCE_COLUMNS,
]


def value_rows(points):
    """One row per point, given as a Measurement queryset. Rows with the same series_id are
    one curve, and a single value is a series of one. series_id is the database's number,
    not a permanent ID. x is where the point sits on its chart: its temperature for a series
    against temperature, and blank for a value against nothing. The conditions are given
    twice, in words and as JSON for code. Choices are given as their stored codes:
    measured, cooling, figure, and the material's kind, compound or mixture. kind is the
    value's kind (measured, extrapolated or computed), not the material's. A mixture has
    no SMILES."""
    points = points.select_related(
        "series__material", "series__property", "series__variable", "series__reference"
    ).order_by(
        "series__material_id", "series__property__sort_order", "series__reference__identifier",
        "series_id", "temperature_c", "x", "id",
    )
    for point in points:
        series = point.series
        material, prop, variable = series.material, series.property, series.variable
        yield [
            material.accession, material.name, material.kind, material.canonical_smiles,
            prop.name, prop.symbol, series.pk, point.qualifier, point.value, prop.unit,
            point.uncertainty, point.temperature_c, variable.code, point.x_value(), variable.unit,
            series.kind, series.method, series.direction, conditions_text(series.conditions),
            # sort_keys, as the words are sorted; ensure_ascii=False writes µ, not µ.
            json.dumps(series.conditions or {}, sort_keys=True, ensure_ascii=False),
            series.taken_from, series.location, "yes" if series.digitised else "no",
            *reference_cells(series.reference),
        ]
