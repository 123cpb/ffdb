from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from repo.models import Reference, clean_doi, clean_patent_number

DOI = "10.1002/chem.201702742"


class LabelTests(SimpleTestCase):
    def test_label_from_the_details(self):
        ref = Reference(identifier=DOI, first_author="Mandle", year=2017, venue="Chem. Eur. J.")
        self.assertEqual(ref.label, "Mandle 2017, Chem. Eur. J.")
        self.assertEqual(str(ref), "Mandle 2017, Chem. Eur. J.")

    def test_no_venue_leaves_author_and_year(self):
        ref = Reference(identifier=DOI, first_author="Mandle", year=2017)
        self.assertEqual(ref.label, "Mandle 2017")

    def test_no_details_falls_back_to_the_identifier(self):
        self.assertEqual(Reference(identifier="EP3260450A1").label, "EP3260450A1")

    def test_a_missing_year_also_falls_back_to_the_identifier(self):
        ref = Reference(identifier=DOI, first_author="Mandle", venue="Chem. Eur. J.")
        self.assertEqual(ref.label, DOI)

    def test_an_unpublished_result_says_so_with_or_without_details(self):
        ref = Reference(kind=Reference.Kind.UNPUBLISHED, identifier="Mandle group 2026, Computed dipole moments")
        self.assertEqual(ref.label, "Mandle group 2026, Computed dipole moments (unpublished)")

        ref.first_author, ref.year, ref.venue = "Mandle", 2026, "University of Leeds"
        self.assertEqual(ref.label, "Mandle 2026, University of Leeds (unpublished)")


class LinkTests(SimpleTestCase):
    def test_articles_and_preprints_link_to_doi_org(self):
        for kind in (Reference.Kind.ARTICLE, Reference.Kind.PREPRINT):
            ref = Reference(kind=kind, identifier=DOI)
            self.assertEqual(ref.link, f"https://doi.org/{DOI}")

    def test_a_patent_links_to_an_espacenet_search(self):
        ref = Reference(kind=Reference.Kind.PATENT, identifier="EP3260450A1")
        self.assertEqual(ref.link, "https://worldwide.espacenet.com/patent/search?q=pn%3DEP3260450A1")

    def test_a_thesis_or_an_unpublished_result_has_no_link(self):
        for kind in (Reference.Kind.THESIS, Reference.Kind.UNPUBLISHED):
            ref = Reference(kind=kind, identifier="Mandle 2015, PhD thesis")
            self.assertEqual(ref.link, "")

    def test_a_hash_in_a_doi_is_encoded(self):
        ref = Reference(kind=Reference.Kind.ARTICLE, identifier="10.1000/a#b")
        self.assertEqual(ref.link, "https://doi.org/10.1000/a%23b")


class CleanDoiTests(SimpleTestCase):
    def test_prefixes_are_removed(self):
        for typed in (
            f"https://doi.org/{DOI}",
            f"http://dx.doi.org/{DOI}",
            f"doi:{DOI}",
            f"DOI: {DOI}",
            f"  {DOI} ",
        ):
            self.assertEqual(clean_doi(typed), DOI, typed)

    def test_a_doi_is_lower_cased(self):
        self.assertEqual(clean_doi("10.1103/PhysRevLett.133.038101"), "10.1103/physrevlett.133.038101")


class CleanPatentNumberTests(SimpleTestCase):
    def test_spaces_are_removed_and_letters_capitalised(self):
        self.assertEqual(clean_patent_number(" ep 3260450 a1 "), "EP3260450A1")


class KindForDoiTests(SimpleTestCase):
    def test_preprint_servers_dois_are_preprints(self):
        for doi in (
            "10.26434/chemrxiv.15004113/v1",
            "10.48550/arXiv.2505.03009",  # as arXiv writes it
            "https://doi.org/10.48550/arxiv.2505.03009",
        ):
            self.assertEqual(Reference.kind_for_doi(doi), Reference.Kind.PREPRINT, doi)

    def test_other_dois_are_articles(self):
        self.assertEqual(Reference.kind_for_doi(DOI), Reference.Kind.ARTICLE)


class UniquenessTests(TestCase):
    def setUp(self):
        Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier=DOI)

    def test_full_clean_cleans_an_articles_doi(self):
        ref = Reference(kind=Reference.Kind.ARTICLE, identifier="https://doi.org/10.1039/C7CP00456G")
        ref.full_clean()
        self.assertEqual(ref.identifier, "10.1039/c7cp00456g")

    def test_a_patent_number_is_left_alone(self):
        ref = Reference(kind=Reference.Kind.PATENT, identifier="EP3260450A1")
        ref.full_clean()
        self.assertEqual(ref.identifier, "EP3260450A1")

    def test_full_clean_tidies_a_patent_number(self):
        ref = Reference(kind=Reference.Kind.PATENT, identifier="ep 3260450 a1")
        ref.full_clean()
        self.assertEqual(ref.identifier, "EP3260450A1")

    def test_the_same_doi_typed_differently_is_refused(self):
        ref = Reference(kind=Reference.Kind.ARTICLE, identifier="https://doi.org/10.1002/CHEM.201702742")
        with self.assertRaisesMessage(ValidationError, "Reference with this Identifier already exists."):
            ref.full_clean()

    def test_the_same_doi_as_a_preprint_is_refused(self):
        ref = Reference(kind=Reference.Kind.PREPRINT, identifier=DOI)
        with self.assertRaisesMessage(ValidationError, "Reference with this Identifier already exists."):
            ref.full_clean()
