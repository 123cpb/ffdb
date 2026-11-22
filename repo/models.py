import re
from collections import defaultdict
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.serializers.base import DeserializationError
from django.db import models, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from . import chem
from .phases import (
    ISOTROPIC,
    NOT_LIQUID_CRYSTAL,
    PhaseSequenceError,
    normalise_label,
    parse_run,
    parse_summary,
    phase_labels,
)


def accession(pk):
    """The public ID for a material's primary key: 12 -> LCR-00012."""
    return f"LCR-{pk:05d}"


# A public ID as someone might type it: LCR-00012, lcr12 or LCR12.
ACCESSION = re.compile(r"LCR-?(\d+)", re.IGNORECASE)


def pk_from_accession(text):
    """The primary key in a public ID, the reverse of accession(): "LCR-00012" -> 12.
    Returns None when the text isn't a public ID."""
    match = ACCESSION.fullmatch(text.strip())
    return int(match.group(1)) if match else None


def name_key(text):
    """A name as names are compared: lower-case letters and digits only, so RM-734 and rm734 are one name."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Labels that mean something only inside one paper: 3, 10a, 4B, (12), compound 3, cpd122.
# Not 5CB, M5 or 12-0-1: those can't be told apart from codes used across papers.
COMPOUND_NUMBER = re.compile(r"(?:(?:compound|cpd)\s*)?\(?\d+[a-z]?\)?", re.IGNORECASE)


def is_compound_number(name):
    """Whether name is a bare compound number, such as 10a, which says nothing outside its paper."""
    return bool(COMPOUND_NUMBER.fullmatch(name.strip()))


# What people paste in front of a DOI: https://doi.org/, http://dx.doi.org/, doi: or DOI:.
DOI_PREFIX = re.compile(r"^(https?://(dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)


def clean_doi(text):
    """A DOI as it is stored, without a prefix and in lower case, because DOIs ignore case:
    "https://doi.org/10.1002/CHEM.201702742" -> "10.1002/chem.201702742"."""
    return DOI_PREFIX.sub("", text.strip()).strip().lower()


def clean_patent_number(text):
    """A patent number as it is stored, without spaces and in capitals, because patent
    offices ignore both: "ep 3260450 a1" -> "EP3260450A1"."""
    return "".join(text.split()).upper()


# The start of the DOIs that preprint servers give: arXiv's and ChemRxiv's, as clean_doi()
# leaves them.
PREPRINT_DOIS = ("10.48550/arxiv.", "10.26434/chemrxiv")


# Fractions are printed rounded, so a mixture's can add up to a little over 100:
# 33.4 + 33.4 + 33.4 = 100.2.
ROUNDING_ALLOWANCE = 0.5


def is_admin(user):
    """Whether user is an admin, said here once and nowhere else: one who may delete materials.
    Admins merge and restore materials, make references public or hide them, and their saves on
    Add data are approved at once, while a curator's wait for an admin's check. Superusers have
    every permission, so they are admins. Templates ask with the is_admin filter (repo_tags)."""
    return user.has_perm("repo.delete_material")


def seen_by(user, rows):
    """rows, a query set, as user's Add data sees them: every row for an admin, whose saves go
    straight in, and only the public ones (the model's public()) for anyone else. So a curator's
    checks never say that hidden data exists, and a compound whose structure is hidden reads as
    new (CardMaterialForm.read_structure())."""
    return rows if is_admin(user) else rows.public()


# What is public, said here once and nowhere else: every public page, search, chart and
# download starts from one of these methods, e.g. Material.objects.public(). Each model's
# objects is made from its query set below (as_manager()), so the method is there on
# Model.objects and on related rows too, e.g. material.phase_sequences.public(). The models
# are named inside the methods, which run only when called, so they can come later in the file.


class PhaseSequenceQuerySet(models.QuerySet):
    def public(self):
        """Approved, under a published reference. It asks for the reference's switch, not
        Reference.objects.public(), which asks for public data, so each rule would wait for the
        other. The answer is the same: a published reference with an approved sequence is public."""
        return self.filter(approved=True, reference__published=True)


class SeriesQuerySet(models.QuerySet):
    def public(self):
        """As for a sequence: approved, under a published reference."""
        return self.filter(approved=True, reference__published=True)


class MeasurementQuerySet(models.QuerySet):
    def public(self):
        """The points of public series: a point is public with its series."""
        return self.filter(series__in=Series.objects.public())


class MaterialQuerySet(models.QuerySet):
    def public(self):
        """Listed, or with a public sequence or series. pk__in, not a join through the
        sequences: a join gives a material once for each of its sequences, and pk__in once."""
        return self.filter(
            Q(listed=True)
            | Q(pk__in=PhaseSequence.objects.public().values("material"))
            | Q(pk__in=Series.objects.public().values("material"))
        )


class ReferenceQuerySet(models.QuerySet):
    def public(self):
        """Published, with a public sequence or series, so FFDB lists only references that gave
        it public data. A public sequence or series already needs its reference published, but
        published=True says the rule as it is written."""
        return self.filter(published=True).filter(
            Q(pk__in=PhaseSequence.objects.public().values("reference"))
            | Q(pk__in=Series.objects.public().values("reference"))
        )


class RetiredIDQuerySet(models.QuerySet):
    def public(self):
        """The IDs of materials that were public when they were retired. Only these answer at
        their address: a material that was never public leaves no public trace."""
        return self.filter(was_public=True)


class Material(models.Model):
    """A compound, identified by its structure, or a mixture, which has no structure and is
    described by its composition: its components in FFDB (MixtureComponent rows) and the
    other components, described in words."""

    class Kind(models.TextChoices):
        COMPOUND = "compound", "Compound"
        MIXTURE = "mixture", "Mixture"

    class CompositionStatus(models.TextChoices):
        LISTED = "listed", "Listed"
        PARTLY_KNOWN = "partly_known", "Partly known"
        NOT_DISCLOSED = "not_disclosed", "Not disclosed"

    # Compound is the default because every material added before mixtures was one, and the
    # imports add only compounds.
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.COMPOUND)
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="As papers name it, e.g. RM734. Optional for a compound, needed for a mixture. "
        "Names can repeat: the LCR ID is what identifies a material.",
    )
    aliases = models.CharField(
        max_length=500,
        blank=True,
        help_text="Other names or codes, comma-separated.",
    )
    smiles = models.TextField("SMILES", blank=True, help_text="Compounds only, stored as typed. A mixture has none.")
    # Worked out from the SMILES on every save (see save()), never typed in. Blank for a mixture.
    canonical_smiles = models.TextField("canonical SMILES", blank=True, editable=False)
    # The full InChIKey identifies a compound, so the database allows each one once (see
    # Meta). Blank for a mixture.
    inchikey = models.CharField("InChIKey", max_length=27, blank=True, editable=False)
    composition_status = models.CharField(
        max_length=13, choices=CompositionStatus.choices, default=CompositionStatus.LISTED,
        help_text="Mixtures only: whether the reference gives every component, only some, or none, "
        "as for many commercial mixtures.",
    )
    other_components = models.TextField(
        blank=True,
        help_text="Mixtures only: components that aren't in FFDB, such as a commercial host, "
        "with their fractions if known.",
    )
    notes = models.TextField(blank=True)
    # Off unless something turns it on, so a material nobody chose to show stays hidden. Not
    # the composition status above, whose Listed means every component is given.
    listed = models.BooleanField(
        default=False,
        help_text="Shown even with no public sequence or value, e.g. a structure from LCPROP's list.",
    )
    created = models.DateTimeField(auto_now_add=True)

    objects = MaterialQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        constraints = [
            # Each InChIKey once. A mixture's is blank, and blanks don't count, so mixtures
            # don't clash with each other.
            models.UniqueConstraint(fields=["inchikey"], condition=~Q(inchikey=""), name="unique_inchikey"),
            # The next two are CheckConstraints: PostgreSQL refuses a row that breaks them,
            # however it is written, even by update() or a raw save, which skip save() and
            # clean(). A kind that is neither compound nor mixture passes neither.
            models.CheckConstraint(
                condition=Q(kind="mixture") | (~Q(smiles="") & ~Q(inchikey="")),
                name="compound_has_structure",
                violation_error_message="A compound needs a SMILES and an InChIKey.",
            ),
            models.CheckConstraint(
                condition=Q(kind="compound") | Q(smiles="", canonical_smiles="", inchikey=""),
                name="mixture_has_no_structure",
                violation_error_message="A mixture has no structure: no SMILES and no InChIKey.",
            ),
        ]

    def __str__(self):
        return f"{self.accession} {self.name}"

    @property
    def accession(self):
        """Permanent public ID, made from the primary key. Before the first save there is none."""
        return accession(self.pk) if self.pk else "LCR-new"

    @property
    def structure_url(self):
        """The address of the drawing. The view ignores ?v=, but it makes the address change
        when the structure does, so browsers can keep a copy without showing an old drawing."""
        return f"{reverse('repo:structure', args=[self.pk])}?v={self.inchikey}"

    @property
    def thumbnail_url(self):
        """The address of the small drawing that lists use, 200 by 128 pixels."""
        return f"{reverse('repo:structure', args=[self.pk])}?size=thumb&v={self.inchikey}"

    def is_public(self):
        """Whether this material is public now, by the rule (MaterialQuerySet.public())."""
        return Material.objects.public().filter(pk=self.pk).exists()

    def alias_list(self):
        """The aliases as a list: "RM-734, DIO-1" -> ["RM-734", "DIO-1"]."""
        return [alias.strip() for alias in self.aliases.split(",") if alias.strip()]

    def names(self):
        """The name, if it has one, then the aliases."""
        return [self.name, *self.alias_list()] if self.name.strip() else self.alias_list()

    def has_name(self, name):
        """Whether name is this material's name or one of its aliases, compared by name_key()."""
        key = name_key(name)
        return key == name_key(self.name) or key in {name_key(alias) for alias in self.alias_list()}

    def alias_problem(self, name):
        """Why name, as a paper prints it, can't be kept as one of this material's aliases,
        or "" when it can."""
        if self.has_name(name):
            return "it is already one of its names"
        if is_compound_number(name):
            return "a bare compound number means something only inside one paper"
        if "," in name:
            return "aliases are kept separated by commas, so one can't contain a comma"
        if not name_key(name):
            return "it has no letters or digits to compare with other names"
        if len(", ".join(self.alias_list() + [name])) > self._meta.get_field("aliases").max_length:
            return "its aliases are full: an admin can tidy them"
        return ""

    def clean(self):
        """Refuse a compound without a readable SMILES or whose structure is already in FFDB,
        and a mixture with a SMILES, without a name, or that is a component of a mixture.
        When nothing is refused, fill in the identifiers as save() will store them, so the
        constraints that full_clean() checks next see them (see Meta)."""
        if self.kind == self.Kind.MIXTURE:
            errors = {}
            if self.smiles.strip():
                errors["smiles"] = "A mixture has no structure: leave the SMILES blank."
            if not self.name.strip():
                errors["name"] = "A mixture needs a name: it has no structure to tell it apart."
            # A compound being made into a mixture. Without a pk, it is in no mixture yet.
            if self.pk and self.in_mixtures.exists():
                mixtures = ", ".join(str(row.mixture) for row in self.in_mixtures.select_related("mixture"))
                errors["kind"] = f"This is a component of {mixtures}, and a component must be a compound."
            if errors:
                raise ValidationError(errors)
            # A compound being made into a mixture still has its identifiers until now.
            self.canonical_smiles = self.inchikey = ""
            return
        if not self.smiles.strip():
            raise ValidationError({"smiles": "A compound needs a SMILES."})
        try:
            canonical, key = chem.standardise(self.smiles)
        except chem.StructureError as exc:
            raise ValidationError({"smiles": str(exc)})
        clash = Material.objects.filter(inchikey=key).exclude(pk=self.pk).first()
        if clash:
            raise ValidationError(
                {"smiles": f"This structure is already in FFDB as {clash.accession} ({clash.name})."}
            )
        # Only when the structure is new to this material. Here self.inchikey is still the
        # saved one (save() works out the new one): blank for a material being added, or a
        # mixture being made a compound. So changing a name is never refused.
        if key != self.inchikey:
            problem = RetiredID.structure_problem(key)
            if problem:
                raise ValidationError({"smiles": problem})
        # Last, because the check above needs the saved InChIKey. Without this, a compound
        # being added would still have a blank one, and compound_has_structure would refuse it.
        self.canonical_smiles, self.inchikey = canonical, key

    def save(self, *args, **kwargs):
        """Work out a compound's identifiers from its SMILES. A mixture has none."""
        if self.kind == self.Kind.COMPOUND:
            self.canonical_smiles, self.inchikey = chem.standardise(self.smiles)
        else:
            self.canonical_smiles = self.inchikey = ""
        super().save(*args, **kwargs)

    def composition_problems(self, fractions):
        """What is wrong with this mixture's composition as a whole, as a list of messages,
        empty when nothing is. fractions is [(fraction, unit), ...], one for each component
        in FFDB. MixtureComponent.clean() checks each component on its own, and the database
        allows each compound once in a mixture (MixtureComponent's constraint)."""
        problems = []
        given = bool(fractions) or bool(self.other_components.strip())
        not_disclosed = self.composition_status == self.CompositionStatus.NOT_DISCLOSED
        if not_disclosed and given:
            problems.append(
                "Not disclosed means no components and no other components: remove them, or choose Partly known."
            )
        if not not_disclosed and not given:
            problems.append(
                "Give the components in FFDB, describe them under Other components, or choose Not disclosed."
            )
        units = {unit for _, unit in fractions}
        if len(units) > 1:
            problems.append("Give every fraction in the same unit: all mol% or all wt%.")
        elif units:
            total = sum(fraction for fraction, _ in fractions)
            if total > 100 + ROUNDING_ALLOWANCE:
                problems.append(f"The fractions add up to {total:g} {units.pop()}, more than 100.")
        return problems

    def possible_stereo_variants(self, among):
        """Other materials, of those in among (a query set), with the same connectivity: the same
        skeleton with different or missing stereochemistry. They are kept as separate materials.
        The admin and the import look among every material, Add data among those its user sees
        (seen_by())."""
        if not self.inchikey:
            return Material.objects.none()
        skeleton = chem.skeleton_key(self.inchikey)
        return among.filter(inchikey__startswith=skeleton + "-").exclude(pk=self.pk)

    def stereo_warning(self, among):
        """The warning shown when possible_stereo_variants() finds any in among, or "" when it
        finds none. The admin, the import and Add data show it; Add data also asks it of a
        material not saved yet, which is called "This structure"."""
        variants = self.possible_stereo_variants(among)
        if not variants:
            return ""
        others = ", ".join(f"{v.accession} ({v.name})" for v in variants)
        subject = self.accession if self.pk else "This structure"
        return (
            f"{subject} has the same connectivity as {others} but different "
            "stereochemistry. Both are kept as separate materials: check that is right, "
            "and say in the name or notes whether a structure without stereochemistry "
            "is racemic or simply not stated."
        )

    def liquid_crystal_phases(self):
        """The liquid-crystal phases in any of this material's public sequences, each once, in
        the vocabulary's order, as its public page lists them. The database does the work: it
        follows the links from Phase to those sequences, and distinct() drops a phase that two
        sequences share."""
        return (
            Phase.objects.filter(phase_sequences__in=self.phase_sequences.public())
            .exclude(code__in=NOT_LIQUID_CRYSTAL)
            .distinct()
        )

    def snapshot(self):
        """This material and every row that deleting it deletes too, as JSON written by Django's
        serializers: its components (a mixture's), its sequences and their transitions, and its
        series and their points, each parent before its children. A sequence's phases are listed
        in its own row. Rows elsewhere, such as its references, are named by primary key. Its
        not-a-duplicate decisions (NotDuplicate) are deleted with it but left out, so restoring
        it doesn't bring them back: each pair is listed again, for an admin to decide."""
        rows = [self, *self.components.all()]
        for sequence in self.phase_sequences.all():
            rows += [sequence, *sequence.transitions.all()]
        for series in self.series.all():
            rows += [series, *series.points.all()]
        return serializers.serialize("json", rows, indent=2)

    def retire(self, user, merged_into=None, moved=None, was_public=None):
        """Delete this material, first saving a RetiredID record of its ID, retired by user,
        with a snapshot of everything deleted and whether it was public. Both happen or
        neither: if the delete fails, the record is undone too. A merge (merge_into()) also
        gives the number it went into, what it moved, and whether it was public, which it
        works out before moving the data: by now the material has none."""
        if was_public is None:
            was_public = self.is_public()
        with transaction.atomic():
            RetiredID.objects.create(
                number=self.pk, kind=self.kind, name=self.name, inchikey=self.inchikey, retired_by=user,
                snapshot=self.snapshot(), merged_into=merged_into, moved=moved or {}, was_public=was_public,
            )
            self.delete()

    def merge_problems(self, target):
        """Why this material can't be merged into target, as a list of messages, empty when
        it can."""
        if target.pk == self.pk:
            return ["A material can't be merged into itself."]
        if target.kind != self.kind:
            return [
                f"{self.accession} is a {self.get_kind_display().lower()} and {target.accession} a "
                f"{target.get_kind_display().lower()}: a material can only be merged into one of its own kind."
            ]
        # The mixtures with both as components. Moving this one's rows to target would put
        # target in them twice, which MixtureComponent's constraint refuses. Two filter()
        # calls across a relation to many rows each follow it separately: one component row
        # must be this material and another target. One filter() with both would need one
        # row to be both, and find nothing.
        shared = Material.objects.filter(components__component=self).filter(components__component=target)
        if shared:
            mixtures = ", ".join(str(mixture) for mixture in shared)
            return [f"Both are components of {mixtures}, which can have each compound once: fix its composition first."]
        return []

    def add_aliases(self, names):
        """Add names to the aliases, one at a time so each is checked against those before
        it, without saving. Returns (added, refused): the names added, and [(name, why), ...]
        for those alias_problem() refuses. A name that is already one of this material's
        names is in neither list: it isn't lost."""
        added, refused = [], []
        for name in names:
            if self.has_name(name):
                continue
            problem = self.alias_problem(name)
            if problem:
                refused.append((name, problem))
            else:
                self.aliases = ", ".join(self.alias_list() + [name])
                added.append(name)
        return added, refused

    def merge_names(self, target, public):
        """Add this material's names to target's aliases, as merging it does, without saving.
        Returns (added, refused), as add_aliases() does. A material that isn't public (public
        is False) gives none, each refused: its names would appear on target's public page."""
        if not public:
            return [], [(name, f"{self.accession} isn't public") for name in self.names()]
        return target.add_aliases(self.names())

    def merge_into(self, target, user):
        """Merge this material, a duplicate, into target: move its sequences, values and
        mixture memberships to target, add its names to target's aliases if it is public,
        point earlier merges into it at target, and retire it, merged by user, with what
        moved recorded, so a merge can be undone. All of it or none. Raises ValidationError
        with the reason when merge_problems() finds one. Returns the names target didn't
        take, as merge_names() does."""
        problems = self.merge_problems(target)
        if problems:
            raise ValidationError(problems)
        # Before its data moves: once it has, a material that isn't listed has nothing public.
        public = self.is_public()
        earlier = RetiredID.objects.filter(merged_into=self.pk)
        with transaction.atomic():
            moved = {
                "sequences": list(self.phase_sequences.values_list("pk", flat=True)),
                "series": list(self.series.values_list("pk", flat=True)),
                "memberships": list(self.in_mixtures.values_list("pk", flat=True)),
                "merges": list(earlier.values_list("number", flat=True)),
            }
            # update(): one query per table, changing only the material. save() isn't run,
            # and needn't be: nothing it works out depends on the material.
            self.phase_sequences.update(material=target)
            self.series.update(material=target)
            self.in_mixtures.update(component=target)
            earlier.update(merged_into=target.pk)
            moved["aliases"], refused = self.merge_names(target, public)
            target.save(update_fields=["aliases"])
            self.retire(user, merged_into=target.pk, moved=moved, was_public=public)
        return refused


def materials_named(name, among):
    """The materials in among (a query set) with this name or alias, compared by name_key(), in
    LCR ID order. Add data looks among those its user sees (seen_by()), and only the mixtures for
    a new mixture's name. Python compares the names, because name_key() drops characters in a way
    the database can't, using only its ordinary features, so every material in among is read. A
    name with no letters or digits fits none."""
    if not name_key(name):
        return []
    # only(): just the fields compared, which is several times quicker than reading every
    # SMILES too. The few materials found read any other field when it is first used.
    materials = among.order_by("pk").only("kind", "name", "aliases")
    return [material for material in materials if material.has_name(name)]


class MixtureComponent(models.Model):
    """One compound in FFDB that is part of a mixture, with its fraction. Components that
    aren't in FFDB are described in the mixture's other components. The rules across all
    of a mixture's components are in Material.composition_problems()."""

    class Unit(models.TextChoices):
        MOL = "mol%", "mol%"
        WT = "wt%", "wt%"

    # Deleting a mixture deletes its components. A compound can't be deleted while a mixture
    # uses it: the mixture would quietly become a different mixture.
    mixture = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="components")
    component = models.ForeignKey(
        Material, on_delete=models.PROTECT, related_name="in_mixtures", help_text="A compound already in FFDB."
    )
    fraction = models.FloatField(help_text="More than 0 and at most 100.")
    # No default, so the admin makes you choose: a default of mol% would quietly save a
    # wt% fraction as mol%.
    unit = models.CharField(max_length=4, choices=Unit.choices)

    class Meta:
        ordering = ["-fraction", "id"]  # the largest component first
        constraints = [
            models.UniqueConstraint(fields=["mixture", "component"], name="each_compound_once_per_mixture"),
            # clean() gives the same rule, with its message beside the fraction. full_clean()
            # skips a constraint on a field that already has an error, so it isn't said twice.
            models.CheckConstraint(
                condition=Q(fraction__gt=0, fraction__lte=100),
                name="fraction_more_than_0_at_most_100",
                violation_error_message="A fraction is more than 0 and at most 100.",
            ),
        ]

    def __str__(self):
        # Django's page refusing to delete a compound lists these, so it names the mixture.
        return f"{self.component} in {self.mixture}: {self.fraction:g} {self.unit}"

    def clean(self):
        """Refuse a component that breaks a rule on its own, saying what is wrong. Every
        problem is reported at once, each on the field to fix where there is one."""
        errors = {}
        if self.fraction is not None and not 0 < self.fraction <= 100:
            errors["fraction"] = "A fraction is more than 0 and at most 100."
        # Compounds only, so a mixture can never contain itself, or a mixture that contains it.
        if self.component_id and self.component.kind != Material.Kind.COMPOUND:
            errors["component"] = (
                f"{self.component} is a mixture, and a component must be a compound: describe a "
                "mixture used as a host under Other components."
            )
        try:
            # On the admin's Add page this is the material being added, not saved yet.
            mixture = self.mixture
        except Material.DoesNotExist:
            mixture = None  # the form already says a component needs a mixture
        if mixture is not None and mixture.kind != Material.Kind.MIXTURE:
            errors[NON_FIELD_ERRORS] = "Only a mixture has components."
        if errors:
            raise ValidationError(errors)


