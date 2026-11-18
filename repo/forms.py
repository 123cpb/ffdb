"""Forms for the Add data pages."""

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


# A pasted table of compounds. The most lines one table can have, as in the prototype.
MAX_TABLE_LINES = 100
NAME_LENGTH = Material._meta.get_field("name").max_length
SEQUENCE_LENGTH = PhaseSequence._meta.get_field("sequence").max_length
METHOD_LENGTH = PhaseSequence._meta.get_field("method").max_length


def table_cells(line):
    """Split one pasted line into its cells: label, SMILES and, optionally, phase sequence.
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
    """The lines of a pasted table that hold compounds, as (header, rows). Each row is
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
    """One compound line of a pasted table, as read_lines() reads it."""

    number: int
    text: str
    label: str
    smiles: str
    sequence: str
    problem: str = ""  # why the line can't be saved, or "" when it can
    canonical_smiles: str = ""
    match: Material | None = None  # the material already in FFDB with this structure
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
    """Read each row from table_rows() as a TableLine: check its cells, structure and
    sequence, and look its structure up in FFDB. Each sequence is read as direction says
    (one of PhaseSequence.Direction). Nothing is saved. A line gets one problem, the first
    found, so each check can rely on those before it."""
    synonyms = Phase.synonym_map()  # read once, for every line's sequence
    first_seen = {}  # InChIKey -> the number of the line it was first on
    lines = []
    for number, text, cells in rows:
        line = TableLine(number, text, *(cells + ["", ""])[:3])
        lines.append(line)
        if len(cells) not in (2, 3):
            line.problem = (
                "Expected a label, a SMILES and, optionally, a phase sequence, but found "
                f"{len(cells)} cell{'' if len(cells) == 1 else 's'}."
            )
            continue
        if not line.label:
            line.problem = "Give the compound's label as the paper prints it."
            continue
        if len(line.label) > NAME_LENGTH:
            line.problem = f"The label is longer than the {NAME_LENGTH} characters a name can have."
            continue
        try:
            line.canonical_smiles, inchikey = chem.standardise(line.smiles)
        except chem.StructureError as exc:
            line.problem = str(exc)
            continue
        if inchikey in first_seen:
            line.problem = f"Same structure as line {first_seen[inchikey]}: give each compound once."
            continue
        first_seen[inchikey] = number
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
        line.match = Material.objects.filter(inchikey=inchikey).first()
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
    """A reference's compounds, pasted as a table into one box. A valid form's cleaned_data
    has table, the lines read as TableLine objects (read_lines()), and header is the number
    of the line read as a header, or None. Every sequence in the table has the direction,
    rate and method chosen once above it. Pass the reference the compounds are from."""

    EMPTY = "Paste at least one line: a label, a SMILES and, optionally, a phase sequence."
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
        "paper gives one, its phase sequence. Write a summary from the solid up, e.g. K 139.8 NF 132.7 N "
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
                    material = Material.objects.create(name=line.label, smiles=line.smiles)
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


# Adding a single value takes two short forms. The first says what the value is of; the
# second asks for what that property needs for that kind of value.


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


