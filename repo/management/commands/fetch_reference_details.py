"""manage.py fetch_reference_details: fill in the first author, year, title and journal of
article and preprint references from Crossref, for those with no details yet. The lookup
itself is in repo/crossref.py, which the Add data page uses too."""

import time
from urllib.error import HTTPError, URLError

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from repo.crossref import details, look_up
from repo.models import Reference

PAUSE = 0.2  # seconds between requests, so FFDB doesn't send Crossref many at once
FIELDS = ["first_author", "year", "title", "venue"]


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
