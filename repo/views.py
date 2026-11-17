from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from . import chem
from .models import Material, pk_from_accession


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
    context = {"query": query, "substructure": substructure, "error": error, "page": page, "search": search}
    return render(request, "repo/materials.html", context)


def material_detail(request, pk):
    """One material's public page. A number with no material gives a 404."""
    material = get_object_or_404(Material, pk=pk)
    formula, mass = chem.formula_and_mass(material.canonical_smiles)
    context = {"material": material, "formula": formula, "mass": mass}
    return render(request, "repo/material_detail.html", context)


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