class ValueChoiceForm(forms.Form):
    """The first form for adding a single value: the material, the property, the kind of
    value and whether it has a temperature. It is sent with GET, because it changes nothing:
    the next page's address holds the choices. A valid form's cleaned_data has material (a
    Material, from either box), property, kind and temperature (True or False). Pass the
    reference the value is from."""

    YES, NO = "yes", "no"

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
    temperature = forms.ChoiceField(
        label="Does the value have a temperature?",
        choices=[(YES, "Yes: measured or computed at a temperature"), (NO, "No, e.g. a gas-phase calculation")],
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, reference, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = reference.materials()
        self.fields["property"].choices = property_choices(self.fields["property"])

    def clean_temperature(self):
        return self.cleaned_data["temperature"] == self.YES

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
        prop, kind = cleaned.get("property"), cleaned.get("kind")
        # The second form has no temperature box for a value without one, so this rule is
        # checked here, with the message the rules for values give.
        if prop and kind and cleaned.get("temperature") is False and "temperature" in prop.conditions_for(kind):
            self.add_error("temperature", must_state(prop, ["temperature"]))
        return cleaned


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


class ValueForm(forms.Form):
    """The second form for adding a single value: the value, and what its property needs for
    its kind. Pass the reference and the first form's cleaned_data as choice. The fields
    depend on them: a temperature box only for a value with a temperature, and a box for
    each of the property's required conditions for the kind, apart from temperature and
    method, which have boxes of their own. Other conditions are typed as key: value lines.

    clean() builds the value as a series of one point, not saved, and checks both by the
    rules for values (Series.clean() and Measurement.clean()), so a value is refused with
    the same messages as an import. save() saves them."""

    qualifier = forms.ChoiceField(
        label="The value is", choices=Measurement.Qualifier.choices, initial=Measurement.Qualifier.EXACT,
        help_text=Measurement._meta.get_field("qualifier").help_text,
    )
    value = forms.FloatField()  # __init__ adds the unit to the label
    uncertainty = forms.FloatField(label="±", required=False)
    temperature_c = forms.FloatField(label="T (°C)")  # __init__ removes it for a value without one
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
        self.prop, self.kind, self.has_temperature = choice["property"], choice["kind"], choice["temperature"]
        self.required = self.prop.conditions_for(self.kind)  # for the line above the form
        self.fields["value"].label = f"Value ({self.prop.unit})"
        if not self.has_temperature:
            del self.fields["temperature_c"]
        if self.prop.methods:
            # The page's <datalist> of the property's methods: the browser offers them as the
            # curator types, and anything else can still be typed.
            self.fields["method"].widget.attrs["list"] = "method-suggestions"
        self.condition_keys = [key for key in self.required if key not in ("temperature", "method")]
        for key in self.condition_keys:
            unit = CONDITION_UNITS.get(key)
            label = capfirst(CONDITION_NAMES.get(key, key)) + (f" ({unit})" if unit else "")
            # Not required here, so a blank box gets the rules' message, as an import would.
            self.fields[f"condition_{key}"] = forms.CharField(label=label, required=False)
        self.series = self.point = None  # built by clean()

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
        # One way to store each value: with a temperature it is against temperature,
        # without one against nothing (see Measurement.position_errors()).
        variable = Variable.objects.get(code=Variable.TEMPERATURE if self.has_temperature else Variable.NONE)
        self.series = Series(
            material=self.material, reference=self.reference, property=self.prop, variable=variable,
            kind=self.kind, method=cleaned.get("method", ""), conditions=conditions,
            direction=Series.Direction.NOT_APPLICABLE,  # one value is no temperature sweep
            taken_from=cleaned.get("taken_from", ""), location=cleaned.get("location", ""),
        )
        self.check_rules(self.series)
        # The point can be built only from boxes that were read: a blank value has nothing to check.
        if not any(self.has_error(name) for name in ["qualifier", "value", "uncertainty", "temperature_c"]):
            self.point = Measurement(
                series=self.series, qualifier=cleaned["qualifier"], value=cleaned["value"],
                uncertainty=cleaned["uncertainty"], temperature_c=cleaned.get("temperature_c"),
            )
            # The series has no primary key yet, which is all full_clean() would say about it.
            self.check_rules(self.point, exclude=["series"])
        return cleaned

    def check_rules(self, obj, **kwargs):
        """Check obj with its full_clean(), which runs the rules for values. Each message goes
        on this form's field of the same name, e.g. uncertainty, or above the form when there is
        none, as with the message that a property's values must state a condition."""
        try:
            obj.full_clean(**kwargs)
        except ValidationError as exc:
            for field, messages in exc.message_dict.items():
                self.add_error(field if field in self.fields else None, messages)

    def save(self, user):
        """Save the series and its point, recorded as entered by user, in one transaction, so
        a failure between the two saves keeps neither. Call it only on a valid form. Returns
        the series."""
        with transaction.atomic():
            self.series.mark_entered(user)
            self.series.save()
            self.point.series = self.series  # again, now the series has its primary key
            self.point.save()
        return self.series