class Phase(models.Model):
    """One phase in the vocabulary, e.g. NF. Sequences are stored under the codes, and the
    synonyms are the other labels papers use, converted to the code when a sequence is read."""

    code = models.CharField(max_length=20, unique=True, help_text="The standard label, e.g. NF.")
    name = models.CharField(max_length=100)
    polar = models.BooleanField(
        default=False, help_text="Phase with polar (ferro- or antiferroelectric) order."
    )
    synonyms = models.CharField(
        max_length=300,
        blank=True,
        help_text="Other labels for this phase, comma-separated, e.g. 'NX, M2'. They are converted "
        "to the code. Case, spaces and underscores are ignored, so N_X and nx count as NX.",
    )
    sort_order = models.PositiveSmallIntegerField(
        default=100, help_text="Lists show phases from the lowest number up."
    )

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return f"{self.code} ({self.name})"

    def synonym_list(self):
        """The synonyms as a list: "N_X, M2" -> ["N_X", "M2"]."""
        return [synonym.strip() for synonym in self.synonyms.split(",") if synonym.strip()]

    def labels(self):
        """Every label for this phase: its code, then its synonyms."""
        return [self.code] + self.synonym_list()

    @classmethod
    def synonym_map(cls):
        """Every label of every phase, normalised, to its phase's code:
        {"ns": "NS", "nx": "NS", "k": "Cr", ...}. Look a label up with normalise_label(label)."""
        return {
            normalise_label(label): phase.code for phase in cls.objects.all() for label in phase.labels()
        }

    def clean(self):
        """Refuse a code or synonym that another phase already has, compared the way
        synonym_map() compares labels. Otherwise synonym_map() would quietly keep only one."""
        taken = {
            normalise_label(label): phase.code
            for phase in Phase.objects.exclude(pk=self.pk)
            for label in phase.labels()
        }
        errors = {}
        if normalise_label(self.code) in taken:
            errors["code"] = f"Phase {taken[normalise_label(self.code)]} already has this label."
        clashes = [
            f"{label} ({taken[normalise_label(label)]})"
            for label in self.synonym_list()
            if normalise_label(label) in taken
        ]
        if clashes:
            errors["synonyms"] = f"Other phases already have these labels: {', '.join(clashes)}."
        if errors:
            raise ValidationError(errors)


