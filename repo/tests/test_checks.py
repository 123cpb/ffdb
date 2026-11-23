"""Checks as the curator types (checks.js): curators' addresses for a compound card's material,
for its runs, for each of its blocks of values and for the compound table. Each reads what it is
sent as Check reads it and sends back what Check shows, from the same template, with any
problems listed first. One more sends back the boxes a block's choice needs, as Check draws them.
Nothing is saved. The script itself is checked in a browser."""

from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

from repo.forms import CompoundTableForm
from repo.models import Material, PhaseSequence, Property, Series, must_state
from repo.tests.test_add_data import FIVE_CB, LOGIN
from repo.tests.test_compound_card import RM734_COOLING, CardTestCase, run_fields, visible
from repo.tests.test_compound_table import THREE_LINES
from repo.tests.test_value_blocks import CHART, DELTA_EPSILON, PS_BLOCK, WITH_A_BAD_LINE
from repo.tests.test_values import RM734

# Each check's address, by its name, with what it takes after it: a card's index from 0, and a
# block's, here the first card's and its first block's.
CHECKS = {
    "card_material": "?card=0", "card_runs": "?card=0", "table": "", "block": "?card=0&block=0",
    "block_boxes": "?card=0&block=0",
}
# Δε, extrapolated, against temperature: the choice the check makes.
EXTRAPOLATED_DELTA_EPSILON = {"property": "dielectric-anisotropy", "kind": "extrapolated", "variable": "temperature"}


class ChecksTestCase(CardTestCase):
    """RM734 is in FFDB, with nothing yet from the article."""

    def setUp(self):
        super().setUp()
        self.addresses = {
            name: reverse(f"repo:check_{name}", args=[self.article.pk]) + numbers for name, numbers in CHECKS.items()
        }

    def ask(self, check, run=None, **material):
        """Send a card to the check for its material or for its phase sequence, as checks.js sends it."""
        return self.client.post(self.addresses[check], self.card_data(run, **material))

    def ask_table(self, table, direction="summary"):
        return self.client.post(self.addresses["table"], self.table_data(table, direction))

    def table_data(self, table, direction="summary"):
        return {"table": table, "direction": direction, "rate": "", "method": ""}

    def check_table(self, table):
        """The table's Check, which reloads the Add data page."""
        return self.client.post(self.add_page(self.article), {**self.table_data(table), "check": ""})

    def everything(self):
        """A card, with a run and a block of values, and a table, so each check has something to read."""
        return {**self.card_data(RM734_COOLING, [PS_BLOCK], smiles=RM734), **self.table_data(THREE_LINES)}

    def ask_block(self, check, block):
        """Send a card for RM734 with block as its first block of values, to the check for what
        the block reads as (block) or for its boxes (block_boxes)."""
        return self.client.post(self.addresses[check], self.card_data(blocks=[block], material="RM734"))


class WhoMayUseThemTests(ChecksTestCase):
    def test_a_logged_out_visitor_is_sent_to_log_in(self):
        self.client.logout()
        for name, address in self.addresses.items():
            with self.subTest(name):
                # Encoded, as the login redirect encodes it: a block's address has an & of its own.
                login = f"{LOGIN}?{urlencode({'next': address})}"
                self.assertRedirects(self.client.post(address, self.everything()), login)

    def test_a_logged_in_user_who_isnt_a_curator_is_refused(self):
        self.client.force_login(User.objects.create_user("reader", password="a-test-password"))
        for name, address in self.addresses.items():
            with self.subTest(name):
                self.assertEqual(self.client.post(address, self.everything()).status_code, 403)

    def test_they_take_only_post(self):
        for name, address in self.addresses.items():
            with self.subTest(name):
                self.assertEqual(self.client.get(address).status_code, 405)

    def test_they_take_the_forms_csrf_token_like_every_form(self):
        # The test client skips the CSRF check unless asked: a browser's requests get it.
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.curator)
        page = client.get(self.add_page(self.article))  # gives the client the CSRF cookie
        token = str(page.context["csrf_token"])  # what {% csrf_token %} put in the page's forms
        for name, address in self.addresses.items():
            with self.subTest(name):
                # A refusal is logged, as on Render, where it would show in the log.
                with self.assertLogs("django.security.csrf", "WARNING"):
                    self.assertEqual(client.post(address, self.everything()).status_code, 403)
                self.assertEqual(client.post(address, {**self.everything(), "csrfmiddlewaretoken": token}).status_code, 200)

    def test_a_number_with_no_reference_gives_a_404(self):
        for name, numbers in CHECKS.items():
            with self.subTest(name):
                address = reverse(f"repo:check_{name}", args=[9999]) + numbers
                self.assertEqual(self.client.post(address, self.everything()).status_code, 404)

    def test_a_number_with_no_block_on_the_card_gives_a_404(self):
        # The card has two blocks, numbered 0 and 1.
        for name in ["block", "block_boxes"]:
            for numbers in ["?card=0&block=2", "?card=0&block=-1", "?card=0&block=x", "?card=0"]:
                with self.subTest(name, numbers=numbers):
                    address = reverse(f"repo:check_{name}", args=[self.article.pk]) + numbers
                    self.assertEqual(self.client.post(address, self.everything()).status_code, 404)

    def test_a_card_number_that_isnt_one_gives_a_404(self):
        # A page has at most 100 cards, numbered 0 to 99.
        for name in ["card_material", "card_runs", "block", "block_boxes"]:
            for card in ["", "?card=100", "?card=-1", "?card=x"]:
                with self.subTest(name, card=card):
                    numbers = card + ("&block=0" if card and name.startswith("block") else "")
                    address = reverse(f"repo:check_{name}", args=[self.article.pk]) + numbers
                    self.assertEqual(self.client.post(address, self.everything()).status_code, 404)

    def test_no_check_saves_anything(self):
        for name, address in self.addresses.items():
            self.client.post(address, {**self.everything(), "card-0-smiles": FIVE_CB, "card-0-name": "5CB"})
            self.client.post(address, {**self.everything(), "card-0-name": "MB-1"})

        self.assertEqual(list(Material.objects.all()), [self.rm734])
        self.assertEqual(Material.objects.get().aliases, "")
        self.assertFalse(PhaseSequence.objects.exists())
        self.assertFalse(Series.objects.exists())


