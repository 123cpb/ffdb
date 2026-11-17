from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["accession", "name"]
    fields = ["name", "aliases", "smiles", "structure", "canonical_smiles", "inchikey", "notes"]
    readonly_fields = ["structure", "canonical_smiles", "inchikey"]

    @admin.display(description="ID", ordering="pk")
    def accession(self, obj):
        return obj.accession

    @admin.display(description="structure")
    def structure(self, obj):
        if not obj.pk:
            return "Shown after saving."
        return format_html(
            '<img src="{}" alt="Structure of {}" width="360" height="220">',
            obj.structure_url,
            obj.name,
        )

    def save_model(self, request, obj, form, change):
        """Save, then warn if another material has the same connectivity."""
        super().save_model(request, obj, form, change)
        warning = obj.stereo_warning()
        if warning:
            self.message_user(request, warning, messages.WARNING)
