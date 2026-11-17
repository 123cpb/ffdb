from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from . import chem
from .models import DOI_PREFIX, Material, Reference, pk_from_accession


def find_materials(text, substructure=""):
    """The materials a search finds, in LCR ID order. A blank search finds them all.

    A material is found when the text is part of its name or an alias (ignoring case),
    is its InChIKey (ignoring case), or is its LCR ID, and when its structure contains
    the substructure (SMILES or SMARTS). Raises chem.StructureError when RDKit can't
    read the substructure.
    """
    materials = Material.objects.order_by("pk")
    text = text.strip()
    if text:
        found = Q(name__icontains=text) | Q(aliases__icontains=text) | Q(inchikey__iexact=text)
        pk = pk_from_accession(text)
        if pk is not None:
            found |= Q(pk=pk)
        materials = materials.filter(found)
    if substructure.strip():
        pattern = chem.substructure_pattern(substructure)
        # The database can't compare structures, so RDKit checks each material found so far.
        rows = materials.values_list("pk", "canonical_smiles")
        matches = [pk for pk, smiles in rows if chem.has_substructure(smiles, pattern)]
        materials = materials.filter(pk__in=matches)
    return materials


def materials(request):
    """The Materials page, and for now the home page: every material, or those that ?q=
    and ?sub= find, 25 to a page. get_page() turns a missing, bad or too-high ?page= into
    a real page. A substructure RDKit can't read lists nothing and says why."""
    query = request.GET.get("q", "").strip()
    substructure = request.GET.get("sub", "").strip()
    error = ""
    try:
        found = find_materials(query, substructure)
    except chem.StructureError as exc:
        found, error = Material.objects.none(), str(exc)
    page = Paginator(found, 25).get_page(request.GET.get("page"))
    # The search as it goes in an address, so the page links keep it. urlencode() turns
    # characters such as # into %23: a bare # would end the address there.
    search = urlencode({key: value for key, value in [("q", query), ("sub", substructure)] if value})
    context = {
        "nav": "materials",  # which link base.html marks as the current section
        "query": query,
        "substructure": substructure,
        "error": error,
        "page": page,
        "search": search,
    }
    return render(request, "repo/materials.html", context)


def material_detail(request, pk):
    """One material's public page. A number with no material gives a 404."""
    material = get_object_or_404(Material, pk=pk)
    formula, mass = chem.formula_and_mass(material.canonical_smiles)
    context = {
        "nav": "materials",
        "material": material,
        "formula": formula,
        "mass": mass,
        # select_related() fetches each sequence's reference in the same query, instead of
        # one more query for every row the template shows.
        "sequences": material.phase_sequences.select_related("reference"),
        "lc_phases": material.liquid_crystal_phases(),
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
    """One reference's public page: its details and link, and the materials it gives
    sequences for, with those sequences. A number with no reference gives a 404."""
    reference = get_object_or_404(references_with_counts(), pk=pk)
    context = {
        "nav": "references",
        "reference": reference,
        # In LCR ID order, as on the Materials page. select_related() fetches each row's
        # material in the same query.
        "sequences": reference.phase_sequences.select_related("material").order_by("material_id", "id"),
    }
    return render(request, "repo/reference_detail.html", context)


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
