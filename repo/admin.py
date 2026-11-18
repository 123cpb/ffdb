from django.contrib import admin, messages
from django.db import models
from django.forms import Textarea
from django.utils.html import format_html

from .models import (
    Material, Measurement, Phase, PhaseSequence, Property, PropertyGroup, Reference, Series, Variable,
)

# The JSON fields hold a short list or a few key: value pairs, so a smaller box than the
# usual 10 rows.
SMALL_JSON_BOX = {models.JSONField: {"widget": Textarea(attrs={"rows": 2, "cols": 80})}}


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


@admin.register(PropertyGroup)
class PropertyGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "code"]


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ["symbol", "name", "group", "unit", "required_conditions", "sort_order"]
    list_filter = ["group"]
    formfield_overrides = SMALL_JSON_BOX


@admin.register(Variable)
class VariableAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "unit", "scale", "condition", "sort_order"]


class PointInline(admin.TabularInline):
    """A series' points, as rows on its page. Against temperature, each point gives its T;
    against another variable, its x and the T it was measured at."""

    model = Measurement
    extra = 1
    fields = ["qualifier", "value", "uncertainty", "temperature_c", "x"]
    verbose_name = "point"
    verbose_name_plural = "points"


@admin.register(Series)
class SeriesAdmin(admin.ModelAdmin):
    list_display = ["material", "property", "variable", "kind", "reference", "point_count"]
    list_filter = ["property", "kind", "variable"]
    # Fetch these with the series, in the same query, rather than one query per row.
    list_select_related = ["material", "property", "variable", "reference"]
    search_fields = ["material__name", "material__aliases", "reference__identifier", "reference__first_author", "method"]
    autocomplete_fields = ["material", "reference"]
    formfield_overrides = SMALL_JSON_BOX
    inlines = [PointInline]

    @admin.display(description="points")
    def point_count(self, obj):
        return obj.points.count()
