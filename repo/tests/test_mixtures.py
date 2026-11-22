"""Mixtures: materials with no structure, made of components. Each test is one rule."""

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from repo.models import Material, MixtureComponent, PhaseSequence, Reference
from repo.tests.test_sequences import RM734

CB5 = "CCCCCc1ccc(-c2ccc(C#N)cc2)cc1"  # 5CB
MIXTURE, COMPOUND = Material.Kind.MIXTURE, Material.Kind.COMPOUND
LISTED = Material.CompositionStatus.LISTED
PARTLY_KNOWN = Material.CompositionStatus.PARTLY_KNOWN
NOT_DISCLOSED = Material.CompositionStatus.NOT_DISCLOSED
MOL, WT = MixtureComponent.Unit.MOL, MixtureComponent.Unit.WT


class MixtureTestCase(TestCase):
    """Two compounds, RM734 and 5CB, and F7, a mixture of them at 70 and 30 mol%. None has
    data, so all three are listed, which makes them public."""

    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734, listed=True)
        cls.cb5 = Material.objects.create(name="5CB", smiles=CB5, listed=True)
        cls.f7 = Material.objects.create(kind=MIXTURE, name="F7", listed=True)
        MixtureComponent.objects.create(mixture=cls.f7, component=cls.rm734, fraction=70, unit=MOL)
        MixtureComponent.objects.create(mixture=cls.f7, component=cls.cb5, fraction=30, unit=MOL)

    def assertRefused(self, row, field):
        """full_clean() refuses row, with a message on field."""
        with self.assertRaises(ValidationError) as caught:
            row.full_clean()
        self.assertIn(field, caught.exception.message_dict)


