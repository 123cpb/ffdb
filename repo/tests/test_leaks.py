"""The leak test: hidden data, each piece carrying a marker, and every public address
requested, as a visitor and as an admin, failing if the marker or anything that identifies a
hidden material appears. Later blocks add their addresses to LeakTests.addresses(), and their
hidden pieces to HiddenData."""

import csv
import io
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import resolve, reverse

from repo.models import (
    Material, MixtureComponent, PhaseSequence, Property, Reference, RetiredID, Series, Variable, WaitingSave, accession,
)
from repo.urls import urlpatterns
from repo.tests.test_import import ARTICLE, FIVE_CB
from repo.tests.test_merge import DUPLICATE_SUMMARY, EIGHT_CB, WRONG_RM734
from repo.tests.test_sequences import RM734, RM734_SUMMARY
from repo.tests.test_switches import UNPUBLISHED_WORK

# In every hidden piece: a name, a method or a reference's identifier, each shown on a page if it leaks.
MARKER = "LEAKMARKER"
EIGHT_OCB = "CCCCCCCCOc1ccc(-c2ccc(C#N)cc2)cc1"
# The Materials page, then its three downloads. Each takes the page's search.
MATERIALS_PAGES = ["repo:materials", "repo:materials_csv", "repo:sequences_csv", "repo:values_csv"]
# The References page searches aimed at the hidden references: the marker, in both their
# identifiers; the unpublished work's words; and the word unpublished, which once found it.
REFERENCE_SEARCHES = [{"q": MARKER}, {"q": UNPUBLISHED_WORK}, {"q": "unpublished"}]
# The addresses in repo/urls.py that only curators can open, by name. The leak test doesn't
# request them, so LeakTests checks that each sends a visitor to log in.
CURATORS_ONLY = {
    "add", "add_from_reference", "add_compound", "check_card_material", "check_card_runs", "check_table",
    "make_cards", "check_block", "check_block_boxes",
}


def csv_rows(response):
    """A download's rows, each a dict keyed by the header: [] for a file with its header only."""
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


