import io
import json
from io import StringIO
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import unquote

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings

from repo.crossref import API, abstract_text, clean_text, first_author, journal, publication_year
from repo.models import Reference

COMMAND = "repo.management.commands.fetch_reference_details"
# Where look_up() finds urlopen, so a test that replaces it there replaces every request.
URLOPEN = "repo.crossref.urlopen"

PCCP = "10.1039/c7cp00456g"
BCSJ = "10.1246/bcsj.56.3571"
MCLC = "10.1080/00268948108071990"
JPCL = "10.1021/acs.jpclett.2c02846"
CHEMRXIV = "10.26434/chemrxiv.15004113/v1"
UNKNOWN = "10.1000/unknown"

# Saved Crossref answers, cut down from the real records to the fields the command reads.
RESPONSES = {
    # A short journal title, and an abstract in JATS, as Crossref gives it.
    PCCP: {
        "type": "journal-article",
        "abstract": (
            "<jats:title>Abstract</jats:title><jats:p>A rod-like liquid crystal with two nematic "
            "phases.</jats:p>\n<jats:p>The lower one is ferroelectric, with P<jats:sub>s</jats:sub> "
            "near 6 µC cm<jats:sup>−2</jats:sup>.</jats:p>"
        ),
        "title": ["A nematic to nematic transformation exhibited by a rod-like liquid crystal"],
        "author": [{"given": "Richard J.", "family": "Mandle", "sequence": "first"}],
        "container-title": ["Physical Chemistry Chemical Physics"],
        "short-container-title": ["Phys. Chem. Chem. Phys."],
        "issued": {"date-parts": [[2017]]},
    },
    # No short journal title, so the full one.
    BCSJ: {
        "type": "journal-article",
        "title": ["Thermal and Dielectric Properties of Liquid Crystals with a Coumarin Skeleton"],
        "author": [{"given": "Hidetsugu", "family": "Nakai", "sequence": "first"}],
        "container-title": ["Bulletin of the Chemical Society of Japan"],
        "short-container-title": [],
        "published-print": {"date-parts": [[1983, 12, 1]]},
        "issued": {"date-parts": [[1983, 12, 1]]},
        "published-online": {"date-parts": [[2006, 6, 27]]},
    },
    # Put online in 2011, thirty years after it was printed.
    MCLC: {
        "type": "journal-article",
        "title": ["Synthesis and Properties of New Liquid Crystalline Materials"],
        "author": [{"given": "D.", "family": "Demus", "sequence": "first"}],
        "container-title": ["Molecular Crystals and Liquid Crystals"],
        "short-container-title": ["Molecular Crystals and Liquid Crystals"],
        "published-print": {"date-parts": [[1981, 1]]},
        "issued": {"date-parts": [[1981, 1]]},
        "published-online": {"date-parts": [[2011, 10, 14]]},
    },
    # A subscript in the title.
    JPCL: {
        "type": "journal-article",
        "title": [
            "Emerging Ferroelectric Uniaxial Lamellar (Smectic A<sub>F</sub>) Fluids for "
            "Bistable In-Plane Polarization Memory"
        ],
        "author": [{"given": "Yaohao", "family": "Song", "sequence": "first"}],
        "container-title": ["The Journal of Physical Chemistry Letters"],
        "short-container-title": ["J. Phys. Chem. Lett."],
        "published-print": {"date-parts": [[2022, 10, 27]]},
        "issued": {"date-parts": [[2022, 10, 20]]},
    },
    # A preprint: no journal.
    CHEMRXIV: {
        "type": "posted-content",
        "title": ["Light-Driven Control of Polar Order in Ferroelectric Nematic Liquid Crystals"],
        "author": [{"given": "Anjali Devi", "family": "Das", "sequence": "first"}],
        "container-title": [],
        "short-container-title": [],
        "issued": {"date-parts": [[2026, 6, 1]]},
    },
}


