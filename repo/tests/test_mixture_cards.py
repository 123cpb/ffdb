"""A compound card's material can be a new mixture: its name in the card's label box, its
composition and its components, read by Add a mixture's form with its rules and messages, and the
question whether it is one of the mixtures FFDB already has with that name. Its runs and values go
on the card as a compound's do, and one Save adds every card."""

from django.contrib.staticfiles import finders
from django.urls import reverse

from repo.forms import CardMaterialForm, MixtureForm
from repo.models import Material, PhaseSequence
from repo.tests.test_add_data import FIVE_CB
from repo.tests.test_compound_card import FIVE_CB_HEATING, visible
from repo.tests.test_several_cards import DIO_HEATING, CardsTestCase

MIXTURE = Material.Kind.MIXTURE
LISTED = Material.CompositionStatus.LISTED
NOT_DISCLOSED = Material.CompositionStatus.NOT_DISCLOSED
# F7's components: DIO, which FFDB calls Aya_5B here, and RM734.
F7_LINES = "Aya_5B, 70, mol%\nRM734, 30, mol%"
# A card's boxes for F7 as a new mixture: its label is its name.
F7 = {"name": "F7", "composition_status": LISTED, "components": F7_LINES}


class MixtureCardTestCase(CardsTestCase):
    """RM734 and Aya_5B (DIO) are compounds in FFDB, with nothing yet from the article."""

    def mixtures(self):
        return list(Material.objects.filter(kind=MIXTURE))

    def material_form(self, response, index=0):
        return self.cards(response)[index].material_form

    def check_material(self, **boxes):
        """The check as the curator types, for the first card's material."""
        address = reverse("repo:check_card_material", args=[self.article.pk]) + "?card=0"
        return self.client.post(address, self.card_data([DIO_HEATING], **boxes))


class SaveTests(MixtureCardTestCase):
    def test_a_new_mixture_and_a_compound_are_saved_in_one_save(self):
        response = self.send_cards(
            {"runs": [DIO_HEATING], **F7}, {"runs": [FIVE_CB_HEATING], "smiles": FIVE_CB, "name": "5CB"},
            button="save", follow=True,
        )

        self.assertRedirects(response, self.add_page(self.article))
        [f7] = self.mixtures()
        five_cb = Material.objects.get(name="5CB")
        self.assertEqual((f7.name, f7.composition_status), ("F7", LISTED))
        self.assertEqual(
            sorted((row.component.name, row.fraction, row.unit) for row in f7.components.all()),
            [("Aya_5B", 70, "mol%"), ("RM734", 30, "mol%")],
        )
        self.assertEqual(
            sorted(PhaseSequence.objects.values_list("material__name", "sequence")),
            [("5CB", "Cr 24 N 35.3 Iso"), ("F7", "Cr 68.8 NF 84.5 N 174 Iso")],
        )
        self.assertEqual(
            self.messages(response),
            [f"Added F7 as {f7.accession}, with 1 run.", f"Added 5CB as {five_cb.accession}, with 1 run."],
        )

    def test_a_mixture_not_disclosed_has_no_components(self):
        self.send_cards(
            {"runs": [DIO_HEATING], "name": "MLC-2132", "composition_status": NOT_DISCLOSED, "notes": "Commercial."},
            button="save",
        )

        [mixture] = self.mixtures()
        self.assertEqual((mixture.name, mixture.notes, mixture.components.count()), ("MLC-2132", "Commercial.", 0))


class CheckTests(MixtureCardTestCase):
    def test_check_shows_the_new_mixture_and_its_component_lines(self):
        response = self.send_cards({"runs": [DIO_HEATING], **F7})

        form = self.material_form(response)
        self.assertTrue(form.is_mixture)
        self.assertEqual((form.found, form.new.name), (None, "F7"))
        page = visible(response)
        self.assertContains(page, "New mixture, named F7")
        self.assertContains(page, "2 component lines, 0 with a problem.")
        self.assertContains(page, '<details class="card-mixture" open>')
        self.assertEqual(self.mixtures(), [])

    def test_a_mixture_is_read_by_add_a_mixtures_rules_with_its_messages(self):
        for boxes, box, problem in [
            ({**F7, "components": "Aya_5B, 70, mol%\nRM-734X, 30, mol%"}, "components",
             "Nothing is saved until every line reads: fix or delete the lines marked below."),
            ({**F7, "composition_status": NOT_DISCLOSED}, "__all__",
             "Not disclosed means no components and no other components: remove them, or choose Partly known."),
            ({"name": "F7", "components": F7_LINES}, "composition_status", "This field is required."),
            ({**F7, "name": ""}, "name", "A mixture needs a name: it has no structure to tell it apart."),
        ]:
            with self.subTest(problem):
                response = self.send_cards({"runs": [DIO_HEATING], **boxes}, button="save")

                self.assertEqual(self.material_form(response).mixture.errors[box], [problem])
                self.assertContains(response, problem)
                self.assertEqual(self.mixtures(), [])

    def test_a_line_that_doesnt_read_is_marked(self):
        response = self.send_cards({"runs": [DIO_HEATING], **F7, "components": "Aya_5B, 70, mol%\nRM-734X, 30, mol%"})

        self.assertContains(response, "Not read: Nothing in FFDB is called RM-734X.")

    def test_the_check_as_the_curator_types_lists_the_mixtures_problems_and_lines(self):
        answer = self.check_material(**{**F7, "composition_status": ""})

        self.assertContains(answer, "<li>Its composition: This field is required.</li>", html=True)
        self.assertContains(answer, "2 component lines, 0 with a problem.")

    def test_a_mixtures_name_problems_name_the_label_box(self):
        answer = self.check_material(**{**F7, "name": ""})

        self.assertContains(
            answer, "<li>Label, as the reference prints it: A mixture needs a name: it has no structure to tell it apart.</li>",
            html=True,
        )