class Entered(models.Model):
    """Who entered a row, in a form or the admin, and when. Both stay blank for a row an
    import added, so blank says it was imported. Abstract: there is no Entered table, and
    each model that inherits from it gets the two columns in its own table."""

    # SET_NULL: deleting a user's account keeps the rows they entered, with entered by blank.
    # Their entered at stays, so those rows don't look imported. related_name="+" gives users
    # no way back to the rows they entered, which nothing needs.
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, editable=False,
        related_name="+",
    )
    # Not auto_now_add, which would fill in a time for imported rows too: mark_entered() sets it.
    entered_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def mark_entered(self, user):
        """Record that user is entering this row now. Call it when the row is added, before
        saving it, and never when it is changed: the record is of who added it."""
        self.entered_by = user
        self.entered_at = timezone.now()


class Reference(Entered):
    """Where data comes from: a journal article, preprint, patent, thesis, or one of the
    group's own results that isn't published yet."""

    class Kind(models.TextChoices):
        ARTICLE = "article", "Journal article"
        PREPRINT = "preprint", "Preprint"
        PATENT = "patent", "Patent"
        THESIS = "thesis", "Thesis"
        UNPUBLISHED = "unpublished", "Unpublished group result"

    # No default, so the admin makes you choose: a default of article would quietly save
    # an arXiv DOI as an article.
    kind = models.CharField(max_length=12, choices=Kind.choices)
    # Unique on its own, so a DOI can't be entered once as an article and again as a preprint.
    identifier = models.CharField(
        max_length=500,
        unique=True,
        help_text="The DOI for an article or preprint, e.g. 10.1002/chem.201702742; the number "
        "for a patent, e.g. EP3260450A1; for a thesis, its author, year and title; for an "
        "unpublished group result, who it is from, the year and what it is.",
    )
    first_author = models.CharField(max_length=100, blank=True, help_text="Surname only, e.g. Mandle.")
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    title = models.CharField(max_length=500, blank=True)
    venue = models.CharField(
        "journal, office or university",
        max_length=200,
        blank=True,
        help_text="As the label shows it: the journal, abbreviated (e.g. Chem. Eur. J.), or the "
        "preprint server; the patent office; or the university.",
    )
    abstract = models.TextField(
        blank=True,
        help_text="As published. Filled in from Crossref when a DOI is added on the Add data page. "
        "Shown only here in the admin until it is decided whether abstracts can be shown publicly.",
    )
    dataset_url = models.URLField(
        "deposited data",
        max_length=300,
        blank=True,
        help_text="A link to the data where it is deposited, e.g. a Research Data Leeds DOI: "
        "https://doi.org/10.5518/1234. Shown on the reference's page.",
    )
    notes = models.TextField(blank=True)
    # Off unless something turns it on, so a reference nobody said was published stays hidden.
    # Published is only this switch: whether the reference is public is ReferenceQuerySet.public().
    published = models.BooleanField(
        default=False,
        help_text="The work is published, so its approved data can be public. An unpublished group result can't be.",
    )

    objects = ReferenceQuerySet.as_manager()

    class Meta:
        ordering = ["kind", "identifier"]
        constraints = [
            # A CheckConstraint, so PostgreSQL refuses it however the row is written. The
            # admin's form checks it too, and shows the message.
            models.CheckConstraint(
                condition=~Q(kind="unpublished") | Q(published=False),
                name="unpublished_is_not_published",
                violation_error_message="An unpublished group result can't be marked published.",
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def label(self):
        """How the reference is cited: "Mandle 2017, Chem. Eur. J.", or "Mandle 2017" without
        a venue. Without a first author and a year, the identifier. An unpublished group
        result ends "(unpublished)" either way, so every page, chart and download that names
        it says so."""
        if not (self.first_author and self.year):
            label = self.identifier
        else:
            label = f"{self.first_author} {self.year}"
            if self.venue:
                label = f"{label}, {self.venue}"
        return f"{label} (unpublished)" if self.kind == self.Kind.UNPUBLISHED else label

    def has_doi(self):
        return self.kind in (self.Kind.ARTICLE, self.Kind.PREPRINT)

    @classmethod
    def kind_for_doi(cls, doi):
        """Preprint for a DOI that a preprint server gave, otherwise journal article. The DOI
        is cleaned first, so 10.48550/arXiv.2505.03009, as arXiv writes it, is a preprint."""
        return cls.Kind.PREPRINT if clean_doi(doi).startswith(PREPRINT_DOIS) else cls.Kind.ARTICLE

    @property
    def link(self):
        """Where to read it: doi.org for a DOI, an Espacenet search for a patent number,
        and "" for a thesis or an unpublished result, which have no standard address."""
        if self.has_doi():
            # quote() turns characters that mean something in an address, such as # and ?,
            # into %23 and %3F, so they reach doi.org as part of the DOI.
            return f"https://doi.org/{quote(self.identifier, safe='/')}"
        if self.kind == self.Kind.PATENT:
            return "https://worldwide.espacenet.com/patent/search?" + urlencode(
                {"q": f"pn={self.identifier}"}
            )
        return ""

    def clean(self):
        """Clean up a DOI or patent number before Django checks it is unique, so a DOI pasted
        as https://doi.org/10.1002/CHEM.201702742 matches 10.1002/chem.201702742, and
        EP 3260450 A1 matches EP3260450A1."""
        if self.has_doi():
            self.identifier = clean_doi(self.identifier)
        elif self.kind == self.Kind.PATENT:
            self.identifier = clean_patent_number(self.identifier)

    def is_public(self):
        """Whether this reference is public now, by the rule (ReferenceQuerySet.public())."""
        return Reference.objects.public().filter(pk=self.pk).exists()


class WaitingSave(models.Model):
    """A curator's Save on Add data, waiting for an admin's check: who saved it, when, and from
    which reference. The phase sequences and series it added point to it (their waiting_save)
    and are saved unapproved, so they are hidden. It also holds the labels its cards gave
    materials already in FFDB, which become aliases only when it is approved, where they can
    (label_problem()). Its page in the admin shows the labels, so the admin sees what the curator
    called a hidden material whose structure they typed (CompoundCard.save()). Approving or
    rejecting it (approve(), reject()) deletes it, so every save in this table is waiting. An
    admin's Save is approved at once and makes none."""

    # CASCADE: the save's rows already stop the reference being deleted (PROTECT), so this
    # deletes only a save with no rows left.
    reference = models.ForeignKey(Reference, on_delete=models.CASCADE, related_name="waiting_saves")
    # SET_NULL, as for Entered.entered_by: deleting the account keeps the save.
    saved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # auto_now_add: a waiting save is only ever made by Save, never by an import.
    saved_at = models.DateTimeField(auto_now_add=True)
    # {"12": "MB-1"}: an LCR number, as text because JSON's keys are, and the label to keep as
    # one of that material's aliases. By number, not a ForeignKey: retiring and restoring the
    # material keeps it.
    labels = models.JSONField(
        default=dict, blank=True,
        help_text="The labels to keep as aliases when it is approved, by the material's LCR number.",
    )

    def __str__(self):
        return f"Save {self.pk} from {self.reference}"

    def materials(self):
        """The materials its sequences and series are for. pk__in, as in Material.objects.public(),
        gives each once."""
        return Material.objects.filter(
            Q(pk__in=self.phase_sequences.values("material")) | Q(pk__in=self.series.values("material"))
        )

    def approve(self):
        """Tick approved on its sequences and series, keep its labels as aliases (keep_labels()),
        and delete it, which leaves its rows with no waiting save (SET_NULL). All of it or none.
        Its data is then public, or hidden while its reference isn't public. Returns what
        keep_labels() returns."""
        with transaction.atomic():
            # update(), as in Material.merge_into(): one query per table. save() isn't run, and
            # needn't be: nothing it works out depends on the switch.
            self.phase_sequences.update(approved=True)
            self.series.update(approved=True)
            kept, refused = self.keep_labels()
            self.delete()
        return kept, refused

    def label_problem(self, material, label):
        """Why label, held for material, won't be kept as one of its aliases, or "" when it will.
        Approving keeps a label only under a published reference, as for an admin's own Save, and
        only where Material.alias_problem() allows, asked when it is approved because the
        material's names may have changed since the save. The published switch, not
        Reference.is_public(): a reference with no public data yet is public once this save's rows
        are approved. The save's page asks it before approving, and keep_labels() as it approves,
        so the two agree."""
        if not self.reference.published:
            return "the reference isn't public"
        return material.alias_problem(label)

    def keep_labels(self):
        """Add each label it holds to its material's aliases, unless label_problem() says why not.
        Returns (kept, refused): [(LCR ID, label), ...] and [(LCR ID, label, why), ...]."""
        kept, refused = [], []
        for number, label in self.labels.items():
            lcr = accession(int(number))
            material = Material.objects.filter(pk=number).first()
            if material is None:
                problem = f"{lcr} is no longer in FFDB"  # retired or merged since the save
            else:
                problem = self.label_problem(material, label)
            if problem:
                refused.append((lcr, label, problem))
            else:
                material.add_aliases([label])
                material.save(update_fields=["aliases"])
                kept.append((lcr, label))
        return kept, refused

    def material_status(self, material):
        """What approving this save means for one of its materials, for the save's page: "public"
        when it is public already, and the save adds to its page; "new" when it isn't, and has no
        data but this save's, which is what Reject would retire; "hidden" when it isn't public and
        has other data, such as a hidden material whose structure a curator typed, which their card
        showed as new (CardMaterialForm.read_structure()). exclude() keeps the rows in no save: a
        nullable link that isn't this save is any other save or none."""
        if material.is_public():
            return "public"
        others = material.phase_sequences.exclude(waiting_save=self).exists() or material.series.exclude(
            waiting_save=self
        ).exists()
        return "hidden" if others else "new"

    def reject(self, user):
        """Delete its sequences and series, which takes their transitions and points with them,
        retire (Material.retire(), by user) each of its materials left with nothing, and delete
        it. All of it or none. Left with nothing: no sequence or series, not listed, and not a
        component of a mixture, which PROTECT would refuse to retire anyway. This can be a
        material the save didn't add, if it had nothing before the save either. Returns the
        retired materials, as text, since retiring takes their pks."""
        with transaction.atomic():
            # Read before the rows go, which takes the materials out of materials(). Mixtures
            # first (False sorts before True), so a compound only in one of them can go after it.
            materials = sorted(self.materials().order_by("pk"), key=lambda m: m.kind != Material.Kind.MIXTURE)
            self.phase_sequences.all().delete()
            self.series.all().delete()
            retired = []
            for material in materials:
                if material.listed or material.phase_sequences.exists() or material.series.exists():
                    continue
                if material.in_mixtures.exists():
                    continue
                retired.append(str(material))
                # It isn't public now, so its retired ID isn't either (was_public), and its address
                # answers 404.
                material.retire(user)
            self.delete()
        return retired


class PhaseSequence(Entered):
    """One material's phase sequence as one reference reports it: a literature summary, from
    the solid up ("K 139.8 NF 132.7 N 187.9 I"), or one heating or cooling run, in the order
    observed ("Iso 187.9 N 132.7 NF 70 Cr" on cooling)."""

    # The stored values match Series.Direction's. Cooling and heating are also what
    # phases.parse_run() takes (phases.COOLING and phases.HEATING).
    class Direction(models.TextChoices):
        SUMMARY = "summary", "Summary"
        COOLING = "cooling", "Cooling"
        HEATING = "heating", "Heating"
        NOT_STATED = "unspecified", "Not stated"

    # Deleting a material deletes its sequences. A reference that has sequences can't be
    # deleted: they would be left without one, and every value needs its reference.
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="phase_sequences")
    reference = models.ForeignKey(Reference, on_delete=models.PROTECT, related_name="phase_sequences")
    # Summary is the default because the sequences imported from the literature are summaries,
    # and adding this field gave every sequence already in FFDB the default.
    direction = models.CharField(
        max_length=12, choices=Direction.choices, default=Direction.SUMMARY,
        help_text="Summary: the reference's summary, heating and cooling together. Otherwise one run.",
    )
    sequence = models.CharField(
        max_length=300,
        help_text='As the reference gives it, with the transition temperatures (°C) between phases. A '
        'summary goes from the solid up, e.g. "K 139.8 NF 132.7 N 187.9 I"; a run in the order observed, '
        'e.g. "Iso 187.9 N 132.7 NF 70 Cr" on cooling. Labels such as K, I or N_X are converted to the '
        "vocabulary's codes.",
    )
    rate_k_per_min = models.FloatField("rate (K/min)", null=True, blank=True)
    method = models.CharField(max_length=100, blank=True, help_text="e.g. DSC or POM.")
    lowest_temperature_c = models.FloatField(
        "lowest temperature reached (°C)", null=True, blank=True,
        help_text="Where the reference gives it, so readers know how far down a phase was looked for.",
    )
    notes = models.CharField(max_length=300, blank=True)
    # Off unless something turns it on, so a sequence nobody checked stays hidden.
    approved = models.BooleanField(default=False, help_text="An admin has checked it.")
    # The curator's save it waits in. SET_NULL: approving deletes the save and keeps the sequence.
    waiting_save = models.ForeignKey(
        WaitingSave, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="phase_sequences"
    )

    # Worked out from the sequence on every save (see save()), never typed in.
    standard_sequence = models.CharField(max_length=300, blank=True, editable=False)
    phases = models.ManyToManyField(
        Phase, blank=True, editable=False, related_name="phase_sequences",
        help_text="The phases in the sequence, so materials can be found by phase.",
    )
    t_clearing_c = models.FloatField("clearing point (°C)", null=True, blank=True, editable=False)
    t_melting_c = models.FloatField("melting point (°C)", null=True, blank=True, editable=False)

    objects = PhaseSequenceQuerySet.as_manager()

    class Meta:
        ordering = ["material__name", "reference__identifier", "id"]

    def __str__(self):
        return f"{self.material}: {self.sequence}"

    def parsed(self, synonyms=None):
        """The sequence read by the parser for its direction, under the vocabulary's codes.
        Raises PhaseSequenceError, with the reason, when it can't be read. Pass synonyms
        (Phase.synonym_map()) when reading many, so the vocabulary is looked up once."""
        if synonyms is None:
            synonyms = Phase.synonym_map()
        if self.direction == self.Direction.SUMMARY:
            return parse_summary(self.sequence, synonyms)
        stated = self.direction if self.direction in (self.Direction.COOLING, self.Direction.HEATING) else None
        return parse_run(self.sequence, synonyms, stated)

    def run_text(self):
        """How the sequence was observed, as the pages show it: "Summary", or e.g.
        "Cooling, 5 K/min, DSC, down to 20 °C", with whatever of those the reference gives."""
        parts = [self.get_direction_display()]
        if self.rate_k_per_min is not None:
            parts.append(f"{self.rate_k_per_min:g} K/min")
        if self.method:
            parts.append(self.method)
        if self.lowest_temperature_c is not None:
            parts.append(f"down to {self.lowest_temperature_c:g} °C")
        return ", ".join(parts)

    def phase_was_renamed(self):
        """Whether reading the sequence gave a liquid-crystal phase a different name, e.g. NX
        read as NS, so the material page shows the sequence as reported too. Labels are
        compared as the vocabulary compares them, so Nf for NF doesn't count. Nor do the
        solids and the isotropic liquid: nearly every paper writes them K and I."""
        pairs = zip(phase_labels(self.sequence), phase_labels(self.standard_sequence))
        return any(
            code not in NOT_LIQUID_CRYSTAL and normalise_label(label) != normalise_label(code)
            for label, code in pairs
        )

    def clean(self):
        """Refuse a sequence the parser can't read, with the parser's message."""
        if not self.sequence.strip():
            return  # the form's "This field is required" already covers a blank sequence
        try:
            self.parsed()
        except PhaseSequenceError as exc:
            raise ValidationError({"sequence": str(exc)})

    def save(self, *args, **kwargs):
        parsed = self.parsed()  # an unreadable sequence stops the save here
        self.standard_sequence = parsed.standard_text()
        clearing = [t.temperature_c for t in parsed.transitions if t.upper == ISOTROPIC]
        self.t_clearing_c = max(clearing) if clearing else None
        self.t_melting_c = parsed.melting_point()
        super().save(*args, **kwargs)
        # Links to phases and transitions need the sequence's primary key, so they come after
        # the save. set() adds the missing phase links and removes the others. The transitions
        # are deleted and added again: simpler than working out which ones changed.
        phases = {phase.code: phase for phase in Phase.objects.filter(code__in=parsed.phases)}
        self.phases.set(phases.values())
        self.transitions.all().delete()
        PhaseTransition.objects.bulk_create(
            PhaseTransition(sequence=self, upper=phases[t.upper], lower=phases[t.lower], temperature_c=t.temperature_c)
            for t in parsed.transitions
        )


class PhaseTransition(models.Model):
    """One transition in a sequence, worked out from it on save (see PhaseSequence.save()),
    so materials can be found by the temperature at which a phase forms from the phase above.
    It is stored the same way whatever the sequence's direction: a heating run's NF to N at
    132.7 °C is stored as N above NF below at 132.7 °C, as a cooling run's N to NF would be."""

    sequence = models.ForeignKey(PhaseSequence, on_delete=models.CASCADE, related_name="transitions")
    # PROTECT: deleting a phase would otherwise quietly delete these rows, and the
    # temperature search would go wrong without saying so. related_name="+" gives Phase no
    # way back to them, which nothing needs.
    upper = models.ForeignKey(Phase, on_delete=models.PROTECT, related_name="+", help_text="The phase stable above.")
    lower = models.ForeignKey(Phase, on_delete=models.PROTECT, related_name="+", help_text="The phase that forms below it.")
    temperature_c = models.FloatField("temperature (°C)")

    class Meta:
        ordering = ["-temperature_c"]  # from the top down, as on cooling

    def __str__(self):
        return f"{self.upper.code} → {self.lower.code} at {self.temperature_c:g} °C"


class PropertyGroup(models.Model):
    """One physical quantity whose components are separate properties, e.g. the refractive
    index: Δn, nₑ and nₒ. A group is only for showing them together: each component keeps
    its own unit and conditions, and a group never combines values. It has no sort order:
    lists show it where its first member would be (see group_properties())."""

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def is_name_list(value):
    """Whether value is a list of names, e.g. ["temperature", "method"]."""
    return isinstance(value, list) and all(isinstance(name, str) and name.strip() for name in value)


class Property(models.Model):
    """One property that values can be given for, e.g. Ps, with the unit every value of it
    is stored in and the conditions every value must state."""

    code = models.SlugField(unique=True, help_text="A short name for addresses and imports, e.g. ps.")
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20, help_text="As tables and charts show it, e.g. Ps.")
    unit = models.CharField(max_length=30, help_text="All values of this property are stored in this unit.")
    # PROTECT: a group can't be deleted while properties are in it.
    group = models.ForeignKey(
        PropertyGroup, on_delete=models.PROTECT, null=True, blank=True, related_name="properties",
        help_text="The quantity this property is one component of, e.g. Refractive index for nₑ. "
        "Blank for a property that is its own quantity, such as Ps.",
    )
    required_conditions = models.JSONField(
        default=list,
        blank=True,
        help_text='What every value must state, as a list, e.g. ["temperature", "method", "frequency_Hz"].',
    )
    required_conditions_by_kind = models.JSONField(
        "required conditions by kind",
        default=dict,
        blank=True,
        help_text='A different list for one kind of value, e.g. {"computed": ["method", "conformer_protocol"]}: '
        "a computed d33 names no reference crystal. Kinds not given here use the required conditions above.",
    )
    description = models.TextField(blank=True)
    methods = models.JSONField(
        default=list,
        blank=True,
        help_text='The usual methods, as a list, e.g. ["current reversal", "P–E hysteresis loop"]. Add data '
        "offers them as suggestions, so one method is written one way.",
    )
    sort_order = models.PositiveSmallIntegerField(
        default=100, help_text="Lists show properties from the lowest number up."
    )

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "properties"

    def __str__(self):
        return f"{self.symbol} ({self.unit})"

    def conditions_for(self, kind):
        """What a value of this kind (measured, extrapolated or computed) must state: the
        kind's own list if it has one, otherwise the required conditions. It returns a copy,
        so changing the list doesn't change the property."""
        by_kind = self.required_conditions_by_kind or {}
        return list(by_kind.get(kind, self.required_conditions or []))

    def clean(self):
        """Refuse required conditions or methods in the wrong shape. A JSON field takes any
        JSON, and conditions_for() would read "temperature" as the letters t, e, m, ... without
        saying so."""
        # An emptied box in the admin arrives as None: it means no conditions, or no methods.
        if self.required_conditions is None:
            self.required_conditions = []
        if self.required_conditions_by_kind is None:
            self.required_conditions_by_kind = {}
        if self.methods is None:
            self.methods = []
        errors = {}
        if not is_name_list(self.required_conditions):
            errors["required_conditions"] = 'Give a list of names, e.g. ["temperature", "method"].'
        if not is_name_list(self.methods):
            errors["methods"] = 'Give a list of names, e.g. ["current reversal", "P–E hysteresis loop"].'
        by_kind = self.required_conditions_by_kind
        if not (isinstance(by_kind, dict) and all(is_name_list(names) for names in by_kind.values())):
            errors["required_conditions_by_kind"] = (
                'Give each kind a list of names, e.g. {"computed": ["method", "conformer_protocol"]}.'
            )
        if errors:
            raise ValidationError(errors)


