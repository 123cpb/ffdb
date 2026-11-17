"""Structure handling with RDKit."""

from rdkit import Chem, RDLogger

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
