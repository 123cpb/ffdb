"""A reference's Add data page has a form for a mixture, with its components typed one per
line. Check shows the compound each line finds and, when a mixture with this name is already
in FFDB, asks whether it is that one. Save adds the mixture, or nothing for one already in
FFDB."""

from django.urls import reverse

from repo.forms import read_components
from repo.models import Material, MixtureComponent
from repo.tests.test_add_data import FIVE_CB, LOGIN, AddDataTestCase
from repo.tests.test_values import DIO, RM734

MIXTURE = Material.Kind.MIXTURE
LISTED = Material.CompositionStatus.LISTED
PARTLY_KNOWN = Material.CompositionStatus.PARTLY_KNOWN
NOT_DISCLOSED = Material.CompositionStatus.NOT_DISCLOSED
F7_LINES = "DIO, 70, mol%\nRM734, 30, mol%"


class MixtureFormTestCase(AddDataTestCase):
    """DIO (also called Aya_5B), RM734 and 5CB are compounds in FFDB, and there are no mixtures."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dio = Material.objects.create(name="DIO", aliases="Aya_5B", smiles=DIO)
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)

    def url(self):
        return reverse("repo:add_mixture", args=[self.article.pk])

    def send(self, components, button="check", follow=False, name="F7", status=LISTED, other="", notes="", same_as=None):
        """Post the mixture form with the Check or Save button, answering the same-name
        question only when same_as is given."""
        data = {"name": name, "composition_status": status, "components": components, "other_components": other,
                "notes": notes, button: ""}
        if same_as is not None:
            data["same_as"] = same_as
        return self.client.post(self.url(), data, follow=follow)

    def mixtures(self):
        return Material.objects.filter(kind=MIXTURE)

    def link(self, material):
        return f'<a href="{reverse("repo:material", args=[material.pk])}">{material.accession}</a>'


class ReadingTests(MixtureFormTestCase):
    def test_a_line_finds_its_compound_by_lcr_id_or_by_a_name_or_alias_as_names_are_compared(self):
        lines = read_components(f"{self.five_cb.accession}, 70, mol%\nrm-734\t20\tmol%\naya 5b, 10, Mol %")

        self.assertEqual(
            [(line.material, line.fraction, line.unit, line.problem) for line in lines],
            [(self.five_cb, 70, "mol%", ""), (self.rm734, 20, "mol%", ""), (self.dio, 10, "mol%", "")],
        )

    def test_a_name_with_a_comma_in_it_stays_whole(self):
        form_b = Material.objects.create(name="DIO, form B", smiles="CCO")

        [line] = read_components("DIO, form B, 30, wt%")

        self.assertEqual((line.material, line.fraction, line.unit), (form_b, 30, "wt%"))

    def test_each_kind_of_problem_line_says_why(self):
        for bad, problem in [
            ("DIO, 70 mol%", "Expected an LCR ID or name, a fraction and a unit, e.g. LCR-00128, 30, mol%."),
            (", 70, mol%", "Expected an LCR ID or name"),
            ("DIO, 70, %", "“%” isn't a unit: give mol% or wt%."),
            ("DIO, seventy, mol%", "“seventy” isn't a number."),
            ("LCR-09999, 70, mol%", "No material has the LCR ID LCR-09999."),
            ("RM2914, 30, mol%", "Nothing in FFDB is called RM2914. Add it first with the compound table"),
            ("DIO, 0, mol%", "A fraction is more than 0 and at most 100."),
            ("DIO, 100.1, mol%", "A fraction is more than 0 and at most 100."),
        ]:
            with self.subTest(bad):
                [line] = read_components(bad)

                self.assertIn(problem, line.problem)

    def test_a_name_that_fits_two_materials_asks_for_the_lcr_id(self):
        other = Material.objects.create(name="DIO-F", aliases="DIO", smiles="CCO")

        [line] = read_components("DIO, 70, mol%")

        self.assertEqual(line.problem, f"More than one material is called DIO ({self.dio}, {other}): give its LCR ID.")
        self.assertEqual(read_components(f"{other.accession}, 70, mol%")[0].material, other)

    def test_a_mixture_is_not_a_component(self):
        Material.objects.create(kind=MIXTURE, name="M5", composition_status=NOT_DISCLOSED)

        [line] = read_components("M5, 10, wt%")

        self.assertIn("is a mixture, and a component must be a compound", line.problem)

    def test_each_compound_is_given_once(self):
        lines = read_components(f"DIO, 60, mol%\n\n{self.dio.accession}, 40, mol%")

        self.assertEqual(lines[1].number, 3)  # the blank line is counted
        self.assertEqual(lines[1].problem, "Same compound as line 1: give each compound once.")


class CheckTests(MixtureFormTestCase):
    def test_check_shows_the_compound_each_line_finds_and_saves_nothing(self):
        response = self.send(F7_LINES)

        self.assertContains(response, self.link(self.dio), html=True)
        self.assertContains(response, reverse("repo:structure", args=[self.rm734.pk]))  # its drawing
        self.assertContains(response, "2 component lines, 0 with a problem. Nothing has been saved.")
        self.assertFalse(self.mixtures().exists())

    def test_a_line_with_a_problem_is_shown_and_save_saves_nothing(self):
        response = self.send("DIO, 70, mol%\nRM2914, 30, mol%", "save")

        self.assertEqual(response.status_code, 200)  # the form again, not a redirect
        self.assertContains(response, "Nothing is saved until every line reads")
        self.assertContains(response, "Not read: Nothing in FFDB is called RM2914.")
        self.assertFalse(self.mixtures().exists())

    def test_the_rules_across_the_components_refuse_the_mixture(self):
        for components, status, message in [
            ("DIO, 70, mol%\nRM734, 30, wt%", LISTED, "Give every fraction in the same unit: all mol% or all wt%."),
            ("DIO, 70, mol%\nRM734, 31, mol%", LISTED, "The fractions add up to 101 mol%, more than 100."),
            ("DIO, 70, mol%", NOT_DISCLOSED, "Not disclosed means no components and no other components"),
            ("", PARTLY_KNOWN, "Give the components in FFDB, describe them under Other components, or choose Not"),
        ]:
            with self.subTest(components=components, status=status):
                response = self.send(components, "save", status=status)

                self.assertContains(response, message)
                self.assertFalse(self.mixtures().exists())

    def test_a_mixture_needs_a_name_and_its_composition_chosen(self):
        response = self.send(F7_LINES, "save", name="", status="")

        self.assertContains(response, "A mixture needs a name: it has no structure to tell it apart.")
        self.assertContains(response, "This field is required.", count=1)  # the composition, not also the name
        self.assertFalse(self.mixtures().exists())


class SaveTests(MixtureFormTestCase):
    def test_save_adds_the_mixture_and_goes_back_to_the_add_data_page_with_its_lcr_id(self):
        response = self.send(
            f"DIO, 70, mol%\n{self.rm734.accession}\t20\tmol%", "save", follow=True,
            status=PARTLY_KNOWN, other="A nematic host, 10 mol%", notes="Table 1.",
        )

        f7 = self.mixtures().get()
        self.assertRedirects(response, self.add_page(self.article))
        self.assertEqual(
            (f7.name, f7.composition_status, f7.other_components, f7.notes, f7.inchikey),
            ("F7", PARTLY_KNOWN, "A nematic host, 10 mol%", "Table 1.", ""),
        )
        self.assertEqual(
            [(row.component, row.fraction, row.unit) for row in f7.components.all()],
            [(self.dio, 70, "mol%"), (self.rm734, 20, "mol%")],
        )
        self.assertEqual(self.messages(response), [f"Added the mixture F7 as {f7.accession}."])

    def test_a_mixture_whose_composition_isnt_disclosed_is_saved_without_components(self):
        self.send("", "save", name="M5", status=NOT_DISCLOSED)

        m5 = self.mixtures().get(name="M5")
        self.assertFalse(m5.components.exists())

    def test_the_add_data_page_has_the_form(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, "<h2>Add a mixture</h2>", html=True)
        self.assertContains(response, f'action="{self.url()}"')

    def test_a_logged_out_visitor_cant_save(self):
        self.client.logout()

        response = self.send(F7_LINES, "save")

        self.assertRedirects(response, f"{LOGIN}?next={self.url()}")
        self.assertFalse(self.mixtures().exists())


class SameNameTests(MixtureFormTestCase):
    """F7, a mixture of DIO and RM734 at 70 and 30 mol%, is already in FFDB."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.f7 = Material.objects.create(kind=MIXTURE, name="F7")
        MixtureComponent.objects.create(mixture=cls.f7, component=cls.dio, fraction=70, unit="mol%")
        MixtureComponent.objects.create(mixture=cls.f7, component=cls.rm734, fraction=30, unit="mol%")

    def test_check_lists_the_mixtures_with_this_name_with_their_compositions_and_asks(self):
        response = self.send(F7_LINES)

        self.assertEqual(response.context["form"].same_name, [self.f7])
        self.assertContains(response, "FFDB already has a mixture called F7.")
        self.assertContains(response, f"{self.link(self.dio)} DIO, 70 mol%")  # as the mixture's page lists it
        self.assertContains(response, "No, a different mixture with the same name")

    def test_save_needs_an_answer(self):
        response = self.send(F7_LINES, "save")

        self.assertContains(response, "Say whether this is one of them or a different mixture.")
        self.assertEqual(self.mixtures().count(), 1)

    def test_saying_it_is_that_mixture_adds_nothing_and_names_it(self):
        response = self.send(F7_LINES, "save", follow=True, same_as=str(self.f7.pk))

        self.assertRedirects(response, self.add_page(self.article))
        self.assertEqual(self.mixtures().count(), 1)
        self.assertEqual(MixtureComponent.objects.count(), 2)
        self.assertEqual(self.messages(response), [f"F7 is already in FFDB as {self.f7.accession}, so nothing was added."])

    def test_saying_it_is_a_different_mixture_adds_a_second_f7_with_its_own_lcr_id(self):
        response = self.send("5CB, 100, wt%", "save", follow=True, same_as="different")

        second = self.mixtures().exclude(pk=self.f7.pk).get(name="F7")
        self.assertEqual([(row.component, row.unit) for row in second.components.all()], [(self.five_cb, "wt%")])
        self.assertEqual(self.f7.components.count(), 2)  # the first F7 is as it was
        self.assertEqual(self.messages(response), [f"Added the mixture F7 as {second.accession}."])

    def test_a_name_written_differently_or_an_alias_still_asks(self):
        Material.objects.filter(pk=self.f7.pk).update(aliases="FMix-7")

        for name in ["f-7", "FMIX7"]:
            with self.subTest(name):
                self.assertEqual(self.send(F7_LINES, name=name).context["form"].same_name, [self.f7])

    def test_a_compound_with_the_name_doesnt_ask(self):
        Material.objects.create(name="M5", smiles="CCO")

        self.send("", "save", name="M5", status=NOT_DISCLOSED)

        self.assertTrue(self.mixtures().filter(name="M5").exists())
