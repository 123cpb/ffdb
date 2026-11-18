"""Looking a DOI up on Crossref: its record, and the Reference details read from it. Used by
the fetch_reference_details command and by the Add data page when a DOI is added."""

import html
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import Reference

API = "https://api.crossref.org/works/"

# Digits and signs written small: "10<sup>4</sup>" is 10⁴ and "CO<sub>2</sub>" is CO₂.
SUPERSCRIPTS = str.maketrans("0123456789+-−", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻")
SUBSCRIPTS = str.maketrans("0123456789+-−", "₀₁₂₃₄₅₆₇₈₉₊₋₋")
# A superscript or subscript, written <sup>…</sup>, or <jats:sup>…</jats:sup> in some records.
SCRIPT = re.compile(r"<(?:jats:)?(sup|sub)>\s*(.*?)\s*</(?:jats:)?\1>", re.DOTALL)


def _script(match):
    """Digits and signs as Unicode; anything else, such as the F in A<sub>F</sub>, as it is."""
    kind, text = match[1], match[2]
    if re.fullmatch(r"[0-9+\-−]+", text):
        return text.translate(SUPERSCRIPTS if kind == "sup" else SUBSCRIPTS)
    return text


def clean_text(value):
    """Plain text from Crossref's, which can carry HTML tags (<i>, <sub>) and entities (&amp;)."""
    text = SCRIPT.sub(_script, value or "")
    text = re.sub(r"<[^>]+>", "", text)  # every other tag
    return " ".join(html.unescape(text).split())


def first(values):
    """The first item of a list Crossref gives, or "" when the list is empty or missing."""
    return values[0] if values else ""


def first_author(message):
    """The first author's surname, or a consortium's name. Crossref marks the first author
    with "sequence": "first"; when it marks none, the first in the list."""
    authors = message.get("author") or []
    marked = [author for author in authors if author.get("sequence") == "first"]
    author = first(marked or authors) or {}
    return clean_text(author.get("family") or author.get("name"))


def publication_year(message):
    """The year a paper is cited by: its print issue's when it has one, because "issued" is
    the earliest date, often the online one. None when Crossref gives neither."""
    for key in ("published-print", "issued"):
        parts = (message.get(key) or {}).get("date-parts") or [[]]
        if parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def journal(message):
    """The journal as Crossref shortens it, else its full name, else "" (a preprint has none)."""
    return clean_text(first(message.get("short-container-title")) or first(message.get("container-title")))


def details(message):
    """The Reference fields from a Crossref record, each text cut to fit its field."""
    found = {
        "first_author": first_author(message),
        "year": publication_year(message),
        "title": clean_text(first(message.get("title"))),
        "venue": journal(message),
    }
    for field in ("first_author", "title", "venue"):
        found[field] = found[field][: Reference._meta.get_field(field).max_length]
    return found


# A paragraph tag in an abstract: <jats:p> or </jats:p>, with or without attributes.
PARAGRAPH = re.compile(r"</?jats:p(?:\s[^>]*)?>")


def abstract_text(message):
    """The abstract as plain text, paragraphs separated by a blank line, or "" when Crossref
    has none. Crossref gives it as JATS XML, often starting with an "Abstract" heading,
    which is dropped."""
    jats = re.sub(r"<jats:title>.*?</jats:title>", "", message.get("abstract") or "", flags=re.DOTALL)
    paragraphs = (clean_text(paragraph) for paragraph in PARAGRAPH.split(jats))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def look_up(doi, mailto):
    """Crossref's record for a DOI. Raises HTTPError when Crossref answers with an error, such
    as 404 for a DOI it doesn't know, and URLError when it can't be reached."""
    # quote() turns characters such as <, ; and # into %3C, %3B and %23, so they reach
    # Crossref as part of the DOI.
    request = Request(API + quote(doi), headers={"User-Agent": f"FFDB (mailto:{mailto})"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)["message"]
