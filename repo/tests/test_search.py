from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from repo.models import Material, PhaseSequence, Reference, pk_from_accession

RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)c(OC)c1"
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
SIX_CB = "CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1"


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


class SubstructureSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)  # nitro, no cyano
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)  # cyano
        cls.six_cb = Material.objects.create(name="6CB", smiles=SIX_CB)  # cyano

    def search(self, **params):
        return self.client.get(reverse("repo:materials"), params)

    def found(self, **params):
        return list(self.search(**params).context["page"].object_list)

    def test_cyano_query_finds_the_cyano_compounds_and_not_the_others(self):
        self.assertEqual(self.found(sub="C#N"), [self.five_cb, self.six_cb])

    def test_smarts_query(self):
        # Read as SMILES, this nitro fragment would match nothing (chem.substructure_pattern).
        self.assertEqual(self.found(sub="[N+](=O)[O-]"), [self.rm734])

    def test_substructure_combines_with_the_name_search(self):
        self.assertEqual(self.found(q="5CB", sub="C#N"), [self.five_cb])
        self.assertEqual(self.found(q="RM734", sub="C#N"), [])

    def test_heading_says_what_was_searched(self):
        response = self.search(q="CB", sub="C#N")

        self.assertContains(response, "2 materials match “CB” and contain <code>C#N</code>")

    def test_unreadable_query_gives_a_message_not_an_error_page(self):
        response = self.search(sub="(((not smiles")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RDKit could not read “(((not smiles” as SMILES or SMARTS.")
        self.assertEqual(list(response.context["page"].object_list), [])


def add_reference(identifier):
    return Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier=identifier)


class PhaseSearchTests(TestCase):
    """The phases are already in the test database, from the 0005_seed_phases migration."""

    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)  # nitro
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)  # cyano
        cls.six_cb = Material.objects.create(name="6CB", smiles=SIX_CB)  # cyano
        first, second = add_reference("10.1000/first"), add_reference("10.1000/second")
        for material, reference, sequence in [
            (cls.rm734, first, "K 139.8 NF 132.7 N 187.9 I"),
            (cls.rm734, second, "K 140 NF 133 N 188 I"),  # NF again, in a second sequence
            (cls.five_cb, first, "K 24 N 35 I"),  # no NF
            (cls.six_cb, first, "K 14 NF 20 N 29 I"),  # made up for the test: 6CB has no NF
        ]:
            PhaseSequence.objects.create(material=material, reference=reference, sequence=sequence)

    def search(self, **params):
        return self.client.get(reverse("repo:materials"), params)

    def found(self, **params):
        return list(self.search(**params).context["page"].object_list)

    def test_phase_lists_only_the_materials_with_it_in_a_sequence(self):
        # RM734 has NF in two sequences but is listed once.
        self.assertEqual(self.found(phase="NF"), [self.rm734, self.six_cb])

    def test_substructure_narrows_the_phase_search(self):
        self.assertEqual(self.found(phase="NF", sub="C#N"), [self.six_cb])

    def test_phase_combines_with_the_name_search(self):
        self.assertEqual(self.found(q="RM", phase="NF"), [self.rm734])
        self.assertEqual(self.found(q="5CB", phase="NF"), [])

    def test_a_phase_not_in_the_vocabulary_is_ignored(self):
        response = self.search(phase="XYZ")

        self.assertEqual(list(response.context["page"].object_list), [self.rm734, self.five_cb, self.six_cb])
        self.assertEqual(response.context["phase"], "")

    def test_heading_and_box_show_the_phase(self):
        response = self.search(q="CB", sub="C#N", phase="NF")

        self.assertContains(response, "1 material with NF matches “CB” and contains <code>C#N</code>")
        self.assertContains(response, '<option value="NF" selected>NF (Ferroelectric nematic)</option>')


class TemperatureSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        cls.six_cb = Material.objects.create(name="6CB", smiles=SIX_CB)
        reference = add_reference("10.1000/test")
        for material, sequence in [
            (cls.rm734, "K 139.8 NF 132.7 N 187.9 I"),  # NF forms at 132.7
            (cls.five_cb, "K 50 NF 90 N 140 I"),  # made up: NF forms at 90, N at 140
            (cls.six_cb, "K 14 N 29 I"),
        ]:
            PhaseSequence.objects.create(material=material, reference=reference, sequence=sequence)

    def search(self, **params):
        return self.client.get(reverse("repo:materials"), params)

    def found(self, **params):
        return list(self.search(**params).context["page"].object_list)

    def test_nf_between_100_and_150_lists_only_materials_whose_nf_forms_in_it(self):
        # 5CB has NF, and N forming at 140, but no one transition is both: its NF forms at 90.
        self.assertEqual(self.found(phase="NF", tmin="100", tmax="150"), [self.rm734])

    def test_a_blank_range_behaves_as_the_phase_search_alone(self):
        self.assertEqual(self.found(phase="NF", tmin="", tmax=""), [self.rm734, self.five_cb])

    def test_with_no_phase_chosen_any_phase_forming_in_the_range_counts(self):
        self.assertEqual(self.found(tmin="100", tmax="150"), [self.rm734, self.five_cb])

    def test_either_end_can_be_left_blank(self):
        self.assertEqual(self.found(phase="NF", tmin="100"), [self.rm734])
        self.assertEqual(self.found(phase="NF", tmax="100"), [self.five_cb])

    def test_a_temperature_that_is_not_a_number_is_ignored(self):
        response = self.search(phase="NF", tmin="hot", tmax="nan")

        self.assertEqual(list(response.context["page"].object_list), [self.rm734, self.five_cb])
        self.assertEqual((response.context["tmin"], response.context["tmax"]), ("", ""))

    def test_heading_boxes_and_page_links_keep_the_range(self):
        response = self.search(phase="NF", tmin="100", tmax="150")

        self.assertContains(response, "1 material with NF forming between 100 and 150 °C")
        self.assertContains(response, 'name="tmin" type="number" step="any" value="100"')
        self.assertEqual(response.context["search"], "phase=NF&tmin=100&tmax=150")

    def test_heading_without_a_phase(self):
        response = self.search(tmin="100")

        self.assertContains(response, "2 materials with a phase forming at 100 °C or above")


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

    def test_page_links_keep_the_substructure_with_its_hash_encoded(self):
        response = self.client.get(reverse("repo:materials"), {"sub": "[#6]"})  # any carbon

        self.assertContains(response, 'href="?sub=%5B%236%5D&amp;page=2"')

    def test_page_links_keep_the_phase(self):
        reference = add_reference("10.1000/test")
        for material in self.materials:  # made up for the test: no alkane has NF
            PhaseSequence.objects.create(material=material, reference=reference, sequence="K 10 NF 20 I")

        response = self.client.get(reverse("repo:materials"), {"phase": "NF"})

        self.assertContains(response, 'href="?phase=NF&amp;page=2"')
