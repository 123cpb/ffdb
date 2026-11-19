"""Forms for the Add data pages, and the admin's merge page (MergeForm)."""

import math
import re
from collections import Counter
from dataclasses import dataclass

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.safestring import mark_safe
from django.utils.text import capfirst

from . import chem
from .models import (
    CONDITION_NAMES,
    CONDITION_UNITS,
    Material,
    Measurement,
    MixtureComponent,
    Phase,
    PhaseSequence,
    Property,
    Reference,
    Series,
    Variable,
    accession,
    clean_doi,
    clean_patent_number,
    group_properties,
    materials_named,
    must_state,
    pk_from_accession,
)
from .phases import PhaseSequenceError

# What every DOI looks like once cleaned: 10., a number of four or more digits, /, then the
# rest, e.g. 10.1002/chem.201702742.
DOI_FORMAT = re.compile(r"10\.\d{4,}/\S+")


class FindReferenceForm(forms.Form):
    """The Add data page's first question: which reference the data is from. A valid form's
    cleaned_data has kind (one of Reference.Kind) and identifier, cleaned up as Reference
    stores it, ready to look the reference up or add it."""

    DOI = "doi"
    PATENT = "patent"
    THESIS = "thesis"

    kind = forms.ChoiceField(
        label="The data is from",
        choices=[
            (DOI, "A journal article or preprint, by its DOI"),
            (PATENT, "A patent, by its number"),
            (THESIS, "A thesis"),
        ],
        # DOI is chosen to begin with, because most references are papers. It can't quietly
        # mislabel anything: a patent number or thesis left under DOI fails the DOI check.
        initial=DOI,
        widget=forms.RadioSelect,
    )
    identifier = forms.CharField(
        label="DOI, patent number, or thesis",
        max_length=Reference._meta.get_field("identifier").max_length,
        help_text="A DOI as you have it, e.g. 10.1002/chem.201702742 or https://doi.org/10.1002/chem.201702742; "
        "a patent number, e.g. EP3260450A1; for a thesis, its author, year and title, "
        "e.g. Parton-Barr 2027, Fluid ferroelectrics.",
    )

    def clean(self):
        cleaned = super().clean()
        kind, text = cleaned.get("kind"), cleaned.get("identifier")
        if not (kind and text):
            return cleaned  # the field errors already say what is missing
        if kind == self.DOI:
            doi = clean_doi(text)
            if not DOI_FORMAT.fullmatch(doi):
                raise ValidationError(
                    {"identifier": "This isn't a DOI: a DOI starts with 10. and has a /, e.g. 10.1002/chem.201702742."}
                )
            cleaned["kind"], cleaned["identifier"] = Reference.kind_for_doi(doi), doi
        elif kind == self.PATENT:
            cleaned["kind"], cleaned["identifier"] = Reference.Kind.PATENT, clean_patent_number(text)
        else:
            cleaned["kind"], cleaned["identifier"] = Reference.Kind.THESIS, " ".join(text.split())
        return cleaned


def material_with_lcr_id(pk):
    """The material with this primary key, as (material, ""), or (None, why none was found).
    Pass the number pk_from_accession() reads from an LCR ID."""
    material = Material.objects.filter(pk=pk).first()
    return (material, "") if material else (None, f"No material has the LCR ID {accession(pk)}.")


# A pasted table of compounds. The most lines one table can have, as in the prototype.
MAX_TABLE_LINES = 100
NAME_LENGTH = Material._meta.get_field("name").max_length
SEQUENCE_LENGTH = PhaseSequence._meta.get_field("sequence").max_length
METHOD_LENGTH = PhaseSequence._meta.get_field("method").max_length


def table_cells(line):
    """Split one pasted line into its cells: label, SMILES or LCR ID and, optionally, phase sequence.
    On tabs if it has any (pasted from a spreadsheet); otherwise on the first two commas, or
    else on the first two runs of spaces, so the sequence keeps its own commas and spaces.
    Empty cells at the end are dropped."""
    if "\t" in line:
        cells = line.split("\t")
    elif "," in line:
        cells = line.split(",", 2)
    else:
        cells = line.split(None, 2)  # None: any run of spaces counts as one break
    cells = [cell.strip() for cell in cells]
    while len(cells) > 1 and not cells[-1]:
        cells.pop()
    return cells


def table_rows(text):
    """The lines of a pasted table that hold materials, as (header, rows). Each row is
    (line number, the line as pasted, its cells). Blank lines are skipped but still counted,
    so the numbers are the pasted text's. A first line without digits, such as "Label, SMILES,
    Phases", is a header: header is its number, or None when the table has none."""
    header, rows = None, []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if not rows and header is None and not any(character.isdigit() for character in line):
            header = number
            continue
        rows.append((number, line.strip(), table_cells(line)))
    return header, rows