def group_properties(properties):
    """Properties as lists show them: [(group, [its members]), ...], with group None for a
    property in no group, one entry each. A group sits where its first member would be, and
    its members keep their own order. Pass properties with select_related("group"), or
    each group costs a query."""
    entries, members = [], {}
    for prop in sorted(properties, key=lambda p: (p.sort_order, p.name)):
        if prop.group_id is None:
            entries.append((None, [prop]))
        elif prop.group_id in members:
            # The group's entry holds this same list, so the member shows up there too.
            members[prop.group_id].append(prop)
        else:
            members[prop.group_id] = [prop]
            entries.append((prop.group, members[prop.group_id]))
    return entries


class Variable(models.Model):
    """What a series is plotted against: its x-axis, e.g. temperature or frequency. Two
    are special, and are found by their codes: against Temperature, each point's
    temperature is its x; against Nothing, a value has no x at all, e.g. a gas-phase
    calculation. Changing either code in the admin would break Measurement.x_value()."""

    TEMPERATURE = "temperature"
    NONE = "none"

    class Scale(models.TextChoices):
        LINEAR = "linear", "Linear"
        LOG = "log", "Logarithmic"

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    unit = models.CharField(
        max_length=30, blank=True,
        help_text="Every x value of this variable is stored in this unit. Blank for Nothing.",
    )
    scale = models.CharField(
        max_length=10, choices=Scale.choices, default=Scale.LINEAR, help_text="How its chart axis is drawn."
    )
    condition = models.CharField(
        max_length=50,
        blank=True,
        help_text='The required condition a series against this variable states through its points, e.g. '
        '"frequency_Hz": a frequency sweep gives its frequencies. Blank if there is none.',
    )
    sort_order = models.PositiveSmallIntegerField(
        default=100, help_text="Lists show variables from the lowest number up."
    )

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.unit})" if self.unit else self.name


