"""Forms for the Add data pages."""

import re

from django import forms
from django.core.exceptions import ValidationError

from .models import Reference, clean_doi, clean_patent_number

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
