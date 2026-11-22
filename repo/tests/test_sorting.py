from django.test import TestCase
from django.urls import reverse

from repo.models import Material, Reference

MATERIALS = reverse("repo:materials")
REFERENCES = reverse("repo:references")
HIDDEN_SORT = '<input type="hidden" name="sort" value="{}">'


class MaterialOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # 26 straight-chain alkanes, methane (C1) to hexacosane (C26), added in that order:
        # one more than a page. Listed, so they are public with no data.
        cls.materials = [Material.objects.create(name=f"C{n}", smiles="C" * n, listed=True) for n in range(1, 27)]

    def listed(self, **params):
        return list(self.client.get(MATERIALS, params).context["page"].object_list)

    def test_newest_added_first_by_default(self):
        self.assertEqual(self.listed(), self.materials[:0:-1])  # C26 down to C2

    def test_by_lcr_id_oldest_first(self):
        self.assertEqual(self.listed(sort="id"), self.materials[:25])  # C1 to C25

    def test_an_unknown_order_is_ignored(self):
        self.assertEqual(self.listed(sort="name"), self.listed())

    def test_page_2_keeps_the_order_and_the_search(self):
        response = self.client.get(MATERIALS, {"q": "C", "sort": "id"})

        self.assertContains(response, 'href="?q=C&amp;sort=id&amp;page=2"')
        self.assertEqual(self.listed(q="C", sort="id", page=2), self.materials[25:])  # C26

    def test_a_search_keeps_the_order(self):
        response = self.client.get(MATERIALS, {"q": "C1", "sort": "id"})

        self.assertContains(response, HIDDEN_SORT.format("id"), html=True)
        # C1, then C10 to C19, in LCR ID order.
        self.assertEqual(list(response.context["page"].object_list), [self.materials[0], *self.materials[9:19]])

    def test_the_default_order_needs_no_hidden_box(self):
        self.assertNotContains(self.client.get(MATERIALS), 'name="sort"')

    def test_the_order_links_keep_the_search(self):
        by_newest = self.client.get(MATERIALS, {"q": "C"})
        by_id = self.client.get(MATERIALS, {"q": "C", "sort": "id"})

        self.assertContains(by_newest, '<a href="?q=C&amp;sort=id">LCR ID</a>', html=True)
        self.assertContains(by_id, f'<a href="{MATERIALS}?q=C">recently added</a>', html=True)

    def test_the_downloads_keep_the_search_but_not_the_order(self):
        response = self.client.get(MATERIALS, {"q": "C", "sort": "id"})

        for name in ("materials_csv", "sequences_csv", "values_csv"):
            self.assertContains(response, f'href="{reverse(f"repo:{name}")}?q=C"')


class ReferenceOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Added in this order, which is neither year order nor its reverse.
        article = Reference.Kind.ARTICLE
        cls.r2017 = Reference.objects.create(kind=article, identifier="10.1000/a", first_author="Mandle", year=2017)
        cls.r2020 = Reference.objects.create(kind=article, identifier="10.1000/b", first_author="Chen", year=2020)
        cls.thesis = Reference.objects.create(kind=Reference.Kind.THESIS, identifier="Parton-Barr, 2027, a thesis")
        cls.r2015 = Reference.objects.create(kind=article, identifier="10.1000/c", first_author="Nishikawa", year=2015)

    def listed(self, **params):
        return list(self.client.get(REFERENCES, params).context["page"].object_list)

    def test_newest_added_first_by_default(self):
        self.assertEqual(self.listed(), [self.r2015, self.thesis, self.r2020, self.r2017])

    def test_by_year_newest_first_and_no_year_last(self):
        self.assertEqual(self.listed(sort="year"), [self.r2020, self.r2017, self.r2015, self.thesis])

    def test_a_search_keeps_the_order(self):
        response = self.client.get(REFERENCES, {"q": "10.1000", "sort": "year"})

        self.assertContains(response, HIDDEN_SORT.format("year"), html=True)
        self.assertEqual(list(response.context["page"].object_list), [self.r2020, self.r2017, self.r2015])

    def test_the_order_links_keep_the_search(self):
        response = self.client.get(REFERENCES, {"kind": "article"})

        self.assertContains(response, '<a href="?kind=article&amp;sort=year">year published</a>', html=True)


class ReferencePagingOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # 26 patents with no year, added EP0000000A1 first. By year, all tie, so they go by
        # identifier: the same order as added, the other way round from newest first.
        cls.patents = [
            Reference.objects.create(kind=Reference.Kind.PATENT, identifier=f"EP{n:07d}A1") for n in range(26)
        ]

    def test_page_2_keeps_the_order_and_the_search(self):
        search = {"q": "EP", "kind": "patent"}
        first = self.client.get(REFERENCES, {**search, "sort": "year"})
        second = self.client.get(REFERENCES, {**search, "sort": "year", "page": 2})
        second_newest = self.client.get(REFERENCES, {**search, "page": 2})

        self.assertContains(first, 'href="?q=EP&amp;kind=patent&amp;sort=year&amp;page=2"')
        self.assertEqual(list(second.context["page"].object_list), self.patents[25:])  # the last added
        self.assertEqual(list(second_newest.context["page"].object_list), self.patents[:1])  # the first added
