"""Possible duplicates, for the admin's page (MaterialAdmin.duplicates_view): pairs of compounds
that may be one compound entered twice. Nothing here changes a material. An admin merges a pair
(Material.merge_into()) or marks it not a duplicate (NotDuplicate), which takes it off the list."""

from collections import defaultdict
from itertools import combinations

from . import chem
from .models import Material, NotDuplicate

# What differs between two compounds with the same connectivity, in the page's order. Two
# compounds that differ only in stereochemistry given in one are most likely one compound,
# entered once without it, so they come first.
ONE_ONLY = "Stereochemistry given in one only"
BOTH = "Stereochemistry given in both"
NEITHER = "Stereochemistry given in neither: they differ in another way, such as isotopes or charge"
HEADINGS = [ONE_ONLY, BOTH, NEITHER]


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
