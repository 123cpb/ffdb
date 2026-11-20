import math
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.views.decorators.http import require_POST

from . import charts, chem, crossref, exports, ribbons
from .forms import (
    MAX_CARDS, CompoundCard, CompoundCards, CompoundTableForm, CurveChoiceForm, CurveForm, FindReferenceForm,
    MixtureForm, ValueChoiceForm, ValueForm,
)
from .models import (
    DOI_PREFIX,
    Material,
    Measurement,
    Phase,
    PhaseSequence,
    PhaseTransition,
    Property,
    Reference,
    RetiredID,
    Series,
    group_properties,
    pk_from_accession,
)


def matching_sequences(phase="", t_min=None, t_max=None):
    """The phase sequences the search's phase and temperature boxes find, or None when all
    three are blank. find_materials() lists the materials that have one, and the Materials list
    draws one of them in each row (ribbon_sequence()), so the two can't disagree.

    A sequence is found when it has the phase (a code, e.g. NF). With t_min or t_max (°C;
    either can be None), the phase must form on cooling in that range, ends included: the
    sequence has a transition into it there. With no phase, a transition into any phase counts.
    The transitions are stored the same way whatever the sequence's direction, so a heating
    run's counts too: NF giving way to N on heating at 132.7 °C counts as NF forming at 132.7 °C.
    """
    if t_min is None and t_max is None:
        return PhaseSequence.objects.filter(phases__code=phase) if phase else None
    # Find the transitions first. Every condition is on the one table, so each filter() narrows
    # the same rows, and a transition has to meet them all: NF forming at 120 °C, not NF
    # somewhere and some other phase forming at 120 °C.
    transitions = PhaseTransition.objects.all()
    if phase:
        transitions = transitions.filter(lower__code=phase)
    if t_min is not None:
        transitions = transitions.filter(temperature_c__gte=t_min)
    if t_max is not None:
        transitions = transitions.filter(temperature_c__lte=t_max)
    return PhaseSequence.objects.filter(transitions__in=transitions)


