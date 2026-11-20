"""The Add data page finds the reference the data is from, or adds it, with Crossref's
details for a DOI; a reference's Add data page shows what FFDB already has from it. Crossref
is FakeCrossref, which answers from saved records."""

from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from repo.models import Material, PhaseSequence, Reference
from repo.tests.test_reference_details import BCSJ, CHEMRXIV, PCCP, UNKNOWN, URLOPEN, FakeCrossref
from repo.tests.test_values import DIO, RM734, add

ADD = reverse("repo:add")
LOGIN = reverse("login")
FIVE_CB = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"


@override_settings(CROSSREF_MAILTO="ffdb-tests@example.org")
class AddDataTestCase(TestCase):
    """A curator, logged in, and one reference of each kind already in FFDB: no materials or
    data yet. Crossref is replaced for every test."""

    @classmethod
    def setUpTestData(cls):
        cls.curator = User.objects.create_user("curator", password="a-test-password")
        cls.curator.groups.add(Group.objects.get(name="Curators"))
        cls.article = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier="10.1002/chem.201702742",
            first_author="Mandle", year=2017, venue="Chem. Eur. J.", abstract="An abstract only the admin shows.",
        )
        cls.patent = Reference.objects.create(kind=Reference.Kind.PATENT, identifier="EP3260450A1")
        cls.thesis = Reference.objects.create(kind=Reference.Kind.THESIS, identifier="Mandle 2015, Two nematic phases")

    def setUp(self):
        self.crossref = FakeCrossref()
        patcher = mock.patch(URLOPEN, self.crossref)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.curator)

    def give(self, kind, identifier, follow=True):
        """Send the Add data form. With follow, the response is the page it redirects to."""
        return self.client.post(ADD, {"kind": kind, "identifier": identifier}, follow=follow)

    def add_page(self, reference):
        return reverse("repo:add_from_reference", args=[reference.pk])

    def messages(self, response):
        return [str(message) for message in response.context["messages"]]