@dataclass
class TableLine:
    """One line of a pasted table, as read_lines() reads it."""

    number: int
    text: str
    label: str
    identifier: str  # a SMILES, or the LCR ID of a material already in FFDB
    sequence: str
    problem: str = ""  # why the line can't be saved, or "" when it can
    canonical_smiles: str = ""  # blank for a mixture
    match: Material | None = None  # the material already in FFDB with this structure or LCR ID
    standard_sequence: str = ""
    sequence_already_there: bool = False  # the match has this sequence from this reference
    alias_problem: str = ""  # why the match won't keep the label as an alias, or ""
    stereo_warning: str = ""  # for a new structure

    @property
    def status(self):
        """"problem", "in_ffdb" or "new"."""
        if self.problem:
            return "problem"
        return "in_ffdb" if self.match else "new"

    def drawing(self):
        """The structure as SVG to put in the page, 200 by 128 pixels like the thumbnails. It
        is drawn from the canonical SMILES, which holds nothing the curator typed but the
        structure, so it can go in as it is. RDKit's SVG starts with an XML declaration, which
        can't go inside a page, so it is cut off."""
        svg = chem.structure_svg(self.canonical_smiles, 200, 128)
        return mark_safe(svg[svg.index("<svg"):])


def read_lines(rows, reference, direction):
    """Read each row from table_rows() as a TableLine: check its cells and sequence, and find
    its material in FFDB, by the LCR ID or by the structure. Each sequence is read as
    direction says (one of PhaseSequence.Direction). Nothing is saved. A line gets one
    problem, the first found, so each check can rely on those before it."""
    synonyms = Phase.synonym_map()  # read once, for every line's sequence
    first_seen = {}  # a material in FFDB's pk, or a new structure's InChIKey -> the number of the line it was first on
    lines = []
    for number, text, cells in rows:
        line = TableLine(number, text, *(cells + ["", ""])[:3])
        lines.append(line)
        if len(cells) not in (2, 3):
            line.problem = (
                "Expected a label, a SMILES or LCR ID and, optionally, a phase sequence, but found "
                f"{len(cells)} cell{'' if len(cells) == 1 else 's'}."
            )
            continue
        if not line.label:
            line.problem = "Give the label as the reference prints it."
            continue
        if len(line.label) > NAME_LENGTH:
            line.problem = f"The label is longer than the {NAME_LENGTH} characters a name can have."
            continue
        # No SMILES reads as an LCR ID, because L isn't an element, so the two can't be confused.
        pk = pk_from_accession(line.identifier)
        if pk is not None:
            line.match, line.problem = material_with_lcr_id(pk)
            if line.problem:
                continue
            # Drawn from the structure FFDB has, so the curator sees it is the one they meant.
            # A mixture has none, so it isn't drawn.
            line.canonical_smiles = line.match.canonical_smiles
        else:
            try:
                line.canonical_smiles, inchikey = chem.standardise(line.identifier)
            except chem.StructureError as exc:
                line.problem = str(exc)
                continue
            line.match = Material.objects.filter(inchikey=inchikey).first()
        # A pk is a number and an InChIKey is text, so the two can never be mistaken for each other.
        key = line.match.pk if line.match else inchikey
        if key in first_seen:
            line.problem = f"Same material as line {first_seen[key]}: give each material once."
            continue
        first_seen[key] = number
        if line.sequence:
            try:
                # An unsaved sequence, so it is read exactly as saving it would read it.
                sequence = PhaseSequence(sequence=line.sequence, direction=direction)
                line.standard_sequence = sequence.parsed(synonyms).standard_text()
            except PhaseSequenceError as exc:
                line.problem = f"Phase sequence: {exc}"
                continue
            # Both texts are stored, and the standard one can be longer: K becomes Cr.
            if max(len(line.sequence), len(line.standard_sequence)) > SEQUENCE_LENGTH:
                line.problem = f"Phase sequence: longer than the {SEQUENCE_LENGTH} characters it can have."
                continue
        if line.match:
            line.alias_problem = line.match.alias_problem(line.label)
            # The direction too: a heating run and a summary can be written the same way.
            line.sequence_already_there = bool(line.sequence) and reference.phase_sequences.filter(
                material=line.match, direction=direction, standard_sequence=line.standard_sequence
            ).exists()
        else:
            # A material not saved: the InChIKey is all stereo_warning() needs.
            line.stereo_warning = Material(inchikey=inchikey).stereo_warning()
    return lines


