import math
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from . import charts, chem
from .models import (
    DOI_PREFIX,
    Material,
    Phase,
    PhaseTransition,
    Property,
    Reference,
    Series,
    group_properties,
    pk_from_accession,
)


def find_materials(text, substructure="", phase="", t_min=None, t_max=None):
    """The materials a search finds, in LCR ID order. A blank search finds them all.

    A material is found when the text is part of its name or an alias (ignoring case),
    is its InChIKey (ignoring case), or is its LCR ID; when at least one of its sequences
    has the phase (a code, e.g. NF); and when its structure contains the substructure
    (SMILES or SMARTS). Raises chem.StructureError when RDKit can't read the substructure.

    With t_min or t_max (°C; either can be None), the phase must form on cooling in that
    range, ends included: some sequence has a transition into it there. With no phase,
    a transition into any phase counts.
    """
    materials = Material.objects.order_by("pk")
    text = text.strip()
    if text:
        found = Q(name__icontains=text) | Q(aliases__icontains=text) | Q(inchikey__iexact=text)
        pk = pk_from_accession(text)
        if pk is not None:
            found |= Q(pk=pk)
        materials = materials.filter(found)
    if t_min is not None or t_max is not None:
        # Find the transitions first. Every condition is on the one table, so each filter()
        # narrows the same rows, and a transition has to meet them all: NF forming at 120 °C,
        # not NF somewhere and some other phase forming at 120 °C.
        transitions = PhaseTransition.objects.all()
        if phase:
            transitions = transitions.filter(lower__code=phase)
        if t_min is not None:
            transitions = transitions.filter(temperature_c__gte=t_min)
        if t_max is not None:
            transitions = transitions.filter(temperature_c__lte=t_max)
        materials = materials.filter(phase_sequences__transitions__in=transitions).distinct()
    elif phase:
        # The database follows Material -> its sequences -> their phases. That gives one row
        # per matching sequence, so a material with the phase in two sequences would come
        # back twice: distinct() keeps one. This comes before the substructure, so RDKit
        # checks fewer structures.
        materials = materials.filter(phase_sequences__phases__code=phase).distinct()
    if substructure.strip():
        pattern = chem.substructure_pattern(substructure)
        # The database can't compare structures, so RDKit checks each material found so far.
        rows = materials.values_list("pk", "canonical_smiles")
        matches = [pk for pk, smiles in rows if chem.has_substructure(smiles, pattern)]
        materials = materials.filter(pk__in=matches)
    return materials


def phase_choices():
    """The phase box's choices, in the vocabulary's order, as (code, label) pairs:
    ("NF", "NF (Ferroelectric nematic)"), or the name alone when it starts with the
    code, as "SmApx (as reported)" does."""
    return [
        (phase.code, phase.name if phase.name.startswith(phase.code) else f"{phase.code} ({phase.name})")
        for phase in Phase.objects.all()
    ]


def temperature_param(request, key):
    """?tmin= or ?tmax= as (the text typed, the number): ("100", 100.0). A blank, or anything
    that isn't a number, gives ("", None), so it is ignored, as an unknown ?phase= is.
    isfinite() refuses "nan" and "inf", which float() accepts."""
    text = request.GET.get(key, "").strip()
    try:
        number = float(text)
    except ValueError:
        return "", None
    return (text, number) if math.isfinite(number) else ("", None)


def range_text(tmin, tmax):
    """The temperature range as the heading says it, from the text typed in each box."""
    if tmin and tmax:
        return f"between {tmin} and {tmax} °C"
    if tmin:
        return f"at {tmin} °C or above"
    if tmax:
        return f"at {tmax} °C or below"
    return ""