class HiddenData(TestCase):
    """RM734, public, with hidden data too; a compound with only waiting data; one whose only
    data is under unpublished work; a hidden mixture whose component, RM734, is public; and a
    public mixture, one of whose components is hidden. RM734's waiting series is a Ps value,
    a property no public series has. It and RM734's waiting sequence from the paper are a curator's
    waiting save, which holds a label for RM734. Two hidden references: the unpublished work, and a
    published article whose only data, a sequence of RM734's, waits. Then three retired IDs: a hidden
    compound deleted, a hidden compound merged into RM734, and a listed mixture merged into
    the hidden mixture, whose address must not lead there."""

    @classmethod
    def setUpTestData(cls):
        cls.paper = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier=ARTICLE, first_author="Mandle", year=2017, published=True
        )
        cls.unpublished = Reference.objects.create(
            kind=Reference.Kind.UNPUBLISHED, identifier=f"{UNPUBLISHED_WORK}, {MARKER}"
        )
        # Published, but not public: nothing it gives is approved yet.
        cls.waiting_paper = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier=f"10.9999/{MARKER}", published=True
        )
        cls.hidden_references = [cls.unpublished, cls.waiting_paper]

        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.public_sequence = cls.sequence(cls.rm734, cls.paper, RM734_SUMMARY)
        cls.public_series = cls.series(cls.rm734, cls.paper, method="current reversal")
        # A label held until the save is approved: it mustn't show among RM734's names before then.
        saved = WaitingSave.objects.create(reference=cls.paper, labels={str(cls.rm734.pk): f"{MARKER} held label"})
        # SmA: a phase that only the waiting sequences have.
        cls.sequence(
            cls.rm734, cls.paper, "K 80 SmA 120 I", approved=False, method=f"{MARKER} waiting sequence", waiting_save=saved
        )
        cls.sequence(cls.rm734, cls.waiting_paper, "K 80 SmA 120 I", approved=False)
        cls.waiting_series = cls.series(
            cls.rm734, cls.paper, approved=False, code="ps", method=f"{MARKER} waiting series", waiting_save=saved
        )
        cls.unpublished_series = cls.series(cls.rm734, cls.unpublished, method=f"{MARKER} unpublished series")

        cls.waiting = Material.objects.create(name=f"{MARKER} waiting compound", smiles=FIVE_CB)
        cls.sequence(cls.waiting, cls.paper, "K 22.5 N 35 I", approved=False)

        cls.unpublished_compound = Material.objects.create(name=f"{MARKER} unpublished compound", smiles=EIGHT_OCB)
        cls.sequence(cls.unpublished_compound, cls.unpublished, "K 54 SmA 67 N 80 I")

        cls.hidden_mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name=f"{MARKER} hidden mixture")
        MixtureComponent.objects.create(mixture=cls.hidden_mixture, component=cls.rm734, fraction=50, unit="mol%")
        cls.hidden_mixture_series = cls.series(cls.hidden_mixture, cls.paper, approved=False)

        cls.public_mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name="F7")
        MixtureComponent.objects.create(mixture=cls.public_mixture, component=cls.rm734, fraction=70, unit="mol%")
        MixtureComponent.objects.create(mixture=cls.public_mixture, component=cls.waiting, fraction=30, unit="mol%")
        cls.public_mixture_sequence = cls.sequence(cls.public_mixture, cls.paper, "K 30 NF 90 N 120 I")

        cls.hidden = [cls.waiting, cls.unpublished_compound, cls.hidden_mixture]
        cls.admin = User.objects.create_superuser("admin", password="a-test-password")

        deleted = Material.objects.create(name=f"{MARKER} deleted compound", smiles=EIGHT_CB)
        cls.sequence(deleted, cls.paper, "K 21 N 40 I", approved=False)
        merged = Material.objects.create(name=f"{MARKER} merged compound", aliases=f"{MARKER}-2", smiles=WRONG_RM734)
        cls.sequence(merged, cls.paper, DUPLICATE_SUMMARY, approved=False)
        into_hidden = Material.objects.create(kind=Material.Kind.MIXTURE, name="F8", listed=True)
        # Each by its number, read first: retiring sets the pk to None, and keeps the rest.
        cls.hidden_retired = {deleted.pk: deleted, merged.pk: merged}
        cls.retired = [deleted.pk, merged.pk, into_hidden.pk]
        deleted.retire(cls.admin)
        merged.merge_into(cls.rm734, cls.admin)
        into_hidden.merge_into(cls.hidden_mixture, cls.admin)

    @staticmethod
    def sequence(material, reference, text, approved=True, **fields):
        return PhaseSequence.objects.create(
            material=material, reference=reference, sequence=text, approved=approved, **fields
        )

    @staticmethod
    def series(material, reference, approved=True, code="birefringence", **fields):
        """A series of one point, of Δn unless code says otherwise."""
        series = Series.objects.create(
            material=material, reference=reference, property=Property.objects.get(code=code),
            variable=Variable.objects.get(code="temperature"), kind=Series.Kind.MEASURED, approved=approved, **fields,
        )
        series.points.create(value=0.2, temperature_c=100)
        return series