class CompoundTableForm(forms.Form):
    """A reference's compounds, pasted as a table into one box, and any material already in
    FFDB, such as a mixture, given by its LCR ID in place of the SMILES. A valid form's
    cleaned_data has table, the lines read as TableLine objects (read_lines()), and header
    is the number of the line read as a header, or None. Every sequence in the table has the
    direction, rate and method chosen once above it. Pass the reference the lines are from."""

    EMPTY = "Paste at least one line: a label, a SMILES or LCR ID and, optionally, a phase sequence."
    NO_DIRECTION = "This table has phase sequences: choose what they are, summaries or cooling or heating runs."

    # These come before the table, so clean_table() can read them.
    direction = forms.ChoiceField(
        label="The sequences are",
        # No default: a default of summary would quietly save a heating run as a summary.
        choices=[("", "Choose…"), *PhaseSequence.Direction.choices],
        required=False,  # clean_table() asks for it only when the table has sequences
    )
    rate = forms.FloatField(label="Rate (K/min)", required=False)
    method = forms.CharField(label="Method", required=False, max_length=METHOD_LENGTH, help_text="e.g. DSC or POM")
    table = forms.CharField(
        label="Compounds from this reference",
        widget=forms.Textarea(attrs={"rows": 8, "spellcheck": "false"}),
        strip=False,  # keeps the tab at the start of a first line whose label is missing
        error_messages={"required": EMPTY},
        help_text="One compound per line: its label as the paper prints it, its SMILES and, if the "
        "paper gives one, its phase sequence. For a material already in FFDB, including a mixture, its LCR ID "
        "can take the place of the SMILES, e.g. LCR-00512. Write a summary from the solid up, e.g. K 139.8 NF 132.7 N "
        "187.9 I, and a run in the order observed, e.g. Iso 187.9 N 132.7 NF 70 K on cooling. Separate "
        "the cells with tabs (as pasted from a spreadsheet), commas or spaces; a label with a space in it "
        f"needs tabs or commas. A first line without digits is read as a header. At most {MAX_TABLE_LINES} lines.",
    )

    def __init__(self, *args, reference, **kwargs):
        super().__init__(*args, **kwargs)
        self.reference = reference
        self.header = None

    def clean_table(self):
        """The table's lines, read. A table with no lines or too many is refused before
        anything is looked up."""
        self.header, rows = table_rows(self.cleaned_data["table"])
        if not rows:
            raise ValidationError(self.EMPTY)
        if len(rows) > MAX_TABLE_LINES:
            raise ValidationError(f"A table can have at most {MAX_TABLE_LINES} lines, and this one has {len(rows)}: split it.")
        direction = self.cleaned_data.get("direction")
        # cells[2:] is the sequence and anything after it: empty for a line of label and SMILES.
        if not direction and any(cells[2:] for _, _, cells in rows):
            raise ValidationError(self.NO_DIRECTION)
        return read_lines(rows, self.reference, direction)

    def save(self, user):
        """Save the lines without a problem, in one transaction, so a failure partway keeps
        none of them. A new structure becomes a material named by its label. A material already
        in FFDB keeps the label as an alias, unless Material.alias_problem() says why not. Each
        sequence is added under the reference, with the table's direction, rate and method, as
        entered by user, unless it is already there. Returns (counts, the stereochemistry
        warnings for the new materials)."""
        counts, warnings = Counter(), []
        observed = {
            "direction": self.cleaned_data["direction"],
            "rate_k_per_min": self.cleaned_data["rate"],
            "method": self.cleaned_data["method"],
        }
        with transaction.atomic():
            for line in self.cleaned_data["table"]:
                if line.problem:
                    continue
                material = line.match
                if material is None:
                    # Only a SMILES line finds no match: an LCR ID line without one is a problem.
                    material = Material.objects.create(name=line.label, smiles=line.identifier)
                    counts["compounds"] += 1
                    # Asked again now it is saved: it has its LCR ID, and it sees the
                    # materials that earlier lines added.
                    warning = material.stereo_warning()
                    if warning:
                        warnings.append(warning)
                elif not line.alias_problem:
                    material.aliases = ", ".join(material.alias_list() + [line.label])
                    material.save()
                    counts["aliases"] += 1
                if line.sequence and not line.sequence_already_there:
                    sequence = PhaseSequence(
                        material=material, reference=self.reference, sequence=line.sequence, **observed
                    )
                    sequence.mark_entered(user)
                    sequence.save()
                    counts["sequences"] += 1
        return counts, warnings


# Adding a single value or a curve takes two short forms. The first says what the values are
# of; the second asks for what that property needs for that kind of value.


def property_choices(field):
    """A property menu's choices, in the Properties page's order: each group's members under
    its name (an optgroup), and each property in no group on its own. The values are codes,
    as the field's to_field_name says. The field still checks the code against its queryset,
    so this changes only how the menu is laid out."""
    choices = [("", field.empty_label)]
    for group, members in group_properties(field.queryset.select_related("group")):
        options = [(p.code, f"{p.name} ({p.symbol}, {p.unit})") for p in members]
        choices += [(group.name, options)] if group else options
    return choices