# How messages and pages name the conditions given in a series' Conditions box. A key not
# listed here is shown as it is. Temperature and method have their own boxes, so they need
# no name.
CONDITION_NAMES = {
    "frequency_Hz": "frequency",
    "cell_gap_um": "cell gap",
    "wavelength_nm": "wavelength",
    "field_V_um": "field",
    "reference_crystal": "reference crystal",
    "electrode_coating": "electrode coating",
    "conformer_protocol": "conformer protocol",
    "solvent": "solvent or gas phase",
    "local_field_model": "local-field model",
    "order_parameter_P1": "⟨P1⟩ assumed",
    "order_parameter_P3": "⟨P3⟩ assumed",
    "density_kg_m3": "density assumed",
}

# The unit of each condition that has one, as pages write it after the value. The key's
# ending says it too: frequency_Hz is in Hz.
CONDITION_UNITS = {
    "frequency_Hz": "Hz",
    "cell_gap_um": "µm",
    "wavelength_nm": "nm",
    "field_V_um": "V/µm",
    "density_kg_m3": "kg/m³",
}


def must_state(prop, missing):
    """The message refusing values of prop that leave out the required conditions in
    missing, e.g. 'Ps values must state: frequency ("frequency_Hz" in Conditions). ...'"""
    readable = ", ".join(
        key if key in ("temperature", "method") else f'{CONDITION_NAMES.get(key, key)} ("{key}" in Conditions)'
        for key in missing
    )
    return (
        f"{prop.symbol} values must state: {readable}. "
        "Without these, values from different references can't be compared."
    )


