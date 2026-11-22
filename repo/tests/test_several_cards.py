"""Several compound cards on one page, with one Save: every card filled in is saved in one
transaction, or none while any card has a problem, which shows on its card. The same material on
two cards is refused, a card left empty or removed is ignored, and a page has at most 100 cards.
Runs and blocks of values can be added to a card and removed from it. cards.js adds and removes
them in the browser; here the page is posted as it would send them."""

from django.contrib.staticfiles import finders
from django.urls import reverse

from repo.forms import CardMaterialForm, CompoundCards
from repo.models import Material, PhaseSequence, Series
from repo.tests.test_add_data import FIVE_CB, WAITING
from repo.tests.test_compound_card import FIVE_CB_HEATING, RM734_COOLING, CardTestCase, run_fields, visible
from repo.tests.test_value_blocks import PS_BLOCK
from repo.tests.test_values import DIO, RM734

DIO_HEATING = run_fields("heating", "Iso", ("174", "N"), ("84.5", "NF"), solid="Cr", t_solid="68.8")
# 5CB's heating run with a typo in a phase label.
TYPO = run_fields("heating", "Iso", ("35.3", "NFF"), solid="Cr", t_solid="24")
UNKNOWN_LABEL = "Unknown phase label: NFF."
# The prompt's check: RM734 with a phase sequence and a value, DIO, which FFDB still calls Aya_5B,
# with a sequence, and a new compound, 5CB, with a sequence.
THREE_CARDS = [
    {"run": RM734_COOLING, "blocks": [PS_BLOCK], "material": "RM734"},
    {"run": DIO_HEATING, "material": "Aya_5B", "name": "DIO"},
    {"run": FIVE_CB_HEATING, "smiles": FIVE_CB, "name": "5CB"},
]


class CardsTestCase(CardTestCase):
    """RM734 and Aya_5B (DIO) are in FFDB, with nothing yet from the article."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.aya = Material.objects.create(name="Aya_5B", smiles=DIO)

    def send_cards(self, *cards, button="check", follow=False, total=None):
        """Post a page of cards with the Check or Save button. Each card is a dict of
        card_data()'s arguments: run, blocks and its material boxes, removed among them. total
        is the page's count of cards, which is how many are sent unless it is given."""
        data = {CompoundCards.TOTAL: str(len(cards) if total is None else total), button: ""}
        for index, card in enumerate(cards):
            data.update(self.card_data(index=index, **card))
        return self.client.post(self.page, data, follow=follow)

    def cards(self, response):
        return response.context["cards"].cards

    def assert_nothing_saved(self):
        self.assertEqual(set(Material.objects.all()), {self.rm734, self.aya})
        self.assertEqual(Material.objects.get(pk=self.aya.pk).aliases, "")
        self.assertFalse(PhaseSequence.objects.exists())
        self.assertFalse(Series.objects.exists())


class OneSaveTests(CardsTestCase):
    def test_save_adds_three_cards_with_a_message_for_each(self):
        response = self.send_cards(*THREE_CARDS, button="save", follow=True)

        self.assertRedirects(response, self.add_page(self.article))
        five_cb = Material.objects.get(name="5CB")
        self.assertEqual(
            self.messages(response),
            [
                f"Saved a phase sequence and 1 block of values for {self.rm734}.",
                f"Saved a phase sequence for {self.aya}. DIO is held with the save, to be kept as one of its aliases "
                "if an admin approves it.",
                f"Added 5CB as {five_cb.accession}, with a phase sequence.",
                WAITING,
            ],
        )
        self.assertEqual(
            sorted(PhaseSequence.objects.values_list("material__name", "sequence")),
            [("5CB", "Cr 24 N 35.3 Iso"), ("Aya_5B", "Cr 68.8 NF 84.5 N 174 Iso"), ("RM734", "Iso 187.9 N 132.7 NF 70 Cr")],
        )
        self.assertEqual(Series.objects.get().material, self.rm734)
        self.assertEqual(Material.objects.get(pk=self.aya.pk).aliases, "")  # held until an admin approves the save

    def test_a_typo_on_the_third_card_saves_nothing_and_shows_on_that_card_only(self):
        cards = [*THREE_CARDS[:2], {**THREE_CARDS[2], "run": TYPO}]

        response = self.send_cards(*cards, button="save")

        self.assert_nothing_saved()
        self.assertContains(response, "Nothing has been saved: see the problems on compound 3.")
        self.assertContains(response, UNKNOWN_LABEL, count=1)
        first, second, third = self.cards(response)
        self.assertIn(UNKNOWN_LABEL, third.run_form.non_field_errors()[0])
        for card in [first, second]:
            with self.subTest(card.number):
                self.assertEqual(card.problems, [])
                self.assertFalse(card.material_form.errors or card.run_form.errors or any(card.block_forms.errors))

    def test_the_same_material_on_two_cards_is_refused_on_the_later_card(self):
        # The first card gives RM734 by name; the later one by its LCR ID, or by its structure.
        for later in [{"material": self.rm734.accession}, {"smiles": RM734}]:
            with self.subTest(later):
                response = self.send_cards(THREE_CARDS[0], {"run": RM734_COOLING, **later}, button="save")

                self.assert_nothing_saved()
                first, second = self.cards(response)
                self.assertEqual(first.problems, [])
                self.assertEqual(second.problems, ["Same material as compound 1: its phase sequence and values belong on one card."])
                self.assertContains(response, "Nothing has been saved: see the problems on compound 2.")

    def test_a_new_structure_on_two_cards_is_the_same_material_however_it_is_written(self):
        response = self.send_cards(
            {"run": FIVE_CB_HEATING, "smiles": FIVE_CB, "name": "5CB"},
            {"run": FIVE_CB_HEATING, "smiles": "N#Cc1ccc(-c2ccc(CCCCC)cc2)cc1", "name": "K15"},
            button="save",
        )

        self.assertContains(response, "Same material as compound 1: its phase sequence and values belong on one card.")
        self.assert_nothing_saved()

    def test_a_card_with_a_material_and_nothing_else_is_refused(self):
        response = self.send_cards(THREE_CARDS[0], {"material": "Aya_5B"}, button="save")

        self.assertEqual(self.cards(response)[1].problems, ["Add a phase sequence or at least one block of values."])
        self.assertContains(response, "Nothing has been saved: see the problems on compound 2.")
        self.assert_nothing_saved()

    def test_a_card_left_empty_is_ignored_and_shows_no_problems(self):
        response = self.send_cards({}, THREE_CARDS[0], button="save", follow=True)

        self.assertEqual(self.messages(response), [f"Saved a phase sequence and 1 block of values for {self.rm734}.", WAITING])

    def test_a_page_of_empty_cards_asks_for_one_to_be_filled_in(self):
        response = self.send_cards({}, {}, button="save")

        self.assertContains(response, CompoundCards.NOTHING)
        self.assertNotContains(response, CardMaterialForm.NO_MATERIAL)
        self.assert_nothing_saved()


