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


def structure_svg(smiles):
    """Draw a structure as an SVG image, 360 by 220 pixels.

    RDKit's default options give black bonds on a white background, so the drawing
    reads the same on the public page and in the admin's dark theme.
    """
    mol = Chem.MolFromSmiles(smiles)
    drawer = rdMolDraw2D.MolDraw2DSVG(360, 220)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)  # works out 2D positions, then draws
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def formula_and_mass(smiles):
    """Return (molecular formula, molar mass in g/mol), e.g. ("C22H17NO8", 423.377)."""
    mol = Chem.MolFromSmiles(smiles)
    return rdMolDescriptors.CalcMolFormula(mol), Descriptors.MolWt(mol)
