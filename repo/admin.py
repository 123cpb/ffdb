import base64

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.forms import Textarea
from django.forms.models import BaseInlineFormSet
from django.shortcuts import get_object_or_404, redirect
from django.template.defaultfilters import pluralize
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from . import chem
from .forms import MergeForm
from .models import (
    Entered, Material, Measurement, MixtureComponent, Phase, PhaseSequence, Property, PropertyGroup, Reference,
    RetiredID, Series, Variable, accession, is_admin,
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


class NewRowsTicked:
    """For an admin page or inline whose new rows start with some switches on: the fields named
    in new_rows_ticked, whose model default is off. Django builds a new row's form without a
    row to read, so the box shows this initial value, ticked; a saved row's form shows the row's
    own. An admin can untick it before saving."""

    new_rows_ticked = []

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.new_rows_ticked:
            kwargs["initial"] = True
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class PhaseSequenceInline(NewRowsTicked, admin.TabularInline):
    """Phase sequences as rows on another admin page. The worked-out fields are shown,
    not edited: they are filled in from the sequence when the row is saved. A sequence added
    here is approved unless unticked: an admin's own rows need no check."""

    model = PhaseSequence
    extra = 1
    new_rows_ticked = ["approved"]
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

    fields = ["reference", *PhaseSequenceInline.observed, "approved", *PhaseSequenceInline.readonly_fields]
    autocomplete_fields = ["reference"]


class ReferenceSequenceInline(PhaseSequenceInline):
    """On a reference's page: each row names its material."""

    fields = ["material", *PhaseSequenceInline.observed, "approved", *PhaseSequenceInline.readonly_fields]
    autocomplete_fields = ["material"]


class ComponentFormSet(BaseInlineFormSet):
    """A mixture's component rows, checked together with what the page says of its
    composition (Material.composition_problems()). Each row has been checked on its own
    first, by MixtureComponent.clean()."""

    def clean(self):
        super().clean()  # Django's own checks, including each compound once in the mixture
        # The material with the values just typed on its page: Django checks the material's
        # form before its rows.
        mixture = self.instance
        rows = [form for form in self.forms if not self._should_delete_form(form)]
        # A compound's rows are refused one by one. A row with errors shows those first.
        if mixture.kind != Material.Kind.MIXTURE or any(form.errors for form in rows):
            return
        # An empty extra row has no cleaned data, so it is left out.
        fractions = [(form.cleaned_data["fraction"], form.cleaned_data["unit"]) for form in rows if form.cleaned_data]
        problems = mixture.composition_problems(fractions)
        if problems:
            raise ValidationError(problems)


class ComponentInline(admin.TabularInline):
    """A mixture's components that are compounds in FFDB, as rows beneath it. Others are
    described in its Other components box."""

    model = MixtureComponent
    fk_name = "mixture"  # component points to Material too, so say which is the row's material
    formset = ComponentFormSet
    fields = ["component", "fraction", "unit"]
    autocomplete_fields = ["component"]
    verbose_name = "component in FFDB"
    verbose_name_plural = "components in FFDB"

    def get_extra(self, request, obj=None, **kwargs):
        """Two empty rows for a mixture, or a material being added, and none for a compound.
        A compound's page still sends its rows, none, so making it a mixture is checked too."""
        return 0 if obj is not None and obj.kind == Material.Kind.COMPOUND else 2


@admin.register(Material)
class MaterialAdmin(EnteredAdmin):  # for its sequence rows: materials don't record it
    list_display = ["accession", "name", "kind", "listed", "is_public"]
    list_filter = ["kind", "listed"]
    # Also what the autocomplete box searches when a sequence's material, or a mixture's
    # component, is chosen.
    search_fields = ["name", "aliases", "inchikey"]
    fields = [
        "kind", "name", "aliases", "smiles", "structure", "canonical_smiles", "inchikey",
        "composition_status", "other_components", "notes", "listed",
    ]
    readonly_fields = ["structure", "canonical_smiles", "inchikey"]
    inlines = [ComponentInline, MaterialSequenceInline]

    @admin.display(description="ID", ordering="pk")
    def accession(self, obj):
        return obj.accession

    @admin.display(description="public", boolean=True)
    def is_public(self, obj):
        """The rule's answer (MaterialQuerySet.public()), worked out the same way as on the public
        pages: one query a row, as SeriesAdmin's point count is."""
        return obj.is_public()

    @admin.display(description="structure")
    def structure(self, obj):
        """The drawing, carried in the page itself as a data: address rather than linked to
        /material/<n>/structure.svg, which answers 404 for a material that isn't public, so
        admins see hidden structures too. Inside an <img>, an SVG can't run anything."""
        if not obj.pk:
            return "Shown after saving."
        if obj.kind == Material.Kind.MIXTURE:
            return "None: a mixture has no structure."
        svg = base64.b64encode(chem.structure_svg(obj.canonical_smiles).encode()).decode()
        return format_html(
            '<img src="data:image/svg+xml;base64,{}" alt="Structure of {}" width="360" height="220">',
            svg,
            obj.name,
        )

    def save_model(self, request, obj, form, change):
        """Save, then warn if another material has the same connectivity."""
        super().save_model(request, obj, form, change)
        warning = obj.stereo_warning()
        if warning:
            self.message_user(request, warning, messages.WARNING)

    def delete_model(self, request, obj):
        """The Delete button: retire the material rather than only delete it (Material.retire())."""
        obj.retire(request.user)

    def delete_queryset(self, request, queryset):
        """Delete selected: each material retired as on its own page, all of them or none."""
        with transaction.atomic():
            for material in queryset:
                material.retire(request.user)

    def get_urls(self):
        """Django's addresses for materials, with a material's merge/ in front, as for
        RetiredIDAdmin's restore/. The link to it is in this model's own change_form.html."""
        merge = self.admin_site.admin_view(self.merge_view)
        return [path("<int:pk>/merge/", merge, name="repo_material_merge"), *super().get_urls()]

    def merge_view(self, request, pk):
        """Merge a duplicate into another material (Material.merge_into()). The ID box is sent
        with GET, which changes nothing, so the page can show what will move first. Its Merge
        button posts to the same address, with the ID, and the merge is checked again then.
        Only admins may merge them (is_admin())."""
        if not is_admin(request.user):
            raise PermissionDenied
        duplicate = get_object_or_404(Material, pk=pk)
        data = request.POST if request.method == "POST" else (request.GET or None)
        form = MergeForm(data, duplicate=duplicate)
        context = {
            **self.admin_site.each_context(request),  # what every admin page needs, e.g. the site's name
            "opts": self.model._meta,
            "title": f"Merge {duplicate}",
            "duplicate": duplicate,
            "form": form,
        }
        if form.is_valid():
            target = form.cleaned_data["into"]
            if request.method == "POST":
                name = str(duplicate)  # before merging, which takes its pk
                refused = duplicate.merge_into(target, request.user)
                self.message_user(request, f"{name} has been merged into {target}.", messages.SUCCESS)
                for alias, why in refused:
                    self.message_user(request, f"{alias} wasn't added to {target.accession}'s aliases: {why}.", messages.WARNING)
                return redirect("admin:repo_material_change", target.pk)
            # What the merge would do. merge_names() changes target only here: it isn't saved.
            context["aliases_added"], context["aliases_refused"] = duplicate.merge_names(target, duplicate.is_public())
            context.update({
                "target": target,
                "sequences": duplicate.phase_sequences.select_related("reference"),
                "series": duplicate.series.select_related("property", "reference"),
                "memberships": duplicate.in_mixtures.select_related("mixture"),
                "earlier": RetiredID.objects.filter(merged_into=duplicate.pk),
                "components": duplicate.components.select_related("component"),
            })
        return TemplateResponse(request, "admin/repo/material/merge.html", context)


@admin.register(RetiredID)
class RetiredIDAdmin(admin.ModelAdmin):
    """Records are made only by deleting a material, and go only when it is restored: only
    the reason can be changed. The Restore button is in this model's own change_form.html."""

    list_display = ["accession", "name", "kind", "retired_by", "retired_at", "merged", "was_public"]
    fields = [
        "accession", "kind", "name", "inchikey", "retired_by", "retired_at", "merged", "moved", "was_public",
        "reason", "deleted_rows",
    ]
    readonly_fields = [
        "accession", "kind", "name", "inchikey", "retired_by", "retired_at", "merged", "moved", "was_public",
        "deleted_rows",
    ]

    @admin.display(description="ID", ordering="number")
    def accession(self, obj):
        return obj.accession

    @admin.display(description="merged into")
    def merged(self, obj):
        # None shows as "-", as for any empty field.
        return accession(obj.merged_into) if obj.merged_into is not None else None

    @admin.display(description="snapshot")
    def deleted_rows(self, obj):
        # <pre> keeps the JSON's line breaks and indents, which a read-only field would lose.
        return format_html("<pre>{}</pre>", obj.snapshot)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        """Django's addresses for retired IDs, with one more in front: a record's restore/,
        which its Restore button posts to. In front, because Django's last address takes any
        <anything>/ and sends it on to that record's page."""
        restore = self.admin_site.admin_view(require_POST(self.restore_view))
        return [path("<int:number>/restore/", restore, name="repo_retiredid_restore"), *super().get_urls()]

    def restore_view(self, request, number):
        """Put the material back (RetiredID.restore()) and go to its page, or say why it can't
        be and stay on the record's. Only admins may restore them (is_admin())."""
        if not is_admin(request.user):
            raise PermissionDenied
        record = get_object_or_404(RetiredID, number=number)
        try:
            material = record.restore()
        except ValidationError as exc:
            for problem in exc.messages:
                self.message_user(request, f"{record.accession} can't be restored: {problem}", messages.ERROR)
            return redirect("admin:repo_retiredid_change", number)
        self.message_user(request, f"{material} has been restored, with everything deleted with it.", messages.SUCCESS)
        return redirect("admin:repo_material_change", material.pk)


@admin.register(Phase)
class PhaseAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "polar", "synonyms", "sort_order"]


