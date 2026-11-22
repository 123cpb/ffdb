"""The leak test: hidden data, each piece carrying a marker, and every public address
requested, as a visitor and as an admin, failing if the marker or anything that identifies a
hidden material appears. Later blocks add their addresses to LeakTests.addresses(), and their
hidden pieces to HiddenData. A curator's Add data checks are swept the same way, with what they
would type of the hidden data (CuratorChecksTests)."""

import csv
import io
from urllib.parse import parse_qsl, urlencode, urlsplit

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils.html import escape

from repo.forms import CompoundCards
from repo.models import (
    Material, MixtureComponent, PhaseSequence, Property, Reference, RetiredID, Series, Variable, WaitingSave, accession,
)
from repo.urls import urlpatterns
from repo.tests.test_add_data import WAITING
from repo.tests.test_compound_card import EMPTY_RUN, FIVE_CB_HEATING
from repo.tests.test_import import ARTICLE, FIVE_CB
from repo.tests.test_merge import DUPLICATE_SUMMARY, EIGHT_CB, WRONG_RM734
from repo.tests.test_sequences import RM734, RM734_SUMMARY
from repo.tests.test_switches import UNPUBLISHED_WORK

# In every hidden piece: a name, a method or a reference's identifier, each shown on a page if it leaks.
MARKER = "LEAKMARKER"
EIGHT_OCB = "CCCCCCCCOc1ccc(-c2ccc(C#N)cc2)cc1"
# 2-Octanol with its stereochemistry given: S is hidden, and R has the same connectivity.
S_OCTANOL, R_OCTANOL = "C[C@H](O)CCCCCC", "C[C@@H](O)CCCCCC"
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
    data is under unpublished work; S-2-octanol, whose only data waits, for the stereochemistry
    warning; a hidden mixture whose component, RM734, is public; and a
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

        # No SmA, and no phase forming between 75 and 85 °C, which the searches below look for.
        cls.stereo = Material.objects.create(name=f"{MARKER} stereo compound", smiles=S_OCTANOL)
        cls.sequence(cls.stereo, cls.paper, "K 10 N 40 I", approved=False)

        cls.hidden_mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name=f"{MARKER} hidden mixture")
        MixtureComponent.objects.create(mixture=cls.hidden_mixture, component=cls.rm734, fraction=50, unit="mol%")
        cls.hidden_mixture_series = cls.series(cls.hidden_mixture, cls.paper, approved=False)

        cls.public_mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name="F7")
        MixtureComponent.objects.create(mixture=cls.public_mixture, component=cls.rm734, fraction=70, unit="mol%")
        MixtureComponent.objects.create(mixture=cls.public_mixture, component=cls.waiting, fraction=30, unit="mol%")
        cls.public_mixture_sequence = cls.sequence(cls.public_mixture, cls.paper, "K 30 NF 90 N 120 I")

        cls.hidden = [cls.waiting, cls.unpublished_compound, cls.stereo, cls.hidden_mixture]
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