class Series(Entered):
    """One curve as its reference reports it: values of one property of one material
    against one variable (temperature, frequency, ...), with everything its points share.
    A single value is a series of one point, so every page and export has one path. The
    points are Measurement rows."""

    # The stored values are the kinds Property.conditions_for() takes.
    class Kind(models.TextChoices):
        MEASURED = "measured", "Measured"
        EXTRAPOLATED = "extrapolated", "Extrapolated (e.g. from mixtures)"
        COMPUTED = "computed", "Computed (DFT, MD, ...)"

    class Direction(models.TextChoices):
        COOLING = "cooling", "Cooling"
        HEATING = "heating", "Heating"
        NOT_STATED = "unspecified", "Not stated"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    class TakenFrom(models.TextChoices):
        TABLE = "table", "Table"
        TEXT = "text", "Text"
        FIGURE = "figure", "Figure"
        DATASET = "dataset", "Data file (deposited, or the group's own)"
        # For lists that don't record it, such as LCPROP's values.
        NOT_RECORDED = "", "Not recorded"

    # Deleting a material deletes its series. A reference that has series can't be
    # deleted, as with sequences, and nor can a property or variable that series use.
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="series")
    reference = models.ForeignKey(Reference, on_delete=models.PROTECT, related_name="series")
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="series")
    variable = models.ForeignKey(
        Variable, on_delete=models.PROTECT, related_name="series", help_text="What the values are plotted against."
    )
    # No default, so the admin makes you choose: a default of measured would quietly save
    # a computed value as measured, and kinds are never mixed.
    kind = models.CharField(max_length=12, choices=Kind.choices)
    method = models.CharField(max_length=100, blank=True)
    conditions = models.JSONField(
        default=dict,
        blank=True,
        help_text='Conditions fixed for the whole series, as key: value pairs, e.g. {"frequency_Hz": 100, '
        '"cell_gap_um": 5}. The variable itself isn\'t one of them.',
    )
    direction = models.CharField(
        max_length=16, choices=Direction.choices, default=Direction.NOT_STATED,
        help_text="Heating or cooling, if the reference says. Not applicable: no temperature sweep, e.g. a "
        "single value at a fixed temperature or a frequency sweep.",
    )
    taken_from = models.CharField(
        max_length=10, choices=TakenFrom.choices, default=TakenFrom.NOT_RECORDED, blank=True,
        help_text="Where in the reference the values are.",
    )
    location = models.CharField(
        "where in the reference", max_length=100, blank=True,
        help_text="e.g. Fig. 3b, Table S2, [0129], or a data file's name.",
    )
    digitised = models.BooleanField("digitised from figure", default=False)
    notes = models.CharField(max_length=300, blank=True)
    # Off unless something turns it on, so a series nobody checked stays hidden.
    approved = models.BooleanField(default=False, help_text="An admin has checked it.")
    # As for a sequence: the curator's save it waits in.
    waiting_save = models.ForeignKey(
        WaitingSave, on_delete=models.SET_NULL, null=True, blank=True, editable=False, related_name="series"
    )

    objects = SeriesQuerySet.as_manager()

    class Meta:
        ordering = ["property__sort_order", "reference__identifier", "id"]
        verbose_name_plural = "series"
        constraints = [
            # As in clean(), which says it beside the field. The database holds it for every way in.
            models.CheckConstraint(
                condition=Q(digitised=False) | Q(taken_from="figure"),
                name="digitised_only_from_figure",
                violation_error_message="Only values read off a figure are digitised.",
            ),
        ]

    def __str__(self):
        if self.is_against_nothing():
            return f"{self.material}: {self.property.symbol}, {self.reference}"
        return f"{self.material}: {self.property.symbol} against {self.variable.name.lower()}, {self.reference}"

    def is_against_temperature(self):
        return self.variable.code == Variable.TEMPERATURE

    def is_against_nothing(self):
        """A value with nothing to plot it against, e.g. a gas-phase calculation."""
        return self.variable.code == Variable.NONE

    def missing_conditions(self):
        """The property's required conditions, for this kind of value, that the series
        doesn't state. Two are left to the points: temperature, which Measurement.clean()
        checks on each one, and the condition that is the series' variable, because a
        frequency sweep gives its frequencies as the points' x."""
        stated_by_points = {"temperature", self.variable.condition}
        missing = []
        for key in self.property.conditions_for(self.kind):
            if key in stated_by_points:
                continue
            value = self.method.strip() if key == "method" else self.conditions.get(key)
            if value in (None, ""):
                missing.append(key)
        return missing

    def clean(self):
        """Refuse a series that breaks a rule for values, saying what is wrong or missing.
        Every problem is reported at once, each on the field to fix where there is one."""
        # An emptied Conditions box in the admin arrives as None, which the database
        # refuses: it means no conditions.
        if self.conditions is None:
            self.conditions = {}
        if not isinstance(self.conditions, dict):
            # The other checks read the conditions as key: value pairs, so stop here.
            raise ValidationError(
                {"conditions": 'Give the conditions as key: value pairs, e.g. {"frequency_Hz": 100}.'}
            )
        errors = {}
        if self.digitised and self.taken_from != self.TakenFrom.FIGURE:
            errors["digitised"] = "Only values read off a figure are digitised: choose Figure under Taken from."
        # Without a property and a variable there is nothing to check the conditions
        # against, and the form already says to choose them.
        if self.property_id and self.variable_id:
            variable = self.variable
            if variable.condition and variable.condition in self.conditions:
                errors["conditions"] = (
                    f"{variable.name} is what this series varies, so it can't also be a fixed "
                    "condition: give it on each point instead."
                )
            missing = self.missing_conditions()
            if missing:
                # It can name the method and conditions together, so it belongs to no one field.
                errors[NON_FIELD_ERRORS] = must_state(self.property, missing)
        if errors:
            raise ValidationError(errors)


