"""Forms for the Add data pages."""

import re
from collections import Counter
from dataclasses import dataclass

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.safestring import mark_safe

from . import chem
from .models import Material, Phase, PhaseSequence, Reference, clean_doi, clean_patent_number
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
