from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from . import chem
from .models import Material, pk_from_accession


def find_materials(text):
    """The materials a search finds, in LCR ID order. Blank text finds them all.

    A material is found when the text is part of its name or an alias (ignoring case),
    is its InChIKey (ignoring case), or is its LCR ID.
    """
    materials = Material.objects.order_by("pk")
    text = text.strip()
    if not text:
        return materials
    found = Q(name__icontains=text) | Q(aliases__icontains=text) | Q(inchikey__iexact=text)
    pk = pk_from_accession(text)
    if pk is not None:
        found |= Q(pk=pk)
    return materials.filter(found)


def materials(request):
    """The Materials page, and for now the home page: every material, or those that ?q=
    finds, 25 to a page. get_page() turns a missing, bad or too-high ?page= into a real page."""
    query = request.GET.get("q", "").strip()
    page = Paginator(find_materials(query), 25).get_page(request.GET.get("page"))
    return render(request, "repo/materials.html", {"query": query, "page": page})


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