def materials(request):
    """The Materials page, and for now the home page: every material, or those that ?q=,
    ?sub=, ?phase=, ?tmin= and ?tmax= find, 25 to a page. get_page() turns a missing, bad
    or too-high ?page= into a real page. A substructure RDKit can't read lists nothing and
    says why. A ?phase= that isn't in the vocabulary is ignored."""
    query = request.GET.get("q", "").strip()
    substructure = request.GET.get("sub", "").strip()
    choices = phase_choices()
    phase = request.GET.get("phase", "")
    if phase not in dict(choices):
        phase = ""
    tmin, t_min = temperature_param(request, "tmin")
    tmax, t_max = temperature_param(request, "tmax")
    error = ""
    try:
        found = find_materials(query, substructure, phase, t_min, t_max)
    except chem.StructureError as exc:
        found, error = Material.objects.none(), str(exc)
    page = Paginator(found, 25).get_page(request.GET.get("page"))
    # The search as it goes in an address, so the page links keep it. urlencode() turns
    # characters such as # into %23: a bare # would end the address there.
    fields = [("q", query), ("sub", substructure), ("phase", phase), ("tmin", tmin), ("tmax", tmax)]
    search = urlencode({key: value for key, value in fields if value})
    context = {
        "nav": "materials",  # which link base.html marks as the current section
        "query": query,
        "substructure": substructure,
        "phase": phase,
        "phase_choices": choices,
        "tmin": tmin,
        "tmax": tmax,
        "range_text": range_text(tmin, tmax),
        "error": error,
        "page": page,
        "search": search,
    }
    return render(request, "repo/materials.html", context)


def value_sections(series, with_charts=False):
    """Series as the values section lists them: [(group, [(property, [its series], [its
    charts]), ...]), ...], with group None for a property in no group. The order is
    group_properties()'s, so a group's members are listed together under its name. Only
    properties with series are listed, and each keeps its series in the order given. The
    charts are charts.charts_for()'s with with_charts, and [] without. Pass series with
    select_related("property__group"), or each property costs a query."""
    by_property = {}
    for one in series:
        # Two rows' property objects are equal when they are the same property (same
        # primary key), so each property's series end up in one list.
        by_property.setdefault(one.property, []).append(one)
    return [
        (group, [
            (prop, by_property[prop], charts.charts_for(prop, by_property[prop]) if with_charts else [])
            for prop in members
        ])
        for group, members in group_properties(by_property)
    ]


def material_detail(request, pk):
    """One material's public page. A number with no material gives a 404."""
    material = get_object_or_404(Material, pk=pk)
    formula, mass = chem.formula_and_mass(material.canonical_smiles)
    # One query for the series with what each row shows of their reference, property and
    # variable, and one more for the points of all of them (prefetch_related), however
    # many series there are.
    series = material.series.select_related("reference", "property__group", "variable").prefetch_related("points")
    context = {
        "nav": "materials",
        "material": material,
        "formula": formula,
        "mass": mass,
        # select_related() fetches each sequence's reference in the same query, instead of
        # one more query for every row the template shows.
        "sequences": material.phase_sequences.select_related("reference"),
        "lc_phases": material.liquid_crystal_phases(),
        "value_sections": value_sections(series, with_charts=True),
    }
    return render(request, "repo/material_detail.html", context)


def references_with_counts():
    """Every reference, each with n_materials: how many materials it gives sequences for.
    The database counts them. distinct=True counts a material once, however many of its
    sequences come from the reference."""
    return Reference.objects.annotate(n_materials=Count("phase_sequences__material", distinct=True))


def find_references(text, kind=""):
    """The references a search finds, newest year first. A blank search finds them all.

    The label is worked out in Python, so the database can't search it. Instead, each word
    of the text must be part of the identifier (a DOI or patent number), first author,
    venue or title, ignoring case, or be the year. So "Mandle 2017" finds the reference
    labelled "Mandle 2017, Chem. Eur. J.". A pasted https://doi.org/ or doi: is removed
    first. kind, if given, is one of Reference.Kind and keeps only that kind.
    """
    references = references_with_counts().order_by(
        F("year").desc(nulls_last=True), "first_author", "identifier"
    )
    # Commas count as spaces, so a whole copied label works.
    for word in DOI_PREFIX.sub("", text.strip()).replace(",", " ").split():
        found = (
            Q(identifier__icontains=word)
            | Q(first_author__icontains=word)
            | Q(venue__icontains=word)
            | Q(title__icontains=word)
        )
        if word.isdigit():
            found |= Q(year=int(word))
        # Each filter() narrows the last, so every word has to match.
        references = references.filter(found)
    if kind:
        references = references.filter(kind=kind)
    return references


