import base64

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.forms import Textarea
from django.forms.models import BaseInlineFormSet
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.defaultfilters import pluralize
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html
from django.views.decorators.http import require_POST

from . import chem, duplicates
from .forms import MergeForm
from .models import (
    Entered, Material, Measurement, MixtureComponent, NotDuplicate, Phase, PhaseSequence, Property, PropertyGroup,
    Reference, RetiredID, Series, Variable, WaitingSave, accession, is_admin,
)
from .views import sequence_ribbons, value_sections

# The JSON fields hold a short list or a few key: value pairs, so a smaller box than the
# usual 10 rows.
SMALL_JSON_BOX = {models.JSONField: {"widget": Textarea(attrs={"rows": 2, "cols": 80})}}

# Who entered a row and when: shown, never typed in.
ENTERED = ["entered_by", "entered_at"]
# Filters by both, on the lists of models that record them. RelatedOnlyFieldListFilter offers
# only the users who have entered a row, not every account. Each filter also has a choice
# for blank, which means imported: "-" for entered by, "No date" for entered at.
ENTERED_FILTERS = [("entered_by", admin.RelatedOnlyFieldListFilter), "entered_at"]


def structure_image(material):
    """A compound's drawing, 360 by 220 pixels as on its public page, carried in the admin's page
    itself as a data: address rather than linked to /material/<n>/structure.svg, which answers 404
    for a material that isn't public, so admins see hidden structures too. Inside an <img>, an SVG
    can't run anything. A material's page and a waiting save's page show it."""
    svg = base64.b64encode(chem.structure_svg(material.canonical_smiles).encode()).decode()
    return format_html(
        '<img src="data:image/svg+xml;base64,{}" alt="Structure of {}" width="360" height="220">',
        svg,
        material.name,
    )


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
        if not obj.pk:
            return "Shown after saving."
        if obj.kind == Material.Kind.MIXTURE:
            return "None: a mixture has no structure."
        return structure_image(obj)

    def save_model(self, request, obj, form, change):
        """Save, then warn if another material has the same connectivity."""
        super().save_model(request, obj, form, change)
        warning = obj.stereo_warning(Material.objects.all())
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
        """Django's addresses for materials, with three in front, as for RetiredIDAdmin's restore/:
        a material's merge/, linked from this model's own change_form.html; duplicates/, linked
        from its change_list.html; and the address a pair's Not a duplicate button posts to."""
        merge = self.admin_site.admin_view(self.merge_view)
        possible_duplicates = self.admin_site.admin_view(self.duplicates_view)
        not_duplicate = self.admin_site.admin_view(require_POST(self.not_duplicate_view))
        return [
            path("<int:pk>/merge/", merge, name="repo_material_merge"),
            path("duplicates/", possible_duplicates, name="repo_material_duplicates"),
            path("duplicates/<int:first>/<int:second>/not-a-duplicate/", not_duplicate, name="repo_material_not_duplicate"),
            *super().get_urls(),
        ]

    def duplicates_view(self, request):
        """Possible duplicates: pairs of compounds with the same connectivity (duplicates.
        connectivity_pairs()), for an admin to merge or mark not a duplicate. Only admins
        (is_admin()), who see hidden compounds too, each marked. Each pair is {"heading": ...,
        "compounds": [a, b]}, and each compound {"material", "hidden", "drawing", "references"}."""
        if not is_admin(request.user):
            raise PermissionDenied
        found = duplicates.connectivity_pairs()
        in_pairs = {material.pk for _, a, b in found for material in (a, b)}
        public = set(Material.objects.public().filter(pk__in=in_pairs).values_list("pk", flat=True))

        def compound(material):
            # Its references: those of its sequences and values, hidden ones too. pk__in, as in
            # Material.objects.public(), gives each reference once.
            references = Reference.objects.filter(
                Q(pk__in=material.phase_sequences.values("reference")) | Q(pk__in=material.series.values("reference"))
            )
            return {
                "material": material,
                "hidden": material.pk not in public,
                "drawing": structure_image(material),
                "references": references,
            }

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Possible duplicates",
            "pairs": [{"heading": heading, "compounds": [compound(a), compound(b)]} for heading, a, b in found],
        }
        return TemplateResponse(request, "admin/repo/material/duplicates.html", context)

    def not_duplicate_view(self, request, first, second):
        """Mark the materials numbered first and second not a duplicate (NotDuplicate.mark()), so
        the pair isn't listed again, and go back to the page. Only admins (is_admin())."""
        if not is_admin(request.user):
            raise PermissionDenied
        if first == second:
            raise Http404("A material can't be a duplicate of itself.")
        a, b = get_object_or_404(Material, pk=first), get_object_or_404(Material, pk=second)
        NotDuplicate.mark(a, b, request.user)
        self.message_user(request, f"{a} and {b} are marked not a duplicate: the pair won't be listed again.", messages.SUCCESS)
        return redirect("admin:repo_material_duplicates")

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

    def has_add_permission(self, request):
        """Only admins (is_admin()) add references here. Curators have add reference too, because
        it is what lets them use Add data, which is where they add them. Without this, a curator
        with staff status would find an Add reference page in the admin (migration 0032)."""
        return is_admin(request.user) and super().has_add_permission(request)

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