class SeriesChoiceForm(forms.Form):
    """What both first forms ask: the material, the property and the kind of value. A first
    form is sent with GET, because it changes nothing: the next page's address holds the
    choices. A valid form's cleaned_data has material (a Material, from either box), property,
    kind and variable, what the values are against. Pass the reference they are from."""

    material = forms.ModelChoiceField(
        queryset=Material.objects.none(),  # __init__ sets the reference's materials
        required=False,  # clean() needs this or an LCR ID
        empty_label="Choose…",
        help_text="One of this reference's materials.",
    )
    lcr_id = forms.CharField(label="or its LCR ID", required=False, max_length=20, help_text="Any material, e.g. LCR-00012.")
    # to_field_name: the address and the menu use the property's code, e.g. property=ps.
    property = forms.ModelChoiceField(queryset=Property.objects.all(), to_field_name="code", empty_label="Choose…")
    # No default: a default of measured would quietly save a computed value as measured.
    kind = forms.ChoiceField(choices=[("", "Choose…"), *Series.Kind.choices])

    def __init__(self, *args, reference, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = reference.materials()
        self.fields["property"].choices = property_choices(self.fields["property"])

    def clean(self):
        cleaned = super().clean()
        material, lcr_id = cleaned.get("material"), cleaned.get("lcr_id")
        if material and lcr_id:
            self.add_error("lcr_id", "Choose a material or give an LCR ID, not both.")
        elif lcr_id:
            pk = pk_from_accession(lcr_id)
            found = Material.objects.filter(pk=pk).first() if pk is not None else None
            if pk is None:
                self.add_error("lcr_id", "This isn't an LCR ID: an LCR ID looks like LCR-00012.")
            elif found is None:
                self.add_error("lcr_id", f"No material has the LCR ID {accession(pk)}.")
            else:
                cleaned["material"] = found
        elif not material:
            self.add_error("material", "Choose one of this reference's materials, or give an LCR ID.")
        return cleaned


class ValueChoiceForm(SeriesChoiceForm):
    """The first form for adding a single value: what SeriesChoiceForm asks, and whether the
    value has a temperature. The answer gives the variable: one way to store each value is
    against temperature with a temperature, and against nothing without one (see
    Measurement.position_errors()). cleaned_data's temperature is True or False."""

    YES, NO = "yes", "no"

    temperature = forms.ChoiceField(
        label="Does the value have a temperature?",
        choices=[(YES, "Yes: measured or computed at a temperature"), (NO, "No, e.g. a gas-phase calculation")],
        widget=forms.RadioSelect,
    )

    def clean_temperature(self):
        return self.cleaned_data["temperature"] == self.YES

    def clean(self):
        cleaned = super().clean()
        if "temperature" not in cleaned:
            return cleaned  # the field's error already says to answer it
        cleaned["variable"] = Variable.objects.get(code=Variable.TEMPERATURE if cleaned["temperature"] else Variable.NONE)
        prop, kind = cleaned.get("property"), cleaned.get("kind")
        # The second form has no temperature box for a value without one, so this rule is
        # checked here, with the message the rules for values give.
        if prop and kind and cleaned["temperature"] is False and "temperature" in prop.conditions_for(kind):
            self.add_error("temperature", must_state(prop, ["temperature"]))
        return cleaned


class CurveChoiceForm(SeriesChoiceForm):
    """The first form for adding a curve: what SeriesChoiceForm asks, and what the curve is
    against. Nothing isn't offered: a value against nothing is one value, added as a single
    value."""

    # to_field_name: the address uses the variable's code, e.g. variable=frequency.
    variable = forms.ModelChoiceField(
        label="Against", queryset=Variable.objects.exclude(code=Variable.NONE), to_field_name="code", empty_label="Choose…"
    )


def condition_value(text):
    """A condition as it is stored: a number when the text reads as one, so 1000 can be
    compared with other values, and otherwise the text, e.g. polyimide. A whole number is
    stored without .0. isfinite() keeps "nan" and "inf" as text."""
    try:
        number = float(text)
    except ValueError:
        return text
    if not math.isfinite(number):
        return text
    return int(number) if number.is_integer() else number


def check_rules(form, obj, **kwargs):
    """Check obj with its full_clean(), which runs the model's rules. Each message goes on
    form's field of the same name, e.g. uncertainty, or above the form when there is none,
    as with the message that a property's values must state a condition."""
    try:
        obj.full_clean(**kwargs)
    except ValidationError as exc:
        for field, messages in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, messages)


