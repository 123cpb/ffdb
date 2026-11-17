"""Structure handling with RDKit."""

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

# RDKit prints its own parse errors to the console. StructureError reports them instead.
RDLogger.DisableLog("rdApp.*")


class StructureError(ValueError):
    """Raised when RDKit cannot read a structure."""


def standardise(smiles):
    """Return (canonical SMILES, InChIKey) for a SMILES string.

    Stereochemistry is kept exactly as given, never added or removed. Nothing else
    is changed either: no salts stripped, no charges neutralised.
    """
    mol = Chem.MolFromSmiles(smiles.strip())
    # An empty string parses as a molecule with no atoms, so check for that too.
    if mol is None or mol.GetNumAtoms() == 0:
        raise StructureError(f"RDKit could not read “{smiles}” as a SMILES. Check it for typing errors.")
    return Chem.MolToSmiles(mol), Chem.MolToInchiKey(mol)


def skeleton_key(inchikey):
    """The first block of an InChIKey: connectivity only.

    It ignores stereochemistry, and also isotopes and protonation, so two materials
    with the same skeleton key may differ in any of those.
    """
    return inchikey.split("-")[0]


def structure_svg(smiles, width=360, height=220):
    """Draw a structure as an SVG image, 360 by 220 pixels unless another size is given.

    RDKit's default options give black bonds on a white background, so the drawing
    reads the same on the public page and in the admin's dark theme.
    """
    mol = Chem.MolFromSmiles(smiles)
    drawer = rdMolDraw2D.MolDraw2DSVG(width, height)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)  # works out 2D positions, then draws
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def formula_and_mass(smiles):
    """Return (molecular formula, molar mass in g/mol), e.g. ("C22H17NO8", 423.377)."""
    mol = Chem.MolFromSmiles(smiles)
    return rdMolDescriptors.CalcMolFormula(mol), Descriptors.MolWt(mol)


def substructure_pattern(text):
    """Read a substructure search, SMILES or SMARTS, as a pattern for has_substructure().

    It is always read as SMARTS, which RDKit also accepts every SMILES as. Read as SMILES,
    a fragment like [N+](=O)[O-] or [#6]#[#7] parses but matches nothing. The catch: a ring
    with alternating double bonds, C1=CC=CC=C1, means just that in SMARTS, so it doesn't
    match benzene. Write aromatic rings in lower case: c1ccccc1.
    """
    pattern = Chem.MolFromSmarts(text.strip())
    # An empty string gives a pattern with no atoms, so check for that too.
    if pattern is None or pattern.GetNumAtoms() == 0:
        raise StructureError(f"RDKit could not read “{text}” as SMILES or SMARTS. Check it for typing errors.")
    return pattern


def has_substructure(smiles, pattern):
    """Whether the structure contains the pattern. Stereochemistry is ignored, so a pattern
    with a stereocentre matches either hand. A SMILES RDKit can't read never matches."""
    mol = Chem.MolFromSmiles(smiles)
    return mol is not None and mol.HasSubstructMatch(pattern)