class FindReferenceTests(AddDataTestCase):
    def test_a_doi_already_in_ffdb_goes_to_its_reference_however_it_is_pasted(self):
        for typed in ["https://doi.org/10.1002/CHEM.201702742", "doi: 10.1002/chem.201702742"]:
            with self.subTest(typed):
                response = self.give("doi", typed)

                self.assertRedirects(response, self.add_page(self.article))
                self.assertEqual(self.messages(response), ["Already in FFDB: Mandle 2017, Chem. Eur. J."])
        self.assertEqual(Reference.objects.count(), 3)
        self.assertEqual(self.crossref.requests, [])

    def test_a_new_doi_is_added_with_its_details_and_abstract_from_crossref(self):
        response = self.give("doi", f"https://doi.org/{PCCP.upper()}")

        reference = Reference.objects.get(identifier=PCCP)
        self.assertRedirects(response, self.add_page(reference))
        self.assertEqual(reference.kind, Reference.Kind.ARTICLE)
        self.assertEqual(reference.label, "Mandle 2017, Phys. Chem. Chem. Phys.")
        self.assertEqual(reference.title, "A nematic to nematic transformation exhibited by a rod-like liquid crystal")
        self.assertTrue(reference.abstract.startswith("A rod-like liquid crystal with two nematic phases.\n\n"))
        self.assertEqual(reference.entered_by, self.curator)
        self.assertIsNotNone(reference.entered_at)
        self.assertEqual(self.messages(response), [
            "Added to FFDB: Mandle 2017, Phys. Chem. Chem. Phys.",
            "Its details are from Crossref, with its abstract.",
        ])

    def test_a_doi_without_an_abstract_on_crossref_is_added_with_its_details(self):
        response = self.give("doi", BCSJ)

        reference = Reference.objects.get(identifier=BCSJ)
        self.assertEqual(reference.first_author, "Nakai")
        self.assertEqual(reference.abstract, "")
        self.assertIn("Its details are from Crossref, which has no abstract for it.", self.messages(response))

    def test_preprint_servers_dois_are_added_as_preprints(self):
        self.give("doi", CHEMRXIV)
        self.give("doi", "10.48550/arXiv.2505.03009")  # as arXiv writes it

        self.assertEqual(Reference.objects.get(identifier=CHEMRXIV).kind, Reference.Kind.PREPRINT)
        self.assertEqual(Reference.objects.get(identifier="10.48550/arxiv.2505.03009").kind, Reference.Kind.PREPRINT)

    def test_a_doi_crossref_doesnt_know_is_added_without_details_and_the_page_says_so(self):
        response = self.give("doi", UNKNOWN)

        reference = Reference.objects.get(identifier=UNKNOWN)
        self.assertRedirects(response, self.add_page(reference))
        self.assertEqual(reference.label, UNKNOWN)
        self.assertEqual(self.messages(response), [
            f"Added to FFDB: {UNKNOWN}",
            "Crossref doesn't know this DOI, so it has no details. "
            "Add its details in the admin, or run fetch_reference_details later.",
        ])

    def test_without_a_connection_the_doi_is_added_without_details_and_the_page_says_so(self):
        self.crossref.unreachable = {PCCP}

        response = self.give("doi", PCCP)

        self.assertEqual(Reference.objects.get(identifier=PCCP).label, PCCP)
        self.assertIn("Crossref couldn't be reached", self.messages(response)[1])

    @override_settings(CROSSREF_MAILTO="")
    def test_without_a_contact_address_crossref_isnt_asked(self):
        response = self.give("doi", PCCP)

        self.assertTrue(Reference.objects.filter(identifier=PCCP).exists())
        self.assertEqual(self.crossref.requests, [])
        self.assertIn("CROSSREF_MAILTO isn't set", self.messages(response)[1])

    def test_text_that_isnt_a_doi_is_refused(self):
        response = self.give("doi", "EP3260450A1", follow=False)

        self.assertEqual(response.status_code, 200)  # the form again, not a redirect
        self.assertContains(response, "This isn&#x27;t a DOI")
        self.assertEqual(Reference.objects.count(), 3)
        self.assertEqual(self.crossref.requests, [])

    def test_a_blank_box_is_refused_for_every_kind_but_an_unpublished_result(self):
        for kind in ["doi", "patent", "thesis"]:
            with self.subTest(kind):
                response = self.give(kind, "  ", follow=False)

                self.assertContains(response, "Give the DOI, patent number or thesis.")
        self.assertEqual(Reference.objects.count(), 3)

    def test_a_patent_number_already_in_ffdb_is_found_however_it_is_typed(self):
        response = self.give("patent", "ep 3260450 a1")

        self.assertRedirects(response, self.add_page(self.patent))
        self.assertEqual(Reference.objects.count(), 3)

    def test_a_new_patent_is_added_tidied_without_asking_crossref(self):
        response = self.give("patent", "wo 2015190399 a1")

        reference = Reference.objects.get(identifier="WO2015190399A1")
        self.assertRedirects(response, self.add_page(reference))
        self.assertEqual(reference.kind, Reference.Kind.PATENT)
        self.assertEqual(reference.entered_by, self.curator)
        self.assertEqual(self.crossref.requests, [])
        self.assertEqual(self.messages(response), ["Added to FFDB: WO2015190399A1"])

    def test_a_thesis_is_added_and_found_again_in_other_capitals(self):
        self.give("thesis", "Parton-Barr  2027,  Fluid ferroelectrics")
        thesis = Reference.objects.get(identifier="Parton-Barr 2027, Fluid ferroelectrics")
        self.assertEqual(thesis.kind, Reference.Kind.THESIS)

        response = self.give("thesis", "parton-barr 2027, fluid ferroelectrics")

        self.assertRedirects(response, self.add_page(thesis))
        self.assertEqual(Reference.objects.count(), 4)

    def test_every_unpublished_result_goes_under_one_reference_with_nothing_typed(self):
        response = self.give("unpublished", "")
        group = Reference.objects.get(identifier="Group results")
        self.assertEqual(group.kind, Reference.Kind.UNPUBLISHED)
        self.assertEqual(group.entered_by, self.curator)
        self.assertEqual(self.messages(response), ["Added to FFDB: Group results (unpublished)"])

        # Anything typed is ignored: it is still the one reference.
        response = self.give("unpublished", "Mandle group 2026, Computed dipole moments")

        self.assertRedirects(response, self.add_page(group))
        self.assertEqual(self.messages(response), ["Already in FFDB: Group results (unpublished)"])
        self.assertEqual(Reference.objects.count(), 4)
        self.assertEqual(self.crossref.requests, [])

    def test_the_browser_lets_the_box_be_sent_empty(self):
        box = str(self.client.get(ADD).context["form"]["identifier"])

        self.assertIn('name="identifier"', box)
        self.assertNotIn("required", box)