class SeriesForm(forms.Form):
    """What both second forms ask: the method and conditions, and where in the reference the
    values are. Pass the reference and the first form's cleaned_data as choice. There is a box
    for each of the property's required conditions for the kind, apart from three given
    another way: temperature and method, which have boxes of their own, and the condition the
    series' variable stands for, which its points give (a frequency sweep gives its
    frequencies). Other conditions are typed as key: value lines.

    clean() builds the series, not saved, and checks it by the rules for values
    (Series.clean()), so it is refused with the same messages as an import. Each subclass
    builds its points in point_list and checks them by the rules too (Measurement.clean()).
    save() saves them all."""

    method = forms.CharField(required=False, max_length=Series._meta.get_field("method").max_length)
    other_conditions = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}),
        help_text="Any others the reference gives, one per line as key: value, e.g. alignment: planar. "
        "A unit goes in the key, e.g. field_V_um: 2.",
    )
    # Not recorded is left out: it is for imported lists that don't record it.
    taken_from = forms.ChoiceField(
        choices=[("", "Choose…"), *[c for c in Series.TakenFrom.choices if c[0] != Series.TakenFrom.NOT_RECORDED]]
    )
    location = forms.CharField(
        label="Location", required=False, max_length=Series._meta.get_field("location").max_length,
        help_text="e.g. Table 2, Fig. 3b or [0129].",
    )

    def __init__(self, *args, reference, choice, **kwargs):
        super().__init__(*args, **kwargs)
        self.reference, self.material = reference, choice["material"]
        self.prop, self.kind, self.variable = choice["property"], choice["kind"], choice["variable"]
        self.required = self.prop.conditions_for(self.kind)  # for the line above the form
        if self.prop.methods:
            # The page's <datalist> of the property's methods: the browser offers them as the
            # curator types, and anything else can still be typed.
            self.fields["method"].widget.attrs["list"] = "method-suggestions"
        given_another_way = {"temperature", "method", self.variable.condition}
        self.condition_keys = [key for key in self.required if key not in given_another_way]
        for key in self.condition_keys:
            unit = CONDITION_UNITS.get(key)
            label = capfirst(CONDITION_NAMES.get(key, key)) + (f" ({unit})" if unit else "")
            # Not required here, so a blank box gets the rules' message, as an import would.
            self.fields[f"condition_{key}"] = forms.CharField(label=label, required=False)
        self.series, self.point_list = None, []  # built by clean()

    def condition_fields(self):
        """The boxes for the property's required conditions, for the template."""
        return [self[f"condition_{key}"] for key in self.condition_keys]

    def clean_other_conditions(self):
        """The Other conditions box as {key: value}, a line each. Blank lines are skipped."""
        own_box = {"temperature", "method", *self.condition_keys}
        conditions = {}
        for number, line in enumerate(self.cleaned_data["other_conditions"].splitlines(), start=1):
            if not line.strip():
                continue
            key, colon, text = (part.strip() for part in line.partition(":"))
            if not (colon and key and text):
                raise ValidationError(f"Line {number}: write each condition as key: value, e.g. alignment: planar.")
            if key in own_box:
                raise ValidationError(f"Line {number}: {key} isn't one of the other conditions: it has its own box or question.")
            if key in conditions:
                raise ValidationError(f"Line {number}: {key} is given twice.")
            conditions[key] = condition_value(text)
        return conditions

    def series_fields(self, cleaned):
        """The series' fields that differ between the forms, as {name: value}. A single value
        is no temperature sweep."""
        return {"direction": Series.Direction.NOT_APPLICABLE}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("taken_from") == Series.TakenFrom.FIGURE and not cleaned.get("location"):
            self.add_error("location", "Give the figure's label, e.g. Fig. 3b.")
        conditions = {
            key: condition_value(cleaned[f"condition_{key}"])
            for key in self.condition_keys
            if cleaned.get(f"condition_{key}")
        }
        conditions.update(cleaned.get("other_conditions") or {})
        self.series = Series(
            material=self.material, reference=self.reference, property=self.prop, variable=self.variable,
            kind=self.kind, method=cleaned.get("method", ""), conditions=conditions,
            taken_from=cleaned.get("taken_from", ""), location=cleaned.get("location", ""),
            **self.series_fields(cleaned),
        )
        # A box that already has an error isn't checked again, so a blank one isn't refused
        # twice. Series.clean() runs whatever is left out.
        check_rules(self, self.series, exclude=list(self.errors))
        return cleaned

    def save(self, user):
        """Save the series and its points, recorded as entered by user, in one transaction, so
        a failure partway keeps none of them. bulk_create() saves all the points in one query.
        Call it only on a valid form. Returns the series."""
        with transaction.atomic():
            self.series.mark_entered(user)
            self.series.save()
            for point in self.point_list:
                point.series = self.series  # again, now the series has its primary key
            Measurement.objects.bulk_create(self.point_list)
        return self.series


class ValueForm(SeriesForm):
    """The second form for adding a single value: the value, and what SeriesForm asks. Only a
    value with a temperature has a box for it. clean() builds the value as a series of one
    point."""

    qualifier = forms.ChoiceField(
        label="The value is", choices=Measurement.Qualifier.choices, initial=Measurement.Qualifier.EXACT,
        help_text=Measurement._meta.get_field("qualifier").help_text,
    )
    value = forms.FloatField()  # __init__ adds the unit to the label
    uncertainty = forms.FloatField(label="±", required=False)
    temperature_c = forms.FloatField(label="T (°C)")  # __init__ removes it for a value without one

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_temperature = self.variable.code == Variable.TEMPERATURE
        self.fields["value"].label = f"Value ({self.prop.unit})"
        if not self.has_temperature:
            del self.fields["temperature_c"]

    def clean(self):
        cleaned = super().clean()
        # The point can be built only from boxes that were read: a blank value has nothing to check.
        if not any(self.has_error(name) for name in ["qualifier", "value", "uncertainty", "temperature_c"]):
            point = Measurement(
                series=self.series, qualifier=cleaned["qualifier"], value=cleaned["value"],
                uncertainty=cleaned["uncertainty"], temperature_c=cleaned.get("temperature_c"),
            )
            # The series has no primary key yet, which is all full_clean() would say about it.
            check_rules(self, point, exclude=["series"])
            self.point_list = [point]
        return cleaned


# A curve's points, pasted into one box. The most lines it can have, as in the prototype. FFDB
# never thins data (data rule 1): whoever has a longer curve thins it and says how in its notes.
MAX_CURVE_POINTS = 5000
# How many lines with a problem the messages name, before saying how many more there are.
NAMED_PROBLEM_LINES = 20
LIMIT_SIGNS = {"<": Measurement.Qualifier.UPPER, ">": Measurement.Qualifier.LOWER}


def point_number(text):
    """A number in a pasted point: "6.08" -> 6.08. The minus sign papers print, −, is read as
    -. Raises ValueError, saying why, for anything else. isfinite() refuses "nan" and "inf",
    which float() accepts."""
    if not text:
        raise ValueError("A cell is empty.")
    try:
        number = float(text.replace("−", "-"))
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        hint = " Use a decimal point." if "," in text else ""
        raise ValueError(f"“{text}” isn't a number.{hint}")
    return number


