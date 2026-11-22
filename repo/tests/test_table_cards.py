"""Make cards: a pasted compound table becomes compound cards, a card for each line, with its
material and its sequence as the card's phase sequence, ready for values. Nothing is made while a line
has a problem. Without JavaScript the table's form brings the page back with the cards; cards.js
asks views.make_cards for them instead and puts them in the page. Here the forms are posted as
each would send them."""

from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from repo.forms import RUN_PHASES, CompoundTableForm, read_sequence, sequence_steps, steps_text
from repo.models import Material, Phase, PhaseSequence, Series
from repo.phases import PhaseSequenceError
from repo.tests.test_add_data import FIVE_CB, LOGIN
from repo.tests.test_compound_card import CardTestCase, visible
from repo.tests.test_value_blocks import PS_BLOCK
from repo.tests.test_values import DIO

# RM734's run as the card writes it and as boxes hold it: cooling, from Iso down to the solid.
RM734_BOXES = {"phase_0": "Iso", "t_1": "187.9", "phase_1": "N", "t_2": "132.7", "phase_2": "NF", "solid": "Cr", "t_solid": "70"}
# A cooling run of nine phases: one more than a card's phase sequence has steps.
NINE_PHASES = "Iso 100 N 90 SmA 80 SmC 70 SmB 60 SmE 50 SmX 40 NF 30 NTB"


class SequenceStepsTests(TestCase):
    """The phase vocabulary is in the database, so these need it."""

    def steps(self, text, direction):
        synonyms = Phase.synonym_map()
        return sequence_steps(text, direction, read_sequence(text, direction, synonyms), synonyms)

    def test_a_summary_is_turned_round_and_its_solid_goes_in_the_solid_box_by_its_code(self):
        self.assertEqual(
            self.steps("K 139.8 NF 132.7 N 187.9 I", "summary"),
            {"phase_0": "I", "t_1": "187.9", "phase_1": "N", "t_2": "132.7", "phase_2": "NF", "solid": "Cr", "t_solid": "139.8"},
        )

    def test_a_summarys_melting_point_can_be_missing(self):
        for text in ["K ? N 24 I", "K N 24 I"]:
            with self.subTest(text):
                self.assertEqual(
                    self.steps(text, "summary"), {"phase_0": "I", "t_1": "24", "phase_1": "N", "solid": "Cr", "t_solid": ""}
                )

    def test_a_cooling_run_keeps_its_order_and_its_solid_comes_off_the_end(self):
        self.assertEqual(self.steps("Iso 187.9 N 132.7 NF 70 K", "cooling"), RM734_BOXES)

    def test_a_heating_run_and_one_not_stated_written_rising_are_turned_round(self):
        for direction in ["heating", "unspecified"]:
            with self.subTest(direction):
                self.assertEqual(self.steps("Cr 70 NF 132.7 N 187.9 Iso", direction), RM734_BOXES)

    def test_labels_and_temperatures_are_kept_as_typed(self):
        self.assertEqual(
            self.steps("Iso 143.0 N 80.50 Nf", "cooling"),
            {"phase_0": "Iso", "t_1": "143.0", "phase_1": "N", "t_2": "80.50", "phase_2": "Nf", "solid": "", "t_solid": ""},
        )

    def test_a_run_of_one_phase(self):
        self.assertEqual(self.steps("N", "unspecified"), {"phase_0": "N", "solid": "", "t_solid": ""})

    def test_the_boxes_write_a_sequence_with_the_same_transitions(self):
        # The same transitions, not always the same text: a card writes a run not stated from the
        # top down (steps_text()), so "Cr 70 NF 132.7 N 187.9 Iso" comes back the other way round.
        for text, direction in [
            ("K 139.8 NF 132.7 N 187.9 I", "summary"),
            ("K ? N 24 I", "summary"),
            ("Iso 187.9 N 132.7 NF 70 K", "cooling"),
            ("Cr 70 NF 132.7 N 187.9 Iso", "heating"),
            ("Cr 70 NF 132.7 N 187.9 Iso", "unspecified"),
            ("g 30 SmA 60 N 90 Iso", "summary"),
        ]:
            with self.subTest(text=text, direction=direction):
                boxes = self.steps(text, direction)
                steps = [(None, boxes["phase_0"])] + [
                    (boxes[f"t_{i}"], boxes[f"phase_{i}"]) for i in range(1, RUN_PHASES) if f"phase_{i}" in boxes
                ]
                written = steps_text(direction, steps, boxes["solid"], boxes["t_solid"])
                self.assertEqual(read_sequence(written, direction).transitions, read_sequence(text, direction).transitions)

    def test_more_phases_than_a_cards_sequence_has_steps_are_refused(self):
        with self.assertRaisesMessage(PhaseSequenceError, f"has 9 phases, not counting the solid, and a card's phase sequence has room for {RUN_PHASES}"):
            self.steps(NINE_PHASES, "cooling")