class RemovedCardTests(CardsTestCase):
    def test_a_removed_card_is_ignored_even_with_a_problem(self):
        response = self.send_cards(
            THREE_CARDS[0], {**THREE_CARDS[2], "run": TYPO, "removed": "1"}, THREE_CARDS[1], button="save", follow=True
        )

        self.assertEqual(len(self.messages(response)), 3)  # a card's, the other card's, and WAITING
        self.assertFalse(Material.objects.filter(name="5CB").exists())
        self.assertEqual(PhaseSequence.objects.count(), 2)

    def test_check_sends_a_removed_card_back_as_its_removed_box_and_the_others_keep_their_numbers(self):
        response = self.send_cards(THREE_CARDS[0], {**THREE_CARDS[2], "removed": "1"}, THREE_CARDS[1])

        page = visible(response)
        self.assertContains(page, '<input type="hidden" name="card-1-removed" value="1">', html=True)
        self.assertNotContains(page, 'name="card-1-material"')
        self.assertContains(page, "data-card", count=2)
        self.assertContains(page, '<h3 id="card-2-title">Compound 3</h3>', html=True)
        self.assertContains(page, 'name="card-TOTAL" value="3"')


class RemovedBlockTests(CardsTestCase):
    BAD_BLOCK = {**PS_BLOCK, "points": "not a point"}

    def test_a_removed_block_with_a_problem_doesnt_stop_save_and_isnt_saved(self):
        removed = {**self.BAD_BLOCK, "DELETE": "1"}

        response = self.send_cards({"run": RM734_COOLING, "blocks": [removed], "material": "RM734"}, button="save", follow=True)

        self.assertEqual(self.messages(response), [f"Saved a phase sequence for {self.rm734}.", WAITING])
        self.assertEqual(PhaseSequence.objects.get().direction, "cooling")
        self.assertFalse(Series.objects.exists())

    def test_check_sends_a_removed_block_back_hidden_with_its_box_set(self):
        blocks = [PS_BLOCK, {**self.BAD_BLOCK, "DELETE": "1"}]

        response = self.send_cards({"run": RM734_COOLING, "blocks": blocks, "material": "RM734"})

        self.assertContains(response, '<fieldset class="value-block" data-row data-check="card-0-block-1-reading" hidden>')
        self.assertContains(response, '<input type="hidden" name="card-0-block-1-DELETE" value="1"', count=1)
        self.assertNotContains(response, "Nothing has been saved: see")  # the removed block's problem isn't one