def point_cells(line):
    """Split one pasted line of a curve into its cells: on tabs if it has any (pasted from a
    spreadsheet), otherwise on commas, or else on runs of spaces. Spaces after < or > are
    dropped first, so "< 0.1" stays one cell."""
    line = re.sub(r"([<>])\s+", r"\1", line.strip())
    if "\t" in line:
        cells = line.split("\t")
    elif "," in line:
        cells = line.split(",")
    else:
        cells = line.split()  # no argument: any run of spaces counts as one break
    return [cell.strip() for cell in cells]


def is_number(cell):
    """Whether a cell reads as a number, with or without a limit sign."""
    try:
        point_number(cell.lstrip("<>"))
    except ValueError:
        return False
    return True


@dataclass
class PointLine:
    """One line of a pasted curve, as read_points() reads it."""

    number: int
    text: str
    x: float | None = None
    qualifier: str = Measurement.Qualifier.EXACT
    value: float | None = None
    uncertainty: float | None = None
    problem: str = ""  # why the line can't be read, or "" when it can


def read_point_line(number, text, cells):
    """One line's cells as a PointLine: x, value and optionally ±, in that order, as the form
    states them. A value starting with < or > is a limit. Nothing is converted."""
    line = PointLine(number, text)
    if len(cells) not in (2, 3):
        line.problem = f"Expected two or three cells (x, value and optionally ±), but found {len(cells)}."
        return line
    x, value, uncertainty = (cells + [""])[:3]
    line.qualifier = LIMIT_SIGNS.get(value[:1], Measurement.Qualifier.EXACT)
    if line.qualifier != Measurement.Qualifier.EXACT:
        value = value[1:]
    try:
        line.x, line.value = point_number(x), point_number(value)
        line.uncertainty = point_number(uncertainty) if uncertainty else None
    except ValueError as exc:
        line.problem = str(exc)
    return line


def read_points(text):
    """The lines of a pasted curve as (header, lines), each line read as a PointLine. Blank
    lines are skipped but still counted, so the numbers are the pasted text's. A first line
    with no number in it, such as "T (°C), Ps (µC/cm²)", is a header: header is its number,
    or None when there is none. Nothing else is skipped: a line that can't be read has its
    problem."""
    header, lines = None, []
    for number, text_line in enumerate(text.splitlines(), start=1):
        if not text_line.strip():
            continue
        cells = point_cells(text_line)
        if header is None and not lines and not any(is_number(cell) for cell in cells):
            header = number
            continue
        lines.append(read_point_line(number, text_line.strip(), cells))
    return header, lines


class CurveForm(SeriesForm):
    """The second form for adding a curve: what SeriesForm asks, and its points, pasted into
    one box and read by read_points(). A curve against temperature asks which way it was
    taken; one against anything else asks the temperature every point was measured at.

    clean() builds a point from every line that reads, in point_list, for Check to count and
    draw. A line that can't be read, or whose point the rules for values refuse, goes in
    problem_lines, and each is named as an error on the box. So the form is valid, and can be
    saved, only when every line is a point."""

    EMPTY = "Paste at least one line: x, then the value."

    direction = forms.ChoiceField(
        # No default, as for sequences: a default would quietly save a heating curve as Not
        # stated. Not applicable is left out: it is for curves that aren't against temperature.
        choices=[("", "Choose…"), *[c for c in Series.Direction.choices if c[0] != Series.Direction.NOT_APPLICABLE]],
        help_text="Whether the points were taken on heating or cooling.",
    )
    # Not required here: the rules for values say whether the property needs a temperature.
    temperature_c = forms.FloatField(label="T (°C)", required=False, help_text="The temperature of every point.")
    digitised = forms.BooleanField(label="Digitised from the figure", required=False)
    notes = forms.CharField(
        required=False, max_length=Series._meta.get_field("notes").max_length,
        help_text=f"e.g. how a curve of more than {MAX_CURVE_POINTS:,} points was thinned.",
    )
    points = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 10, "spellcheck": "false"}),
        strip=False,  # stripping blank lines from the start would change the line numbers
        error_messages={"required": EMPTY},
    )  # __init__ gives it its label and help, with the units

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.against_temperature = self.variable.code == Variable.TEMPERATURE
        del self.fields["temperature_c" if self.against_temperature else "direction"]
        points = self.fields["points"]
        points.label = f"Points: {self.variable}, {self.prop.symbol} ({self.prop.unit}) and optionally ±"
        points.help_text = (
            f"One point a line: the {self.variable.name.lower()}, the value and, if the reference gives one, its ±, "
            "separated by tabs (as pasted from a spreadsheet), commas or spaces. Start a value with < or > for an "
            "upper or lower limit. Give them in these units: nothing is converted. A first line with no number in "
            f"it is read as a header. At most {MAX_CURVE_POINTS:,} lines."
        )
        self.header, self.problem_lines = None, []  # set by clean_points() and clean()

    def clean_points(self):
        """The box's lines, read. A box with no lines or too many is refused as a whole."""
        self.header, lines = read_points(self.cleaned_data["points"])
        if not lines:
            raise ValidationError(self.EMPTY)
        if len(lines) > MAX_CURVE_POINTS:
            raise ValidationError(
                f"A curve can have at most {MAX_CURVE_POINTS:,} lines, and this one has {len(lines):,}. FFDB never "
                "thins data: thin the curve yourself, and say how in its notes."
            )
        return lines

    def series_fields(self, cleaned):
        """The direction, which only a curve against temperature has, digitised and notes."""
        return {
            "direction": cleaned.get("direction", "") if self.against_temperature else Series.Direction.NOT_APPLICABLE,
            "digitised": cleaned.get("digitised", False),
            "notes": cleaned.get("notes", ""),
        }

    def clean(self):
        cleaned = super().clean()  # builds and checks the series
        box_messages = {}  # the rules' messages about the T box, each once: a dict keeps their order
        for line in cleaned.get("points", []):
            if line.problem:
                self.problem_lines.append(line)
                continue
            point = Measurement(
                series=self.series, qualifier=line.qualifier, value=line.value, uncertainty=line.uncertainty,
                # Against temperature, x is the temperature. Against anything else, every point
                # has the T box's temperature.
                temperature_c=line.x if self.against_temperature else cleaned.get("temperature_c"),
                x=None if self.against_temperature else line.x,
            )
            try:
                point.full_clean(exclude=["series"])
            except ValidationError as exc:
                errors = exc.message_dict
                # About the T box, so the same for every point.
                box_messages.update(dict.fromkeys(errors.pop("temperature_c", [])))
                if errors:
                    line.problem = " ".join(message for messages in errors.values() for message in messages)
                    self.problem_lines.append(line)
                    continue
            self.point_list.append(point)
        if box_messages:
            self.add_error("temperature_c" if "temperature_c" in self.fields else None, list(box_messages))
        if self.problem_lines:
            named = [f"Line {line.number}: {line.problem}" for line in self.problem_lines[:NAMED_PROBLEM_LINES]]
            more = len(self.problem_lines) - NAMED_PROBLEM_LINES
            if more > 0:
                named.append(f"…and {more:,} more line{'' if more == 1 else 's'} with a problem.")
            self.add_error("points", ["Nothing is saved until every line is a point: fix or delete these lines.", *named])
        return cleaned


