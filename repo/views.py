import math
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize

from . import charts, chem, crossref, exports, ribbons
from .forms import (
    CompoundTableForm, CurveChoiceForm, CurveForm, FindReferenceForm, MixtureForm, ValueChoiceForm, ValueForm,
)
from .models import (
    DOI_PREFIX,
    Material,
    Measurement,
    Phase,
    PhaseTransition,
    Property,
    Reference,
    RetiredID,
    Series,
    group_properties,
    pk_from_accession,
)


def find_materials(text, substructure="", phase="", t_min=None, t_max=None):
    """The materials a search finds, in LCR ID order. A blank search finds them all.

    A material is found when the text is part of its name or an alias (ignoring case),
    is its InChIKey (ignoring case), or is its LCR ID; when at least one of its sequences
    has the phase (a code, e.g. NF); and when its structure contains the substructure
    (SMILES or SMARTS). A substructure finds compounds only, because a mixture has no
    structure. Raises chem.StructureError when RDKit can't read the substructure.

    With t_min or t_max (°C; either can be None), the phase must form on cooling in that
    range, ends included: some sequence has a transition into it there. With no phase,
    a transition into any phase counts. The transitions are stored the same way whatever
    the sequence's direction, so a heating run's counts too: NF giving way to N on heating
    at 132.7 °C counts as NF forming at 132.7 °C.
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
        # The database can't compare structures, so RDKit checks each compound found so far.
        # Mixtures are left out here, rather than left to RDKit: it reads a mixture's blank
        # SMILES as a molecule with no atoms, which happens to match nothing.
        rows = materials.filter(kind=Material.Kind.COMPOUND).values_list("pk", "canonical_smiles")
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


def search_results(request):
    """The Materials page's search, read the same way by the page and its downloads, so a
    download holds the materials the page lists. Returns (terms, found, error).

    terms is what ?q=, ?sub=, ?phase=, ?tmin= and ?tmax= hold, as typed, in that order. A
    ?phase= that isn't in the vocabulary is ignored, and so is a ?tmin= or ?tmax= that isn't
    a number. found is what find_materials() finds. A substructure RDKit can't read finds
    nothing, and error says why; otherwise error is ""."""
    phase = request.GET.get("phase", "")
    if phase and not Phase.objects.filter(code=phase).exists():
        phase = ""
    tmin, t_min = temperature_param(request, "tmin")
    tmax, t_max = temperature_param(request, "tmax")
    terms = {
        "q": request.GET.get("q", "").strip(),
        "sub": request.GET.get("sub", "").strip(),
        "phase": phase,
        "tmin": tmin,
        "tmax": tmax,
    }
    try:
        return terms, find_materials(terms["q"], terms["sub"], phase, t_min, t_max), ""
    except chem.StructureError as exc:
        return terms, Material.objects.none(), str(exc)


# How ?sort= orders the Materials and References pages. The first, newest added first, is
# the default, so whoever opens either list sees what is new. A primary key counts up as
# rows go into the database, and the imports add their rows one at a time in the file's
# order, so the highest is the newest: imported or entered, it needs no date. A material's
# primary key is its LCR ID, so newest first is LCR ID backwards. A restored material is
# back under its old number, so it goes back to its old place.
MATERIAL_SORTS = {
    "newest": ["-pk"],
    "id": ["pk"],
}
REFERENCE_SORTS = {
    "newest": ["-pk"],
    "year": [F("year").desc(nulls_last=True), "first_author", "identifier"],
}


def sort_param(request, sorts):
    """?sort= if it is one of sorts, otherwise "newest": a missing or unknown one is ignored."""
    sort = request.GET.get("sort", "")
    return sort if sort in sorts else "newest"


def paging_query(kept, sort):
    """What the page links keep, as it goes in an address: the search (kept, a dict of the
    terms that aren't blank) and the order, unless it is the default."""
    return urlencode({**kept, "sort": sort} if sort != "newest" else kept)


def materials(request):
    """The Materials page, and for now the home page: every material, or those that the
    search finds (search_results()), 25 to a page, in the order ?sort= chooses
    (MATERIAL_SORTS). get_page() turns a missing, bad or too-high ?page= into a real page.
    A substructure RDKit can't read lists nothing and says why."""
    terms, found, error = search_results(request)
    sort = sort_param(request, MATERIAL_SORTS)
    # order_by() replaces the LCR ID order that find_materials() gives. The downloads keep that
    # order, because they use search_results() themselves, so a file is the same whichever
    # order the page shows.
    page = Paginator(found.order_by(*MATERIAL_SORTS[sort]), 25).get_page(request.GET.get("page"))
    kept = {key: value for key, value in terms.items() if value}
    # The search as it goes in an address, so the order links and the downloads keep it.
    # urlencode() turns characters such as # into %23: a bare # would end the address there.
    search = urlencode(kept)
    context = {
        "nav": "materials",  # which link base.html marks as the current section
        "query": terms["q"],
        "substructure": terms["sub"],
        "phase": terms["phase"],
        "phase_choices": phase_choices(),
        "tmin": terms["tmin"],
        "tmax": terms["tmax"],
        "range_text": range_text(terms["tmin"], terms["tmax"]),
        "error": error,
        "page": page,
        "search": search,
        "sort": sort,
        "paging": paging_query(kept, sort),
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


def sequence_ribbons(sequences):
    """Each sequence with its ribbon, all on one temperature axis, for the material page's
    table: ([(sequence, ribbon), ...], axis). A screen reader reads each ribbon as its
    sequence, how it was observed and its reference."""
    data = [ribbons.ribbon_data(sequence) for sequence in sequences]
    lo, hi = ribbons.temperature_range(data)
    rows = [
        (
            sequence,
            ribbons.ribbon_svg(
                one, lo, hi, label=f"{sequence.standard_sequence} (°C), {sequence.run_text()}, {sequence.reference.label}"
            ),
        )
        for sequence, one in zip(sequences, data)
    ]
    return rows, ribbons.axis_svg(lo, hi)


def material_detail(request, pk):
    """One material's public page. A compound's page shows its structure and the mixtures it
    is in; a mixture's, its composition. A merged ID leads to the material it went into, a
    retired ID gives a page saying what it was, and a number never given out gives a 404."""
    material = Material.objects.filter(pk=pk).first()
    if material is None:
        record = get_object_or_404(RetiredID, number=pk)
        if record.merged_into is not None:
            # A 302, which browsers ask again each time, not a 301, which they keep for good:
            # an undone merge would leave them going to the wrong material.
            return redirect("repo:material", record.merged_into)
        # 410 Gone, not 404 Not Found: the address did name a material, and never will again,
        # so search engines can drop it. The snapshot isn't shown: what was deleted may be wrong.
        return render(request, "repo/retired_material.html", {"nav": "materials", "record": record}, status=410)
    formula, mass = "", None  # a mixture has no structure to work them out from
    if material.kind == Material.Kind.COMPOUND:
        formula, mass = chem.formula_and_mass(material.canonical_smiles)
    # One query for the series with what each row shows of their reference, property and
    # variable, and one more for the points of all of them (prefetch_related), however
    # many series there are.
    series = material.series.select_related("reference", "property__group", "variable").prefetch_related("points")
    # select_related() fetches each sequence's reference in the same query, instead of one more
    # query for every row the template shows. prefetch_related() fetches the transitions of
    # all of them in one more query, and the phases either side in two more, for the ribbons.
    sequences = material.phase_sequences.select_related("reference").prefetch_related(
        "transitions__upper", "transitions__lower"
    )
    sequence_rows, axis = sequence_ribbons(sequences)
    context = {
        "nav": "materials",
        "material": material,
        "formula": formula,
        "mass": mass,
        "sequence_rows": sequence_rows,
        "axis": axis,
        "lc_phases": material.liquid_crystal_phases(),
        "value_sections": value_sections(series, with_charts=True),
        # A mixture's components in FFDB, largest first (MixtureComponent's ordering), each
        # with its compound fetched in the same query. None for a compound.
        "components": material.components.select_related("component"),
        # The mixtures a compound is in, in LCR ID order, each row with its fraction. None
        # for a mixture: a component must be a compound.
        "in_mixtures": material.in_mixtures.select_related("mixture").order_by("mixture_id"),
    }
    return render(request, "repo/material_detail.html", context)


def references_with_counts():
    """Every reference, each with n_materials: how many materials it gives sequences for.
    The database counts them. distinct=True counts a material once, however many of its
    sequences come from the reference."""
    return Reference.objects.annotate(n_materials=Count("phase_sequences__material", distinct=True))


def find_references(text, kind=""):
    """The references a search finds, in no set order: the page orders them
    (REFERENCE_SORTS). A blank search finds them all.

    The label is worked out in Python, so the database can't search it. Instead, each word
    of the text must be part of the identifier (a DOI or patent number), first author,
    venue or title, ignoring case, or be the year. So "Mandle 2017" finds the reference
    labelled "Mandle 2017, Chem. Eur. J.". The word unpublished also finds the unpublished
    group results, whose labels end "(unpublished)". A pasted https://doi.org/ or doi: is
    removed first. kind, if given, is one of Reference.Kind and keeps only that kind.
    """
    references = references_with_counts()
    # Commas and brackets count as spaces, so a whole copied label works.
    words = DOI_PREFIX.sub("", text.strip()).translate(str.maketrans(",()", "   ")).split()
    for word in words:
        found = (
            Q(identifier__icontains=word)
            | Q(first_author__icontains=word)
            | Q(venue__icontains=word)
            | Q(title__icontains=word)
        )
        if word.isdigit():
            found |= Q(year=int(word))
        if word.lower() == "unpublished":
            found |= Q(kind=Reference.Kind.UNPUBLISHED)
        # Each filter() narrows the last, so every word has to match.
        references = references.filter(found)
    if kind:
        references = references.filter(kind=kind)
    return references


def references(request):
    """The References page: every reference, or those that ?q= and ?kind= find, 25 to a
    page, in the order ?sort= chooses (REFERENCE_SORTS). A ?kind= that isn't one of the
    kinds is ignored."""
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    if kind not in Reference.Kind.values:
        kind = ""
    sort = sort_param(request, REFERENCE_SORTS)
    found = find_references(query, kind).order_by(*REFERENCE_SORTS[sort])
    page = Paginator(found, 25).get_page(request.GET.get("page"))
    kept = {key: value for key, value in [("q", query), ("kind", kind)] if value}
    context = {
        "nav": "references",
        "query": query,
        "kind": kind,
        "kind_label": Reference.Kind(kind).label if kind else "",
        "kinds": Reference.Kind.choices,
        "page": page,
        "search": urlencode(kept),
        "sort": sort,
        "paging": paging_query(kept, sort),
    }
    return render(request, "repo/references.html", context)


def reference_tables(reference):
    """What a reference's public page and its Add data page both show: its sequences and its
    property values, as {"sequences": ..., "value_sections": ...} for the template."""
    # As on the material page, with each row's material in place of its reference.
    series = (
        reference.series.select_related("material", "property__group", "variable")
        .prefetch_related("points")
        .order_by("material_id", "id")
    )
    return {
        # In LCR ID order, as on the Materials page. select_related() fetches each row's
        # material in the same query.
        "sequences": reference.phase_sequences.select_related("material").order_by("material_id", "id"),
        "value_sections": value_sections(series),
    }


def reference_detail(request, pk):
    """One reference's public page: its details and link, the materials it gives
    sequences for, with those sequences, and the property values it gives. A number with
    no reference gives a 404."""
    reference = get_object_or_404(references_with_counts(), pk=pk)
    context = {"nav": "references", "reference": reference, **reference_tables(reference)}
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
    # The charts draw the series in material order, whatever the table's sort, so a line
    # keeps its colour when the table is sorted by value. sorted() runs the queries once:
    # the queryset keeps the rows, and the template's table uses them again.
    in_material_order = sorted(series, key=lambda one: (one.material_id, one.reference.identifier, one.pk))
    context = {
        "nav": "properties",
        "property": prop,
        "charts": charts.charts_for(prop, in_material_order, by_material=True),
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


# The downloads (exports.py). The Materials page's three are for the materials its search
# finds: a search that finds nothing, or a substructure RDKit can't read, gives a file with
# its header and no rows.


def materials_csv(request):
    found = search_results(request)[1]
    return exports.csv_response("ffdb-materials.csv", exports.MATERIAL_COLUMNS, exports.material_rows(found))


def sequences_csv(request):
    found = search_results(request)[1]
    return exports.csv_response("ffdb-sequences.csv", exports.SEQUENCE_COLUMNS, exports.sequence_rows(found))


def values_csv(request):
    points = Measurement.objects.filter(series__material__in=search_results(request)[1])
    return exports.csv_response("ffdb-values.csv", exports.VALUE_COLUMNS, exports.value_rows(points))


def property_values_csv(request, code):
    """Every value of one property. A code with no property gives a 404."""
    prop = get_object_or_404(Property, code=code)
    points = Measurement.objects.filter(series__property=prop)
    return exports.csv_response(f"ffdb-{prop.code}-values.csv", exports.VALUE_COLUMNS, exports.value_rows(points))


def structure(request, pk):
    """A material's structure drawing, as an SVG image of its own: 360 by 220 pixels,
    or 200 by 128 with ?size=thumb (Material.thumbnail_url). A mixture has no structure, so
    its address gives a 404, as a number with no material does."""
    material = get_object_or_404(Material, pk=pk, kind=Material.Kind.COMPOUND)
    width, height = (200, 128) if request.GET.get("size") == "thumb" else (360, 220)
    response = HttpResponse(
        chem.structure_svg(material.canonical_smiles, width, height), content_type="image/svg+xml"
    )
    # Browsers may keep the drawing for a day (86,400 seconds). Pages link to it through
    # Material.structure_url, which changes when the structure does.
    response["Cache-Control"] = "max-age=86400"
    return response


# Curators' pages. login_required sends someone who isn't logged in to the login page.
# permission_required refuses (403) anyone logged in without the permission; without
# raise_exception it would send them back to the login page instead. Adding a reference is
# what makes someone a curator here, because adding data starts from a reference. Superusers
# have every permission, so admins count as curators. base.html shows the Add data link on
# the same permission.


@login_required
@permission_required("repo.add_reference", raise_exception=True)
def add_data(request):
    """The Add data page: which reference is the data from? A reference already in FFDB goes
    straight on to its Add data page. A new one is added first, recorded as entered by this
    curator, with its details from Crossref for a DOI. Either way the curator is sent on with
    a redirect, so reloading the next page doesn't send the form again. The message says
    what happened, and the next page shows it (base.html)."""
    form = FindReferenceForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        kind, identifier = form.cleaned_data["kind"], form.cleaned_data["identifier"]
        # iexact, so a thesis typed in other capitals is found. DOIs and patent numbers are
        # already stored in one case.
        reference = Reference.objects.filter(identifier__iexact=identifier).first()
        # The label comes last in each message, because it can end in a full stop (Chem. Eur. J.).
        if reference:
            messages.info(request, f"Already in FFDB: {reference.label}")
        else:
            reference = Reference(kind=kind, identifier=identifier)
            reference.mark_entered(request.user)
            found = fill_from_crossref(reference) if reference.has_doi() else ""
            reference.save()
            messages.success(request, f"Added to FFDB: {reference.label}")
            if found:
                messages.info(request, found)
        return redirect("repo:add_from_reference", reference.pk)
    return render(request, "repo/add.html", {"nav": "add", "form": form})


def fill_from_crossref(reference):
    """Fill in a new reference's details and abstract from Crossref, before it is saved.
    Returns what happened, for the curator. If Crossref can't help, the reference is added
    without details, which fetch_reference_details can fill in later."""
    later = "Add its details in the admin, or run fetch_reference_details later."
    if not settings.CROSSREF_MAILTO:
        return f"Crossref wasn't asked, because CROSSREF_MAILTO isn't set. {later}"
    try:
        message = crossref.look_up(reference.identifier, settings.CROSSREF_MAILTO)
    except HTTPError as exc:  # before URLError, because it is a kind of URLError
        if exc.code == 404:
            return f"Crossref doesn't know this DOI, so it has no details. {later}"
        return f"Crossref answered with an error (HTTP {exc.code}), so it has no details. {later}"
    except (URLError, TimeoutError):
        return f"Crossref couldn't be reached, so it has no details. {later}"
    except (ValueError, KeyError):  # the answer wasn't JSON, or had no record in it
        return f"Crossref's answer couldn't be read, so it has no details. {later}"
    for field, value in crossref.details(message).items():
        setattr(reference, field, value)
    reference.abstract = crossref.abstract_text(message)
    return "Its details are from Crossref" + (
        ", with its abstract." if reference.abstract else ", which has no abstract for it."
    )


@login_required
@permission_required("repo.add_reference", raise_exception=True)
def add_from_reference(request, pk):
    """A reference's Add data page: a box for a table of its compounds, the forms for adding a
    mixture, a value or a curve, and what FFDB already has from it. Its materials are those it gives sequences or values for. A number with no
    reference gives a 404.

    Check reads the table and shows each line, saving nothing. Save (a POST with save in
    it) reads the table again and saves the lines without a problem. Then it sends the
    curator back here with a redirect, so reloading the page can't save twice. The messages
    say what was saved and list the lines that weren't, with their text, to fix and paste
    again."""
    reference = get_object_or_404(references_with_counts(), pk=pk)
    form = CompoundTableForm(request.POST if request.method == "POST" else None, reference=reference)
    if form.is_valid() and "save" in request.POST:
        counts, warnings = form.save(request.user)
        compounds, sequences, aliases = counts["compounds"], counts["sequences"], counts["aliases"]
        messages.success(
            request,
            f"Saved from the table: {compounds} new compound{pluralize(compounds)}, "
            f"{sequences} sequence{pluralize(sequences)}, {aliases} alias{pluralize(aliases, 'es')}.",
        )
        for warning in warnings:
            messages.warning(request, warning)
        for line in form.cleaned_data["table"]:
            if line.problem:
                messages.error(request, f"Line {line.number} not saved: {line.problem} The line: {line.text}")
        return redirect("repo:add_from_reference", reference.pk)
    # The lines Check read, or none: an unbound form, or one refused as a whole.
    lines = form.cleaned_data["table"] if form.is_valid() else []
    context = {
        "nav": "add",
        "reference": reference,
        "form": form,
        "lines": lines,
        # How many lines have each status, for the line above the table: {"new": 2, ...}.
        # A Counter gives 0 for a status no line has.
        "status_counts": Counter(line.status for line in lines),
        "materials": reference.materials(),
        # The first forms for adding a single value and a curve, sent to add_value and
        # add_curve. Both have a material box: auto_id gives the curve's boxes other ids, so
        # each label still points at its own box.
        "choice": ValueChoiceForm(reference=reference),
        "curve_choice": CurveChoiceForm(reference=reference, auto_id="id_curve_%s"),
        # The form for adding a mixture, sent to add_mixture.
        "mixture_form": MixtureForm(),
        **reference_tables(reference),
    }
    return render(request, "repo/add_from_reference.html", context)


@login_required
@permission_required("repo.add_reference", raise_exception=True)
def add_value(request, pk):
    """Adding a single value from a reference, in two forms. The first, ValueChoiceForm, is
    sent with GET, so this page's address holds its choices, e.g. ?material=12&property=ps&
    kind=measured&temperature=yes. Once they are valid the page also shows the second,
    ValueForm, which asks for what that property needs for that kind. It posts to this same
    address, so the choices come with it. Saving sends the curator back to the reference's
    Add data page with a redirect, so reloading can't save twice. A number with no reference
    gives a 404."""
    reference = get_object_or_404(Reference, pk=pk)
    # A visit with no choices has an empty request.GET, which counts as false, so the first
    # form is shown empty, without errors.
    choice = ValueChoiceForm(request.GET or None, reference=reference)
    form = None
    if choice.is_valid():
        data = request.POST if request.method == "POST" else None
        form = ValueForm(data, reference=reference, choice=choice.cleaned_data)
        if form.is_valid():
            series = form.save(request.user)
            messages.success(request, f"Saved a {series.property.symbol} value for {series.material.accession}.")
            return redirect("repo:add_from_reference", reference.pk)
    context = {"nav": "add", "reference": reference, "choice": choice, "form": form}
    return render(request, "repo/add_value.html", context)


@login_required
@permission_required("repo.add_reference", raise_exception=True)
def add_curve(request, pk):
    """Adding a curve from a reference, in two forms, as add_value() adds a single value. The
    first, CurveChoiceForm, also chooses what the curve is against, e.g. ?material=12&
    property=ps&kind=measured&variable=temperature. The second, CurveForm, has a box for the
    points. Check reads them and shows how many points were read, the lines that weren't, and
    the chart the curve will make, saving nothing. Save (a POST with save in it) saves the
    curve, but only once every line is a point. A number with no reference gives a 404."""
    reference = get_object_or_404(Reference, pk=pk)
    choice = CurveChoiceForm(request.GET or None, reference=reference)
    form, curve_charts = None, []
    if choice.is_valid():
        data = request.POST if request.method == "POST" else None
        form = CurveForm(data, reference=reference, choice=choice.cleaned_data)
        if form.is_valid() and "save" in request.POST:
            series = form.save(request.user)
            n = len(form.point_list)
            messages.success(
                request, f"Saved a {series.property.symbol} curve of {n} point{pluralize(n)} for {series.material.accession}."
            )
            return redirect("repo:add_from_reference", reference.pk)
        if form.point_list:
            # Drawn as the material page draws it, from the points read, even when some lines
            # weren't. chart() gives None for a curve whose points are all limits.
            drawn = charts.chart(form.prop, form.kind, form.variable, [(form.series, form.point_list)])
            curve_charts = [drawn] if drawn else []
    context = {"nav": "add", "reference": reference, "choice": choice, "form": form, "charts": curve_charts}
    return render(request, "repo/add_curve.html", context)


@login_required
@permission_required("repo.add_reference", raise_exception=True)
def add_mixture(request, pk):
    """Adding a mixture from a reference. The empty form is on the reference's Add data page
    and posts here. Check reads it and shows each component line with the compound it found,
    or why it found none, saving nothing. When mixtures with this name are in FFDB, the form
    lists them and asks whether this is one of them. Save (a POST with save in it) reads it
    again and adds the mixture, or nothing for one already in FFDB, and sends the curator back
    to the Add data page with a redirect, so reloading can't save twice. Either way the message
    gives the mixture's LCR ID. A number with no reference gives a 404."""
    reference = get_object_or_404(Reference, pk=pk)
    form = MixtureForm(request.POST if request.method == "POST" else None)
    if form.is_valid() and "save" in request.POST:
        mixture, added = form.save()
        if added:
            messages.success(request, f"Added the mixture {mixture.name} as {mixture.accession}.")
        else:
            messages.info(request, f"{mixture.name} is already in FFDB as {mixture.accession}, so nothing was added.")
        return redirect("repo:add_from_reference", reference.pk)
    context = {"nav": "add", "reference": reference, "form": form}
    return render(request, "repo/add_mixture.html", context)
