"""manage.py fetch_reference_details: fill in the first author, year, title and journal of
article and preprint references from Crossref, for those with no details yet."""

import html
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from repo.models import Reference

API = "https://api.crossref.org/works/"
PAUSE = 0.2  # seconds between requests, so FFDB doesn't send Crossref many at once
FIELDS = ["first_author", "year", "title", "venue"]

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


def look_up(doi, mailto):
    """Crossref's record for a DOI. Raises HTTPError when Crossref answers with an error, such
    as 404 for a DOI it doesn't know, and URLError when it can't be reached."""
    # quote() turns characters such as <, ; and # into %3C, %3B and %23, so they reach
    # Crossref as part of the DOI.
    request = Request(API + quote(doi), headers={"User-Agent": f"FFDB (mailto:{mailto})"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)["message"]


class Command(BaseCommand):
    help = (
        "Fill in the first author, year, title and journal of article and preprint references "
        "from Crossref, for those with no details yet. Needs CROSSREF_MAILTO in .env."
    )

    def handle(self, *args, **options):
        mailto = settings.CROSSREF_MAILTO
        if not mailto:
            raise CommandError("Set CROSSREF_MAILTO in .env: Crossref asks for a contact address.")

        # Only references with no details at all, so nothing typed in the admin is overwritten.
        todo = list(
            Reference.objects.filter(
                kind__in=[Reference.Kind.ARTICLE, Reference.Kind.PREPRINT],
                first_author="", year=None, title="", venue="",
            ).order_by("identifier")
        )
        self.stdout.write(f"References to look up: {len(todo)}")

        filled, not_found = 0, []
        for i, reference in enumerate(todo):
            if i:
                time.sleep(PAUSE)
            try:
                message = look_up(reference.identifier, mailto)
            except HTTPError as exc:  # caught before URLError, because it is a kind of URLError
                reason = "not on Crossref" if exc.code == 404 else f"HTTP {exc.code}"
                not_found.append((reference.identifier, reason))
                continue
            except (URLError, TimeoutError) as exc:
                # Without a connection every lookup would fail the same way, so stop at the first.
                raise CommandError(
                    f"Could not reach Crossref: {exc}. The {filled} references filled in so far "
                    "are saved. Run the command again to carry on."
                )
            except (ValueError, KeyError):  # the answer wasn't JSON, or had no record in it
                not_found.append((reference.identifier, "answer couldn't be read"))
                continue
            for field, value in details(message).items():
                setattr(reference, field, value)
            # Saved straight away, so a run that stops partway keeps what it found.
            reference.save(update_fields=FIELDS)
            filled += 1

        self.stdout.write(f"Filled in: {filled}")
        self.stdout.write(f"Couldn't find: {len(not_found)}")
        for identifier, reason in not_found:
            self.stdout.write(f"  {identifier} ({reason})")