# A mixture from a reference: its components in FFDB, typed or pasted one per line, and the
# rest of its composition in words.

# The units a component line can give, keyed as they are compared: in lower case, without
# spaces, so "Mol %" is mol%.
COMPONENT_UNITS = {unit.lower(): unit for unit in MixtureComponent.Unit.values}


@dataclass
class ComponentLine:
    """One line of a mixture's components, as read_components() reads it."""

    number: int
    text: str
    material: Material | None = None
    fraction: float | None = None
    unit: str = ""
    problem: str = ""  # why the line can't be read, or "" when it can


def find_component(text):
    """The material that text names, by its LCR ID or by a name or alias that fits it alone,
    as (material, ""), or (None, why none was found). Whether it is a compound is left to
    MixtureComponent.clean()."""
    pk = pk_from_accession(text)
    if pk is not None:
        return material_with_lcr_id(pk)
    found = materials_named(text)
    if len(found) == 1:
        return found[0], ""
    if found:
        return None, f"More than one material is called {text} ({', '.join(map(str, found))}): give its LCR ID."
    return None, (
        f"Nothing in FFDB is called {text}. Add it first with the compound table, or describe it under Other components."
    )


def read_components(text):
    """A mixture's component lines, each read as a ComponentLine: the compound, its fraction
    and its unit. Blank lines are skipped but still counted, so the numbers are the typed
    text's. Nothing is saved. A line gets one problem, the first found, so each check can
    rely on those before it."""
    lines, first_seen = [], {}  # first_seen: a compound's pk -> the number of the line it was first on
    for number, text_line in enumerate(text.splitlines(), start=1):
        if not text_line.strip():
            continue
        line = ComponentLine(number, text_line.strip())
        lines.append(line)
        # On tabs, or else on the last two commas, so a name with a comma in it stays whole.
        cells = text_line.split("\t") if "\t" in text_line else text_line.rsplit(",", 2)
        cells = [cell.strip() for cell in cells]
        if len(cells) != 3 or not cells[0]:
            line.problem = "Expected an LCR ID or name, a fraction and a unit, e.g. LCR-00128, 30, mol%."
            continue
        name, fraction, unit = cells
        line.unit = COMPONENT_UNITS.get(unit.replace(" ", "").lower(), "")
        if not line.unit:
            line.problem = f"“{unit}” isn't a unit: give mol% or wt%."
            continue
        try:
            line.fraction = point_number(fraction)
        except ValueError as exc:
            line.problem = str(exc)
            continue
        line.material, line.problem = find_component(name)
        if line.problem:
            continue
        try:
            # The rules for one component on its own: a compound, with a fraction more than 0
            # and at most 100. It has no mixture yet, which clean() allows.
            MixtureComponent(component=line.material, fraction=line.fraction, unit=line.unit).clean()
        except ValidationError as exc:
            line.problem = " ".join(exc.messages)
            continue
        if line.material.pk in first_seen:
            line.problem = f"Same compound as line {first_seen[line.material.pk]}: give each compound once."
            continue
        first_seen[line.material.pk] = number
    return lines