class WhichMaterialTests(MixtureCardTestCase):
    def test_a_smiles_and_a_mixture_are_refused_together(self):
        response = self.send_cards({"runs": [DIO_HEATING], **F7, "smiles": FIVE_CB})

        self.assertEqual(self.material_form(response).non_field_errors(), [CardMaterialForm.NOT_MORE_THAN_ONE])

    def test_a_label_alone_isnt_a_mixture_and_shows_no_mixtures_problems(self):
        response = self.send_cards({"runs": [DIO_HEATING], "name": "F7"})

        form = self.material_form(response)
        self.assertFalse(form.is_mixture)
        self.assertFalse(form.mixture.is_bound)  # drawn empty, and never checked
        self.assertEqual(form.non_field_errors(), [CardMaterialForm.NO_MATERIAL])
        self.assertContains(response, '<details class="card-mixture">')
        self.assertNotContains(response, "This field is required.")

    def test_two_new_mixtures_with_one_name_on_one_page_are_one_material(self):
        response = self.send_cards(
            {"runs": [DIO_HEATING], **F7}, {"runs": [FIVE_CB_HEATING], **F7, "name": "f-7"}, button="save"
        )

        self.assertEqual(
            self.cards(response)[1].problems, ["Same material as compound 1: its runs and values belong on one card."]
        )
        self.assertEqual(self.mixtures(), [])


class SameNameTests(MixtureCardTestCase):
    """FFDB already has a mixture called F7, whose composition isn't disclosed."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.old_f7 = Material.objects.create(kind=MIXTURE, name="F7", composition_status=NOT_DISCLOSED)

    def test_add_a_mixtures_form_finds_mixtures_of_its_name_with_a_prefix(self):
        self.assertEqual(MixtureForm({"card-0-name": "f-7"}, prefix="card-0").same_name, [self.old_f7])

    def test_check_asks_whether_it_is_the_mixture_ffdb_has(self):
        response = self.send_cards({"runs": [DIO_HEATING], **F7})

        self.assertContains(response, "FFDB already has a mixture called F7.")
        self.assertContains(response, 'name="card-0-same_as"', count=2)  # yes, or a different mixture

    def test_save_waits_for_the_answer(self):
        response = self.send_cards({"runs": [DIO_HEATING], **F7}, button="save")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.material_form(response).mixture.errors["same_as"], ["Say whether this is one of them or a different mixture."]
        )
        self.assertEqual(self.mixtures(), [self.old_f7])
        self.assertFalse(PhaseSequence.objects.exists())

    def test_a_different_mixture_with_the_same_name_is_added(self):
        self.send_cards({"runs": [DIO_HEATING], **F7, "same_as": "different"}, button="save")

        new_f7 = Material.objects.get(kind=MIXTURE, name="F7", composition_status=LISTED)
        self.assertEqual(new_f7.components.count(), 2)
        self.assertEqual(PhaseSequence.objects.get().material, new_f7)

    def test_the_mixture_ffdb_has_gets_the_run_and_nothing_is_added(self):
        response = self.send_cards({"runs": [DIO_HEATING], **F7, "same_as": str(self.old_f7.pk)}, button="save", follow=True)

        self.assertEqual(self.mixtures(), [self.old_f7])
        self.assertEqual(self.old_f7.components.count(), 0)  # what was typed for a new one isn't saved
        self.assertEqual(PhaseSequence.objects.get().material, self.old_f7)
        self.assertEqual(self.messages(response), [f"Saved 1 run for {self.old_f7}."])

    def test_the_question_is_asked_as_the_curator_types_and_keeps_its_answer(self):
        answer = self.check_material(**F7, same_as=str(self.old_f7.pk))

        self.assertContains(answer, "FFDB already has a mixture called F7.")
        self.assertEqual(answer.context["form"].mixture["same_as"].value(), str(self.old_f7.pk))
        self.assertEqual(answer.context["form"].found, self.old_f7)


class PageTests(MixtureCardTestCase):
    def test_an_empty_card_has_the_mixtures_boxes_closed_and_no_question(self):
        page = visible(self.client.get(self.add_page(self.article)))

        self.assertContains(page, '<details class="card-mixture">')
        self.assertContains(page, 'name="card-0-composition_status"', count=3)  # one radio for each status
        self.assertContains(page, 'name="card-0-components"')
        self.assertNotContains(page, 'name="card-0-same_as"')

    def test_the_composition_script_is_on_every_page_with_a_mixtures_boxes(self):
        pages = [
            self.add_page(self.article),
            reverse("repo:add_compound", args=[self.article.pk]),
            reverse("repo:add_mixture", args=[self.article.pk]),
        ]
        for address in pages:
            with self.subTest(address):
                response = self.client.get(address)
                self.assertContains(response, "data-composition-details")
                self.assertContains(response, "repo/composition.js")
        with open(finders.find("repo/composition.js"), encoding="utf-8") as script:
            self.assertIn('"not_disclosed"', script.read())
