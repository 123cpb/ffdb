import re

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from . import chem


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
        The admin and the import both show it."""
        variants = self.possible_stereo_variants()
        if not variants:
            return ""
        others = ", ".join(f"{v.accession} ({v.name})" for v in variants)
        return (
            f"{self.accession} has the same connectivity as {others} but different "
            "stereochemistry. Both are kept as separate materials: check that is right, "
            "and say in the name or notes whether a structure without stereochemistry "
            "is racemic or simply not stated."
        )