class CuratorChecksTests(HiddenData):
    """A curator's Add data checks look only among public data (models.seen_by()), so what they
    type of hidden data finds nothing hidden: each hidden material's SMILES, LCR ID, name and
    alias, retired or not, the held label, and the mixtures' names. A hidden structure reads as
    new, and saving it adds the card's data to it. A reference's Add data page lists only its
    public data. An admin's checks still find hidden materials, because their saves go straight in."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.curator = User.objects.create_user("curator")
        cls.curator.groups.add(Group.objects.get(name="Curators"))

    def setUp(self):
        self.client.force_login(self.curator)

    def hidden_materials(self):
        """Each hidden material, retired or not, by its number."""
        return {material.pk: material for material in self.hidden} | self.hidden_retired

    def typed_ids(self):
        return [accession(number) for number in self.hidden_materials()]

    def typed_smiles(self):
        """Each hidden compound's SMILES, and R-2-octanol's, whose only stereo variant is hidden."""
        return [material.smiles for material in self.hidden_materials().values() if material.smiles] + [R_OCTANOL]

    def typed_names(self):
        """Each hidden material's names, and the label held for RM734 until its save is approved."""
        names = [name for material in self.hidden_materials().values() for name in material.names()]
        return names + [label for saved in WaitingSave.objects.all() for label in saved.labels.values()]

    def secrets(self):
        """What a curator must never see: the marker, and each hidden material's LCR ID and address."""
        found = [MARKER]
        for number in self.hidden_materials():
            found += [accession(number), reverse("repo:material", args=[number])]
        return found

    def assert_no_secrets(self, response, typed):
        """None of secrets() in response, once what the curator typed is taken out: it is shown back
        to them, and tells them nothing new. A hidden material found by what was typed would still
        show, by its address, and by whichever of its name and LCR ID wasn't typed."""
        self.assertIn(response.status_code, (200, 422))  # 422: a table that makes no cards
        text = response.content.decode()
        for value in sorted(typed, key=len, reverse=True):  # longest first, so no part of one is left
            text = text.replace(escape(value), "").replace(value, "")
        for secret in self.secrets():
            with self.subTest(address=response.request["PATH_INFO"], typed=typed, secret=secret):
                self.assertNotIn(secret, text)

    def card(self, index=0, run=EMPTY_RUN, **boxes):
        """A card's boxes as the page sends them: boxes are its material's, by name, and its one
        block is left empty."""
        prefix = f"card-{index}"
        data = {f"{prefix}-block-TOTAL_FORMS": "1", f"{prefix}-block-INITIAL_FORMS": "0"}
        data.update({f"{prefix}-run-{name}": value for name, value in run.items()})
        data.update({f"{prefix}-{name}": value for name, value in boxes.items()})
        return data

    def check_material(self, **boxes):
        """The material check's answer for one card with these boxes."""
        address = reverse("repo:check_card_material", args=[self.paper.pk])
        return self.client.post(f"{address}?card=0", self.card(**boxes))

    def check_cards(self, *cards):
        """The paper's Check for a page of cards, each a dict of card()'s boxes."""
        data = {CompoundCards.TOTAL: str(len(cards)), "check": ""}
        for index, boxes in enumerate(cards):
            data.update(self.card(index, **boxes))
        return self.client.post(reverse("repo:add_compound", args=[self.paper.pk]), data)

    def check_table(self, table, direction=""):
        address = reverse("repo:check_table", args=[self.paper.pk])
        return self.client.post(address, {"table": table, "direction": direction})

    def table(self, identifiers):
        """A table of one line per identifier, a SMILES or an LCR ID, each labelled by its line."""
        return "\n".join(f"line{number}\t{identifier}" for number, identifier in enumerate(identifiers, start=1))

    def mixture(self, name, components):
        """A card's boxes for a new mixture called name, with these component lines."""
        lines = "\n".join(f"{component}, 10, mol%" for component in components)
        return {"name": name, "composition_status": Material.CompositionStatus.LISTED, "components": lines}

    def test_no_add_data_check_shows_a_curator_hidden_data(self):
        ids, smiles, names = self.typed_ids(), self.typed_smiles(), self.typed_names()
        mixtures = [self.hidden_mixture.name, self.public_mixture.name]  # F7, public, has a hidden component
        make_cards = f"{reverse('repo:make_cards', args=[self.paper.pk])}?start=0"
        answers = [
            (self.check_table(self.table(ids + smiles)), ids + smiles),
            (self.client.post(make_cards, {"table": self.table(smiles), "direction": ""}), smiles),
        ]
        answers += [(self.check_material(material=text), [text]) for text in ids + names]
        answers += [(self.check_material(smiles=text, name="10a"), [text]) for text in smiles]
        answers += [(self.check_material(**self.mixture(name, ids + names)), [name, *ids, *names]) for name in mixtures]
        cards = [
            *[{"material": text} for text in ids + names],
            *[{"smiles": text, "name": "10a"} for text in smiles],
            *[self.mixture(name, ids + names) for name in mixtures],
        ]
        answers.append((self.check_cards(*cards), ids + names + smiles + mixtures))
        # A reference's own identifier is on its page: the curator typed it on Add data to get there.
        for reference in [self.paper, *self.hidden_references]:
            page = self.client.get(reverse("repo:add_from_reference", args=[reference.pk]))
            answers.append((page, [reference.identifier]))
        for response, typed in answers:
            self.assert_no_secrets(response, typed)

    def test_a_hidden_materials_lcr_id_name_or_alias_finds_nothing_in_any_box(self):
        for text in self.typed_ids() + self.typed_names():
            with self.subTest(text=text):
                form = self.check_material(material=text).context["form"]
                self.assertIsNone(form.found)
                not_found = ("No material has the LCR ID", "Nothing in FFDB is called")
                self.assertTrue(form.errors["material"][0].startswith(not_found))
        answer = self.check_material(**self.mixture("F9", self.typed_ids() + self.typed_names()))
        lines = answer.context["form"].mixture.lines
        self.assertTrue(all(line.problem and line.material is None for line in lines))
        table_lines = self.check_table(self.table(self.typed_ids())).context["lines"]
        self.assertTrue(all(line.problem.startswith("No material has the LCR ID") for line in table_lines))

    def test_a_hidden_structure_reads_as_a_new_compound_with_no_id_or_name(self):
        answer = self.check_material(smiles=FIVE_CB, name="5CB")

        self.assertTrue(answer.context["form"].shown_as_new)
        self.assertContains(answer, "New compound, named 5CB")
        self.assertNotContains(answer, "In FFDB")
        # As for a new compound, the label is asked for before anything else.
        answer = self.check_material(smiles=FIVE_CB)
        label = "Give the label the reference prints for this compound: it becomes its name."
        self.assertEqual(answer.context["form"].errors["name"], [label])
        lines = self.check_table(self.table(self.typed_smiles())).context["lines"]
        self.assertEqual({line.status for line in lines}, {"new"})

    def test_saving_a_hidden_structure_adds_the_cards_data_to_it_and_holds_the_label(self):
        before = Material.objects.count()
        data = {CompoundCards.TOTAL: "1", "save": "", **self.card(run=FIVE_CB_HEATING, smiles=FIVE_CB, name="5CB")}

        response = self.client.post(reverse("repo:add_compound", args=[self.paper.pk]), data, follow=True)

        self.assertEqual(Material.objects.count(), before)
        saved = WaitingSave.objects.get(saved_by=self.curator)
        run = self.waiting.phase_sequences.get(waiting_save=saved)
        self.assertEqual((run.standard_sequence, run.approved), ("Cr 24 N 35.3 Iso", False))
        self.assertEqual(saved.labels, {str(self.waiting.pk): "5CB"})
        # As a new compound's: no LCR ID, and nothing about aliases.
        messages = [str(message) for message in response.context["messages"]]
        self.assertEqual(messages, ["Added 5CB, with a phase sequence.", WAITING])
        self.assert_no_secrets(response, [FIVE_CB, "5CB"])

    def test_a_hidden_structures_label_is_held_even_if_it_wont_be_kept(self):
        # A bare compound number isn't kept as an alias, but the admin checking the save sees what
        # the curator called it, as their card called it a new compound named 10a.
        data = {CompoundCards.TOTAL: "1", "save": "", **self.card(run=FIVE_CB_HEATING, smiles=FIVE_CB, name="10a")}

        response = self.client.post(reverse("repo:add_compound", args=[self.paper.pk]), data, follow=True)

        self.assertEqual(WaitingSave.objects.get(saved_by=self.curator).labels, {str(self.waiting.pk): "10a"})
        messages = [str(message) for message in response.context["messages"]]
        self.assertEqual(messages, ["Added 10a, with a phase sequence.", WAITING])

    def test_the_same_name_question_offers_only_public_mixtures_and_names_only_public_components(self):
        hidden = self.check_material(**self.mixture(self.hidden_mixture.name, [])).context["form"].mixture
        self.assertEqual(hidden.same_name, [])

        answer = self.check_material(**self.mixture("F7", []))

        self.assertEqual(answer.context["form"].mixture.same_name, [self.public_mixture])
        self.assertContains(answer, f'<a href="/material/{self.rm734.pk}/">{self.rm734.accession}</a> RM734, 70 mol%')
        self.assertContains(answer, "<li>1 more component in FFDB isn't public yet.</li>", html=True)

    def test_a_stereo_warning_names_only_public_materials(self):
        self.assertEqual(self.check_material(smiles=R_OCTANOL, name="R-2-octanol").context["form"].stereo_warning, "")
        [line] = self.check_table(self.table([R_OCTANOL])).context["lines"]
        self.assertEqual(line.stereo_warning, "")

    def test_a_sequence_is_already_there_only_if_it_is_public(self):
        # RM734's waiting summary from the paper.
        answer = self.check_table(f"RM734\t{self.rm734.accession}\tK 80 SmA 120 I", direction="summary")
        [line] = answer.context["lines"]

        self.assertFalse(line.sequence_already_there)

    def test_a_references_add_data_page_lists_only_its_public_data(self):
        page = self.client.get(reverse("repo:add_from_reference", args=[self.paper.pk])).context
        self.assertEqual(list(page["materials"]), [self.rm734, self.public_mixture])
        self.assertEqual(list(page["sequences"]), [self.public_sequence, self.public_mixture_sequence])
        series = [one for group, members in page["value_sections"] for prop, rows, charts in members for one in rows]
        self.assertEqual(series, [self.public_series])

        # RM734 is public, but its only data from the waiting paper waits.
        response = self.client.get(reverse("repo:add_from_reference", args=[self.waiting_paper.pk]))
        self.assertEqual((list(response.context["materials"]), list(response.context["sequences"])), ([], []))
        self.assertContains(response, "No materials from this reference yet.")

    def test_an_admins_checks_still_find_hidden_materials(self):
        self.client.force_login(self.admin)
        found = f'In FFDB as <a href="/material/{self.waiting.pk}/">{self.waiting.accession}</a>'

        self.assertContains(self.check_material(smiles=FIVE_CB, name="5CB"), found)
        self.assertContains(self.check_material(material=self.waiting.accession), found)
        table = f"5CB\t{FIVE_CB}\nRM734\t{self.rm734.accession}\tK 80 SmA 120 I"
        lines = self.check_table(table, direction="summary").context["lines"]
        found = [(line.status, line.match) for line in lines]
        self.assertEqual(found, [("in_ffdb", self.waiting), ("in_ffdb", self.rm734)])
        self.assertTrue(lines[1].sequence_already_there)
        warning = self.check_material(smiles=R_OCTANOL, name="R-2-octanol").context["form"].stereo_warning
        self.assertTrue(warning.startswith(f"This structure has the same connectivity as {self.stereo.accession}"))
        page = self.client.get(reverse("repo:add_from_reference", args=[self.waiting_paper.pk]))
        self.assertEqual(list(page.context["materials"]), [self.rm734])
