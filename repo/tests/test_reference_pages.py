import csv
import io

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Reference, Series
from repo.tests.test_values import add

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"


class ReferenceTestCase(TestCase):
    """One reference of each kind. The article gives sequences for two materials, two of
    them for RM734; the patent gives one; the preprint and thesis give none."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Reference.objects.create(
            kind=Reference.Kind.ARTICLE,
            identifier="10.1002/chem.201702742",
            first_author="Mandle",
            year=2017,
            venue="Chem. Eur. J.",
            title="A nematic to nematic transformation exhibited by a rod-like liquid crystal",
            published=True,
        )
        cls.patent = Reference.objects.create(
            kind=Reference.Kind.PATENT, identifier="EP3260450A1", year=2016, published=True
        )
        cls.preprint = Reference.objects.create(
            kind=Reference.Kind.PREPRINT, identifier="10.48550/arxiv.2401.00001", year=2024
        )
        cls.thesis = Reference.objects.create(
            kind=Reference.Kind.THESIS, identifier="Parton-Barr, 2027, Fluid ferroelectrics"
        )
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        for material, sequence in [
            (cls.rm734, "K 139.8 NF 132.7 N 187.9 I"),
            (cls.rm734, "K 140 NX 133 N 188 I"),
            (cls.five_cb, "K 22.5 N 35 I"),
        ]:
            PhaseSequence.objects.create(material=material, reference=cls.article, sequence=sequence, approved=True)
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.patent, sequence="K 140 N 188 I", approved=True)

    def search(self, **params):
        return self.client.get(reverse("repo:references"), params)

    def found(self, **params):
        """The references a search finds, read from the page's context."""
        return list(self.search(**params).context["page"].object_list)


class ReferenceSearchTests(ReferenceTestCase):
    def test_a_doi_finds_its_reference_however_it_is_typed(self):
        for query in [
            "10.1002/chem.201702742",
            "10.1002/CHEM.201702742",
            "https://doi.org/10.1002/chem.201702742",
            "doi:10.1002/chem.201702742",
            "chem.2017",
        ]:
            self.assertEqual(self.found(q=query), [self.article], query)

    def test_a_patent_number_finds_its_reference(self):
        for query in ["EP3260450A1", "ep3260450"]:
            self.assertEqual(self.found(q=query), [self.patent], query)

    def test_the_label_finds_its_reference(self):
        for query in ["Mandle 2017", "mandle chem. eur. j.", self.article.label]:
            self.assertEqual(self.found(q=query), [self.article], query)

    def test_a_word_from_the_title(self):
        self.assertEqual(self.found(q="rod-like"), [self.article])

    def test_every_word_must_match(self):
        self.assertEqual(self.found(q="Mandle 2016"), [])

    def test_a_huge_number_finds_nothing_without_an_error(self):
        self.assertEqual(self.found(q="99999999999999999999"), [])

    def test_the_kind_choice_shows_only_patents(self):
        self.assertEqual(self.found(kind="patent"), [self.patent])
        self.assertEqual(self.found(kind="patent", q="10.1002"), [])

    def test_an_unknown_kind_is_ignored(self):
        self.assertEqual(len(self.found(kind="paper")), 4)

    def test_heading_says_what_was_searched(self):
        self.assertContains(self.search(kind="patent", q="EP"), "1 patent reference matches “EP”")

    def test_no_match_says_so(self):
        self.assertContains(self.search(q="nothing like this"), "No references match")


class ReferenceListTests(ReferenceTestCase):
    def test_by_year_newest_year_first_and_no_year_last(self):
        self.assertEqual(self.found(sort="year"), [self.preprint, self.article, self.patent, self.thesis])

    def test_row_shows_label_linking_to_its_page_kind_and_material_count(self):
        response = self.search()
        rows = {reference: reference.n_materials for reference in response.context["page"]}

        # RM734's two sequences from the article count as one material.
        self.assertEqual(rows, {self.article: 2, self.patent: 1, self.preprint: 0, self.thesis: 0})
        page = reverse("repo:reference", args=[self.article.pk])
        self.assertContains(response, f'<a href="{page}">Mandle 2017, Chem. Eur. J.</a>', html=True)
        self.assertContains(response, "<td>Journal article</td>", html=True)

    def test_nav_links_to_references_and_marks_the_section_you_are_in(self):
        link = reverse("repo:references")
        on_references = self.search()
        on_materials = self.client.get(reverse("repo:materials"))

        self.assertContains(on_references, f'<a href="{link}" aria-current="page">References</a>', html=True)
        self.assertContains(on_materials, f'<a href="{link}">References</a>', html=True)


class ReferencePagingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for n in range(26):
            Reference.objects.create(kind=Reference.Kind.PATENT, identifier=f"EP{n:07d}A1")

    def test_25_to_a_page_and_the_page_links_keep_the_search(self):
        url = reverse("repo:references")
        first = self.client.get(url, {"q": "EP", "kind": "patent"})
        second = self.client.get(url, {"q": "EP", "kind": "patent", "page": 2})

        self.assertEqual(len(first.context["page"].object_list), 25)
        self.assertContains(first, 'href="?q=EP&amp;kind=patent&amp;page=2"')
        self.assertEqual(len(second.context["page"].object_list), 1)


class ReferencePageTests(ReferenceTestCase):
    def page(self, reference):
        return self.client.get(reverse("repo:reference", args=[reference.pk]))

    def test_shows_the_details_and_links_to_doi_org(self):
        response = self.page(self.article)

        self.assertContains(response, "<h1 class=\"page-title reference-label\">Mandle 2017, Chem. Eur. J.</h1>")
        self.assertContains(response, self.article.title)
        self.assertContains(response, f'<a href="{self.article.link}">10.1002/chem.201702742</a>', html=True)

    def test_lists_its_materials_and_their_sequences(self):
        response = self.page(self.article)

        self.assertContains(response, "2 materials, 3 sequences.")
        for material in (self.rm734, self.five_cb):
            page = reverse("repo:material", args=[material.pk])
            self.assertContains(response, f'<a href="{page}">{material.accession}</a>', html=True)
        self.assertContains(response, "Cr 139.8 N<sub>F</sub> 132.7 N 187.9 Iso")
        self.assertContains(response, "Cr 22.5 N 35 Iso")
        self.assertContains(response, "Reported as K 140 NX 133 N 188 I")
        self.assertNotContains(response, "Cr 140 N 188 Iso")  # the patent's sequence

    def test_a_reference_without_sequences_says_so(self):
        self.assertContains(self.page(self.thesis), "No phase sequences from this reference.")

    def test_unknown_number_is_not_found(self):
        response = self.client.get(reverse("repo:reference", args=[9999]))

        self.assertEqual(response.status_code, 404)

    def test_edit_in_admin_link_is_for_staff_only(self):
        edit_url = reverse("admin:repo_reference_change", args=[self.article.pk])

        self.assertNotContains(self.page(self.article), edit_url)

        self.client.force_login(User.objects.create_user("curator", is_staff=True))
        self.assertContains(self.page(self.article), edit_url)


class UnpublishedResultTests(ReferenceTestCase):
    """An unpublished group result, with a link to its deposited data, gives RM734 a computed
    dipole moment. Its label says it is unpublished wherever it appears."""

    LABEL = "Mandle group 2026, Computed dipole moments (unpublished)"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.result = Reference.objects.create(
            kind=Reference.Kind.UNPUBLISHED, identifier="Mandle group 2026, Computed dipole moments",
            dataset_url="https://doi.org/10.5518/1234",
        )
        dipole = add(
            cls.rm734, cls.result, "dipole-moment", "none", Series.Kind.COMPUTED,
            method="DFT", conditions={"conformer_protocol": "lowest", "solvent": "gas phase"},
        )
        dipole.points.create(value=11.4)

    def test_its_page_says_it_is_unpublished_and_links_to_the_deposited_data(self):
        response = self.client.get(reverse("repo:reference", args=[self.result.pk]))

        self.assertContains(response, '<p class="reference-kind">Unpublished group result</p>', html=True)
        self.assertContains(response, f'<h1 class="page-title reference-label">{self.LABEL}</h1>', html=True)
        self.assertContains(response, "not yet published or peer reviewed")
        self.assertContains(response, '<a href="https://doi.org/10.5518/1234">https://doi.org/10.5518/1234</a>', html=True)

    def test_a_reference_without_deposited_data_has_no_line_for_it(self):
        self.assertNotContains(self.client.get(reverse("repo:reference", args=[self.article.pk])), "Deposited data")

    def test_the_material_page_leaves_its_value_out(self):
        # Unpublished work can't be published, so its data is never public, however it is
        # labelled. RM734's page still shows its public sequences.
        response = self.client.get(reverse("repo:material", args=[self.rm734.pk]))

        self.assertNotContains(response, self.LABEL)
        self.assertNotContains(response, "Dipole moment")

    def test_the_values_download_says_it_is_unpublished(self):
        text = self.client.get(reverse("repo:values_csv")).content.decode("utf-8-sig")
        [row] = csv.DictReader(io.StringIO(text))

        self.assertEqual((row["reference"], row["reference_kind"], row["reference_link"]), (self.LABEL, "unpublished", ""))

    def test_a_whole_copied_label_or_the_word_unpublished_finds_it(self):
        self.assertEqual(self.found(q=self.LABEL), [self.result])
        self.assertEqual(self.found(q="Unpublished"), [self.result])

    def test_the_kind_choice_shows_only_unpublished_results(self):
        self.assertEqual(self.found(kind="unpublished"), [self.result])
