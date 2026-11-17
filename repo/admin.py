from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Material, Phase, PhaseSequence, Reference


class PhaseSequenceInline(admin.TabularInline):
    """Phase sequences as rows on another admin page. The worked-out fields are shown,
    not edited: they are filled in from the sequence when the row is saved."""

    model = PhaseSequence
    extra = 1
    readonly_fields = ["standard_sequence", "phase_list", "t_clearing_c", "t_melting_c"]

    @admin.display(description="phases")
    def phase_list(self, obj):
        if not obj.pk:
            return ""  # a new row has no phases until it is saved
        return ", ".join(phase.code for phase in obj.phases.all())


class MaterialSequenceInline(PhaseSequenceInline):
    """On a material's page: each row names its reference."""

    fields = ["reference", "sequence", "notes", *PhaseSequenceInline.readonly_fields]
    autocomplete_fields = ["reference"]


class ReferenceSequenceInline(PhaseSequenceInline):
    """On a reference's page: each row names its material."""

    fields = ["material", "sequence", "notes", *PhaseSequenceInline.readonly_fields]
    autocomplete_fields = ["material"]


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["accession", "name"]
    # Also what the autocomplete box searches when a sequence's material is chosen.
    search_fields = ["name", "aliases", "inchikey"]
    fields = ["name", "aliases", "smiles", "structure", "canonical_smiles", "inchikey", "notes"]
    readonly_fields = ["structure", "canonical_smiles", "inchikey"]
    inlines = [MaterialSequenceInline]

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


@admin.register(Phase)
class PhaseAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "polar", "synonyms", "sort_order"]


@admin.register(Reference)
class ReferenceAdmin(admin.ModelAdmin):
    list_display = ["label", "kind", "identifier"]
    # Also what the autocomplete box searches when a sequence's reference is chosen.
    search_fields = ["identifier", "first_author", "title"]
    inlines = [ReferenceSequenceInline]