class MixtureForm(forms.Form):
    """A mixture as a reference gives it: its name, whether its composition is known, its
    components in FFDB as lines (read_components()), and the rest in words. A mixture has
    no structure, so FFDB can't tell by itself whether it already has it. When mixtures with
    this name or alias are in FFDB (same_name), the form asks whether it is one of them,
    because names such as M5 are reused by different groups.

    clean() reads the lines, builds the mixture, not saved, and checks it by the rules for
    mixtures, so it is refused with the same messages as in the admin. The form is valid,
    and can be saved, only when every line reads."""

    DIFFERENT = "different"

    # Not required here: Material.clean() says a mixture needs a name, and why.
    name = forms.CharField(required=False, max_length=NAME_LENGTH, help_text="As the reference prints it, e.g. F7.")
    # None is chosen to begin with: a default of Listed would quietly save a partly known
    # mixture as listed.
    composition_status = forms.ChoiceField(
        label="Its composition",
        choices=Material.CompositionStatus.choices,
        widget=forms.RadioSelect,
        help_text="Whether the reference gives every component, only some, or none, as for many commercial mixtures.",
    )
    components = forms.CharField(
        label="Components in FFDB",
        required=False,  # Not disclosed has none, and Other components can describe them all
        widget=forms.Textarea(attrs={"rows": 4, "spellcheck": "false"}),
        strip=False,  # stripping blank lines from the start would change the line numbers
        help_text="One compound per line: its LCR ID or name, its fraction and the unit (mol% or wt%), separated by "
        "commas or tabs, e.g. LCR-00128, 30, mol%. A compound that isn't in FFDB yet: add it first with the compound "
        "table, or describe it under Other components.",
    )
    other_components = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Components that aren't in FFDB, such as a commercial host, with their fractions if known.",
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    same_as = forms.ChoiceField(
        label="Is it one of these?",
        widget=forms.RadioSelect,
        error_messages={"required": "Say whether this is one of them or a different mixture."},
    )  # __init__ gives it its choices, or removes it when no mixture has this name

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The question's choices depend on the name, so it is read as posted, before it is
        # cleaned. Cleaning only strips spaces, which name_key() ignores anyway. An unbound
        # form has no name, which fits no mixture.
        self.same_name = materials_named(self.data.get("name", ""), Material.Kind.MIXTURE)
        if self.same_name:
            self.fields["same_as"].choices = [
                *[(str(mixture.pk), f"Yes, it's {mixture.accession}") for mixture in self.same_name],
                (self.DIFFERENT, "No, a different mixture with the same name"),
            ]
        else:
            del self.fields["same_as"]
        self.lines, self.problem_lines = [], []  # set by clean_components()
        self.mixture = self.existing = None  # set by clean()

    def clean_components(self):
        """The component lines, read. One line with a problem refuses the box: each line's
        problem is shown with the lines."""
        self.lines = read_components(self.cleaned_data["components"])
        self.problem_lines = [line for line in self.lines if line.problem]
        if self.problem_lines:
            raise ValidationError("Nothing is saved until every line reads: fix or delete the lines marked below.")
        return self.lines

    def clean(self):
        cleaned = super().clean()
        self.mixture = Material(
            kind=Material.Kind.MIXTURE,
            name=cleaned.get("name", ""),
            composition_status=cleaned.get("composition_status", ""),
            other_components=cleaned.get("other_components", ""),
            notes=cleaned.get("notes", ""),
        )
        # A box that already has an error isn't checked again, so a status not chosen isn't
        # refused twice.
        check_rules(self, self.mixture, exclude=list(self.errors))
        # The rules across the components, once every line reads and the status is chosen.
        if "components" in cleaned and cleaned.get("composition_status"):
            for problem in self.mixture.composition_problems([(line.fraction, line.unit) for line in self.lines]):
                self.add_error(None, problem)
        # None for a different mixture, or when the question isn't asked or answered.
        self.existing = next((m for m in self.same_name if str(m.pk) == cleaned.get("same_as")), None)
        return cleaned

    def save(self):
        """Add the mixture and its components in one transaction, so a failure partway keeps
        neither, and return (the mixture, True). For a mixture already in FFDB, return it with
        False: nothing typed here is saved. Call it only on a valid form."""
        if self.existing is not None:
            return self.existing, False
        with transaction.atomic():
            self.mixture.save()
            MixtureComponent.objects.bulk_create(
                MixtureComponent(mixture=self.mixture, component=line.material, fraction=line.fraction, unit=line.unit)
                for line in self.lines
            )
        return self.mixture, True


class MergeForm(forms.Form):
    """The admin's merge page: the LCR ID of the material a duplicate goes into. It is valid
    when Material.merge_problems() finds no reason to refuse, and its cleaned into is that
    material."""

    into = forms.CharField(label="Merge into", help_text="The LCR ID of the material that stays, e.g. LCR-00012.")

    def __init__(self, *args, duplicate, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicate = duplicate

    def clean_into(self):
        pk = pk_from_accession(self.cleaned_data["into"])
        if pk is None:
            raise ValidationError("Give an LCR ID, e.g. LCR-00012.")
        target, why = material_with_lcr_id(pk)
        if target is None:
            raise ValidationError(why)
        problems = self.duplicate.merge_problems(target)
        if problems:
            raise ValidationError(problems)
        return target