@admin.register(Reference)
class ReferenceAdmin(NewRowsTicked, EnteredAdmin):
    """A reference added here is published unless unticked, as on Add data. Ticked for
    unpublished work, the form is refused with the database's message (Reference's constraint).
    On the list, admins can mark the references they tick published, or not published."""

    list_display = ["label", "kind", "identifier", "published", *ENTERED]
    list_filter = ["published", *ENTERED_FILTERS]
    new_rows_ticked = ["published"]
    # Also what the autocomplete box searches when a sequence's reference is chosen.
    search_fields = ["identifier", "first_author", "title"]
    readonly_fields = ENTERED
    inlines = [ReferenceSequenceInline]
    # The list's Action menu, after Django's own "Delete selected references".
    actions = ["mark_published", "mark_not_published"]

    def has_admin_permission(self, request):
        """For the actions with permissions=["admin"]: Django offers them only when this is
        True. Only admins (is_admin()), as for merging and restoring materials."""
        return is_admin(request.user)

    @admin.action(description="Mark published", permissions=["admin"])
    def mark_published(self, request, queryset):
        """Tick published on the references ticked on the list, so their approved data is public.
        Unpublished work is refused, as the database would refuse it: when its paper comes out,
        change its kind and identifier to the paper's first. The others are still marked."""
        unpublished = Reference.Kind.UNPUBLISHED
        for reference in queryset.filter(kind=unpublished):
            self.message_user(
                request,
                f"{reference} wasn't marked published: it is unpublished work. "
                "Change its kind and identifier to its paper's on its page first.",
                messages.ERROR,
            )
        n = self.switch(request, queryset.exclude(kind=unpublished), True, "Marked published.")
        if n:
            self.message_user(
                request, f"Marked published: {n} reference{pluralize(n)}. Their approved data is public.", messages.SUCCESS
            )

    @admin.action(description="Mark not published (hide its data)", permissions=["admin"])
    def mark_not_published(self, request, queryset):
        """Untick published on the references ticked on the list, so none of their data is
        public, and a material with nothing public left answers "not found". The undo for
        Mark published."""
        n = self.switch(request, queryset, False, "Marked not published.")
        self.message_user(
            request, f"Marked not published: {n} reference{pluralize(n)}. Their data is hidden.", messages.SUCCESS
        )

    def switch(self, request, queryset, published, message):
        """Set published on each reference in queryset and return how many there were. Each one
        changed gets message in its admin history, as a change saved on its page would, so the
        history says who made data public or hid it. One already set is left alone, so its
        history doesn't record a change that didn't happen."""
        references = list(queryset)
        for reference in references:
            if reference.published != published:
                reference.published = published
                reference.save(update_fields=["published"])
                self.log_change(request, reference, message)
        return len(references)


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
class SeriesAdmin(NewRowsTicked, EnteredAdmin):
    """A series added here is approved unless unticked, as a sequence is (PhaseSequenceInline)."""

    list_display = ["material", "property", "variable", "kind", "reference", "point_count", "approved", *ENTERED]
    list_filter = ["property", "kind", "variable", "approved", *ENTERED_FILTERS]
    new_rows_ticked = ["approved"]
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