def references(request):
    """The References page: every reference, or those that ?q= and ?kind= find, 25 to a
    page. A ?kind= that isn't one of the kinds is ignored."""
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    if kind not in Reference.Kind.values:
        kind = ""
    page = Paginator(find_references(query, kind), 25).get_page(request.GET.get("page"))
    search = urlencode({key: value for key, value in [("q", query), ("kind", kind)] if value})
    context = {
        "nav": "references",
        "query": query,
        "kind": kind,
        "kind_label": Reference.Kind(kind).label if kind else "",
        "kinds": Reference.Kind.choices,
        "page": page,
        "search": search,
    }
    return render(request, "repo/references.html", context)


def reference_detail(request, pk):
    """One reference's public page: its details and link, the materials it gives
    sequences for, with those sequences, and the property values it gives. A number with
    no reference gives a 404."""
    reference = get_object_or_404(references_with_counts(), pk=pk)
    # As on the material page, with each row's material in place of its reference.
    series = (
        reference.series.select_related("material", "property__group", "variable")
        .prefetch_related("points")
        .order_by("material_id", "id")
    )
    context = {
        "nav": "references",
        "reference": reference,
        # In LCR ID order, as on the Materials page. select_related() fetches each row's
        # material in the same query.
        "sequences": reference.phase_sequences.select_related("material").order_by("material_id", "id"),
        "value_sections": value_sections(series),
    }
    return render(request, "repo/reference_detail.html", context)


def properties_with_counts():
    """Every property, each with n_materials and n_references: how many materials and how
    many references give values for it. As in references_with_counts(), distinct=True
    counts each once, however many series it has. select_related() fetches each group in
    the same query, for group_properties()."""
    return Property.objects.select_related("group").annotate(
        n_materials=Count("series__material", distinct=True),
        n_references=Count("series__reference", distinct=True),
    )


def properties(request):
    """The Properties page: every property in the vocabulary's order, with its counts, and a
    group's members together under its name."""
    context = {"nav": "properties", "entries": group_properties(properties_with_counts())}
    return render(request, "repo/properties.html", context)


# How ?sort= orders a property page's series. By value, a series counts as its lowest
# point (lowest_value, worked out in property_detail()): a single value is its value, and a
# curve sits where its range starts. A series with no points has no lowest value, so it
# goes last. Ties, and the material order itself, go by LCR ID, then reference.
PROPERTY_SORTS = {
    "material": ["material_id", "reference__identifier", "id"],
    "value": [F("lowest_value").asc(nulls_last=True), "material_id", "reference__identifier", "id"],
}


def property_detail(request, code):
    """One property's public page: what its values must state, and every value of it across
    materials, by material or, with ?sort=value, by value. Any other ?sort= is ignored. A
    code with no property gives a 404."""
    prop = get_object_or_404(properties_with_counts(), code=code)
    sort = request.GET.get("sort", "")
    if sort not in PROPERTY_SORTS:
        sort = "material"
    # Min() has the database find each series' lowest point, so it can sort by it. The
    # rest is as on the material page: one query for the series and one for their points.
    series = (
        prop.series.select_related("material", "reference", "property", "variable")
        .prefetch_related("points")
        .annotate(lowest_value=Min("points__value"))
        .order_by(*PROPERTY_SORTS[sort])
    )
    context = {
        "nav": "properties",
        "property": prop,
        # The kinds with conditions of their own, in the order the kinds are listed:
        # [("computed", ["method", ...])]. A kind name that isn't one of the kinds is left out.
        "by_kind": [
            (kind, prop.conditions_for(kind))
            for kind in Series.Kind.values
            if kind in (prop.required_conditions_by_kind or {})
        ],
        "series_list": series,
        "sort": sort,
    }
    return render(request, "repo/property_detail.html", context)


def structure(request, pk):
    """A material's structure drawing, as an SVG image of its own: 360 by 220 pixels,
    or 200 by 128 with ?size=thumb (Material.thumbnail_url)."""
    material = get_object_or_404(Material, pk=pk)
    width, height = (200, 128) if request.GET.get("size") == "thumb" else (360, 220)
    response = HttpResponse(
        chem.structure_svg(material.canonical_smiles, width, height), content_type="image/svg+xml"
    )
    # Browsers may keep the drawing for a day (86,400 seconds). Pages link to it through
    # Material.structure_url, which changes when the structure does.
    response["Cache-Control"] = "max-age=86400"
    return response
