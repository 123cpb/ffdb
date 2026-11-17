from django.contrib import admin, messages

from .models import Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["accession", "name"]
    fields = ["name", "aliases", "smiles", "canonical_smiles", "inchikey", "notes"]
    readonly_fields = ["canonical_smiles", "inchikey"]

    @admin.display(description="ID", ordering="pk")
    def accession(self, obj):
        return obj.accession

    def save_model(self, request, obj, form, change):
        """Save, then warn if another material has the same connectivity."""
        super().save_model(request, obj, form, change)
        variants = obj.possible_stereo_variants()
        if variants:
            others = ", ".join(f"{v.accession} ({v.name})" for v in variants)
            self.message_user(
                request,
                f"{obj.accession} has the same connectivity as {others} but different "
                "stereochemistry. Both are kept as separate materials: check that is right, "
                "and say in the name or notes whether a structure without stereochemistry "
                "is racemic or simply not stated.",
                messages.WARNING,
            )