def find_materials(text, substructure="", phase="", t_min=None, t_max=None):
    """The materials a search finds, in LCR ID order. A blank search finds them all.

    A material is found when the text is part of its name or an alias (ignoring case),
    is its InChIKey (ignoring case), or is its LCR ID; when at least one of its sequences
    meets the phase and temperature boxes (matching_sequences()); and when its structure
    contains the substructure (SMILES or SMARTS). A substructure finds compounds only,
    because a mixture has no structure. Raises chem.StructureError when RDKit can't read
    the substructure.
    """
    materials = Material.objects.order_by("pk")
    text = text.strip()
    if text:
        found = Q(name__icontains=text) | Q(aliases__icontains=text) | Q(inchikey__iexact=text)
        pk = pk_from_accession(text)
        if pk is not None:
            found |= Q(pk=pk)
        materials = materials.filter(found)
    sequences = matching_sequences(phase, t_min, t_max)
    if sequences is not None:
        # The database follows Material -> its sequences. That gives one row per matching
        # sequence, so a material with two would come back twice: distinct() keeps one. This
        # comes before the substructure, so RDKit checks fewer structures.
        materials = materials.filter(phase_sequences__in=sequences).distinct()
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
    download holds the materials the page lists. Returns (terms, found, matched, error).

    terms is what ?q=, ?sub=, ?phase=, ?tmin= and ?tmax= hold, as typed, in that order. A
    ?phase= that isn't in the vocabulary is ignored, and so is a ?tmin= or ?tmax= that isn't
    a number. found is what find_materials() finds, and matched what matching_sequences()
    finds, for the ribbons. A substructure RDKit can't read finds nothing, and error says why;
    otherwise error is ""."""
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
    matched = matching_sequences(phase, t_min, t_max)
    try:
        return terms, find_materials(terms["q"], terms["sub"], phase, t_min, t_max), matched, ""
    except chem.StructureError as exc:
        return terms, Material.objects.none(), matched, str(exc)


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


# The Materials list's ribbons are narrower than a material page's (ribbons.ribbon_svg()), to
# leave room for the drawing beside them.
LIST_RIBBON_WIDTH = 600


def ribbon_label(sequence):
    """What a screen reader says for a sequence's ribbon: the sequence, how it was observed
    and its reference, "Cr 139.8 NF 132.7 N 187.9 Iso (°C), Summary, Mandle 2017, Chem. Eur. J."."""
    return f"{sequence.standard_sequence} (°C), {sequence.run_text()}, {sequence.reference.label}"


def ribbon_sequence(sequences, preferred=frozenset()):
    """The one sequence the Materials list draws for a material, from its sequences, or None
    when it has none. First a sequence the search found (preferred, a set of their primary
    keys), so a search for NF forming at 100 to 110 °C shows the run with NF forming there.
    Then anything but a heating run, because a phase seen only on cooling (monotropic) doesn't
    show on heating. Then the first entered: the lowest primary key.

    min() compares the keys in order, and False comes before True."""
    return min(
        sequences,
        key=lambda s: (s.pk not in preferred, s.direction == PhaseSequence.Direction.HEATING, s.pk),
        default=None,
    )


def list_ribbons(materials, preferred=frozenset()):
    """Each material with its ribbon (ribbon_sequence()), or None when it has no sequence, all
    on one temperature axis: ([(material, ribbon), ...], axis). The axis is "" when no material
    has a ribbon. Pass materials with their sequences prefetched, with each sequence's reference
    and transitions and the phases either side, or each row costs queries."""
    chosen = [(material, ribbon_sequence(material.phase_sequences.all(), preferred)) for material in materials]
    data = {sequence.pk: ribbons.ribbon_data(sequence) for _, sequence in chosen if sequence}
    lo, hi = ribbons.temperature_range(data.values())
    rows = [
        (
            material,
            ribbons.ribbon_svg(
                data[sequence.pk], lo, hi, label=ribbon_label(sequence),
                width=LIST_RIBBON_WIDTH, height=22, temperatures=False, block_ends=True,
            ) if sequence else None,
        )
        for material, sequence in chosen
    ]
    return rows, ribbons.axis_svg(lo, hi, width=LIST_RIBBON_WIDTH) if data else ""


def materials(request):
    """The Materials page, and for now the home page: every material, or those that the
    search finds (search_results()), 25 to a page, in the order ?sort= chooses
    (MATERIAL_SORTS), each with a ribbon (list_ribbons()). get_page() turns a missing, bad or
    too-high ?page= into a real page. A substructure RDKit can't read lists nothing and says why."""
    terms, found, matched, error = search_results(request)
    sort = sort_param(request, MATERIAL_SORTS)
    # order_by() replaces the LCR ID order that find_materials() gives. The downloads keep that
    # order, because they use search_results() themselves, so a file is the same whichever
    # order the page shows. prefetch_related() fetches the sequences of the page's 25 materials
    # in one query, and their references, transitions and phases in one query each, for the
    # ribbons, instead of queries for every row.
    found = found.order_by(*MATERIAL_SORTS[sort]).prefetch_related(
        "phase_sequences__reference", "phase_sequences__transitions__upper", "phase_sequences__transitions__lower"
    )
    page = Paginator(found, 25).get_page(request.GET.get("page"))
    preferred = set()
    if matched is not None:
        # The sequences the search found, of this page's materials only: one more query.
        preferred = set(matched.filter(material__in=[m.pk for m in page]).values_list("pk", flat=True))
    rows, axis = list_ribbons(page, preferred)
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
        "rows": rows,
        "axis": axis,
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


