from django.shortcuts import get_object_or_404, render

from .models import Material


def material_detail(request, pk):
    """One material's public page. A number with no material gives a 404."""
    material = get_object_or_404(Material, pk=pk)
    return render(request, "repo/material_detail.html", {"material": material})