class MaterialTests(ChecksTestCase):
    def test_rm734s_smiles_finds_it_and_the_answer_is_what_check_shows(self):
        answer = self.ask("card_material", smiles=RM734, name="MB-1")

        self.assertContains(answer, f'In FFDB as <a href="/material/{self.rm734.pk}/">{self.rm734.accession}</a>')
        self.assertContains(answer, "MB-1 will be kept as an alias.")
        self.assertInHTML(answer.content.decode(), self.send(RM734_COOLING, smiles=RM734, name="MB-1").content.decode())

    def test_a_new_structure_is_drawn_before_its_label_is_given_and_the_label_is_asked_for(self):
        answer = self.ask("card_material", smiles=FIVE_CB)

        self.assertContains(answer, '<div role="img" aria-label="Structure from the SMILES"><svg')
        self.assertContains(answer, "Not in FFDB")
        self.assertContains(
            answer,
            "<li>Label, as the reference prints it: Give the label the reference prints for this compound: "
            "it becomes its name.</li>",
            html=True,
        )
        self.assertContains(self.ask("card_material", smiles=FIVE_CB, name="5CB"), "New compound, named 5CB")

    def test_a_new_structure_with_the_connectivity_of_one_in_ffdb_is_warned_about_before_its_label(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC", listed=True)

        answer = self.ask("card_material", smiles="CC(O)CCCCCC")

        self.assertContains(answer, f"This structure has the same connectivity as {s_octanol.accession}")

    def test_each_problem_names_its_box(self):
        for material, problem in [
            ({"material": "XYZ"}, "In FFDB: LCR ID or name: Nothing in FFDB is called XYZ. Check it, or give the SMILES of a new compound."),
            ({"smiles": "C1CC"}, "Or new: SMILES: RDKit could not read “C1CC” as a SMILES. Check it for typing errors."),
            ({"material": "RM734", "smiles": FIVE_CB}, "Fill in one of these, not more: an LCR ID or name, a SMILES, or a new mixture."),
        ]:
            with self.subTest(material):
                self.assertContains(self.ask("card_material", **material), f"<li>{problem}</li>", html=True)

    def test_with_its_boxes_empty_the_answer_is_empty(self):
        self.assertEqual(self.ask("card_material", RM734_COOLING).content, b"")


class RunTests(ChecksTestCase):
    def test_a_sequence_that_reads_shows_its_ribbon_and_the_answer_is_what_check_shows(self):
        answer = self.ask("card_runs", RM734_COOLING, material="RM734")

        self.assertContains(answer, "Iso 187.9 N 132.7 N<sub>F</sub> 70 Cr")
        self.assertContains(
            answer, 'aria-label="Iso 187.9 N 132.7 NF 70 Cr (°C), Cooling, 5 K/min, DSC, Mandle 2017, Chem. Eur. J."'
        )
        self.assertInHTML(answer.content.decode(), self.send(RM734_COOLING, material="RM734").content.decode())

    def test_a_typo_gets_the_parsers_message_and_no_ribbon(self):
        typo = run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NFF"))

        answer = self.ask("card_runs", typo, material="RM734")

        # No title before the message: the answer sits right under the card's one sequence.
        self.assertContains(
            answer, "<li>Unknown phase label: NFF. Use a label from the phase vocabulary, or add it there first. "
            "The steps read as: Iso 187.9 N 132.7 NFF</li>",
            html=True,
        )
        self.assertNotContains(answer, '<svg class="ribbon"')

    def test_a_box_that_doesnt_read_is_named_by_its_label(self):
        answer = self.ask("card_runs", run_fields("cooling", "Iso", ("18o.9", "N")), material="RM734")

        self.assertContains(answer, "<li>Transition into phase 2 (°C): Enter a number.</li>", html=True)

    def test_a_sequence_left_as_the_page_shows_it_gives_an_empty_answer(self):
        self.assertEqual(self.ask("card_runs", material="RM734").content.strip(), b"")


class TableTests(ChecksTestCase):
    def test_a_pasted_table_shows_its_lines_read_and_the_answer_is_what_check_shows(self):
        answer = self.ask_table(THREE_LINES)

        self.assertEqual(answer.context["status_counts"], {"new": 1, "in_ffdb": 1, "problem": 1})
        self.assertContains(answer, "Line 1 has no digits, so it was read as a header.")
        self.assertContains(answer, "Problem: Phase sequence: Unknown phase label: NFF.")
        self.assertInHTML(answer.content.decode(), self.check_table(THREE_LINES).content.decode())

    def test_a_table_with_sequences_and_no_direction_says_so(self):
        answer = self.ask_table(THREE_LINES, direction="")

        self.assertContains(answer, f"<li>Compounds from this reference: {CompoundTableForm.NO_DIRECTION}</li>", html=True)

    def test_an_empty_table_gives_an_empty_answer(self):
        self.assertEqual(self.ask_table(" \n").content, b"")


class BlockBoxesTests(ChecksTestCase):
    """The boxes a block's choice of property, kind and what it is against needs."""

    def test_extrapolated_delta_epsilon_against_temperature_asks_its_frequency_but_not_the_cell(self):
        answer = self.ask_block("block_boxes", EXTRAPOLATED_DELTA_EPSILON)

        self.assertContains(answer, '<label for="id_card-0-block-0-condition_frequency_Hz">Frequency (Hz)</label>', html=True)
        self.assertNotContains(answer, "condition_cell_gap_um")
        self.assertNotContains(answer, "condition_electrode_coating")
        self.assertContains(answer, '<p class="entry-form__help">Extrapolated Δε values must state: temperature, frequency.</p>', html=True)
        self.assertContains(answer, 'name="card-0-block-0-direction"')  # against temperature, a curve's direction
        self.assertContains(answer, 'list="card-0-block-0-method-suggestions"')
        self.assertContains(answer, '<option value="dielectric spectroscopy">')
        # Measured, it needs the cell too.
        measured = self.ask_block("block_boxes", {**EXTRAPOLATED_DELTA_EPSILON, "kind": "measured"})
        self.assertContains(measured, 'name="card-0-block-0-condition_cell_gap_um"')
        self.assertContains(measured, 'name="card-0-block-0-condition_electrode_coating"')

    def test_against_frequency_the_points_give_the_frequency_and_a_temperature_is_asked(self):
        answer = self.ask_block("block_boxes", {**EXTRAPOLATED_DELTA_EPSILON, "variable": "frequency"})

        self.assertNotContains(answer, "condition_frequency_Hz")
        self.assertNotContains(answer, 'name="card-0-block-0-direction"')
        self.assertContains(answer, 'name="card-0-block-0-temperature_c"')
        self.assertContains(answer, "<label for=\"id_card-0-block-0-points\">Points: Frequency (Hz), Δε (dimensionless) and optionally ±</label>", html=True)

    def test_what_was_typed_is_kept_and_the_boxes_are_what_check_draws(self):
        answer = self.ask_block("block_boxes", DELTA_EPSILON)

        self.assertContains(answer, 'name="card-0-block-0-condition_frequency_Hz" value="1000"')
        self.assertContains(answer, "25, 5.2</textarea>")
        check = self.send(blocks=[DELTA_EPSILON], material="RM734")
        self.assertInHTML(answer.content.decode(), check.content.decode())

    def test_with_nothing_chosen_it_says_to_choose(self):
        answer = self.ask_block("block_boxes", {"location": "Table 2"})

        self.assertContains(answer, "Choose what the values are of and against: the boxes they need appear here")
        self.assertNotContains(answer, "condition_")


class BlockReadingTests(ChecksTestCase):
    """What a block's lines read as, under its problems."""

    def test_a_ps_curve_with_a_bad_line_shows_19_points_the_bad_line_and_the_chart(self):
        answer = self.ask_block("block", {**PS_BLOCK, "points": "\n".join(WITH_A_BAD_LINE)})

        self.assertContains(answer, "19 points read, 1 line with a problem. Nothing has been saved.")
        self.assertContains(
            answer,
            "<li>Values 1, Points: Temperature (°C), Ps (µC/cm²) and optionally ±: Line 12: “2..92” isn't a number.</li>",
            html=True,
        )
        self.assertContains(answer, '<figure class="chart">', count=1)
        self.assertContains(answer, CHART)

    def test_it_reads_as_check_reads(self):
        answer = self.ask_block("block", PS_BLOCK)
        check = self.send(blocks=[PS_BLOCK], material="RM734")

        for shown in ["20 points read, 0 lines with a problem. Nothing has been saved.", CHART]:
            self.assertContains(answer, shown)
            self.assertContains(check, shown)

    def test_a_single_value_is_read_but_not_drawn(self):
        answer = self.ask_block("block", DELTA_EPSILON)

        self.assertContains(answer, "1 point read, 0 lines with a problem.")
        self.assertNotContains(answer, '<figure class="chart">')

    def test_every_problem_of_the_block_is_listed(self):
        delta_epsilon = Property.objects.get(code="dielectric-anisotropy")

        answer = self.ask_block("block", {**DELTA_EPSILON, "condition_frequency_Hz": "", "taken_from": ""})

        self.assertContains(answer, f"<li>Values 1: {must_state(delta_epsilon, ['frequency_Hz'])}</li>", html=True)
        self.assertContains(answer, "<li>Values 1, Taken from: This field is required.</li>", html=True)
        self.assertContains(answer, "1 point read, 0 lines with a problem.")  # the lines still read

    def test_a_block_left_empty_gives_an_empty_answer(self):
        self.assertEqual(self.ask_block("block", {}).content, b"")


class PageTests(ChecksTestCase):
    """What Django can see of the checks on the pages: the answer boxes, the parts that name
    them and the script."""

    def test_both_card_pages_have_the_answer_boxes_the_parts_that_name_them_and_the_script(self):
        pages = {
            "Add data": self.client.get(self.add_page(self.article)),
            "Check": self.send(RM734_COOLING, material="RM734"),
        }
        for name, response in pages.items():
            response = visible(response)
            with self.subTest(name):
                for box, check in [("card-0-material-result", "card_material"), ("card-0-runs-result", "card_runs")]:
                    self.assertContains(
                        response, f'<div id="{box}" aria-live="polite" data-check-url="{self.addresses[check]}">', count=1
                    )
                self.assertContains(response, '<fieldset data-check="card-0-material-result">', count=1)
                self.assertContains(response, 'data-check="card-0-runs-result"', count=1)
                self.assertContains(response, '<script src="/static/repo/checks.js" defer></script>', html=True)

    def test_both_card_pages_have_each_blocks_boxes_and_answer_box(self):
        pages = {
            "Add data": self.client.get(self.add_page(self.article)),
            "Check": self.send(blocks=[PS_BLOCK], material="RM734"),
        }
        # The & of the query is written &amp; in the page, as an & in HTML should be.
        numbers = "?card=0&amp;block=0"  # the card's one block
        boxes = reverse("repo:check_block_boxes", args=[self.article.pk]) + numbers
        reading = reverse("repo:check_block", args=[self.article.pk]) + numbers
        for name, response in pages.items():
            response = visible(response)
            with self.subTest(name):
                self.assertContains(response, 'data-check="card-0-block-0-reading"', count=1)
                self.assertContains(response, '<div class="entry-form__row" data-boxes="card-0-block-0-boxes">', count=1)
                self.assertContains(response, f'<div id="card-0-block-0-boxes" data-boxes-url="{boxes}">', count=1)
                self.assertContains(
                    response, f'<div id="card-0-block-0-reading" aria-live="polite" data-check-url="{reading}">', count=1
                )

    def test_the_add_data_page_has_the_tables_answer_box(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, 'data-check="table-result"', count=1)
        self.assertContains(
            response, f'<div id="table-result" aria-live="polite" data-check-url="{self.addresses["table"]}">', count=1
        )

    def test_the_script_is_in_the_static_files(self):
        # {% static %} writes the address whether or not the file is there, so this checks it is.
        self.assertTrue(finders.find("repo/checks.js"))