class Measurement(models.Model):
    """One point of a series: a value, with the temperature it was measured at and, for a
    series against another variable, that variable's value (x)."""

    class Qualifier(models.TextChoices):
        EXACT = "exact", "= exact"
        UPPER = "upper", "< upper limit"
        LOWER = "lower", "> lower limit"

    SIGNS = {Qualifier.EXACT: "=", Qualifier.UPPER: "<", Qualifier.LOWER: ">"}

    series = models.ForeignKey(Series, on_delete=models.CASCADE, related_name="points")
    qualifier = models.CharField(
        "value is", max_length=5, choices=Qualifier.choices, default=Qualifier.EXACT,
        help_text='For "not detected", choose < and enter the detection limit, not 0.',
    )
    value = models.FloatField()
    uncertainty = models.FloatField("±", null=True, blank=True)
    temperature_c = models.FloatField(
        "T (°C)", null=True, blank=True,
        help_text="For a series against another variable, the temperature it was measured at.",
    )
    x = models.FloatField(
        null=True, blank=True,
        help_text="The series' variable at this point, in its unit. Empty for a series against temperature "
        "(its x is T) and for a value against nothing.",
    )

    objects = MeasurementQuerySet.as_manager()

    class Meta:
        # series_id, not series: "series" would sort by Series's own ordering, through a join.
        ordering = ["series_id", "temperature_c", "x", "id"]
        constraints = [
            # As in clean(), which says it beside the field. The database holds it for every way in.
            models.CheckConstraint(
                condition=Q(qualifier="exact") | Q(uncertainty__isnull=True),
                name="limit_has_no_uncertainty",
                violation_error_message="A limit has no ±: enter the limit itself as the value.",
            ),
        ]

    def __str__(self):
        prop = self.series.property
        return f"{self.series.material} {prop.symbol} {self.SIGNS[self.qualifier]} {self.value:g} {prop.unit}"

    def is_limit(self):
        """Whether the value is a limit, < or >, rather than a value."""
        return self.qualifier != self.Qualifier.EXACT

    def x_value(self):
        """Where the point goes on its series' x-axis: its temperature for a series against
        temperature, x for one against anything else, and None for a value against nothing."""
        if self.series.is_against_nothing():
            return None
        return self.temperature_c if self.series.is_against_temperature() else self.x

    def clean(self):
        """Refuse a point that breaks a rule for values, saying what is wrong or missing.
        Every problem is reported at once, each on the field to fix."""
        errors = {}
        if self.qualifier != self.Qualifier.EXACT and self.uncertainty is not None:
            errors["uncertainty"] = "A limit has no ±: enter the limit itself as the value."
        try:
            # On the admin's Add page this is the series being added, not saved yet.
            series = self.series
        except Series.DoesNotExist:
            series = None  # the form already says a point needs a series
        if series is not None and series.property_id and series.variable_id:
            errors.update(self.position_errors(series))
        if errors:
            raise ValidationError(errors)

    def position_errors(self, series):
        """What is wrong with this point's x and temperature for its series, as
        {field: message}: what each point must give depends on what the series is against."""
        errors = {}
        variable, prop = series.variable, series.property
        if series.is_against_nothing():
            # One way to store each value: a value with a temperature is against temperature.
            if self.x is not None:
                errors["x"] = "A value against nothing has no x."
            if self.temperature_c is not None:
                errors["temperature_c"] = (
                    "A value with a temperature is plotted against temperature: choose Temperature "
                    "as what the series is against."
                )
        elif series.is_against_temperature():
            if self.x is not None:
                errors["x"] = "A series against temperature gives each point's temperature as T, not x."
        elif self.x is None:
            errors["x"] = f"Give the {variable.name.lower()} ({variable.unit}) of each point."
        elif variable.scale == Variable.Scale.LOG and self.x <= 0:
            errors["x"] = f"{variable.name} is drawn on a log axis, so it must be more than 0."
        if self.temperature_c is None:
            if "temperature" in prop.conditions_for(series.kind):
                errors["temperature_c"] = must_state(prop, ["temperature"])
            elif series.is_against_temperature():
                errors["temperature_c"] = (
                    "Give each point's temperature, or choose Nothing as what the series is against "
                    "for a value with nothing to plot it against (e.g. a gas-phase calculation)."
                )
        return errors