class AccessTests(AddDataTestCase):
    def test_a_logged_out_visitor_cant_add_anything(self):
        self.client.logout()

        response = self.give("patent", "WO2015190399A1", follow=False)

        self.assertRedirects(response, f"{LOGIN}?next={ADD}")
        self.assertFalse(Reference.objects.filter(identifier="WO2015190399A1").exists())
        page = self.add_page(self.article)
        self.assertRedirects(self.client.get(page), f"{LOGIN}?next={page}")

    def test_a_logged_in_user_who_isnt_a_curator_is_refused(self):
        self.client.force_login(User.objects.create_user("other"))

        self.assertEqual(self.give("patent", "WO2015190399A1", follow=False).status_code, 403)
        self.assertFalse(Reference.objects.filter(identifier="WO2015190399A1").exists())
        self.assertEqual(self.client.get(self.add_page(self.article)).status_code, 403)


class ReferenceAddPageTests(AddDataTestCase):
    """The article gives a sequence for RM734 and a Ps value for DIO. 5CB has neither."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.dio = Material.objects.create(name="DIO", smiles=DIO)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.article, sequence="K 139.8 NF 132.7 N 187.9 I")
        add(cls.dio, cls.article, "ps", method="current reversal", conditions={"frequency_Hz": 50, "cell_gap_um": 8},
            ).points.create(value=6.08, temperature_c=60)

    def test_lists_its_materials_from_sequences_and_values_with_both(self):
        response = self.client.get(self.add_page(self.article))

        for material in (self.rm734, self.dio):
            page = reverse("repo:material", args=[material.pk])
            self.assertContains(response, f'<a href="{page}">{material.accession}</a>', html=True)
        self.assertNotContains(response, self.five_cb.accession)
        self.assertContains(response, "Cr 139.8 N<sub>F</sub> 132.7 N 187.9 Iso")
        self.assertContains(response, "Spontaneous polarisation, Ps")

    def test_the_cards_are_the_one_way_to_add_data_and_the_table_only_makes_them(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, '<button type="submit" name="save">Save</button>', count=1, html=True)  # the cards'
        self.assertContains(response, 'name="cards"')  # the table's Make cards
        # The forms the cards replaced, gone with their addresses.
        for removed in ["value", "curve", "mixture"]:
            with self.subTest(removed):
                with self.assertRaises(NoReverseMatch):
                    reverse(f"repo:add_{removed}", args=[self.article.pk])
                self.assertEqual(self.client.get(f"{self.add_page(self.article)}{removed}/").status_code, 404)

    def test_a_reference_with_nothing_yet_says_so(self):
        response = self.client.get(self.add_page(self.thesis))

        self.assertContains(response, "No materials from this reference yet.")
        self.assertContains(response, "No phase sequences from this reference.")

    def test_unknown_number_is_not_found(self):
        self.assertEqual(self.client.get(reverse("repo:add_from_reference", args=[9999])).status_code, 404)

    def test_the_public_page_links_curators_to_it(self):
        public = reverse("repo:reference", args=[self.article.pk])

        self.assertContains(self.client.get(public), f'href="{self.add_page(self.article)}"')
        self.client.logout()
        self.assertNotContains(self.client.get(public), self.add_page(self.article))

    def test_the_abstract_is_shown_only_in_the_admin(self):
        abstract = self.article.abstract

        self.assertNotContains(self.client.get(self.add_page(self.article)), abstract)
        self.assertNotContains(self.client.get(reverse("repo:reference", args=[self.article.pk])), abstract)
        self.client.force_login(User.objects.create_superuser("admin"))
        self.assertContains(self.client.get(reverse("admin:repo_reference_change", args=[self.article.pk])), abstract)
