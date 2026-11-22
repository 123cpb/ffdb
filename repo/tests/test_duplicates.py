"""The possible-duplicates page in the admin, for admins only: pairs of compounds with the same
connectivity, hidden ones included and marked, with what differs. Not a duplicate stores the pair,
who decided and when, so it isn't listed again, and the decision goes with either material."""

from django.contrib.auth.models import Group, User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from repo.duplicates import BOTH, NEITHER, ONE_ONLY
from repo.models import Material, NotDuplicate, PhaseSequence, Reference
from repo.tests.test_import import ARTICLE, ARXIV, FIVE_CB

PAGE = reverse("admin:repo_material_duplicates")


def not_duplicate_url(a, b):
    return reverse("admin:repo_material_not_duplicate", args=[a.pk, b.pk])


class DuplicatesTests(TestCase):
    """2-Octanol three ways: S and R, with their stereochemistry, and racemic, without it. R is hidden.
    Benzene and benzene with one deuterium, neither with stereochemistry; 5CB, with nothing of the
    same connectivity; and two mixtures, which have no structure. An admin is logged in."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin")
        cls.curator = User.objects.create_user("curator", is_staff=True)
        cls.curator.groups.add(Group.objects.get(name="Curators"))
        cls.paper = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier=ARTICLE, first_author="Mandle", year=2017, published=True
        )
        cls.s = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")
        PhaseSequence.objects.create(material=cls.s, reference=cls.paper, sequence="K 10 N 40 I", approved=True)
        cls.rac = Material.objects.create(name="rac-2-octanol", smiles="CC(O)CCCCCC", listed=True)
        cls.r = Material.objects.create(name="R-2-octanol", smiles="C[C@@H](O)CCCCCC")  # hidden: no data, not listed
        cls.benzene = Material.objects.create(name="benzene", smiles="c1ccccc1", listed=True)
        cls.d_benzene = Material.objects.create(name="benzene-d1", smiles="[2H]c1ccccc1", listed=True)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB, listed=True)
        for name in ["F7", "F8"]:
            Material.objects.create(kind=Material.Kind.MIXTURE, name=name, other_components="a host", listed=True)

    def setUp(self):
        self.client.force_login(self.admin)

    def pairs(self):
        """The page's pairs with the same connectivity, as (heading, a, b)."""
        response = self.client.get(PAGE)
        return [
            (pair["heading"], pair["compounds"][0]["material"], pair["compounds"][1]["material"])
            for pair in response.context["connectivity_pairs"]
        ]

    def test_pairs_with_the_same_connectivity_are_found_the_likeliest_first(self):
        self.assertEqual(
            self.pairs(),
            [
                (ONE_ONLY, self.s, self.rac),
                (ONE_ONLY, self.rac, self.r),
                (BOTH, self.s, self.r),
                (NEITHER, self.benzene, self.d_benzene),
            ],
        )

    def test_each_compound_has_its_drawing_references_and_merge_links_both_ways(self):
        response = self.client.get(PAGE)

        self.assertContains(response, 'alt="Structure of S-2-octanol"', count=2)  # in two pairs
        self.assertContains(response, "References: Mandle 2017", count=2)
        self.assertContains(response, f"{reverse('admin:repo_material_merge', args=[self.s.pk])}?into={self.rac.accession}")
        self.assertContains(response, f"{reverse('admin:repo_material_merge', args=[self.rac.pk])}?into={self.s.accession}")
        self.assertContains(response, f'action="{not_duplicate_url(self.s, self.rac)}"')

    def test_a_hidden_compound_is_listed_and_marked_hidden(self):
        response = self.client.get(PAGE)

        hidden = {
            compound["material"]
            for pair in response.context["connectivity_pairs"]
            for compound in pair["compounds"]
            if compound["hidden"]
        }
        self.assertEqual(hidden, {self.r})
        self.assertContains(response, "<strong>Hidden:</strong>", count=2)  # R is in two pairs

    def test_mixtures_are_never_listed(self):
        listed = {material for _, a, b in self.pairs() for material in (a, b)}

        self.assertEqual(listed, {self.s, self.rac, self.r, self.benzene, self.d_benzene})

    def test_a_pair_marked_not_a_duplicate_is_gone_and_stays_gone(self):
        # Posted the other way round: the pair is stored lower number first.
        response = self.client.post(not_duplicate_url(self.rac, self.s))

        self.assertRedirects(response, PAGE)
        decision = NotDuplicate.objects.get()
        self.assertEqual((decision.first, decision.second, decision.decided_by), (self.s, self.rac, self.admin))
        self.assertIsNotNone(decision.decided_at)
        self.assertNotIn((ONE_ONLY, self.s, self.rac), self.pairs())
        self.assertEqual(len(self.pairs()), 3)  # the others are still listed

        self.client.post(not_duplicate_url(self.s, self.rac))
        self.assertEqual(NotDuplicate.objects.count(), 1)  # marked twice, stored once

    def test_the_database_stores_a_pair_one_way_round(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotDuplicate.objects.create(first=self.rac, second=self.s)  # the higher number first
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotDuplicate.objects.create(first=self.s, second=self.s)

    def test_deleting_a_decision_lists_the_pair_again(self):
        decision = NotDuplicate.mark(self.s, self.rac, self.admin)

        self.assertContains(self.client.get(reverse("admin:repo_notduplicate_changelist")), str(self.s))
        self.client.post(reverse("admin:repo_notduplicate_delete", args=[decision.pk]), {"post": "yes"})

        self.assertFalse(NotDuplicate.objects.exists())
        self.assertIn((ONE_ONLY, self.s, self.rac), self.pairs())

    def test_a_decision_goes_with_either_material_when_it_is_retired_or_merged(self):
        NotDuplicate.mark(self.s, self.rac, self.admin)
        NotDuplicate.mark(self.benzene, self.d_benzene, self.admin)
        NotDuplicate.mark(self.s, self.r, self.admin)

        self.rac.retire(self.admin)
        self.d_benzene.merge_into(self.benzene, self.admin)

        self.assertEqual(list(NotDuplicate.objects.values_list("first", "second")), [(self.s.pk, self.r.pk)])

    def test_only_admins_can_open_the_page_or_mark_a_pair(self):
        self.assertContains(self.client.get(reverse("admin:repo_material_changelist")), f'href="{PAGE}"')

        self.client.force_login(self.curator)
        self.assertEqual(self.client.get(PAGE).status_code, 403)
        self.assertEqual(self.client.post(not_duplicate_url(self.s, self.rac)).status_code, 403)

        self.client.logout()
        self.assertRedirects(self.client.get(PAGE), f"{reverse('admin:login')}?next={PAGE}")
        self.client.post(not_duplicate_url(self.s, self.rac))

        self.assertFalse(NotDuplicate.objects.exists())


class SequencePairsTests(TestCase):
    """The second list: compounds with a sequence each from the same reference that match. Each
    test makes its own compounds, alkanes of different lengths, so no two share a connectivity and
    the first list stays empty. Their sequences aren't approved, so they are hidden: the list
    includes them. An admin is logged in."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin")
        cls.paper = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier=ARTICLE, first_author="Mandle", year=2017, published=True
        )
        cls.preprint = Reference.objects.create(
            kind=Reference.Kind.PREPRINT, identifier=ARXIV, first_author="Gibb", year=2025, published=True
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.carbons = 1

    def compound(self, sequence, reference=None, direction=PhaseSequence.Direction.SUMMARY):
        """A new compound, an alkane one carbon longer than the last, with this one sequence,
        from the paper unless another reference is given."""
        self.carbons += 1
        material = Material.objects.create(name=f"C{self.carbons}", smiles="C" * self.carbons)
        PhaseSequence.objects.create(
            material=material, reference=reference or self.paper, direction=direction, sequence=sequence
        )
        return material

    def sequence_pairs(self):
        """The page's pairs with matching sequences, as (reference, a, b)."""
        response = self.client.get(PAGE)
        return [
            (pair["heading"], pair["compounds"][0]["material"], pair["compounds"][1]["material"])
            for pair in response.context["sequence_pairs"]
        ]

    def test_sequences_within_half_a_kelvin_are_found(self):
        # 128.3 - 127.8 is exactly 0.5 K, which the computer stores as a little more.
        a = self.compound("K 127.8 N 140 I")
        b = self.compound("K 128.3 N 140.4 I")

        self.assertEqual(self.sequence_pairs(), [(self.paper, a, b)])
        response = self.client.get(PAGE)
        self.assertContains(response, f'<a href="{reverse("admin:repo_reference_change", args=[self.paper.pk])}">Mandle 2017</a>')
        self.assertContains(response, "Matching: K 127.8 N 140 I (Summary)", count=1)
        self.assertContains(response, "Matching: K 128.3 N 140.4 I (Summary)", count=1)
        self.assertContains(response, f"{reverse('admin:repo_material_merge', args=[a.pk])}?into={b.accession}")
        self.assertContains(response, f"{reverse('admin:repo_material_merge', args=[b.pk])}?into={a.accession}")
        self.assertContains(response, f'action="{not_duplicate_url(a, b)}"')

    def test_sequences_0_6_K_apart_are_not_found(self):
        self.compound("K 100 N 140 I")
        self.compound("K 100 N 140.6 I")

        self.assertEqual(self.sequence_pairs(), [])

    def test_sequences_in_different_directions_are_not_found(self):
        self.compound("K 100 N 140 I")  # a summary
        self.compound("K 100 N 140 I", direction=PhaseSequence.Direction.HEATING)

        self.assertEqual(self.sequence_pairs(), [])

    def test_sequences_without_a_liquid_crystal_phase_are_not_found(self):
        self.compound("K 60 I")
        self.compound("K 60.2 I")

        self.assertEqual(self.sequence_pairs(), [])

    def test_sequences_from_different_references_are_not_found(self):
        self.compound("K 100 N 140 I")
        self.compound("K 100 N 140 I", reference=self.preprint)

        self.assertEqual(self.sequence_pairs(), [])

    def test_the_melting_point_must_match_too(self):
        # The NF lies below the melting point, so the melting point isn't one of the saved transitions.
        self.compound("K 77 NF 48 I")
        self.compound("K 111 NF 48 I")
        # A melting point not given matches only another not given.
        unknown = self.compound("K ? N 140 I")
        also_unknown = self.compound("K N 140.2 I")
        self.compound("K 100 N 140 I")

        self.assertEqual(self.sequence_pairs(), [(self.paper, unknown, also_unknown)])

    def test_matches_are_pairs_and_do_not_chain(self):
        # The first matches the second and the second the third, but the first and third are 0.8 K apart.
        first = self.compound("K 100 N 140 I")
        second = self.compound("K 100 N 140.4 I")
        third = self.compound("K 100 N 140.8 I")

        self.assertEqual(self.sequence_pairs(), [(self.paper, first, second), (self.paper, second, third)])

    def test_a_pair_marked_not_a_duplicate_is_gone(self):
        a = self.compound("K 100 N 140 I")
        b = self.compound("K 100 N 140 I")

        self.client.post(not_duplicate_url(a, b))

        self.assertEqual(self.sequence_pairs(), [])