class LeakTests(HiddenData):
    def searches(self):
        """Each Materials page search aimed at hidden data: the marker, and each hidden
        material's LCR ID and InChIKey, retired or not; a cyano group, which only hidden
        compounds have; and SmA, a phase forming between 75 and 85 °C, and Ps, which only
        hidden data has, some of it RM734's, which is public."""
        searches = [{"q": MARKER}, {"sub": "C#N"}, {"phase": "SmA"}, {"tmin": "75", "tmax": "85"}, {"prop": "ps"}]
        hidden = {material.pk: material for material in self.hidden} | self.hidden_retired
        for number, material in hidden.items():
            searches += [{"q": text} for text in (accession(number), material.inchikey) if text]
        return searches

    def addresses(self):
        """Every public address the test requests: the home page; the Materials page and each
        download, blank and with each search; each material's page and structure image,
        hidden or not, and each retired ID's; the References page, blank, with each search and
        with the unpublished kind, and each reference's page; and the Properties page, and each
        property's page, blank and with every series ticked, and its download. Every row is
        requested, so a hidden piece a later block adds is requested too."""
        numbers = [*Material.objects.values_list("pk", flat=True), *RetiredID.objects.values_list("number", flat=True)]
        searched = [
            f"{reverse(name)}?{urlencode(search)}" if search else reverse(name)
            for name in MATERIALS_PAGES for search in [{}, *self.searches()]
        ]
        pages = [
            reverse(name, args=[number]) for number in sorted(numbers) for name in ("repo:material", "repo:structure")
        ]
        references = reverse("repo:references")
        reference_searches = [
            f"{references}?{urlencode(search)}" for search in [*REFERENCE_SEARCHES, {"kind": "unpublished"}]
        ]
        reference_pages = [
            reverse("repo:reference", args=[pk]) for pk in Reference.objects.values_list("pk", flat=True)
        ]
        every_series = urlencode([("series", pk) for pk in Series.objects.values_list("pk", flat=True)])
        property_pages = []
        for code in Property.objects.values_list("code", flat=True):
            page = reverse("repo:property", args=[code])
            property_pages += [page, f"{page}?{every_series}", reverse("repo:property_values_csv", args=[code])]
        return [
            reverse("repo:home"), *searched, *pages,
            references, *reference_searches, *reference_pages,
            reverse("repo:properties"), *property_pages,
        ]

    def secrets(self):
        """What must never appear: the marker, each hidden material's LCR ID, SMILES,
        InChIKey and address (a mixture has no SMILES or InChIKey), retired or not, and each
        hidden reference's address."""
        found = [MARKER]
        hidden = {material.pk: material for material in self.hidden} | self.hidden_retired
        for number, material in hidden.items():
            found += [accession(number), reverse("repo:material", args=[number])]
            found += [text for text in (material.smiles, material.canonical_smiles, material.inchikey) if text]
        found += [reverse("repo:reference", args=[reference.pk]) for reference in self.hidden_references]
        return found

    def test_no_public_address_shows_hidden_data_to_a_visitor_or_an_admin(self):
        for user in (None, self.admin):
            if user:
                self.client.force_login(user)
            for address in self.addresses():
                response = self.client.get(address)
                # A redirect's address too: one to a hidden material would name it.
                text = response.content.decode() + response.get("Location", "")
                # The Materials page shows what was typed in its search, in the box and the
                # heading, which tells a visitor nothing new: the test below checks what it finds.
                typed = {value for _, value in parse_qsl(urlsplit(address).query)}
                for secret in self.secrets():
                    if secret in typed:
                        continue
                    with self.subTest(user=str(user), address=address, secret=secret):
                        self.assertNotIn(secret, text)

    def test_a_search_aimed_at_hidden_data_finds_nothing(self):
        for search in self.searches():
            with self.subTest(search=search):
                page = self.client.get(reverse("repo:materials"), search)
                self.assertEqual(page.context["page"].paginator.count, 0)
                for name in MATERIALS_PAGES[1:]:
                    self.assertEqual(csv_rows(self.client.get(reverse(name), search)), [], name)

    def test_a_hidden_materials_page_and_structure_image_are_not_found(self):
        for material in self.hidden:
            for name in ("repo:material", "repo:structure"):
                with self.subTest(material=material.name, address=name):
                    self.assertEqual(self.client.get(reverse(name, args=[material.pk])).status_code, 404)

    def test_a_retired_hidden_material_and_a_merge_into_one_are_not_found(self):
        for number in self.retired:
            with self.subTest(number=number):
                self.assertEqual(self.client.get(reverse("repo:material", args=[number])).status_code, 404)

    def test_a_merged_hidden_materials_names_are_not_added(self):
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")

    def test_a_reference_search_aimed_at_hidden_references_finds_nothing(self):
        for search in REFERENCE_SEARCHES:
            with self.subTest(search=search):
                page = self.client.get(reverse("repo:references"), search)
                self.assertEqual(page.context["page"].paginator.count, 0)

    def test_a_hidden_references_page_is_not_found(self):
        for reference in self.hidden_references:
            with self.subTest(reference=reference.identifier):
                self.assertEqual(self.client.get(reverse("repo:reference", args=[reference.pk])).status_code, 404)

    def test_every_address_is_in_the_leak_test_or_for_curators_only(self):
        # The name of each address the leak test requests, read back from the address itself.
        requested = {resolve(urlsplit(address).path).url_name for address in self.addresses()}
        for pattern in urlpatterns:
            with self.subTest(address=str(pattern.pattern)):
                self.assertTrue(pattern.name in requested or pattern.name in CURATORS_ONLY)

    def test_an_address_for_curators_only_sends_a_visitor_to_log_in(self):
        login = reverse("login")
        for pattern in urlpatterns:
            if pattern.name not in CURATORS_ONLY:
                continue
            with self.subTest(address=str(pattern.pattern)):
                args = [self.paper.pk] if "<int:pk>" in str(pattern.pattern) else []
                response = self.client.get(reverse(f"repo:{pattern.name}", args=args))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(f"{login}?"))