class FakeCrossref:
    """Stands in for urlopen. It answers from RESPONSES, with 404 for a DOI that isn't there,
    and keeps every request so tests can check what was asked."""

    def __init__(self):
        self.requests = []
        self.unreachable = set()  # DOIs for which the connection fails

    def __call__(self, request, timeout):
        self.requests.append(request)
        doi = unquote(request.full_url.removeprefix(API))
        if doi in self.unreachable:
            raise URLError("no connection")
        if doi not in RESPONSES:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(json.dumps({"status": "ok", "message": RESPONSES[doi]}).encode())

    def dois(self):
        """The DOIs asked for, in order."""
        return [unquote(request.full_url.removeprefix(API)) for request in self.requests]


@override_settings(CROSSREF_MAILTO="ffdb-tests@example.org")
class FetchReferenceDetailsTests(TestCase):
    def setUp(self):
        for identifier in [PCCP, BCSJ, MCLC, JPCL, UNKNOWN]:
            Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier=identifier)
        Reference.objects.create(kind=Reference.Kind.PREPRINT, identifier=CHEMRXIV)
        Reference.objects.create(kind=Reference.Kind.PATENT, identifier="EP3260450A1")
        Reference.objects.create(kind=Reference.Kind.THESIS, identifier="Mandle 2015, PhD thesis")

        self.crossref = FakeCrossref()
        for patcher in (mock.patch(URLOPEN, self.crossref), mock.patch(f"{COMMAND}.PAUSE", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_command(self):
        """Run the command and return what it printed."""
        out = StringIO()
        call_command("fetch_reference_details", stdout=out)
        return out.getvalue()

    def reference(self, identifier):
        return Reference.objects.get(identifier=identifier)

    def test_labels_are_made_from_crossrefs_details(self):
        self.run_command()

        self.assertEqual(self.reference(PCCP).label, "Mandle 2017, Phys. Chem. Chem. Phys.")
        self.assertEqual(self.reference(BCSJ).label, "Nakai 1983, Bulletin of the Chemical Society of Japan")
        self.assertEqual(self.reference(MCLC).label, "Demus 1981, Molecular Crystals and Liquid Crystals")

    def test_a_preprint_has_no_journal(self):
        self.run_command()

        preprint = self.reference(CHEMRXIV)
        self.assertEqual(preprint.label, "Das 2026")
        self.assertEqual(preprint.venue, "")

    def test_titles_are_plain_text(self):
        self.run_command()

        self.assertEqual(
            self.reference(JPCL).title,
            "Emerging Ferroelectric Uniaxial Lamellar (Smectic AF) Fluids for Bistable In-Plane Polarization Memory",
        )

    def test_a_doi_crossref_doesnt_know_is_printed_and_left_empty(self):
        output = self.run_command()

        self.assertIn("References to look up: 6\nFilled in: 5\nCouldn't find: 1\n", output)
        self.assertIn(f"  {UNKNOWN} (not on Crossref)", output)
        self.assertEqual(self.reference(UNKNOWN).label, UNKNOWN)

    def test_patents_and_theses_are_not_looked_up(self):
        self.run_command()

        self.assertEqual(sorted(self.crossref.dois()), sorted([PCCP, BCSJ, MCLC, JPCL, CHEMRXIV, UNKNOWN]))

    def test_a_second_run_looks_up_only_what_it_couldnt_find(self):
        self.run_command()
        self.crossref.requests.clear()

        output = self.run_command()

        self.assertEqual(self.crossref.dois(), [UNKNOWN])
        self.assertIn("References to look up: 1\n", output)

    def test_details_typed_in_the_admin_are_kept(self):
        Reference.objects.filter(identifier=PCCP).update(venue="PCCP")

        self.run_command()

        self.assertNotIn(PCCP, self.crossref.dois())
        self.assertEqual(self.reference(PCCP).venue, "PCCP")
        self.assertEqual(self.reference(PCCP).first_author, "")

    def test_requests_carry_the_contact_address(self):
        self.run_command()

        agents = {request.get_header("User-agent") for request in self.crossref.requests}
        self.assertEqual(agents, {"FFDB (mailto:ffdb-tests@example.org)"})

    def test_without_a_contact_address_nothing_is_looked_up(self):
        with override_settings(CROSSREF_MAILTO=""):
            with self.assertRaisesMessage(CommandError, "Set CROSSREF_MAILTO in .env"):
                self.run_command()

        self.assertEqual(self.crossref.requests, [])

    def test_no_connection_stops_the_run_and_keeps_what_was_found(self):
        # References are looked up in identifier order, so JPCL and PCCP come before MCLC.
        self.crossref.unreachable = {MCLC}

        with self.assertRaisesMessage(CommandError, "Could not reach Crossref"):
            self.run_command()

        self.assertEqual(self.reference(JPCL).first_author, "Song")
        self.assertEqual(self.reference(PCCP).first_author, "Mandle")
        self.assertEqual(self.reference(MCLC).label, MCLC)


class CleanTextTests(SimpleTestCase):
    def test_tags_are_dropped_and_entities_unescaped(self):
        self.assertEqual(clean_text("<i>N</i>-type &amp; <scp>p</scp>-type"), "N-type & p-type")

    def test_digits_in_subscripts_and_superscripts_become_unicode(self):
        self.assertEqual(clean_text("CO<sub>2</sub> at 10<sup>4</sup> Hz"), "CO₂ at 10⁴ Hz")

    def test_letters_in_a_subscript_join_the_text_before_them(self):
        self.assertEqual(clean_text("Smectic A<sub>F</sub> phase"), "Smectic AF phase")

    def test_jats_tags_are_read_too(self):
        self.assertEqual(clean_text("P<jats:sub>s</jats:sub> of 10<jats:sup>−3</jats:sup>"), "Ps of 10⁻³")

    def test_spaces_are_tidied(self):
        self.assertEqual(clean_text("  A\n  nematic   phase "), "A nematic phase")

    def test_nothing_gives_an_empty_string(self):
        self.assertEqual(clean_text(None), "")


class AbstractTextTests(SimpleTestCase):
    def test_the_heading_is_dropped_and_paragraphs_kept(self):
        self.assertEqual(
            abstract_text(RESPONSES[PCCP]),
            "A rod-like liquid crystal with two nematic phases.\n\n"
            "The lower one is ferroelectric, with Ps near 6 µC cm⁻².",
        )

    def test_an_abstract_without_paragraph_tags_is_one_paragraph(self):
        self.assertEqual(abstract_text({"abstract": "  A plain   abstract. "}), "A plain abstract.")

    def test_no_abstract_gives_an_empty_string(self):
        self.assertEqual(abstract_text(RESPONSES[BCSJ]), "")


class FirstAuthorTests(SimpleTestCase):
    def test_the_author_marked_first(self):
        message = {"author": [
            {"family": "Gibb", "sequence": "additional"},
            {"family": "Mandle", "sequence": "first"},
        ]}
        self.assertEqual(first_author(message), "Mandle")

    def test_the_first_listed_when_none_is_marked(self):
        self.assertEqual(first_author({"author": [{"family": "Mandle"}, {"family": "Gibb"}]}), "Mandle")

    def test_a_consortiums_name(self):
        self.assertEqual(first_author({"author": [{"name": "LC Consortium", "sequence": "first"}]}), "LC Consortium")

    def test_no_authors(self):
        self.assertEqual(first_author({}), "")


class PublicationYearTests(SimpleTestCase):
    def test_the_print_issue_comes_first(self):
        message = {"published-print": {"date-parts": [[2018, 1]]}, "issued": {"date-parts": [[2017, 12, 5]]}}
        self.assertEqual(publication_year(message), 2018)

    def test_issued_without_a_print_issue(self):
        self.assertEqual(publication_year({"issued": {"date-parts": [[2024, 7, 11]]}}), 2024)

    def test_no_year(self):
        self.assertIsNone(publication_year({"issued": {"date-parts": [[None]]}}))


class JournalTests(SimpleTestCase):
    def test_the_short_title_comes_first(self):
        message = {"short-container-title": ["Nat Commun"], "container-title": ["Nature Communications"]}
        self.assertEqual(journal(message), "Nat Commun")

    def test_the_full_title_without_a_short_one(self):
        message = {"short-container-title": [], "container-title": ["Bulletin of the Chemical Society of Japan"]}
        self.assertEqual(journal(message), "Bulletin of the Chemical Society of Japan")

    def test_no_journal(self):
        self.assertEqual(journal({"container-title": []}), "")
