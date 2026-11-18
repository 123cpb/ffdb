from django.contrib import admin, messages
from django.db import models
from django.forms import Textarea
from django.forms.models import BaseInlineFormSet
from django.utils.html import format_html

from .models import (
    Entered, Material, Measurement, Phase, PhaseSequence, Property, PropertyGroup, Reference, Series, Variable,
)

# The JSON fields hold a short list or a few key: value pairs, so a smaller box than the
# usual 10 rows.
SMALL_JSON_BOX = {models.JSONField: {"widget": Textarea(attrs={"rows": 2, "cols": 80})}}

# Who entered a row and when: shown, never typed in.
ENTERED = ["entered_by", "entered_at"]
# Filters by both, on the lists of models that record them. RelatedOnlyFieldListFilter offers
# only the users who have entered a row, not every account. Each filter also has a choice
# for blank, which means imported: "-" for entered by, "No date" for entered at.
ENTERED_FILTERS = [("entered_by", admin.RelatedOnlyFieldListFilter), "entered_at"]


class EnteredAdmin(admin.ModelAdmin):
    """An admin page that records who adds a row, and when (see Entered): the page's own
    row, and each new row in its inlines, such as the sequence rows on a material's page.
    Rows of models that don't record it are saved as usual. Changing a row records nothing."""

    def save_model(self, request, obj, form, change):
        if not change and isinstance(obj, Entered):
            obj.mark_entered(request.user)
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """Save one inline's rows. Django's own version calls formset.save(); these are the same
        steps written out, as Django's documentation shows, so new rows can be marked first."""
        rows = formset.save(commit=False)  # the new and changed rows, not yet saved
        for row in formset.deleted_objects:  # commit=False leaves the deleting to us too
            row.delete()
        for row in rows:
            if row.pk is None and isinstance(row, Entered):  # no primary key yet: a new row
                row.mark_entered(request.user)
            row.save()
        formset.save_m2m()


class PhaseSequenceInline(admin.TabularInline):
    """Phase sequences as rows on another admin page. The worked-out fields are shown,
    not edited: they are filled in from the sequence when the row is saved."""

    model = PhaseSequence
    extra = 1
    readonly_fields = ["standard_sequence", "phase_list", "t_clearing_c", "t_melting_c", *ENTERED]
    # How the sequence was observed, after the sequence itself in each row.
    observed = ["direction", "sequence", "rate_k_per_min", "method", "lowest_temperature_c", "notes"]

    @admin.display(description="phases")
    def phase_list(self, obj):
        if not obj.pk:
            return ""  # a new row has no phases until it is saved
        return ", ".join(phase.code for phase in obj.phases.all())


class MaterialSequenceInline(PhaseSequenceInline):
    """On a material's page: each row names its reference."""

    fields = ["reference", *PhaseSequenceInline.observed, *PhaseSequenceInline.readonly_fields]
    autocomplete_fields = ["reference"]


class ReferenceSequenceInline(PhaseSequenceInline):
    """On a reference's page: each row names its material."""

    fields = ["material", *PhaseSequenceInline.observed, *PhaseSequenceInline.readonly_fields]
    autocomplete_fields = ["material"]


@admin.register(Material)
class MaterialAdmin(EnteredAdmin):  # for its sequence rows: materials don't record it
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
class ReferenceAdmin(EnteredAdmin):
    list_display = ["label", "kind", "identifier", *ENTERED]
    list_filter = ENTERED_FILTERS
    # Also what the autocomplete box searches when a sequence's reference is chosen.
    search_fields = ["identifier", "first_author", "title"]
    readonly_fields = ENTERED
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


class PointFormSet(BaseInlineFormSet):
    """The point rows on a series' page, with a plainer message than Django's "Please
    submit at least 1 form." when a save would leave the series with no points."""

    default_error_messages = {"too_few_forms": "A series needs at least one point."}


class PointInline(admin.TabularInline):
    """A series' points, as rows on its page. Against temperature, each point gives its T;
    against another variable, its x and the T it was measured at. Each row is checked by
    Measurement.clean(), and its errors show on the row."""

    model = Measurement
    formset = PointFormSet
    extra = 1
    min_num = 1  # a series has at least one point
    fields = ["qualifier", "value", "uncertainty", "temperature_c", "x"]
    verbose_name = "point"
    verbose_name_plural = "points"

    def get_formset(self, request, obj=None, **kwargs):
        # validate_min makes min_num a rule. Without it the Add page still asks for its first
        # row, but ticking Delete on every point of a saved series would save.
        return super().get_formset(request, obj, validate_min=True, **kwargs)


@admin.register(Series)
class SeriesAdmin(EnteredAdmin):
    list_display = ["material", "property", "variable", "kind", "reference", "point_count", *ENTERED]
    list_filter = ["property", "kind", "variable", *ENTERED_FILTERS]
    # Fetch these with the series, in the same query, rather than one query per row.
    list_select_related = ["material", "property", "variable", "reference", "entered_by"]
    search_fields = ["material__name", "material__aliases", "reference__identifier", "reference__first_author", "method"]
    autocomplete_fields = ["material", "reference"]
    readonly_fields = ENTERED
    formfield_overrides = SMALL_JSON_BOX
    inlines = [PointInline]

    @admin.display(description="points")
    def point_count(self, obj):
        return obj.points.count()