class LimitTests(CardsTestCase):
    def test_a_page_of_100_cards_gets_past_djangos_default_limit_on_boxes(self):
        cards = [THREE_CARDS[0], *[{}] * 99]
        data = {CompoundCards.TOTAL: "100"}
        for index, card in enumerate(cards):
            data.update(self.card_data(index=index, **card))
        self.assertGreater(len(data), 1000, "Django's default for DATA_UPLOAD_MAX_NUMBER_FIELDS")

        response = self.client.post(self.page, {**data, "save": ""}, follow=True)

        self.assertEqual(self.messages(response), [f"Saved a phase sequence and 1 block of values for {self.rm734}.", WAITING])

    def test_a_page_of_more_than_100_cards_is_refused(self):
        response = self.send_cards(THREE_CARDS[0], button="save", total=101)

        self.assertContains(response, "A page can have at most 100 cards, and this one has 101.")
        self.assertEqual(len(self.cards(response)), 100)
        self.assert_nothing_saved()

    def test_a_page_above_djangos_default_size_gets_through_and_one_above_ours_doesnt(self):
        data = {**self.card_data(RM734_COOLING, material="RM734"), "check": ""}
        # A box the form doesn't have, so the page is large but quick to read.
        big = {**data, "padding": "x" * 3_000_000}
        self.assertGreater(len(big["padding"]), 2_621_440, "Django's default for DATA_UPLOAD_MAX_MEMORY_SIZE")
        self.assertEqual(self.client.post(self.page, big).status_code, 200)

        with self.assertLogs("django.security.RequestDataTooBig"):
            response = self.client.post(self.page, {**data, "padding": "x" * 11_000_000})
        self.assertEqual(response.status_code, 400)


class PageTests(CardsTestCase):
    """What Django can see of adding and removing cards and blocks: the counts the server
    reads, the empty ones cards.js copies, the buttons, hidden until it shows them, and the
    script. The script itself is checked in a browser."""

    def pages(self):
        return {
            "Add data": self.client.get(self.add_page(self.article)),
            "Check": self.send_cards(THREE_CARDS[0]),
        }

    def test_without_javascript_the_page_has_one_card_with_its_phase_sequence_and_one_block(self):
        page = visible(self.client.get(self.add_page(self.article)))

        self.assertContains(page, "data-card", count=1)
        self.assertContains(page, "data-phase-builder", count=1)
        self.assertContains(page, '<fieldset class="value-block"', count=1)
        self.assertContains(page, '<input type="hidden" name="card-TOTAL" value="1" data-max="100">', html=True)

    def test_the_empty_card_and_block_cards_js_copies_have_placeholders_for_their_index(self):
        for name, response in self.pages().items():
            with self.subTest(name):
                self.assertContains(response, '<template id="card-template">', count=1)
                self.assertContains(response, '<h3 id="card-__card__-title">Compound __cardnumber__</h3>', html=True)
                self.assertContains(response, 'name="card-__card__-material"', count=1)
                self.assertContains(response, 'name="card-__card__-run-direction"', count=1)  # its one sequence
                self.assertNotContains(response, "-run-template")  # a sequence is never copied on its own
                # The empty card's own empty block, and the first card's.
                self.assertContains(response, '<template id="card-__card__-block-template">', count=1)
                self.assertContains(response, '<template id="card-0-block-template">', count=1)
                self.assertContains(response, "<legend>Values __number__</legend>", count=2, html=True)
                self.assertContains(response, 'data-check-url="/add/reference/%d/check/block/?card=0&amp;block=__prefix__"'
                                    % self.article.pk, count=1)

    def test_every_button_for_adding_and_removing_is_hidden_until_the_script_shows_it(self):
        for name, response in self.pages().items():
            page = visible(response)
            with self.subTest(name):
                self.assertContains(page, '<button type="button" class="secondary-button" data-add-card hidden>'
                                    "+ Add another compound</button>", count=1, html=True)
                self.assertContains(page, 'data-remove-card aria-label="Remove compound 1" hidden>', count=1)
                self.assertContains(page, 'data-add-row="card-0-block" hidden>+ Add a block of values</button>', count=1)
                self.assertContains(page, 'data-remove-row aria-label="Remove values 1" hidden>', count=1)
                self.assertContains(page, '<input type="hidden" name="card-0-block-0-DELETE"', count=1)
                self.assertNotContains(page, "Add a run")
                self.assertNotContains(page, "Remove run")
                self.assertContains(page, '<script src="/static/repo/cards.js" defer></script>', html=True)

    def test_the_script_is_in_the_static_files(self):
        self.assertTrue(finders.find("repo/cards.js"))


class CheckAddressTests(CardsTestCase):
    """The checks as the curator types read the card ?card= says, and its block ?block= says."""

    def test_the_material_check_reads_the_card_its_address_names(self):
        data = {CompoundCards.TOTAL: "3"}
        for index, card in enumerate([{}, {}, {"run": RM734_COOLING, "smiles": RM734}]):
            data.update(self.card_data(index=index, **card))
        address = reverse("repo:check_card_material", args=[self.article.pk])

        self.assertContains(self.client.post(address + "?card=2", data), f"In FFDB as <a href=\"/material/{self.rm734.pk}/\">")
        self.assertEqual(self.client.post(address + "?card=0", data).content, b"")

    def test_the_block_check_reads_the_block_of_the_card_its_address_names(self):
        data = {CompoundCards.TOTAL: "2"}
        data.update(self.card_data(blocks=[{}, {}], index=0, material="Aya_5B"))  # its block 1 is empty
        data.update(self.card_data(blocks=[{}, PS_BLOCK], index=1, material="RM734"))
        address = reverse("repo:check_block", args=[self.article.pk])

        self.assertContains(self.client.post(address + "?card=1&block=1", data), "20 points read, 0 lines with a problem.")
        self.assertEqual(self.client.post(address + "?card=0&block=1", data).content, b"")
