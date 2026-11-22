from django.contrib.auth.models import Group, User
from django.contrib.messages import get_messages
from django.urls import reverse

from repo.models import Material, MixtureComponent, PhaseSequence, Property, RetiredID, Series, Variable
from repo.tests.test_retired import RetiredTestCase, all_rows, snapshot_rows

# RM734 without one of its methoxy groups: RM734 entered with the wrong structure.
WRONG_RM734 = "COc1ccc(C(=O)Oc2ccc(C(=O)Oc3ccc([N+](=O)[O-])cc3)cc2)cc1"
EIGHT_CB = "CCCCCCCCc1ccc(-c2ccc(C#N)cc2)cc1"
DUPLICATE_SUMMARY = "K 120.5 NF 130 N 175 I"


def messages_text(response):
    return " ".join(str(message) for message in get_messages(response.wsgi_request))


class MergeTests(RetiredTestCase):
    """RetiredTestCase's materials, and a duplicate of RM734, RM-734, entered with the wrong
    structure, with a sequence, a single value, 10 mol% of F7, and names: RM-734 is one of
    RM734's names already, 3 is a bare compound number, and RM734-II is new."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.duplicate = Material.objects.create(name="RM-734", aliases="3, RM734-II", smiles=WRONG_RM734)
        cls.sequence = PhaseSequence.objects.create(
            material=cls.duplicate, reference=cls.reference, sequence=DUPLICATE_SUMMARY, approved=True
        )
        cls.series = Series.objects.create(
            material=cls.duplicate, reference=cls.reference, property=Property.objects.get(code="ps"),
            variable=Variable.objects.get(code="temperature"), kind=Series.Kind.MEASURED, approved=True,
        )
        cls.series.points.create(value=5.0, temperature_c=90)
        cls.membership = MixtureComponent.objects.create(
            mixture=cls.f7, component=cls.duplicate, fraction=10, unit=MixtureComponent.Unit.MOL
        )

    def merge_url(self, material):
        return reverse("admin:repo_material_merge", args=[material.pk])

    def preview(self, material, into):
        """What the ID box sends: a GET, which changes nothing."""
        return self.client.get(self.merge_url(material), {"into": into})

    def merge(self, material, into):
        """What the Merge button sends."""
        return self.client.post(self.merge_url(material), {"into": into})

    def test_a_merge_moves_the_sequences_values_and_memberships(self):
        number = self.duplicate.pk
        change_url = reverse("admin:repo_material_change", args=[number])
        self.assertContains(self.client.get(change_url), self.merge_url(self.duplicate))  # the link

        response = self.merge(self.duplicate, self.rm734.accession)

        self.assertRedirects(response, reverse("admin:repo_material_change", args=[self.rm734.pk]))
        self.assertFalse(Material.objects.filter(pk=number).exists())
        for row in [self.sequence, self.series]:
            row.refresh_from_db()
            self.assertEqual(row.material, self.rm734)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.component, self.rm734)
        record = RetiredID.objects.get(number=number)
        # Public, though by the time it was retired its data had moved: it was asked first.
        self.assertEqual((record.merged_into, record.retired_by, record.was_public), (self.rm734.pk, self.admin, True))
        self.assertEqual(record.moved, {
            "sequences": [self.sequence.pk], "series": [self.series.pk], "memberships": [self.membership.pk],
            "merges": [], "aliases": ["RM734-II"],
        })
        self.assertEqual(snapshot_rows(record), {("repo.material", number)})  # only what was deleted

    def test_the_preview_shows_what_will_move_and_changes_nothing(self):
        before = all_rows()

        response = self.preview(self.duplicate, self.rm734.accession)

        for text in [
            DUPLICATE_SUMMARY, "Ps, Mandle 2017", "F7: 10 mol%", "RM734-II", "3: a bare compound number",
            f"Merge {self.duplicate.accession} into {self.rm734.accession}",
        ]:
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertEqual(all_rows(), before)
        self.assertEqual(Material.objects.get(pk=self.rm734.pk).aliases, "")

    def test_names_become_aliases_and_those_that_cant_are_listed(self):
        response = self.merge(self.duplicate, self.rm734.accession)

        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "RM734-II")  # RM-734 is already its name
        text = messages_text(response)
        self.assertIn(f"3 wasn't added to {self.rm734.accession}'s aliases: a bare compound number", text)
        self.assertNotIn("RM-734 wasn't", text)

    def test_a_merged_id_leads_to_the_material_it_went_into(self):
        number = self.duplicate.pk
        self.duplicate.merge_into(self.rm734, self.admin)
        target_url = reverse("repo:material", args=[self.rm734.pk])

        response = self.client.get(reverse("repo:material", args=[number]))

        self.assertRedirects(response, target_url)  # a 302
        self.assertContains(self.client.get(target_url), "Cr 120.5 NF 130 N 175 Iso")  # its sequence, as pages show it

    def test_a_hidden_material_gives_no_names_and_its_id_is_not_found(self):
        hidden = Material.objects.create(name="RM734 waiting", aliases="RM734-III", smiles=EIGHT_CB)  # no data: hidden
        number, why = hidden.pk, f"{hidden.accession} isn't public"

        preview = self.preview(hidden, self.rm734.accession)
        response = self.merge(hidden, self.rm734.accession)

        self.assertEqual(preview.context["aliases_added"], [])
        self.assertEqual(preview.context["aliases_refused"], [("RM734 waiting", why), ("RM734-III", why)])
        self.assertIn(f"RM734-III wasn't added to {self.rm734.accession}'s aliases: {why}.", messages_text(response))
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")
        record = RetiredID.objects.get(number=number)
        self.assertEqual((record.was_public, record.moved["aliases"]), (False, []))
        self.assertEqual(self.client.get(reverse("repo:material", args=[number])).status_code, 404)

    def test_a_merged_id_leads_to_a_hidden_material_only_once_it_is_public(self):
        # Listed, so public, with no data to move: F7, which has none either, stays hidden.
        again = Material.objects.create(kind=Material.Kind.MIXTURE, name="Mixture 7", listed=True)
        address = reverse("repo:material", args=[again.pk])
        again.merge_into(self.f7, self.admin)

        response = self.client.get(address)

        self.assertEqual(response.status_code, 404)  # not a redirect, which would name F7
        Material.objects.filter(pk=self.f7.pk).update(listed=True)
        self.assertRedirects(self.client.get(address), reverse("repo:material", args=[self.f7.pk]))

    def test_earlier_merges_are_pointed_at_the_new_material(self):
        earlier = Material.objects.create(name="RM734 again", smiles=EIGHT_CB, listed=True)
        earlier_number, number = earlier.pk, self.duplicate.pk
        earlier.merge_into(self.duplicate, self.admin)

        self.duplicate.merge_into(self.rm734, self.admin)

        self.assertEqual(RetiredID.objects.get(number=earlier_number).merged_into, self.rm734.pk)
        self.assertEqual(RetiredID.objects.get(number=number).moved["merges"], [earlier_number])
        self.assertRedirects(
            self.client.get(reverse("repo:material", args=[earlier_number])),
            reverse("repo:material", args=[self.rm734.pk]),
        )

    def test_each_refusal_changes_nothing(self):
        # Apostrophes are left out of the messages looked for: the page writes them &#x27;.
        cases = {
            "a compound into a mixture": (self.duplicate, self.f7.accession, "only be merged into one of its own kind"),
            "a mixture into a compound": (self.f7, self.rm734.accession, "only be merged into one of its own kind"),
            "into itself": (self.duplicate, self.duplicate.accession, "be merged into itself"),
            "both in one mixture": (self.duplicate, self.cb5.accession, f"Both are components of {self.f7}"),
            "not in FFDB": (self.duplicate, "LCR-99999", "No material has the LCR ID LCR-99999."),
            "not an ID": (self.duplicate, "RM734", "Give an LCR ID"),
        }
        for case, (material, into, message) in cases.items():
            with self.subTest(case):
                before = all_rows()
                response = self.merge(material, into)
                self.assertContains(response, message)  # the page again, with why
                self.assertEqual(all_rows(), before)

    def test_a_mixture_merged_into_a_mixture_leaves_its_composition_in_the_snapshot(self):
        again = Material.objects.create(kind=Material.Kind.MIXTURE, name="Mixture 7", listed=True)
        row = MixtureComponent.objects.create(mixture=again, component=self.cb5, fraction=30, unit=MixtureComponent.Unit.MOL)
        number = again.pk

        again.merge_into(self.f7, self.admin)

        record = RetiredID.objects.get(number=number)
        self.assertEqual(snapshot_rows(record), {("repo.material", number), ("repo.mixturecomponent", row.pk)})
        self.assertEqual(set(self.f7.components.all()), {self.component, self.membership})  # its own, unchanged
        self.f7.refresh_from_db()
        self.assertEqual(self.f7.aliases, "Mixture 7")

    def test_a_curator_cannot_merge(self):
        curator = User.objects.create_user("curator", is_staff=True)
        curator.groups.add(Group.objects.get(name="Curators"))
        before = all_rows()
        self.client.force_login(curator)

        change_page = self.client.get(reverse("admin:repo_material_change", args=[self.duplicate.pk]))
        self.assertNotContains(change_page, self.merge_url(self.duplicate))
        self.assertEqual(self.preview(self.duplicate, self.rm734.accession).status_code, 403)
        self.assertEqual(self.merge(self.duplicate, self.rm734.accession).status_code, 403)
        self.assertEqual(all_rows(), before)

    def test_restore_refuses_a_merged_record(self):
        number = self.duplicate.pk
        self.duplicate.merge_into(self.rm734, self.admin)
        restore_url = reverse("admin:repo_retiredid_restore", args=[number])
        self.assertNotContains(self.client.get(reverse("admin:repo_retiredid_change", args=[number])), restore_url)
        before = all_rows()

        response = self.client.post(restore_url)

        self.assertIn(f"it was merged into {self.rm734.accession}", messages_text(response))
        self.assertEqual(all_rows(), before)