def sequence_ribbons(sequences, data=None):
    """Each sequence with its ribbon, all on one temperature axis, for the material page's
    rows: ([(sequence, ribbon), ...], axis). A screen reader reads each ribbon as its
    sequence, how it was observed and its reference (ribbon_label()). data is each sequence's
    ribbons.RibbonData when they aren't saved, as for a card's runs being checked; otherwise it
    is worked out from their stored transitions."""
    if data is None:
        data = [ribbons.ribbon_data(sequence) for sequence in sequences]
    lo, hi = ribbons.temperature_range(data)
    rows = [
        (sequence, ribbons.ribbon_svg(one, lo, hi, label=ribbon_label(sequence)))
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
    again.

    Make cards (a POST with cards in it) is the page without JavaScript's: with cards.js,
    make_cards() answers it. Once every line reads, the page comes back with a card for each
    line in place of its cards, nothing saved, and the table emptied, as the cards hold its
    lines now. Otherwise it comes back with no cards made, and why, above the table's lines."""
    reference = get_object_or_404(references_with_counts(), pk=pk)
    form = CompoundTableForm(request.POST if request.method == "POST" else None, reference=reference)
    # A page of one empty compound card, sent to add_compound, unless the table makes cards.
    cards, cards_problems = CompoundCards(None, reference), []
    if "cards" in request.POST:
        try:
            cards = cards_from_table(form)
        except ValidationError as exc:
            cards_problems = exc.messages
        else:
            n = cards.total
            messages.success(
                request, f"Made {n} card{pluralize(n)} from the table: add their values, then Check and Save the cards."
            )
            form = CompoundTableForm(reference=reference)
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
    context = {
        "nav": "add",
        "reference": reference,
        "form": form,
        **table_reading(form),
        "cards_problems": cards_problems,
        "materials": reference.materials(),
        # The first forms for adding a single value and a curve, sent to add_value and
        # add_curve. Both have a material box: auto_id gives the curve's boxes other ids, so
        # each label still points at its own box.
        "choice": ValueChoiceForm(reference=reference),
        "curve_choice": CurveChoiceForm(reference=reference, auto_id="id_curve_%s"),
        # The form for adding a mixture, sent to add_mixture.
        "mixture_form": MixtureForm(),
        "cards": cards,
        **reference_tables(reference),
    }
    return render(request, "repo/add_from_reference.html", context)


def table_reading(form):
    """What a compound table's Check shows (_table_lines.html), from its CompoundTableForm: the
    lines read, or none for an unbound form or one refused as a whole, and how many lines have
    each status, for the line above the table: {"new": 2, ...}. A Counter gives 0 for a status
    no line has. The Add data page and the table's check as the curator types both use it."""
    lines = form.cleaned_data["table"] if form.is_valid() else []
    return {"lines": lines, "status_counts": Counter(line.status for line in lines)}


def show_runs(cards):
    """Keep what Check read of each card's runs on it, on an axis of the card's own, for
    _card_runs_result.html. Ask once each card has been checked (CompoundCard.is_valid())."""
    for card in cards:
        card.sequence_rows, card.axis = sequence_ribbons(card.runs, card.ribbon_data)


def cards_from_table(form, start=0):
    """The page of compound cards a pasted table makes (a CompoundTableForm, form): a card for each
    line, from index start, each as Check shows it, with any problem it would have at Save, such
    as a card with no run yet. Raises ValidationError, saying why, when the table can't make
    them (CompoundTableForm.cards_data())."""
    cards = CompoundCards(form.cards_data(start), form.reference)
    made = cards.cards[start:]
    for card in made:
        card.is_valid()
    show_runs(made)
    return cards


# Checks as the curator types (checks.js). Each reads one part of a form with the code its Check
# uses, and sends back that part as Check shows it, from the same template, with any problems
# listed first (_check_answer.html). The script puts the answer in the page in place of the last
# one. Curators only, as their pages are. POST only, as the forms are, so each takes the form's
# CSRF token, which the script sends with the rest of the form. Nothing is saved. One more,
# check_block_boxes(), sends back boxes rather than an answer: those a block's choice needs. A
# card's checks are sent every card on the page, and read only the one their address names.


def check_answer(request, problem_forms, result, context):
    """The answer: the problems of each (title, form) in problem_forms, then the template result
    with context."""
    return render(request, "repo/_check_answer.html", {"problem_forms": problem_forms, "result": result, **context})


def number_param(request, key, limit):
    """The index in ?key= of a check's address, e.g. ?card=2: a whole number from 0 to below
    limit. Anything else gives a 404. A card or block copied by cards.js has its index only once
    copied, so its address carries it here rather than in the path, which {% url %} writes."""
    try:
        index = int(request.GET.get(key, ""))
    except ValueError:
        raise Http404(f"No {key} number.")
    if not 0 <= index < limit:
        raise Http404(f"No {key} with this number.")
    return index


def card_sent(request, pk):
    """Card ?card= (its index from 0, as in its prefix, card-0) of the compound cards sent, built
    as views.add_compound builds it: only that card is read."""
    reference = get_object_or_404(Reference, pk=pk)
    return CompoundCard(request.POST, reference, number_param(request, "card", MAX_CARDS))


def checked_card(request, pk):
    """Card ?card= of the compound cards sent, read exactly as views.add_compound's Check reads
    it."""
    card = card_sent(request, pk)
    card.is_valid()
    return card


@login_required
@permission_required("repo.add_reference", raise_exception=True)
@require_POST
def check_card_material(request, pk):
    """What the server makes of a compound card's material: for a new mixture, with the problems
    of its own boxes too. With all its boxes empty the answer is empty, rather than a message
    asking for one of them."""
    form = checked_card(request, pk).material_form
    if not form.has_changed():
        return HttpResponse()
    problem_forms = [("", form), *([("", form.mixture)] if form.is_mixture else [])]
    return check_answer(request, problem_forms, "repo/_card_material_result.html", {"form": form})


@login_required
@permission_required("repo.add_reference", raise_exception=True)
@require_POST
def check_card_runs(request, pk):
    """What the server makes of a compound card's runs: those that read, on one axis, and each
    other run's problems under its number. A run left as the page shows it is skipped, as Check
    skips it, so with none filled in the answer is empty."""
    card = checked_card(request, pk)
    sequence_rows, axis = sequence_ribbons(card.runs, card.ribbon_data)
    # A run's number is its index + 1, as its heading says. A removed run is hidden, so its
    # problems aren't listed.
    runs = [
        (f"Run {number}", form) for number, form in enumerate(card.run_forms, start=1)
        if not card.run_forms.removed(form)
    ]
    return check_answer(
        request, runs, "repo/_card_runs_result.html", {"sequence_rows": sequence_rows, "axis": axis}
    )


@login_required
@permission_required("repo.add_reference", raise_exception=True)
@require_POST
def check_table(request, pk):
    """What the server makes of a compound table, as its Check shows it. With the table empty
    the answer is empty, rather than a message asking for a line."""
    form = CompoundTableForm(request.POST, reference=get_object_or_404(Reference, pk=pk))
    if not request.POST.get("table", "").strip():
        return HttpResponse()
    return check_answer(request, [("", form)], "repo/_table_lines.html", {"form": form, **table_reading(form)})


@login_required
@permission_required("repo.add_reference", raise_exception=True)
@require_POST
def make_cards(request, pk):
    """Make cards without a reload (cards.js): the compound table is sent as Check sends it, and
    ?start= is the index the new cards start at, after the cards on the page. The answer is the
    new cards, each as Check shows it, to go in the page. Nothing is saved. A table that can't
    make them gets the table's answer as Check shows it, with why no card was made, and status
    422 (Unprocessable Content: the request is understood, but can't be done), so the script can
    tell the two apart."""
    reference = get_object_or_404(Reference, pk=pk)
    form = CompoundTableForm(request.POST, reference=reference)
    start = number_param(request, "start", MAX_CARDS + 1)  # a page already full gets cards_data()'s message
    try:
        cards = cards_from_table(form, start)
    except ValidationError as exc:
        context = {"form": form, **table_reading(form), "cards_problems": exc.messages}
        response = check_answer(request, [("", form)], "repo/_table_lines.html", context)
        response.status_code = 422
        return response
    return render(request, "repo/_made_cards.html", {"reference": reference, "cards": cards.cards[start:]})


def checked_block(request, pk):
    """Block ?block= (its index from 0, as in its prefix, card-0-block-0) of card ?card= of the
    compound cards sent, built exactly as views.add_compound's Check builds it, with its index. It
    is checked when something asks for its errors. A block number the card hasn't got gives a
    404."""
    blocks = card_sent(request, pk).block_forms.forms
    index = number_param(request, "block", len(blocks))
    return index, blocks[index]


@login_required
@permission_required("repo.add_reference", raise_exception=True)
@require_POST
def check_block(request, pk):
    """What the server makes of one block of values on a compound card: its problems, then what
    its lines read as, with a curve's chart, from the template Check shows them with. A block
    left empty gives an empty answer, as Check skips it."""
    index, form = checked_block(request, pk)
    if not form.has_changed():
        return HttpResponse()
    return check_answer(request, [(f"Values {index + 1}", form)], "repo/_curve_reading.html", {"form": form})


@login_required
@permission_required("repo.add_reference", raise_exception=True)
@require_POST
def check_block_boxes(request, pk):
    """The boxes one block of values needs for its property, kind and what it is against, with
    what was typed in them, as Check draws them (_block_boxes.html). checks.js puts them in place
    of the block's boxes when the choice changes, and drops what Check would say at them: the
    block's answer (check_block()) lists its problems."""
    _, form = checked_block(request, pk)
    return render(request, "repo/_block_boxes.html", {"form": form})


def card_saved(request, card, material, added, kept):
    """Say what Save added from one compound card, leaving out a part the card didn't have:
    "Added 10a as LCR-00512, with 1 run and 2 blocks of values." The arguments are what
    CompoundCards.save() gives for the card."""
    parts = []
    if card.runs:
        parts.append(f"{len(card.runs)} run{pluralize(len(card.runs))}")
    if card.blocks:
        parts.append(f"{len(card.blocks)} block{pluralize(len(card.blocks))} of values")
    saved = " and ".join(parts)
    if added:
        messages.success(request, f"Added {material.name} as {material.accession}, with {saved}.")
        # Asked again now it is saved, so the warning names it by its LCR ID.
        warning = material.stereo_warning()
        if warning:
            messages.warning(request, warning)
    else:
        alias = f" {card.material_form.cleaned_data['name']} is kept as one of its aliases." if kept else ""
        messages.success(request, f"Saved {saved} for {material}.{alias}")


@login_required
@permission_required("repo.add_reference", raise_exception=True)
def add_compound(request, pk):
    """Adding compound cards from a reference: on each, a material, in FFDB or new, its runs and
    its blocks of values. A page of one empty card is on the reference's Add data page and posts
    here, with every card the curator added (cards.js). Check reads every card filled in and shows
    what Save would add: each material, and each run that reads with its standard sequence and
    ribbon, as the material's page will show them, and each problem on its card, saving nothing.
    Save (a POST with save in it) reads them again and adds every card, in one transaction, or
    nothing while any card has a problem, and sends the curator back to the Add data page with a
    redirect, so reloading can't save twice. A number with no reference gives a 404."""
    reference = get_object_or_404(Reference, pk=pk)
    cards = CompoundCards(request.POST if request.method == "POST" else None, reference)
    if cards.is_valid() and "save" in request.POST:
        for saved in cards.save(request.user):
            card_saved(request, *saved)
        return redirect("repo:add_from_reference", reference.pk)
    show_runs(cards.cards)
    context = {"nav": "add", "reference": reference, "cards": cards}
    return render(request, "repo/add_compound.html", context)


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
    form = None
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
    # What Check read, and the chart (form.curve_charts()), are in _curve_reading.html.
    context = {"nav": "add", "reference": reference, "choice": choice, "form": form}
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