def missing_links(rows):
    """What the rows read back from a snapshot point to outside it that is no longer in FFDB,
    as {model: {primary keys}}: their foreign keys, such as a sequence's reference, and their
    many-to-many links, a sequence's phases. Links between rows in the snapshot, such as a
    point's series, are left out: those rows come back together."""
    in_snapshot = {(type(row.object), row.object.pk) for row in rows}
    wanted = defaultdict(set)  # {model: {primary keys the rows point to}}
    for row in rows:
        for field in row.object._meta.concrete_fields:
            if not field.is_relation:
                continue
            pk = getattr(row.object, field.attname)  # e.g. reference_id: read without a query
            if pk is not None and (field.related_model, pk) not in in_snapshot:
                wanted[field.related_model].add(pk)
        for name, pks in row.m2m_data.items():  # {"phases": [primary keys]}
            wanted[row.object._meta.get_field(name).related_model].update(pks)
    missing = {}
    for model, pks in wanted.items():
        gone = pks - set(model.objects.filter(pk__in=pks).values_list("pk", flat=True))
        if gone:
            missing[model] = gone
    return missing


class RetiredID(models.Model):
    """An LCR ID whose material has been deleted (Material.retire()), so that what it was is
    kept, and the material can be put back from the snapshot. PostgreSQL never gives a
    number out twice, so only retired IDs need a table: while a material exists, its ID is
    its primary key."""

    number = models.PositiveIntegerField(primary_key=True, help_text="The material's primary key: LCR-00012 is 12.")
    kind = models.CharField(max_length=8, choices=Material.Kind.choices)
    name = models.CharField(max_length=200, blank=True)
    inchikey = models.CharField("InChIKey", max_length=27, blank=True)
    # SET_NULL, as for Entered.entered_by: deleting the account keeps the record.
    retired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    retired_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(
        blank=True,
        help_text="Why it was deleted, e.g. entered twice, or not a liquid crystal. Shown on the ID's public page.",
    )
    # A number, not a ForeignKey to Material: it still names the ID it went into if that
    # material is deleted later, when a ForeignKey would be blanked or delete this record.
    merged_into = models.PositiveIntegerField(
        null=True, blank=True, editable=False,
        help_text="After a merge: the number of the material it went into. Its address redirects there.",
    )
    # What a merge moved, so it can be undone: {"sequences": [primary keys], "series": [...],
    # "memberships": [...], "merges": [numbers of the earlier merges pointed on], "aliases":
    # [names added]}. Empty for a material deleted on its own.
    moved = models.JSONField(default=dict, blank=True, editable=False)
    # The JSON text from Material.snapshot(), as serializers.deserialize("json", ...) reads it back.
    snapshot = models.TextField(editable=False)
    # Off unless retire() turns it on, so a record nobody said was public leaves no trace.
    # Whether the address answers is RetiredIDQuerySet.public().
    was_public = models.BooleanField(
        default=False, editable=False,
        help_text="Whether the material was public when it was retired. If not, its address answers not found.",
    )

    objects = RetiredIDQuerySet.as_manager()

    class Meta:
        ordering = ["-retired_at"]
        verbose_name = "retired ID"

    def __str__(self):
        return f"{self.accession} {self.name}"

    @property
    def accession(self):
        return accession(self.number)

    @classmethod
    def structure_problem(cls, inchikey):
        """Why a compound with this InChIKey can't be added to FFDB, or "" when it can: it was a
        retired ID's structure, and a structure has one ID. Restoring is the only way a retired
        ID comes back, and a merged one's data is with the material it went into. Should two
        records have it, the most recently retired is named (Meta's ordering). A material that
        was never public is ignored: naming it would leave a trace of it. Its structure can be
        added again, and restore() then refuses to bring it back."""
        record = cls.objects.public().filter(inchikey=inchikey).first() if inchikey else None
        if record is None:
            return ""
        was = f"This structure was {record.accession} ({record.name})"
        if record.merged_into is not None:
            return f"{was}, merged into {accession(record.merged_into)}: add the data there."
        return f"{was}, now retired: an admin can restore {record.accession}."

    def restore(self):
        """Put the material back from the snapshot, under its old number, with every row
        deleted with it, then delete this record: all of it or none. What comes back is what
        would be there had the material never been deleted, so it is refused, raising
        ValidationError with every reason, when something has changed that would have
        stopped that. A merged record is refused: its rows were moved, not deleted, so they aren't
        in the snapshot. Returns the material."""
        if self.merged_into is not None:
            raise ValidationError(
                f"it was merged into {accession(self.merged_into)}, and its sequences and values are there "
                "now: Restore doesn't undo a merge."
            )
        try:
            # Each row as a DeserializedObject, which holds the model instance, not yet saved,
            # as .object. Reading them makes no queries.
            rows = list(serializers.deserialize("json", self.snapshot))
        except DeserializationError as exc:
            # A migration since the delete has changed the tables, e.g. removed a field.
            raise ValidationError(f"the snapshot no longer fits FFDB's tables ({exc}).")
        missing = missing_links(rows)
        # A deleted account doesn't stop it: entered by goes back blank, as SET_NULL would
        # have left it had the material stayed (see Entered.entered_by). Nor does a waiting
        # save that has gone, approved or rejected since: the link goes back blank too.
        gone_users = missing.pop(get_user_model(), set())
        gone_saves = missing.pop(WaitingSave, set())
        problems = []
        if self.inchikey:  # a compound's: a mixture's is blank
            clash = Material.objects.filter(inchikey=self.inchikey).first()
            if clash:
                problems.append(
                    f"its structure is now in FFDB as {clash.accession} ({clash.name}), and a structure has one ID."
                )
        for model, pks in missing.items():
            for pk in sorted(pks):
                name = accession(pk) if model is Material else f"{model._meta.verbose_name} {pk}"
                problems.append(f"{name} is no longer in FFDB.")
        # A compound can't be made a mixture while it is a component, but it can once its
        # mixture has gone.
        components = [row.object.component_id for row in rows if isinstance(row.object, MixtureComponent)]
        for component in Material.objects.filter(pk__in=components, kind=Material.Kind.MIXTURE):
            problems.append(f"its component {component.accession} is now a mixture, and a component must be a compound.")
        if problems:
            raise ValidationError(problems)
        with transaction.atomic():
            for row in rows:  # each parent before its children, as snapshot() wrote them
                if isinstance(row.object, Entered) and row.object.entered_by_id in gone_users:
                    row.object.entered_by = None
                if isinstance(row.object, (PhaseSequence, Series)) and row.object.waiting_save_id in gone_saves:
                    row.object.waiting_save = None
                # A raw save: the model's own save() isn't run, so nothing is worked out again
                # and each row comes back as it was. force_insert: add the row, never change
                # one that is already there.
                row.save(force_insert=True)
            self.delete()
        return rows[0].object


class NotDuplicate(models.Model):
    """Two materials an admin has said are not one compound entered twice, on the possible-
    duplicates page (MaterialAdmin.duplicates_view), so the pair isn't listed there again. Kept by
    material, not by structure: CASCADE deletes the decision with either material, when it is
    retired or merged into another (Material.retire()), and a structure corrected since keeps it."""

    # The lower LCR number first (see Meta), so a pair has one way to be stored. related_name="+":
    # nothing needs to go from a material to its decisions. CASCADE still deletes them with it.
    first = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="+")
    second = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="+")
    # SET_NULL, as for WaitingSave.saved_by: deleting the account keeps the decision.
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "not-a-duplicate decision"
        constraints = [
            # Each pair once. On its own it would still allow (12, 40) and (40, 12), so the next
            # constraint says which way round a pair is stored.
            models.UniqueConstraint(fields=["first", "second"], name="each_pair_once"),
            # F("second") is the same row's second column: the database compares the two numbers.
            models.CheckConstraint(
                condition=Q(first__lt=F("second")),
                name="lower_number_first",
                violation_error_message="A pair is two different materials, the lower LCR number first.",
            ),
        ]

    def __str__(self):
        return f"{self.first.accession} and {self.second.accession}"

    @classmethod
    def mark(cls, a, b, user):
        """Record that materials a and b are not duplicates, decided by user, in either order.
        Marking a pair already marked keeps the first decision. Returns the decision."""
        first, second = sorted([a, b], key=lambda material: material.pk)
        decision, _ = cls.objects.get_or_create(first=first, second=second, defaults={"decided_by": user})
        return decision