@admin.register(WaitingSave)
class WaitingSaveAdmin(admin.ModelAdmin):
    """The review queue: curators' saves waiting for an admin's check, oldest first, for admins only
    (is_admin()). A save is made only by Save on Add data and leaves only by Approve or Reject, whose
    buttons are in this model's own change_form.html. So its page is view-only, with no Delete:
    deleting it would leave its rows unapproved, in no save."""

    list_display = ["__str__", "saved_by", "saved_at", "reference", "material_count", "sequence_count", "series_count"]
    list_select_related = ["reference", "saved_by"]
    ordering = ["saved_at"]  # here, not in the model's Meta, which would need a migration
    fields = ["reference", "saved_by", "saved_at"]
    readonly_fields = fields
    actions = None  # each save is looked at on its own page first

    class Media:
        # The look of the public pages' data templates, which the save's page includes (preview()).
        # The admin puts it in the page's <head>, after its own stylesheets.
        css = {"all": ["repo/data.css"]}

    def has_view_permission(self, request, obj=None):
        return is_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="materials")
    def material_count(self, obj):
        return obj.materials().count()

    @admin.display(description="phase sequences")
    def sequence_count(self, obj):
        return obj.phase_sequences.count()

    @admin.display(description="blocks of values")
    def series_count(self, obj):
        return obj.series.count()

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        """Django's last step for a save's page, after it has checked who may see it: add what the
        save adds, as the public pages would show it (preview()), for change_form.html."""
        context.update(self.preview(obj))
        return super().render_change_form(request, context, add, change, form_url, obj)

    def preview(self, saved):
        """What saved adds, as {"published": ..., "entries": [...]}: whether its reference is
        published, so approving makes its data public, and an entry for each of its materials, in
        LCR ID order. Each has the material; its status (WaitingSave.material_status()); the label
        held for it, if any, with why it won't be kept (label_problem()); a compound's drawing or a
        mixture's components, all of them, as the admin sees hidden data; and the save's own
        sequences and values for it, as the material's public page draws them, with the same
        functions (views.sequence_ribbons(), views.value_sections()). The sequences and values are
        fetched as that page fetches them, all in a few queries, and shared out by material."""
        sequences = list(
            saved.phase_sequences.select_related("reference").prefetch_related("transitions__upper", "transitions__lower")
        )
        series = list(
            saved.series.select_related("reference", "property__group", "variable").prefetch_related("points")
        )
        entries = []
        for material in saved.materials().order_by("pk"):
            its_sequences = [one for one in sequences if one.material_id == material.pk]
            sequence_rows, axis = sequence_ribbons(its_sequences) if its_sequences else ([], "")
            label = saved.labels.get(str(material.pk), "")
            compound = material.kind == Material.Kind.COMPOUND
            entries.append({
                "material": material,
                "status": saved.material_status(material),
                "label": label,
                "label_problem": saved.label_problem(material, label) if label else "",
                "drawing": structure_image(material) if compound else "",
                "components": [] if compound else material.components.select_related("component"),
                "sequence_rows": sequence_rows,
                "axis": axis,
                "value_sections": value_sections([one for one in series if one.material_id == material.pk], with_charts=True),
            })
        return {"published": saved.reference.published, "entries": entries}

    def get_urls(self):
        """Django's addresses for waiting saves, with a save's approve/ and reject/ in front, which
        its buttons post to, as for RetiredIDAdmin's restore/."""
        approve = self.admin_site.admin_view(require_POST(self.approve_view))
        reject = self.admin_site.admin_view(require_POST(self.reject_view))
        return [
            path("<int:pk>/approve/", approve, name="repo_waitingsave_approve"),
            path("<int:pk>/reject/", reject, name="repo_waitingsave_reject"),
            *super().get_urls(),
        ]

    def waiting_save(self, request, pk):
        """The save at pk, for an admin only (is_admin()). One approved or rejected since is gone: 404."""
        if not is_admin(request.user):
            raise PermissionDenied
        return get_object_or_404(WaitingSave.objects.select_related("reference"), pk=pk)

    def approve_view(self, request, pk):
        """Approve the save (WaitingSave.approve()), say what became of its data and labels, and go
        back to the queue. The save is deleted, so its history page would go with it: its
        reference's history records who approved it, as for Mark published."""
        saved = self.waiting_save(request, pk)
        reference = saved.reference
        kept, refused = saved.approve()
        if reference.is_public():
            outcome = "its data is public."
        else:
            outcome = f"its data stays hidden, because {reference} isn't published. Marking it published makes it public."
        self.message_user(request, f"Approved save {pk} from {reference}: {outcome}", messages.SUCCESS)
        for lcr, label in kept:
            self.message_user(request, f"{label} is kept as one of {lcr}'s aliases.", messages.SUCCESS)
        for lcr, label, why in refused:
            self.message_user(request, f"{label} wasn't kept as one of {lcr}'s aliases: {why}.", messages.WARNING)
        self.log_change(request, reference, f"Approved save {pk}.")
        return redirect("admin:repo_waitingsave_changelist")

    def reject_view(self, request, pk):
        """Reject the save (WaitingSave.reject()), say what it retired, and go back to the queue.
        Its reference's history records who rejected it, as for approve_view()."""
        saved = self.waiting_save(request, pk)
        reference = saved.reference
        retired = saved.reject(request.user)
        self.message_user(
            request, f"Rejected save {pk} from {reference}: its phase sequences and values are deleted.", messages.SUCCESS
        )
        for material in retired:
            self.message_user(
                request,
                f"{material} is retired, because the save left it with no data. It isn't public, so its address answers not found.",
                messages.SUCCESS,
            )
        self.log_change(request, reference, f"Rejected save {pk}." + (f" Retired {', '.join(retired)}." if retired else ""))
        return redirect("admin:repo_waitingsave_changelist")


@admin.register(NotDuplicate)
class NotDuplicateAdmin(admin.ModelAdmin):
    """The pairs marked not a duplicate on the possible-duplicates page, with who decided and when,
    for admins only (is_admin()). Decisions are made only by the page's button, so here they can
    only be read, or deleted, which is the undo: the pair is listed on the page again."""

    list_display = ["first", "second", "decided_by", "decided_at"]
    list_select_related = ["first", "second", "decided_by"]
    ordering = ["-decided_at"]  # the newest first, here rather than in the model's Meta
    fields = ["first", "second", "decided_by", "decided_at"]
    readonly_fields = fields

    def has_view_permission(self, request, obj=None):
        return is_admin(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return is_admin(request.user)
