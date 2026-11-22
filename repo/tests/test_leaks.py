"""The leak test: hidden data, each piece carrying a marker, and every public address
requested, as a visitor and as an admin, failing if the marker or anything that identifies a
hidden material appears. Later blocks add their addresses to LeakTests.addresses(), and their
hidden pieces to HiddenData."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from repo.models import (
    Material, MixtureComponent, PhaseSequence, Property, Reference, RetiredID, Series, Variable, accession,
)
from repo.tests.test_import import ARTICLE, FIVE_CB
from repo.tests.test_merge import DUPLICATE_SUMMARY, EIGHT_CB, WRONG_RM734
from repo.tests.test_sequences import RM734, RM734_SUMMARY
from repo.tests.test_switches import UNPUBLISHED_WORK

# In every hidden piece: a name, a method or a reference's identifier, each shown on a page if it leaks.
MARKER = "LEAKMARKER"
EIGHT_OCB = "CCCCCCCCOc1ccc(-c2ccc(C#N)cc2)cc1"


class HiddenData(TestCase):
    """RM734, public, with hidden data too; a compound with only waiting data; one whose only
    data is under unpublished work; a hidden mixture whose component, RM734, is public; and a
    public mixture, one of whose components is hidden. Then three retired IDs: a hidden
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

        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.public_sequence = cls.sequence(cls.rm734, cls.paper, RM734_SUMMARY)
        cls.public_series = cls.series(cls.rm734, cls.paper, method="current reversal")
        # SmA: a phase that only the waiting sequence has.
        cls.sequence(cls.rm734, cls.paper, "K 80 SmA 120 I", approved=False, method=f"{MARKER} waiting sequence")
        cls.series(cls.rm734, cls.paper, approved=False, method=f"{MARKER} waiting series")
        cls.series(cls.rm734, cls.unpublished, method=f"{MARKER} unpublished series")

        cls.waiting = Material.objects.create(name=f"{MARKER} waiting compound", smiles=FIVE_CB)
        cls.sequence(cls.waiting, cls.paper, "K 22.5 N 35 I", approved=False)

        cls.unpublished_compound = Material.objects.create(name=f"{MARKER} unpublished compound", smiles=EIGHT_OCB)
        cls.sequence(cls.unpublished_compound, cls.unpublished, "K 54 SmA 67 N 80 I")

        cls.hidden_mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name=f"{MARKER} hidden mixture")
        MixtureComponent.objects.create(mixture=cls.hidden_mixture, component=cls.rm734, fraction=50, unit="mol%")
        cls.series(cls.hidden_mixture, cls.paper, approved=False)

        cls.public_mixture = Material.objects.create(kind=Material.Kind.MIXTURE, name="F7")
        MixtureComponent.objects.create(mixture=cls.public_mixture, component=cls.rm734, fraction=70, unit="mol%")
        MixtureComponent.objects.create(mixture=cls.public_mixture, component=cls.waiting, fraction=30, unit="mol%")
        cls.sequence(cls.public_mixture, cls.paper, "K 30 NF 90 N 120 I")

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
    def series(material, reference, approved=True, **fields):
        """A Δn series of one point."""
        series = Series.objects.create(
            material=material, reference=reference, property=Property.objects.get(code="birefringence"),
            variable=Variable.objects.get(code="temperature"), kind=Series.Kind.MEASURED, approved=approved, **fields,
        )
        series.points.create(value=0.2, temperature_c=100)
        return series


class LeakTests(HiddenData):
    def addresses(self):
        """Every public address the test requests: each material's page and structure image,
        hidden or not, and each retired ID's, so a hidden piece a later block adds is requested too."""
        numbers = [*Material.objects.values_list("pk", flat=True), *RetiredID.objects.values_list("number", flat=True)]
        return [reverse(name, args=[number]) for number in sorted(numbers) for name in ("repo:material", "repo:structure")]

    def secrets(self):
        """What must never appear: the marker, and each hidden material's LCR ID, SMILES,
        InChIKey and address (a mixture has no SMILES or InChIKey), retired or not."""
        found = [MARKER]
        hidden = {material.pk: material for material in self.hidden} | self.hidden_retired
        for number, material in hidden.items():
            found += [accession(number), reverse("repo:material", args=[number])]
            found += [text for text in (material.smiles, material.canonical_smiles, material.inchikey) if text]
        return found

    def test_no_public_address_shows_hidden_data_to_a_visitor_or_an_admin(self):
        for user in (None, self.admin):
            if user:
                self.client.force_login(user)
            for address in self.addresses():
                response = self.client.get(address)
                # A redirect's address too: one to a hidden material would name it.
                text = response.content.decode() + response.get("Location", "")
                for secret in self.secrets():
                    with self.subTest(user=str(user), address=address, secret=secret):
                        self.assertNotIn(secret, text)

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
