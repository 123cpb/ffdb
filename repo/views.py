from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from . import chem
from .models import Material


def material_detail(request, pk):
    """One material's public page. A number with no material gives a 404."""
    material = get_object_or_404(Material, pk=pk)
    formula, mass = chem.formula_and_mass(material.canonical_smiles)
    context = {"material": material, "formula": formula, "mass": mass}
    return render(request, "repo/material_detail.html", context)


def structure(request, pk):
    """A material's structure drawing, as an SVG image of its own."""
    material = get_object_or_404(Material, pk=pk)
    response = HttpResponse(chem.structure_svg(material.canonical_smiles), content_type="image/svg+xml")
    # Browsers may keep the drawing for a day (86,400 seconds). Pages link to it through
    # Material.structure_url, which changes when the structure does.
    response["Cache-Control"] = "max-age=86400"
    return response