class MaterialsPageTests(HiddenData):
    """What no marker would show: hidden data counted or drawn for RM734, which is public."""

    def test_the_materials_file_counts_only_public_phases_references_and_components(self):
        rows = {row["material_id"]: row for row in csv_rows(self.client.get(reverse("repo:materials_csv")))}
        rm734, f7 = rows[self.rm734.accession], rows[self.public_mixture.accession]

        # Not the waiting sequence's SmA, and not the unpublished work that gives RM734 a value.
        self.assertEqual((rm734["lc_phases"], rm734["n_references"]), ("N; NF", "1"))
        self.assertEqual(f7["components"], f"{self.rm734.accession} RM734, 70 mol%")

    def test_a_row_draws_a_public_sequence_when_a_hidden_one_would_come_first(self):
        # A heating run comes last (views.ribbon_sequence()), so RM734's waiting summary,
        # whose method carries the marker, would be drawn if it counted.
        PhaseSequence.objects.filter(pk=self.public_sequence.pk).update(direction=PhaseSequence.Direction.HEATING)

        response = self.client.get(reverse("repo:materials"))

        self.assertContains(response, "Cr 139.8 NF 132.7 N 187.9 Iso (°C), Heating, Mandle 2017")
        self.assertNotContains(response, MARKER)


class PublicDataTests(HiddenData):
    """A public material's page still shows its public data, and only that."""

    def page(self, material):
        response = self.client.get(reverse("repo:material", args=[material.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def test_a_compounds_page_shows_its_public_sequence_values_phases_and_mixtures(self):
        page = self.page(self.rm734).context

        self.assertEqual([sequence for sequence, ribbon in page["sequence_rows"]], [self.public_sequence])
        series = [one for group, members in page["value_sections"] for prop, rows, charts in members for one in rows]
        self.assertEqual(series, [self.public_series])
        self.assertEqual([phase.code for phase in page["lc_phases"]], ["N", "NF"])  # not the waiting SmA
        self.assertEqual([row.mixture for row in page["in_mixtures"]], [self.public_mixture])

    def test_a_mixtures_page_lists_its_public_components_and_says_how_many_are_not(self):
        response = self.page(self.public_mixture)

        self.assertEqual([row.component for row in response.context["components"]], [self.rm734])
        self.assertContains(response, "<li>1 more component in FFDB isn't public yet.</li>", html=True)

    def test_a_public_compounds_structure_image_is_drawn(self):
        response = self.client.get(reverse("repo:structure", args=[self.rm734.pk]))

        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/svg+xml"))


class ReferenceAndPropertyTests(HiddenData):
    """What no marker would show: hidden data counted, listed or drawn for the paper, which
    is public, and for Δn, which has public values."""

    def property_page(self, code, *series):
        return self.client.get(reverse("repo:property", args=[code]), {"series": [one.pk for one in series]})

    def test_the_references_page_lists_only_the_paper_and_counts_its_public_materials(self):
        page = self.client.get(reverse("repo:references")).context["page"]

        # RM734 and F7, not the waiting compound.
        self.assertEqual([(reference, reference.n_materials) for reference in page], [(self.paper, 2)])

    def test_the_papers_page_lists_only_its_public_sequences_and_values(self):
        response = self.client.get(reverse("repo:reference", args=[self.paper.pk]))

        self.assertEqual(list(response.context["sequences"]), [self.public_sequence, self.public_mixture_sequence])
        sections = response.context["value_sections"]
        series = [one for group, members in sections for prop, rows, charts in members for one in rows]
        self.assertEqual(series, [self.public_series])
        self.assertContains(response, "2 materials, 2 sequences.")

    def test_the_properties_page_counts_only_public_materials_and_references(self):
        entries = self.client.get(reverse("repo:properties")).context["entries"]
        counts = {prop.code: (prop.n_materials, prop.n_references) for group, members in entries for prop in members}

        # Not the hidden mixture, or the unpublished work, which give Δn values too.
        self.assertEqual(counts["birefringence"], (1, 1))
        self.assertEqual(counts["ps"], (0, 0))

    def test_a_property_page_lists_and_draws_only_public_series(self):
        hidden = [self.unpublished_series, self.hidden_mixture_series]
        birefringence = self.property_page("birefringence", self.public_series, *hidden)
        ps = self.property_page("ps", self.waiting_series)

        self.assertEqual(list(birefringence.context["series_list"]), [self.public_series])
        self.assertEqual(birefringence.context["chosen"], [self.public_series.pk])
        self.assertEqual((list(ps.context["series_list"]), ps.context["chosen"]), ([], []))
        self.assertContains(ps, "No Ps values yet.")

    def test_a_property_download_holds_only_public_values(self):
        def series_ids(code):
            response = self.client.get(reverse("repo:property_values_csv", args=[code]))
            return [row["series_id"] for row in csv_rows(response)]

        self.assertEqual(series_ids("birefringence"), [str(self.public_series.pk)])
        self.assertEqual(series_ids("ps"), [])
