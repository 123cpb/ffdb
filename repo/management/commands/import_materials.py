"""manage.py import_materials <path>: load names, structures, phase sequences and their
references from a merged list."""

import csv
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from repo import chem
from repo.models import Material, Phase, PhaseSequence, Reference, RetiredID, clean_doi, name_key
from repo.phases import PhaseSequenceError, parse_summary

# The columns of the merged list that this command reads. Its other columns
# (alt_reference and values) are for later stages.
COLUMNS = ["origin", "line", "name", "smiles", "sequence", "reference", "reference_kind"]

def reference_kind(kind, identifier):
    """The Reference kind for the merged list's reference_kind, or None for a kind FFDB
    doesn't take, such as a manuscript number. A DOI is an article unless a preprint
    server gave it (Reference.kind_for_doi(), which the Add data page uses too)."""
    if kind == "doi":
        return Reference.kind_for_doi(identifier)
    return {"patent": Reference.Kind.PATENT, "thesis": Reference.Kind.THESIS}.get(kind)


class Command(BaseCommand):
    help = (
        "Import names, structures, phase sequences and references from a merged list, a CSV "
        f"with {', '.join(COLUMNS)} columns."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="the CSV file, kept outside the repository")

    def handle(self, *args, **options):
        path = options["path"]
        try:
            # utf-8-sig also reads a file that Excel saved with a byte-order mark.
            with open(path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                missing = [column for column in COLUMNS if column not in (reader.fieldnames or [])]
                if missing:
                    raise CommandError(f"{path} has no {', '.join(missing)} column. It needs {', '.join(COLUMNS)}.")
                rows = list(reader)
        except OSError as exc:
            raise CommandError(f"Could not read {path}: {exc}")

        # A row can now change two things, its material and its sequence, so each step
        # adds to these counts rather than returning one outcome per row.
        self.counts = Counter()
        self.added = []  # the primary keys of the materials this run adds
        synonyms = Phase.synonym_map()  # read once, for checking every row's sequence
        # One transaction: if anything goes wrong partway, nothing from this run is kept.
        with transaction.atomic():
            for row in rows:
                self.import_row(row, synonyms)
            # A material this run added that has no sequence is in the list for its structure
            # alone, as LCPROP's are, so it is listed. Its rows had no sequence, or the import
            # skipped it. Only now: a later row can still give it one.
            listed = Material.objects.filter(pk__in=self.added, phase_sequences__isnull=True).update(listed=True)

        self.stdout.write(f"Rows read: {len(rows)}")
        self.stdout.write(f"Materials added: {self.counts['material added']}")
        self.stdout.write(f"Names added as aliases: {self.counts['alias']}")
        self.stdout.write(f"Materials already there: {self.counts['material already there']}")
        self.stdout.write(f"Rows skipped: {self.counts['skipped']}")
        self.stdout.write(f"References added: {self.counts['reference added']}")
        self.stdout.write(f"Sequences added: {self.counts['sequence added']}")
        self.stdout.write(f"Sequences already there: {self.counts['sequence already there']}")
        self.stdout.write(f"Sequences skipped: {self.counts['sequence skipped']}")
        self.stdout.write(f"Materials listed, with no sequence: {listed}")

    def import_row(self, row, synonyms):
        """Import one row: its material, then its reference and sequence if it has one."""
        where = f"{row['origin']} line {row['line']}"  # its place in the original list
        name = (row["name"] or "").strip()
        outcome, material = self.import_material(row, where, name)
        self.counts[outcome] += 1
        if (row["sequence"] or "").strip():
            self.counts[self.import_sequence(row, material, where, name, synonyms)] += 1

    def import_material(self, row, where, name):
        """Returns (what happened, the material). What happened is "material added",
        "alias", "material already there" or "skipped", when the material is None."""
        smiles = (row["smiles"] or "").strip()
        if not smiles:
            return self.skip(where, name, "no SMILES"), None
        try:
            _, inchikey = chem.standardise(smiles)
        except chem.StructureError as exc:
            return self.skip(where, name, str(exc)), None

        material = Material.objects.filter(inchikey=inchikey).first()
        if material is None:
            # A retired ID's structure would get a second ID: restoring is the way back.
            problem = RetiredID.structure_problem(inchikey)
            if problem:
                return self.skip(where, name, problem), None
            material = Material.objects.create(name=name, smiles=smiles)
            self.added.append(material.pk)
            warning = material.stereo_warning()
            if warning:
                self.stdout.write(f"{where}: {warning}")
            return "material added", material

        # The structure is already in FFDB: keep the row's name as an alias, if it's a new name.
        if name_key(name) and not material.has_name(name):
            material.aliases = ", ".join(material.alias_list() + [name])
            material.save()
            return "alias", material
        return "material already there", material

    def import_sequence(self, row, material, where, name, synonyms):
        """Add the row's reference, if it's new, and its sequence, if it's new. Returns
        "sequence added", "sequence already there" or "sequence skipped"."""
        if material is None:
            return "sequence skipped"  # the row's structure was skipped, and that was printed
        identifier = (row["reference"] or "").strip()
        kind = reference_kind(row["reference_kind"], identifier)
        if kind is None:
            return self.skip(
                where, name, f"reference kind {row['reference_kind']!r} isn't one FFDB takes ({identifier})",
                what="sequence skipped",
            )
        sequence = row["sequence"].strip()
        try:
            # The standardised text decides whether the sequence is new, so "Nf" and "NF" match.
            standard = parse_summary(sequence, synonyms).standard_text()
        except PhaseSequenceError as exc:
            return self.skip(where, name, str(exc), what="sequence skipped")

        # Only now, with a sequence to cite it, is the reference added if it's new.
        if kind in (Reference.Kind.ARTICLE, Reference.Kind.PREPRINT):
            identifier = clean_doi(identifier)
        # A new reference is public unless it is unpublished work. One already in FFDB keeps
        # its switch, so an import never undoes an admin hiding it.
        reference, created = Reference.objects.get_or_create(
            identifier=identifier, defaults={"kind": kind, "public": kind != Reference.Kind.UNPUBLISHED}
        )
        self.counts["reference added"] += created  # True counts as 1, False as 0

        # The same structure and reference with a different sequence: both are kept. The list
        # holds summaries, so a run from the reference with the same text isn't this sequence.
        summary = PhaseSequence.Direction.SUMMARY
        if reference.phase_sequences.filter(material=material, direction=summary, standard_sequence=standard).exists():
            return "sequence already there"
        # Approved, as an admin's own saves are: only an admin runs an import.
        PhaseSequence.objects.create(
            material=material, reference=reference, direction=summary, sequence=sequence, approved=True
        )
        return "sequence added"

    def skip(self, where, name, reason, what="skipped"):
        self.stdout.write(f"{where} ({name or 'no name'}): {what}, {reason}")
        return what