class CompoundOrMixtureTests(MixtureTestCase):
    def test_a_material_is_a_compound_unless_it_says_otherwise(self):
        self.assertEqual(self.rm734.kind, COMPOUND)

    def test_a_compound_needs_a_smiles(self):
        self.assertRefused(Material(name="RM734"), "smiles")

    def test_a_mixture_has_no_smiles(self):
        self.assertRefused(Material(kind=MIXTURE, name="F8", smiles=CB5), "smiles")

    def test_a_mixture_needs_a_name(self):
        self.assertRefused(Material(kind=MIXTURE, other_components="A host"), "name")

    def test_a_mixture_is_saved_without_identifiers_and_mixtures_do_not_clash(self):
        m5 = Material.objects.create(kind=MIXTURE, name="M5", composition_status=NOT_DISCLOSED)

        for mixture in (self.f7, m5):
            mixture.refresh_from_db()
            self.assertEqual((mixture.canonical_smiles, mixture.inchikey), ("", ""))

    def test_the_database_still_allows_each_inchikey_once(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Material.objects.create(name="RM734 again", smiles=RM734)

    def test_a_compound_in_a_mixture_cannot_become_a_mixture(self):
        self.cb5.kind, self.cb5.smiles = MIXTURE, ""

        with self.assertRaises(ValidationError) as caught:
            self.cb5.full_clean()
        self.assertIn(str(self.f7), caught.exception.message_dict["kind"][0])


class ComponentTests(MixtureTestCase):
    def test_a_component_must_be_a_compound(self):
        m5 = Material.objects.create(kind=MIXTURE, name="M5", composition_status=NOT_DISCLOSED)

        self.assertRefused(MixtureComponent(mixture=self.f7, component=m5, fraction=10, unit=MOL), "component")

    def test_only_a_mixture_has_components(self):
        row = MixtureComponent(mixture=self.rm734, component=self.cb5, fraction=10, unit=MOL)

        self.assertRefused(row, "__all__")

    def test_a_fraction_is_more_than_0_and_at_most_100(self):
        mixture = Material.objects.create(kind=MIXTURE, name="F8")
        for fraction in (0, -5, 100.1):
            with self.subTest(fraction=fraction):
                row = MixtureComponent(mixture=mixture, component=self.cb5, fraction=fraction, unit=MOL)
                self.assertRefused(row, "fraction")
        MixtureComponent(mixture=mixture, component=self.cb5, fraction=100, unit=MOL).full_clean()

    def test_each_compound_appears_once_in_a_mixture(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            MixtureComponent.objects.create(mixture=self.f7, component=self.cb5, fraction=5, unit=MOL)

    def test_a_compound_cannot_be_deleted_while_a_mixture_uses_it(self):
        with self.assertRaises(ProtectedError):
            self.cb5.delete()

    def test_deleting_a_mixture_deletes_its_components_but_not_the_compounds(self):
        self.f7.delete()

        self.assertFalse(MixtureComponent.objects.exists())
        self.assertEqual(Material.objects.count(), 2)


class CompositionTests(SimpleTestCase):
    """composition_problems() reads only the mixture's own fields, so nothing is saved."""

    def problems(self, fractions, status=LISTED, other=""):
        mixture = Material(kind=MIXTURE, name="F7", composition_status=status, other_components=other)
        return mixture.composition_problems(fractions)

    def test_components_in_one_unit_adding_up_to_100_are_fine(self):
        self.assertEqual(self.problems([(70, MOL), (30, MOL)]), [])

    def test_fractions_must_all_be_in_one_unit(self):
        [problem] = self.problems([(70, MOL), (30, WT)])

        self.assertIn("same unit", problem)

    def test_fractions_may_add_up_to_half_a_percent_over_100_but_no_more(self):
        self.assertEqual(self.problems([(70, WT), (30.5, WT)]), [])
        self.assertEqual(self.problems([(70, WT), (30.6, WT)]), ["The fractions add up to 100.6 wt%, more than 100."])

    def test_not_disclosed_means_no_components_and_no_text(self):
        self.assertEqual(self.problems([], NOT_DISCLOSED), [])
        for fractions, other in (([(30, MOL)], ""), ([], "A commercial host")):
            with self.subTest(fractions=fractions, other=other):
                [problem] = self.problems(fractions, NOT_DISCLOSED, other)
                self.assertIn("Not disclosed means", problem)

    def test_listed_and_partly_known_need_components_or_text(self):
        for status in (LISTED, PARTLY_KNOWN):
            with self.subTest(status=status):
                [problem] = self.problems([], status)
                self.assertIn("choose Not disclosed", problem)
                self.assertEqual(self.problems([], status, "A commercial host"), [])


class AdminTests(MixtureTestCase):
    add_url = reverse("admin:repo_material_add")

    def setUp(self):
        # A curator, not a superuser: a superuser has every permission, so the admin would
        # show them sections a curator doesn't get.
        curator = User.objects.create_user("curator", password="pw", is_staff=True)
        curator.groups.add(Group.objects.get(name="Curators"))
        self.client.force_login(curator)

    def add_mixture(self, name, rows, status=LISTED, other=""):
        """Fill in the admin's Add material form for a mixture, one component row for each
        (material, fraction, unit) in rows, and press Save."""
        data = {
            "kind": MIXTURE, "name": name, "aliases": "", "smiles": "", "notes": "",
            "composition_status": status, "other_components": other,
            "components-TOTAL_FORMS": str(len(rows)), "components-INITIAL_FORMS": "0",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        for i, (material, fraction, unit) in enumerate(rows):
            data |= {f"components-{i}-component": material.pk, f"components-{i}-fraction": fraction, f"components-{i}-unit": unit}
        return self.client.post(self.add_url, data)

    def test_a_mixture_is_saved_with_its_components(self):
        response = self.add_mixture("F8", [(self.rm734, 60, MOL), (self.cb5, 40, MOL)])

        self.assertEqual(response.status_code, 302)  # saved, and sent back to the list
        f8 = Material.objects.get(name="F8")
        self.assertEqual(f8.kind, MIXTURE)
        self.assertEqual([(c.component, c.fraction) for c in f8.components.all()], [(self.rm734, 60), (self.cb5, 40)])

    def test_a_mixture_whose_composition_is_not_disclosed_is_saved_with_no_components(self):
        response = self.add_mixture("M5", [], NOT_DISCLOSED)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Material.objects.get(name="M5").components.exists())

    def test_the_composition_rules_are_checked_and_nothing_is_saved(self):
        response = self.add_mixture("F8", [(self.rm734, 60, MOL), (self.cb5, 40, WT)])

        self.assertContains(response, "Give every fraction in the same unit")
        self.assertFalse(Material.objects.filter(name="F8").exists())

    def test_a_mixtures_page_shows_its_components(self):
        response = self.client.get(reverse("admin:repo_material_change", args=[self.f7.pk]))

        self.assertContains(response, "Components in FFDB")
        self.assertContains(response, 'name="components-TOTAL_FORMS" value="4"')  # F7's 2, and 2 empty

    def test_the_composition_status_must_match_what_is_given(self):
        cases = [
            (LISTED, "", "or choose Not disclosed"),  # listed, with nothing given
            (NOT_DISCLOSED, "A commercial host", "Not disclosed means no components"),
        ]
        for status, other, message in cases:
            with self.subTest(status=status):
                response = self.add_mixture("F8", [], status, other)

                self.assertContains(response, message)
                self.assertFalse(Material.objects.filter(name="F8").exists())

    def test_a_compound_given_twice_is_refused(self):
        response = self.add_mixture("F8", [(self.cb5, 60, MOL), (self.cb5, 40, MOL)])

        self.assertContains(response, "Please correct the duplicate data")
        self.assertFalse(Material.objects.filter(name="F8").exists())

    def test_a_compound_in_a_mixture_is_not_deleted(self):
        # Only admins delete materials (migration 0021), so a superuser rather than the curator.
        self.client.force_login(User.objects.create_superuser("admin", password="pw"))
        url = reverse("admin:repo_material_delete", args=[self.cb5.pk])

        self.assertContains(self.client.get(url), "protected related objects")
        self.client.post(url, {"post": "yes"})
        self.assertTrue(Material.objects.filter(pk=self.cb5.pk).exists())

    def test_a_mixture_with_components_cannot_become_a_compound(self):
        rows = list(self.f7.components.all())
        data = {
            "kind": COMPOUND, "name": "F7", "aliases": "", "smiles": "CCO", "notes": "",
            "composition_status": LISTED, "other_components": "",
            "components-TOTAL_FORMS": "2", "components-INITIAL_FORMS": "2",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        for i, row in enumerate(rows):
            data |= {
                f"components-{i}-id": row.pk, f"components-{i}-mixture": self.f7.pk,
                f"components-{i}-component": row.component_id, f"components-{i}-fraction": row.fraction,
                f"components-{i}-unit": row.unit,
            }
        response = self.client.post(reverse("admin:repo_material_change", args=[self.f7.pk]), data)

        self.assertContains(response, "Only a mixture has components.")
        self.f7.refresh_from_db()
        self.assertEqual(self.f7.kind, MIXTURE)

    def test_a_mixtures_page_says_it_has_no_structure(self):
        response = self.client.get(reverse("admin:repo_material_change", args=[self.f7.pk]))

        self.assertContains(response, "None: a mixture has no structure.")


class MixturePageTests(MixtureTestCase):
    """A mixture's page shows its composition, and a compound's page the mixtures it is in."""

    def page(self, material):
        return self.client.get(reverse("repo:material", args=[material.pk]))

    def link(self, material):
        """The link to material's page, as the pages write it: its LCR ID."""
        return f'<a href="{reverse("repo:material", args=[material.pk])}">{material.accession}</a>'

    def test_a_mixtures_page_lists_its_components_largest_first_each_linking_to_its_page(self):
        response = self.page(self.f7)

        self.assertContains(response, "<dd>Mixture</dd>", html=True)
        self.assertContains(response, f"{self.link(self.rm734)} RM734, 70 mol%")
        self.assertContains(response, f"{self.link(self.cb5)} 5CB, 30 mol%")
        text = response.content.decode()
        self.assertLess(text.index("70 mol%"), text.index("30 mol%"))

    def test_a_mixtures_page_has_no_drawing_formula_molar_mass_or_identifiers(self):
        response = self.page(self.f7)

        for absent in ("structure.svg", "Formula", "g/mol", "SMILES", "InChIKey"):
            self.assertNotContains(response, absent)

    def test_other_components_come_after_the_listed_ones_and_partly_known_says_so(self):
        f8 = Material.objects.create(
            kind=MIXTURE, name="F8", composition_status=PARTLY_KNOWN, other_components="A nematic host", listed=True
        )
        MixtureComponent.objects.create(mixture=f8, component=self.rm734, fraction=10, unit=WT)

        response = self.page(f8)

        self.assertContains(response, "Other components: A nematic host")
        self.assertContains(response, "Partly known: only what is listed here is known.")
        text = response.content.decode()
        self.assertLess(text.index("10 wt%"), text.index("Other components"))

    def test_a_mixture_whose_composition_is_not_disclosed_says_so(self):
        m5 = Material.objects.create(kind=MIXTURE, name="M5", composition_status=NOT_DISCLOSED, listed=True)

        self.assertContains(self.page(m5), "<dd>Not disclosed</dd>", html=True)

    def test_a_compounds_page_lists_the_mixtures_it_is_in_with_its_fraction(self):
        response = self.page(self.cb5)

        self.assertContains(response, "<h2>In mixtures</h2>", html=True)
        self.assertContains(response, f"{self.link(self.f7)} F7: 30 mol%")

    def test_a_compound_in_no_mixture_has_no_in_mixtures_section(self):
        lone = Material.objects.create(name="6CB", smiles="CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1", listed=True)

        self.assertNotContains(self.page(lone), "In mixtures")


class MixtureListAndSearchTests(MixtureTestCase):
    """Mixtures on the Materials page: listed without a drawing, and found by every search
    except a substructure."""

    def found(self, **params):
        """The materials the Materials page lists, read from its context, as in test_search."""
        return list(self.client.get(reverse("repo:materials"), params).context["page"].object_list)

    def test_the_list_shows_a_mixture_without_a_drawing(self):
        response = self.client.get(reverse("repo:materials"))

        self.assertContains(response, '<span class="result__mixture">Mixture</span>', count=1, html=True)
        self.assertContains(response, reverse("repo:structure", args=[self.rm734.pk]))
        self.assertNotContains(response, reverse("repo:structure", args=[self.f7.pk]))

    def test_a_mixtures_drawing_address_gives_a_404(self):
        response = self.client.get(reverse("repo:structure", args=[self.f7.pk]))

        self.assertEqual(response.status_code, 404)

    def test_searching_by_name_or_alias_finds_a_mixture(self):
        Material.objects.filter(pk=self.f7.pk).update(aliases="FMix-7")

        self.assertEqual(self.found(q="F7"), [self.f7])
        self.assertEqual(self.found(q="fmix"), [self.f7])

    def test_phase_and_temperature_searches_find_a_mixture_with_a_sequence(self):
        reference = Reference.objects.create(kind=Reference.Kind.ARTICLE, identifier="10.1000/f7")
        PhaseSequence.objects.create(
            material=self.f7, reference=reference, direction=PhaseSequence.Direction.COOLING,
            sequence="Iso 120 N 95 NF 40 Cr",
        )

        self.assertEqual(self.found(phase="NF"), [self.f7])
        self.assertEqual(self.found(phase="NF", tmin="90", tmax="100"), [self.f7])  # NF forms at 95

    def test_a_substructure_search_finds_compounds_only_and_says_so(self):
        response = self.client.get(reverse("repo:materials"), {"sub": "c1ccccc1"})

        self.assertEqual(list(response.context["page"].object_list), [self.cb5, self.rm734])
        self.assertContains(response, "compounds only, because mixtures have no structure")
