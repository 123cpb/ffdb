import re
from urllib.parse import quote, urlencode

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from . import chem
from .phases import (
    CRYSTAL,
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


class Material(models.Model):
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="As papers name it, e.g. RM734. Optional, and names can repeat: "
        "the LCR ID is what identifies a material.",
    )
    aliases = models.CharField(
        max_length=500,
        blank=True,
        help_text="Other names or codes, comma-separated.",
    )
    smiles = models.TextField("SMILES", help_text="Stored as typed.")
    # Worked out from the SMILES on every save (see save()), never typed in.
    canonical_smiles = models.TextField("canonical SMILES", blank=True, editable=False)
    # The full InChIKey identifies a compound, so the database allows each one once.
    inchikey = models.CharField("InChIKey", max_length=27, unique=True, blank=True, editable=False)
    notes = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

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

    def alias_list(self):
        """The aliases as a list: "RM-734, DIO-1" -> ["RM-734", "DIO-1"]."""
        return [alias.strip() for alias in self.aliases.split(",") if alias.strip()]

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
            return "its aliases are full: tidy them in the admin"
        return ""

    def clean(self):
        """Refuse an unreadable SMILES, and a structure that is already in FFDB."""
        if not self.smiles.strip():
            return  # the form's "This field is required" already covers a blank SMILES
        try:
            _, key = chem.standardise(self.smiles)
        except chem.StructureError as exc:
            raise ValidationError({"smiles": str(exc)})
        clash = Material.objects.filter(inchikey=key).exclude(pk=self.pk).first()
        if clash:
            raise ValidationError(
                {"smiles": f"This structure is already in FFDB as {clash.accession} ({clash.name})."}
            )

    def save(self, *args, **kwargs):
        self.canonical_smiles, self.inchikey = chem.standardise(self.smiles)
        super().save(*args, **kwargs)

    def possible_stereo_variants(self):
        """Other materials with the same connectivity: the same skeleton with different or
        missing stereochemistry. They are kept as separate materials."""
        if not self.inchikey:
            return Material.objects.none()
        skeleton = chem.skeleton_key(self.inchikey)
        return Material.objects.filter(inchikey__startswith=skeleton + "-").exclude(pk=self.pk)

    def stereo_warning(self):
        """The warning shown when possible_stereo_variants() finds any, or "" when it finds none.
        The admin, the import and the compound table show it; the table also asks it of a
        material not saved yet, which is called "This structure"."""
        variants = self.possible_stereo_variants()
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
        """The liquid-crystal phases in any of this material's sequences, each once, in the
        vocabulary's order. The database does the work: it follows the links from Phase to
        this material's sequences, and distinct() drops a phase that two sequences share."""
        return (
            Phase.objects.filter(phase_sequences__material=self)
            .exclude(code__in=NOT_LIQUID_CRYSTAL)
            .distinct()
        )


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
    """Where data comes from: a journal article, preprint, patent or thesis."""

    class Kind(models.TextChoices):
        ARTICLE = "article", "Journal article"
        PREPRINT = "preprint", "Preprint"
        PATENT = "patent", "Patent"
        THESIS = "thesis", "Thesis"

    # No default, so the admin makes you choose: a default of article would quietly save
    # an arXiv DOI as an article.
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # Unique on its own, so a DOI can't be entered once as an article and again as a preprint.
    identifier = models.CharField(
        max_length=500,
        unique=True,
        help_text="The DOI for an article or preprint, e.g. 10.1002/chem.201702742; the number "
        "for a patent, e.g. EP3260450A1; for a thesis, its author, year and title.",
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
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["kind", "identifier"]

    def __str__(self):
        return self.label

    @property
    def label(self):
        """How the reference is cited: "Mandle 2017, Chem. Eur. J.", or "Mandle 2017" without
        a venue. Without a first author and a year, the identifier."""
        if not (self.first_author and self.year):
            return self.identifier
        label = f"{self.first_author} {self.year}"
        return f"{label}, {self.venue}" if self.venue else label

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
        and "" for a thesis, which has no standard address."""
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

    # Worked out from the sequence on every save (see save()), never typed in.
    standard_sequence = models.CharField(max_length=300, blank=True, editable=False)
    phases = models.ManyToManyField(
        Phase, blank=True, editable=False, related_name="phase_sequences",
        help_text="The phases in the sequence, so materials can be found by phase.",
    )
    t_clearing_c = models.FloatField("clearing point (°C)", null=True, blank=True, editable=False)
    t_melting_c = models.FloatField("melting point (°C)", null=True, blank=True, editable=False)

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
        # The melting point is the temperature written after the crystal, when it comes first:
        # in a summary or a heating run. In a summary it is kept even when a monotropic phase
        # lies below it, so it isn't always one of the transitions. A cooling run ends with the
        # crystal, and the temperature it crystallises at isn't a melting point.
        self.t_melting_c = parsed.temperatures[0] if parsed.phases[0] == CRYSTAL and parsed.temperatures else None
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
        """Refuse required conditions in the wrong shape. A JSON field takes any JSON, and
        conditions_for() would read "temperature" as the letters t, e, m, ... without saying so."""
        # An emptied box in the admin arrives as None: it means no conditions.
        if self.required_conditions is None:
            self.required_conditions = []
        if self.required_conditions_by_kind is None:
            self.required_conditions_by_kind = {}
        errors = {}
        if not is_name_list(self.required_conditions):
            errors["required_conditions"] = 'Give a list of names, e.g. ["temperature", "method"].'
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

    class Meta:
        ordering = ["property__sort_order", "reference__identifier", "id"]
        verbose_name_plural = "series"

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

    class Meta:
        # series_id, not series: "series" would sort by Series's own ordering, through a join.
        ordering = ["series_id", "temperature_c", "x", "id"]

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
