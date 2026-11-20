"""Checks as the curator types (checks.js): three curators' addresses, for a compound card's
material, for its runs and for the compound table. Each reads what it is sent as Check reads it
and sends back what Check shows, from the same template, with any problems listed first. Nothing
is saved. The script itself is checked in a browser."""

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import Client
from django.urls import reverse

from repo.forms import CompoundTableForm
from repo.models import Material, PhaseSequence
from repo.tests.test_add_data import FIVE_CB, LOGIN
from repo.tests.test_compound_card import RM734_COOLING, CardTestCase, run_fields
from repo.tests.test_compound_table import THREE_LINES
from repo.tests.test_values import RM734

CHECKS = ["card_material", "card_runs", "table"]


class ChecksTestCase(CardTestCase):
    """RM734 is in FFDB, with nothing yet from the article."""

    def setUp(self):
        super().setUp()
        self.addresses = {name: reverse(f"repo:check_{name}", args=[self.article.pk]) for name in CHECKS}

    def ask(self, check, runs=(), **material):
        """Send a card to the check for its material or for its runs, as checks.js sends it."""
        return self.client.post(self.addresses[check], self.card_data(runs, **material))

    def ask_table(self, table, direction="summary"):
        return self.client.post(self.addresses["table"], self.table_data(table, direction))

    def table_data(self, table, direction="summary"):
        return {"table": table, "direction": direction, "rate": "", "method": ""}

    def check_table(self, table):
        """The table's Check, which reloads the Add data page."""
        return self.client.post(self.add_page(self.article), {**self.table_data(table), "check": ""})

    def everything(self):
        """A card and a table, so each check has something to read."""
        return {**self.card_data([RM734_COOLING], smiles=RM734), **self.table_data(THREE_LINES)}


class WhoMayUseThemTests(ChecksTestCase):
    def test_a_logged_out_visitor_is_sent_to_log_in(self):
        self.client.logout()
        for name, address in self.addresses.items():
            with self.subTest(name):
                self.assertRedirects(self.client.post(address, self.everything()), f"{LOGIN}?next={address}")

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
                self.assertEqual(client.post(address, self.everything()).status_code, 403)
                self.assertEqual(client.post(address, {**self.everything(), "csrfmiddlewaretoken": token}).status_code, 200)

    def test_a_number_with_no_reference_gives_a_404(self):
        for name in CHECKS:
            with self.subTest(name):
                self.assertEqual(self.client.post(reverse(f"repo:check_{name}", args=[9999]), self.everything()).status_code, 404)

    def test_no_check_saves_anything(self):
        for name, address in self.addresses.items():
            self.client.post(address, {**self.everything(), "card-smiles": FIVE_CB, "card-name": "5CB"})
            self.client.post(address, {**self.everything(), "card-name": "MB-1"})

        self.assertEqual(list(Material.objects.all()), [self.rm734])
        self.assertEqual(Material.objects.get().aliases, "")
        self.assertFalse(PhaseSequence.objects.exists())


class MaterialTests(ChecksTestCase):
    def test_rm734s_smiles_finds_it_and_the_answer_is_what_check_shows(self):
        answer = self.ask("card_material", smiles=RM734, name="MB-1")

        self.assertContains(answer, f'In FFDB as <a href="/material/{self.rm734.pk}/">{self.rm734.accession}</a>')
        self.assertContains(answer, "MB-1 will be kept as an alias.")
        self.assertInHTML(answer.content.decode(), self.send([RM734_COOLING], smiles=RM734, name="MB-1").content.decode())

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
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")

        answer = self.ask("card_material", smiles="CC(O)CCCCCC")

        self.assertContains(answer, f"This structure has the same connectivity as {s_octanol.accession}")

    def test_each_problem_names_its_box(self):
        for material, problem in [
            ({"material": "XYZ"}, "In FFDB: LCR ID or name: Nothing in FFDB is called XYZ. Check it, or give the SMILES of a new compound."),
            ({"smiles": "C1CC"}, "Or new: SMILES: RDKit could not read “C1CC” as a SMILES. Check it for typing errors."),
            ({"material": "RM734", "smiles": FIVE_CB}, "Give an LCR ID or name, or a SMILES, not both."),
        ]:
            with self.subTest(material):
                self.assertContains(self.ask("card_material", **material), f"<li>{problem}</li>", html=True)

    def test_with_its_boxes_empty_the_answer_is_empty(self):
        self.assertEqual(self.ask("card_material", [RM734_COOLING]).content, b"")


