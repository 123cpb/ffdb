"""Possible duplicates, for the admin's page (MaterialAdmin.duplicates_view): pairs of compounds
that may be one compound entered twice, in two lists. connectivity_pairs() finds compounds with
the same connectivity; sequence_pairs() finds compounds with matching sequences from one
reference, which may be one compound entered with two different structures. A waiting save's page
flags the pairs of each of its materials (pairs_of()). Nothing here changes a material. An admin
merges a pair (Material.merge_into()) or marks it not a duplicate (NotDuplicate), which takes it off
both lists."""

from collections import defaultdict
from itertools import combinations

from . import chem
from .models import Material, NotDuplicate, Phase, PhaseSequence
from .phases import NOT_LIQUID_CRYSTAL, PhaseSequenceError

# What differs between two compounds with the same connectivity, in the page's order. Two
# compounds that differ only in stereochemistry given in one are most likely one compound,
# entered once without it, so they come first.
ONE_ONLY = "Stereochemistry given in one only"
BOTH = "Stereochemistry given in both"
NEITHER = "Stereochemistry given in neither: they differ in another way, such as isotopes or charge"
HEADINGS = [ONE_ONLY, BOTH, NEITHER]

# Two sequences match when each temperature is within this many kelvin of the other's. From the
# prototype, where on 3 November 2026 0 K found 5 groups; 0.5 K found 11, adding 241 against
# 240.6 °C and values rounded to whole degrees; and 1 K found 17, adding neighbours in
# homologous series, which are different compounds.
SEQUENCE_TOLERANCE_K = 0.5


def what_differs(a, b):
    """Which of HEADINGS describes compounds a and b, from their canonical SMILES."""
    given = [chem.gives_stereo(a.canonical_smiles), chem.gives_stereo(b.canonical_smiles)]
    if all(given):
        return BOTH
    if any(given):
        return ONE_ONLY
    return NEITHER


def connectivity_pairs():
    """Pairs of compounds with the same connectivity, the first block of the InChIKey
    (chem.skeleton_key()), and so different structures, since no two share a whole InChIKey.
    Hidden ones are included: only admins see the page. A pair marked not a duplicate is left out.
    Returns [(heading, a, b), ...], a's LCR number lower than b's, in the order of HEADINGS and
    then by LCR ID. Mixtures have no InChIKey and are never read. Every compound is read in one
    query and Python groups them, which reads more plainly than asking the database to cut the
    first block off each InChIKey."""
    groups = defaultdict(list)  # {skeleton key: [compounds]}
    for compound in Material.objects.filter(kind=Material.Kind.COMPOUND).order_by("pk"):
        groups[chem.skeleton_key(compound.inchikey)].append(compound)
    decided = set(NotDuplicate.objects.values_list("first", "second"))  # {(12, 40), ...}
    pairs = []
    for compounds in groups.values():
        # Each two of a group, in its LCR ID order: three compounds give three pairs. A group of
        # one gives none.
        for a, b in combinations(compounds, 2):
            if (a.pk, b.pk) not in decided:
                pairs.append((what_differs(a, b), a, b))
    return sorted(pairs, key=lambda pair: (HEADINGS.index(pair[0]), pair[1].pk, pair[2].pk))


def temperatures_match(first, second):
    """Whether two sequences' temperatures match, each within SEQUENCE_TOLERANCE_K of the other's,
    in order. A temperature not given (None, a melting point written ?) matches only another not
    given. Sequences with the same phases have the same number of temperatures. The 1e-9 is there
    because a computer stores numbers in binary: 128.3 - 127.8 comes out as 0.5000000000000142, and
    without it a gap of exactly 0.5 K wouldn't count."""
    return all(
        (x is None and y is None) or (x is not None and y is not None and abs(x - y) <= SEQUENCE_TOLERANCE_K + 1e-9)
        for x, y in zip(first, second)
    )


def sequence_pairs():
    """Pairs of compounds with a sequence each from the same reference that match: the same
    direction, the same phases in the same order, the solid included, and every temperature, the
    melting point included, within SEQUENCE_TOLERANCE_K (temperatures_match()). Only sequences with
    a liquid-crystal phase count, because compounds that only melt at the same temperature are
    common. Hidden and waiting sequences are included: only admins see the page, and a waiting one
    may be what duplicates a compound. A pair marked not a duplicate is left out.

    Returns [(reference, a, b, matching), ...], a's LCR number lower than b's, matching being the
    sequences of either from that reference that match one of the other's, sorted by reference and
    then by LCR ID. The same two compounds matching in two references are a pair under each."""
    synonyms = Phase.synonym_map()  # once, not once for each sequence
    sequences = PhaseSequence.objects.filter(material__kind=Material.Kind.COMPOUND).select_related("material", "reference")
    # Only sequences in one bucket can match: {(reference, direction, phases): [(sequence, temperatures)]}
    buckets = defaultdict(list)
    for sequence in sequences:
        try:
            parsed = sequence.parsed(synonyms)
        except PhaseSequenceError:
            continue  # a label since taken out of the vocabulary: it can't be compared
        if all(phase in NOT_LIQUID_CRYSTAL for phase in parsed.phases):
            continue
        # The temperatures as written, not the saved transitions: a summary's melting point isn't
        # a transition when a monotropic phase lies below it, and it has to match too.
        key = (sequence.reference_id, sequence.direction, tuple(parsed.phases))
        buckets[key].append((sequence, parsed.temperatures))

    decided = set(NotDuplicate.objects.values_list("first", "second"))  # {(12, 40), ...}
    found = defaultdict(set)  # {(reference, a, b): {matching sequences}}
    for entries in buckets.values():
        for (one, one_temperatures), (two, two_temperatures) in combinations(entries, 2):
            if one.material_id == two.material_id or not temperatures_match(one_temperatures, two_temperatures):
                continue
            a, b = sorted([one.material, two.material], key=lambda material: material.pk)
            if (a.pk, b.pk) not in decided:
                found[(one.reference, a, b)] |= {one, two}

    pairs = [
        (reference, a, b, sorted(matching, key=lambda sequence: (sequence.material_id, sequence.pk)))
        for (reference, a, b), matching in found.items()
    ]
    return sorted(pairs, key=lambda pair: (pair[0].label, pair[0].pk, pair[1].pk, pair[2].pk))


def pairs_of(material, connectivity, sequences):
    """The pairs that include material, from the two lists connectivity_pairs() and sequence_pairs()
    return, for a waiting save's page (WaitingSaveAdmin.preview()): [(the other compound, heading or
    reference, the other's matching sequences), ...]. A pair from the first list has no sequences.
    The lists are passed in, so a page with several materials works them out once."""
    found = []
    for heading, a, b in connectivity:
        if material in (a, b):
            found.append((b if material == a else a, heading, []))
    for reference, a, b, matching in sequences:
        if material in (a, b):
            other = b if material == a else a
            found.append((other, reference, [sequence for sequence in matching if sequence.material_id == other.pk]))
    return found
