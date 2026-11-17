from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from repo.models import Material, pk_from_accession

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"


class PublicIdReadingTests(SimpleTestCase):
    def test_public_ids_are_read_in_any_case_with_or_without_the_hyphen(self):
        self.assertEqual(pk_from_accession("LCR-00069"), 69)
        self.assertEqual(pk_from_accession("lcr69"), 69)

    def test_other_text_is_not_a_public_id(self):
        self.assertIsNone(pk_from_accession("RM734"))
        self.assertIsNone(pk_from_accession("NFR-00001"))  # the prototype's old prefix


class SearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", aliases="K15", smiles=FIVE_CB)

    def found(self, query):
        """The materials a search finds. Read from the page's context, not its text,
        because the search box repeats whatever was typed."""
        response = self.client.get(reverse("repo:materials"), {"q": query})
        return list(response.context["page"].object_list)

    def test_empty_search_lists_everything_in_id_order(self):
        self.assertEqual(self.found(""), [self.rm734, self.five_cb])

    def test_name_search_ignores_case_and_finds_part_of_a_name(self):
        for query in ["RM734", "rm734", "rm7"]:
            self.assertEqual(self.found(query), [self.rm734], query)

    def test_alias_search(self):
        self.assertEqual(self.found("k15"), [self.five_cb])

    def test_lcr_id_search(self):
        self.assertEqual(self.found(self.rm734.accession), [self.rm734])
        self.assertEqual(self.found(f"lcr{self.rm734.pk}"), [self.rm734])

    def test_a_huge_lcr_number_finds_nothing_without_an_error(self):
        self.assertEqual(self.found("LCR-99999999999999999999"), [])

    def test_inchikey_search_must_be_exact(self):
        key = self.rm734.inchikey
        self.assertEqual(self.found(key), [self.rm734])
        self.assertEqual(self.found(key.lower()), [self.rm734])
        self.assertEqual(self.found(key.split("-")[0]), [])  # the first block alone

    def test_no_match_says_so(self):
        response = self.client.get(reverse("repo:materials"), {"q": "nothing like this"})

        self.assertContains(response, "No materials match")

    def test_home_page_is_the_materials_page(self):
        response = self.client.get(reverse("repo:home"))

        self.assertTemplateUsed(response, "repo/materials.html")
        self.assertEqual(list(response.context["page"].object_list), [self.rm734, self.five_cb])

    def test_row_shows_thumbnail_id_and_name_linking_to_the_page(self):
        response = self.client.get(reverse("repo:materials"))

        self.assertContains(response, f'href="{reverse("repo:material", args=[self.five_cb.pk])}"')
        self.assertContains(response, f'<img src="{escape(self.five_cb.thumbnail_url)}"')
        self.assertContains(response, self.five_cb.accession)
        self.assertContains(response, '<span class="result__name">5CB</span>')


class PagingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # 26 straight-chain alkanes, methane to hexacosane: 26 different structures.
        cls.materials = [Material.objects.create(name=f"C{n}", smiles="C" * n) for n in range(1, 27)]

    def test_25_to_a_page(self):
        first = self.client.get(reverse("repo:materials"))
        second = self.client.get(reverse("repo:materials"), {"page": 2})

        self.assertEqual(list(first.context["page"].object_list), self.materials[:25])
        self.assertContains(first, "Page 1 of 2")
        self.assertEqual(list(second.context["page"].object_list), self.materials[25:])

    def test_page_links_keep_the_search(self):
        response = self.client.get(reverse("repo:materials"), {"q": "C"})

        self.assertContains(response, 'href="?q=C&amp;page=2"')