class MakeCardsTestCase(CardTestCase):
    """RM734 and Aya_5B (DIO) are in FFDB, with nothing yet from the article."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.aya = Material.objects.create(name="Aya_5B", smiles=DIO, listed=True)

    def setUp(self):
        super().setUp()
        self.make_cards = reverse("repo:make_cards", args=[self.article.pk])

    def three_lines(self):
        """Three lines from a paper, as summaries: a new compound, RM734 by its LCR ID, and DIO by
        its SMILES, which FFDB has as Aya_5B."""
        return "\n".join([
            "Label\tSMILES\tPhases",
            f"5CB\t{FIVE_CB}\tK 24 N 35.3 I",
            f"RM734\t{self.rm734.accession}\tK 139.8 NF 132.7 N 187.9 I",
            f"DIO\t{DIO}\tK 68.8 NF 84.5 N 174 I",
        ])

    def table_data(self, table, direction="summary", rate="", method=""):
        return {"table": table, "direction": direction, "rate": rate, "method": method}

    def make(self, table, **fields):
        """Press Make cards on a page without JavaScript."""
        return self.client.post(self.add_page(self.article), {**self.table_data(table, **fields), "cards": ""})

    def make_here(self, table, start=0, **fields):
        """Press Make cards with JavaScript: cards.js sends the table to make_cards."""
        return self.client.post(f"{self.make_cards}?start={start}", self.table_data(table, **fields))

    def assert_nothing_saved(self):
        self.assertEqual(set(Material.objects.all()), {self.rm734, self.aya})
        self.assertFalse(PhaseSequence.objects.exists())

    def assert_no_cards(self, response):
        """The page came back with its one empty card, as it is before anything is sent."""
        cards = response.context["cards"]
        self.assertIsNone(cards.data)
        self.assertEqual(cards.total, 1)


class WithoutJavaScriptTests(MakeCardsTestCase):
    def test_each_line_becomes_a_card_with_its_material_and_its_sequence(self):
        response = self.make(self.three_lines(), method="DSC", rate="5")

        cards = response.context["cards"]
        self.assertEqual(cards.total, 3)
        self.assertEqual(
            [(card.material_form["material"].value(), card.material_form["smiles"].value(), card.material_form["name"].value())
             for card in cards.cards],
            [(None, FIVE_CB, "5CB"), (self.rm734.accession, None, "RM734"), (None, DIO, "DIO")],
        )
        # Each as Check shows it: the material found or new, and its sequence read, with nothing wrong.
        self.assertEqual([card.material_form.found for card in cards.cards], [None, self.rm734, self.aya])
        self.assertEqual(cards.cards[0].material_form.new.name, "5CB")
        self.assertEqual(
            [(card.run.sequence, card.run.direction, card.run.rate_k_per_min, card.run.method) for card in cards.cards],
            [
                ("Cr 24 N 35.3 I", "summary", 5.0, "DSC"),
                ("Cr 139.8 NF 132.7 N 187.9 I", "summary", 5.0, "DSC"),
                ("Cr 68.8 NF 84.5 N 174 I", "summary", 5.0, "DSC"),
            ],
        )
        self.assertEqual([card.problems for card in cards.cards], [[], [], []])
        self.assertTrue(all(card.sequence_rows for card in cards.cards))  # each sequence's ribbon
        self.assertEqual(
            self.messages(response), ["Made 3 cards from the table: add their values, then Check and Save the cards."]
        )
        self.assertIsNone(response.context["form"]["table"].value())  # the table is emptied
        page = visible(response)
        self.assertContains(page, 'name="card-TOTAL" value="3"')
        self.assertContains(page, f'name="card-2-smiles" value="{DIO}"')
        self.assertContains(page, "Compound 3")
        self.assert_nothing_saved()

    def test_the_cards_are_saved_with_a_value_added_to_one(self):
        # The cards as the page sends them back, with a block of values typed on RM734's.
        form = CompoundTableForm(self.table_data(self.three_lines()), reference=self.article, user=self.curator)
        data = form.cards_data()
        data.update({f"card-1-block-0-{name}": value for name, value in PS_BLOCK.items()})

        response = self.client.post(self.page, {**data, "save": ""})

        self.assertRedirects(response, self.add_page(self.article))
        self.assertEqual(
            sorted(PhaseSequence.objects.values_list("material__name", "sequence")),
            [("5CB", "Cr 24 N 35.3 I"), ("Aya_5B", "Cr 68.8 NF 84.5 N 174 I"), ("RM734", "Cr 139.8 NF 132.7 N 187.9 I")],
        )
        self.assertEqual(Series.objects.get().material, self.rm734)
        self.assertEqual(Material.objects.get(pk=self.aya.pk).aliases, "")  # held until an admin approves the save

    def test_a_sequence_ffdb_already_has_from_this_reference_is_left_off_the_card(self):
        PhaseSequence.objects.create(
            material=self.rm734, reference=self.article, sequence="K 139.8 NF 132.7 N 187.9 I", approved=True
        )
        form = CompoundTableForm(self.table_data(self.three_lines()), reference=self.article, user=self.curator)
        data = form.cards_data()

        self.assertNotIn("card-1-run-direction", data)  # RM734's card: its material and label, but no sequence
        self.assertEqual((data["card-1-material"], data["card-1-name"]), (self.rm734.accession, "RM734"))

        data.update({f"card-1-block-0-{name}": value for name, value in PS_BLOCK.items()})
        self.client.post(self.page, {**data, "save": ""})

        self.assertEqual(self.rm734.phase_sequences.count(), 1)  # not added again
        self.assertEqual(Series.objects.get().material, self.rm734)

    def test_a_line_without_a_sequence_makes_a_card_with_no_sequence_yet(self):
        response = self.make(f"5CB\t{FIVE_CB}", direction="")

        [card] = response.context["cards"].cards
        self.assertEqual(card.material_form["smiles"].value(), FIVE_CB)
        self.assertIsNone(card.run)
        self.assertEqual(card.problems, ["Add a phase sequence or at least one block of values."])

    def test_a_table_with_a_bad_line_makes_no_cards_and_names_the_line(self):
        table = self.three_lines().replace("K 139.8 NF", "K 139.8 NFF")

        response = self.make(table)

        self.assert_no_cards(response)
        problems = response.context["cards_problems"]
        self.assertEqual(problems[0], CompoundTableForm.NO_CARDS_LINES)
        self.assertEqual(problems[1:], ["Line 3: Phase sequence: Unknown phase label: NFF. "
                                        "Use a label from the phase vocabulary, or add it there first."])
        self.assertContains(response, CompoundTableForm.NO_CARDS_LINES)
        self.assertEqual(response.context["form"]["table"].value(), table)  # kept, to fix
        self.assertEqual(self.messages(response), [])
        self.assert_nothing_saved()

    def test_a_table_that_cant_be_read_makes_no_cards(self):
        response = self.make(self.three_lines(), direction="")

        self.assertEqual(response.context["cards_problems"], [CompoundTableForm.NO_CARDS])
        self.assertEqual(response.context["form"].errors["table"], [CompoundTableForm.NO_DIRECTION])
        self.assert_no_cards(response)

    def test_a_sequence_a_card_cant_hold_makes_no_cards(self):
        table = f"5CB\t{FIVE_CB}\t{NINE_PHASES}"

        response = self.make(table, direction="cooling")

        self.assertEqual(response.context["lines"][0].problem, "")
        self.assertEqual(
            response.context["cards_problems"][1:],
            [f"Line 1: The sequence has 9 phases, not counting the solid, and a card's phase sequence has room for {RUN_PHASES}."],
        )
        self.assert_no_cards(response)

    def test_the_tables_form_has_the_button(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(
            response, f'<button type="submit" name="cards" data-make-cards data-url="{self.make_cards}">'
        )


class WithJavaScriptTests(MakeCardsTestCase):
    def test_the_cards_come_numbered_on_from_the_pages_cards(self):
        response = self.make_here(self.three_lines(), start=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([card.index for card in response.context["cards"]], [2, 3, 4])
        self.assertContains(response, f'name="card-2-smiles" value="{FIVE_CB}"')
        self.assertContains(response, f'name="card-3-material" value="{self.rm734.accession}"')
        self.assertContains(response, "Compound 5")
        self.assertNotContains(response, 'name="card-1-')
        self.assertNotContains(response, 'name="card-TOTAL"')  # cards.js counts them
        self.assert_nothing_saved()

    def test_a_table_that_cant_make_them_gets_why_with_status_422(self):
        response = self.make_here(self.three_lines().replace("K 139.8 NF", "K 139.8 NFF"), start=1)

        self.assertEqual(response.status_code, 422)
        self.assertContains(response, CompoundTableForm.NO_CARDS_LINES, status_code=422)
        self.assertContains(response, "Line 3: Phase sequence: Unknown phase label: NFF.", status_code=422)
        self.assertContains(response, "Problem: Phase sequence", status_code=422)  # the table's lines, as Check shows them

    def test_more_cards_than_a_page_can_have_are_refused(self):
        response = self.make_here(self.three_lines(), start=99)

        self.assertContains(
            response, "No cards made: a page can have at most 100 cards, and these 3 would make it 102.", status_code=422
        )

    def test_start_must_be_a_card_index(self):
        for start in ["", "x", "-1", "101"]:
            with self.subTest(start):
                response = self.client.post(f"{self.make_cards}?start={start}", self.table_data(self.three_lines()))
                self.assertEqual(response.status_code, 404)

    def test_curators_only_and_post_only(self):
        address = f"{self.make_cards}?start=0"
        self.assertEqual(self.client.get(address).status_code, 405)
        self.client.force_login(User.objects.create_user("reader", password="a-test-password"))
        self.assertEqual(self.client.post(address, self.table_data(self.three_lines())).status_code, 403)
        self.client.logout()
        login = f"{LOGIN}?{urlencode({'next': address})}"
        self.assertRedirects(self.client.post(address, self.table_data(self.three_lines())), login)

    def test_the_script_sends_the_button_to_its_address(self):
        with open(finders.find("repo/cards.js"), encoding="utf-8") as script:
            text = script.read()
        self.assertIn("[data-make-cards]", text)
        self.assertIn("makeButton.dataset.url", text)