class RunTests(ChecksTestCase):
    def test_a_run_that_reads_shows_its_sequence_and_ribbon_and_the_answer_is_what_check_shows(self):
        answer = self.ask("card_runs", [RM734_COOLING], material="RM734")

        self.assertContains(answer, "Iso 187.9 N 132.7 N<sub>F</sub> 70 Cr")
        self.assertContains(
            answer, 'aria-label="Iso 187.9 N 132.7 NF 70 Cr (°C), Cooling, 5 K/min, DSC, Mandle 2017, Chem. Eur. J."'
        )
        self.assertInHTML(answer.content.decode(), self.send([RM734_COOLING], material="RM734").content.decode())

    def test_a_typo_gets_the_parsers_message_under_its_runs_number(self):
        typo = run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NFF"))

        answer = self.ask("card_runs", [RM734_COOLING, typo], material="RM734")

        self.assertContains(
            answer, "<li>Run 2: Unknown phase label: NFF. Use a label from the phase vocabulary, or add it there first. "
            "The steps read as: Iso 187.9 N 132.7 NFF</li>",
            html=True,
        )
        self.assertContains(answer, "Iso 187.9 N 132.7 N<sub>F</sub> 70 Cr")  # run 1 still reads

    def test_a_box_that_doesnt_read_is_named_with_its_run(self):
        answer = self.ask("card_runs", [run_fields("cooling", "Iso", ("18o.9", "N"))], material="RM734")

        self.assertContains(answer, "<li>Run 1, Transition into phase 2 (°C): Enter a number.</li>", html=True)

    def test_runs_left_as_the_page_shows_them_give_an_empty_answer(self):
        self.assertEqual(self.ask("card_runs", material="RM734").content.strip(), b"")


class TableTests(ChecksTestCase):
    def test_a_pasted_table_shows_its_lines_read_and_the_answer_is_what_check_shows(self):
        answer = self.ask_table(THREE_LINES)

        self.assertEqual(answer.context["status_counts"], {"new": 1, "in_ffdb": 1, "problem": 1})
        self.assertContains(answer, "Line 1 has no digits, so it was read as a header.")
        self.assertContains(answer, "Not saved: Phase sequence: Unknown phase label: NFF.")
        self.assertInHTML(answer.content.decode(), self.check_table(THREE_LINES).content.decode())

    def test_a_table_with_sequences_and_no_direction_says_so(self):
        answer = self.ask_table(THREE_LINES, direction="")

        self.assertContains(answer, f"<li>Compounds from this reference: {CompoundTableForm.NO_DIRECTION}</li>", html=True)

    def test_an_empty_table_gives_an_empty_answer(self):
        self.assertEqual(self.ask_table(" \n").content, b"")


class PageTests(ChecksTestCase):
    """What Django can see of the checks on the pages: the answer boxes, the parts that name
    them and the script."""

    def test_both_card_pages_have_the_answer_boxes_the_parts_that_name_them_and_the_script(self):
        pages = {
            "Add data": self.client.get(self.add_page(self.article)),
            "Check": self.send([RM734_COOLING], material="RM734"),
        }
        for name, response in pages.items():
            with self.subTest(name):
                for box, check in [("card-material-result", "card_material"), ("card-runs-result", "card_runs")]:
                    self.assertContains(
                        response, f'<div id="{box}" aria-live="polite" data-check-url="{self.addresses[check]}">', count=1
                    )
                self.assertContains(response, '<fieldset data-check="card-material-result">', count=1)
                self.assertContains(response, 'data-check="card-runs-result"', count=2)  # each run
                self.assertContains(response, '<script src="/static/repo/checks.js" defer></script>', html=True)

    def test_the_add_data_page_has_the_tables_answer_box(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, 'data-check="table-result"', count=1)
        self.assertContains(
            response, f'<div id="table-result" aria-live="polite" data-check-url="{self.addresses["table"]}">', count=1
        )

    def test_the_script_is_in_the_static_files(self):
        # {% static %} writes the address whether or not the file is there, so this checks it is.
        self.assertTrue(finders.find("repo/checks.js"))
