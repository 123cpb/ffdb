"""A compound card on a reference's Add data page: its material, in FFDB or new, and its one
phase sequence, built from phase steps. Check shows what Save would add, saving nothing; Save adds all of
it, or nothing while anything on the card has a problem."""

import re

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.test import SimpleTestCase
from django.urls import reverse

from repo import chem
from repo.forms import RUN_PHASES, CompoundCards, RunForm, steps_text
from repo.models import Material, PhaseSequence, RetiredID
from repo.tests.test_add_data import FIVE_CB, LOGIN, WAITING, AddDataTestCase
from repo.tests.test_values import RM734


def run_fields(direction, top, *below, solid="", t_solid="", rate="", method="", lowest=""):
    """A phase sequence's boxes as the page sends them. below is (temperature, phase) pairs, hottest
    first; the steps after them are sent empty, as the page sends them."""
    fields = {
        "direction": direction, "rate": rate, "method": method, "lowest": lowest,
        "phase_0": top, "solid": solid, "t_solid": t_solid,
    }
    for i in range(1, RUN_PHASES):
        fields[f"t_{i}"], fields[f"phase_{i}"] = below[i - 1] if i <= len(below) else ("", "")
    return fields


EMPTY_RUN = run_fields("", "Iso")  # a phase sequence left as the page shows it


def visible(response):
    """The page without what its <template>s hold, which the browser doesn't show: the page as it
    is without JavaScript. A template can hold others, as the empty card holds an empty block."""
    kept, depth = [], 0
    for part in re.split(r"(<template[^>]*>|</template>)", response.content.decode()):
        if part.startswith("<template"):
            depth += 1
        elif part == "</template>":
            depth -= 1
        elif depth == 0:
            kept.append(part)
    return HttpResponse("".join(kept))
RM734_COOLING = run_fields(
    "cooling", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="70", rate="5", method="DSC"
)
FIVE_CB_HEATING = run_fields("heating", "Iso", ("35.3", "N"), solid="Cr", t_solid="24")


class StepsTextTests(SimpleTestCase):
    STEPS = [(None, "Iso"), ("187.9", "N"), ("132.7", "NF")]

    def test_a_cooling_run_or_one_not_stated_is_written_from_the_top_down(self):
        for direction in ["cooling", "unspecified"]:
            with self.subTest(direction):
                self.assertEqual(steps_text(direction, self.STEPS, "Cr", "70"), "Iso 187.9 N 132.7 NF 70 Cr")

    def test_a_heating_run_is_written_from_the_solid_up(self):
        self.assertEqual(steps_text("heating", self.STEPS, "Cr", "70"), "Cr 70 NF 132.7 N 187.9 Iso")

    def test_a_summarys_melting_point_can_lie_above_a_phase_below_it(self):
        self.assertEqual(steps_text("summary", self.STEPS, "K", "139.8"), "K 139.8 NF 132.7 N 187.9 Iso")

    def test_without_a_solid_or_its_temperature(self):
        self.assertEqual(steps_text("cooling", self.STEPS), "Iso 187.9 N 132.7 NF")
        self.assertEqual(steps_text("summary", [(None, "Iso"), ("24", "N")], "Cr"), "Cr ? N 24 Iso")

    def test_labels_and_temperatures_are_kept_as_typed(self):
        self.assertEqual(steps_text("cooling", [(None, "I"), ("143.0", "Nf")]), "I 143.0 Nf")


class CardTestCase(AddDataTestCase):
    """RM734 is in FFDB, with nothing yet from the article."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734, listed=True)

    def setUp(self):
        super().setUp()
        self.page = reverse("repo:add_compound", args=[self.article.pk])

    def card_data(self, run=None, blocks=(), index=0, **material):
        """The boxes of card index (from 0) as the page sends them: material is its boxes, by
        name (material, smiles, name), run fills its phase sequence, or None leaves it as the page
        shows it, and blocks fills its blocks of values, each a dict of its boxes by name, the rest
        left empty. A card has at least one block, as the page has without JavaScript."""
        prefix = f"card-{index}"
        n_blocks = max(1, len(blocks))
        data = {f"{prefix}-block-TOTAL_FORMS": str(n_blocks), f"{prefix}-block-INITIAL_FORMS": "0"}
        data.update({f"{prefix}-{key}": value for key, value in material.items()})
        data.update({f"{prefix}-run-{key}": value for key, value in (run or EMPTY_RUN).items()})
        for i, block in enumerate(blocks):
            data.update({f"{prefix}-block-{i}-{key}": value for key, value in block.items()})
        return data

    def send(self, run=None, button="check", follow=False, blocks=(), **material):
        """Post a page of one card with the Check or Save button."""
        data = {**self.card_data(run, blocks, **material), CompoundCards.TOTAL: "1", button: ""}
        return self.client.post(self.page, data, follow=follow)

    def card(self, response):
        """The first card of the page sent back."""
        return response.context["cards"].cards[0]


class CheckTests(CardTestCase):
    def test_rm734_by_its_lcr_id_shows_it_in_ffdb_and_the_runs_ribbon_saving_nothing(self):
        response = self.send(RM734_COOLING, material=self.rm734.accession)

        self.assertContains(response, f'In FFDB as <a href="/material/{self.rm734.pk}/">{self.rm734.accession}</a>')
        self.assertContains(response, "Iso 187.9 N 132.7 N<sub>F</sub> 70 Cr")
        self.assertContains(
            response, 'aria-label="Iso 187.9 N 132.7 NF 70 Cr (°C), Cooling, 5 K/min, DSC, Mandle 2017, Chem. Eur. J."'
        )
        self.assertContains(response, "Nothing has been saved.")
        self.assertFalse(PhaseSequence.objects.exists())

    def test_a_material_is_found_by_its_name_or_an_alias_as_names_are_compared(self):
        self.rm734.aliases = "MB-1"
        self.rm734.save()
        for text in ["RM734", "rm-734", "MB1"]:
            with self.subTest(text):
                self.assertEqual(self.card(self.send(RM734_COOLING, material=text)).material_form.found, self.rm734)

    def test_an_unknown_or_shared_name_says_why_it_found_nothing(self):
        Material.objects.create(name="Twin", smiles="CCO", listed=True)
        Material.objects.create(name="Twin", smiles="CCCO", listed=True)
        for text, problem in [
            ("XYZ", "Nothing in FFDB is called XYZ. Check it, or give the SMILES of a new compound."),
            ("Twin", "More than one material is called Twin"),
            ("LCR-09999", "No material has the LCR ID LCR-09999."),
        ]:
            with self.subTest(text):
                self.assertContains(self.send(RM734_COOLING, material=text), problem)

    def test_a_new_structure_is_shown_new_with_its_drawing(self):
        response = self.send(FIVE_CB_HEATING, smiles=FIVE_CB, name="5CB")

        self.assertContains(response, "New compound, named 5CB")
        self.assertContains(response, '<div role="img" aria-label="Structure of 5CB"><svg')
        self.assertContains(response, "Cr 24 N 35.3 Iso")
        self.assertEqual(Material.objects.count(), 1)

    def test_a_new_structure_is_drawn_before_its_label_is_given(self):
        response = self.send(FIVE_CB_HEATING, smiles=FIVE_CB)

        self.assertContains(response, '<div role="img" aria-label="Structure from the SMILES"><svg')
        self.assertContains(response, "Not in FFDB")
        self.assertContains(response, "Give the label the reference prints for this compound: it becomes its name.")

    def test_a_structure_already_in_ffdb_finds_that_material_and_says_what_happens_to_the_label(self):
        response = self.send(RM734_COOLING, smiles=RM734, name="MB-1")
        self.assertEqual(self.card(response).material_form.found, self.rm734)
        self.assertContains(response, "MB-1 will be kept as an alias.")

        response = self.send(RM734_COOLING, smiles=RM734, name="10b")
        self.assertContains(response, "10b won't be kept as an alias: a bare compound number")

    def test_a_new_structure_with_the_connectivity_of_one_in_ffdb_is_warned_about(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC", listed=True)

        response = self.send(FIVE_CB_HEATING, smiles="CC(O)CCCCCC", name="rac-2-octanol")

        self.assertContains(response, f"This structure has the same connectivity as {s_octanol.accession}")

    def test_a_retired_structure_is_refused_with_the_models_message(self):
        Material.objects.create(name="5CB", smiles=FIVE_CB, listed=True).retire(self.curator)
        _, inchikey = chem.standardise(FIVE_CB)

        response = self.send(FIVE_CB_HEATING, smiles=FIVE_CB, name="5CB")

        self.assertEqual(self.card(response).material_form.errors["smiles"], [RetiredID.structure_problem(inchikey)])

    def test_the_material_boxes_need_one_way_to_find_it_and_a_new_compound_its_label(self):
        for material, problem in [
            ({"material": "RM734", "smiles": FIVE_CB}, "Fill in one of these, not more: an LCR ID or name, a SMILES, or a new mixture."),
            ({}, "Give the LCR ID or name of a material in FFDB, the SMILES of a new compound, or the composition of a new mixture."),
            ({"smiles": FIVE_CB}, "Give the label the reference prints for this compound: it becomes its name."),
            ({"smiles": "C1CC", "name": "X"}, "RDKit could not read"),
        ]:
            with self.subTest(material):
                self.assertContains(self.send(RM734_COOLING, **material), problem)

    def test_a_box_that_cant_go_into_a_sequence_says_why(self):
        for run, field, problem in [
            (run_fields("cooling", "Iso", ("187.9", "")), "phase_1", "Give the phase that forms at this temperature."),
            (run_fields("cooling", "Iso", ("", "N")), "t_1", "Give the temperature of the transition into N."),
            (run_fields("cooling", "Iso", ("187.9", "Sm A")), "phase_1", RunForm.NOT_ONE_WORD),
            (run_fields("cooling", "Iso", ("187.9", "N"), t_solid="70"), "solid", "Choose the solid, or clear its temperature."),
            (run_fields("", "Iso", ("187.9", "N")), "direction", "Choose what this is: a summary, or a cooling or heating run."),
            (run_fields("cooling", "", ("187.9", "N")), "phase_0", "Give the top phase, e.g. Iso."),
        ]:
            with self.subTest(problem):
                form = self.card(self.send(run, material="RM734")).run_form
                self.assertEqual(form.errors[field], [problem])

    def test_a_label_the_vocabulary_doesnt_have_gets_the_parsers_message_and_the_steps_as_read(self):
        response = self.send(run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NFF")), material="RM734")

        self.assertContains(response, "Unknown phase label: NFF.")
        self.assertContains(response, "The steps read as: Iso 187.9 N 132.7 NFF")

    def test_steps_left_empty_between_others_are_skipped(self):
        steps = run_fields("cooling", "Iso", ("187.9", "N"), ("", ""), ("132.7", "NF"))

        self.assertContains(self.send(steps, material="RM734"), "Iso 187.9 N 132.7 N<sub>F</sub>")


class SaveTests(CardTestCase):
    def test_save_adds_rm734s_cooling_run_and_its_page_shows_it(self):
        self.client.force_login(self.admin)  # an admin's save is approved at once, so the page shows it
        response = self.send(RM734_COOLING, "save", follow=True, material=self.rm734.accession)

        self.assertRedirects(response, self.add_page(self.article))
        self.assertEqual(self.messages(response), [f"Saved a phase sequence for {self.rm734.accession} RM734."])
        run = PhaseSequence.objects.get()
        self.assertEqual(
            (run.material, run.reference, run.direction, run.sequence, run.standard_sequence, run.rate_k_per_min,
             run.method, run.entered_by),
            (self.rm734, self.article, "cooling", "Iso 187.9 N 132.7 NF 70 Cr", "Iso 187.9 N 132.7 NF 70 Cr", 5, "DSC",
             self.admin),
        )
        page = self.client.get(reverse("repo:material", args=[self.rm734.pk]))
        self.assertContains(page, '<span class="ribbon-row__run">Cooling, 5 K/min, DSC</span>', html=True)

    def test_a_new_compound_typed_as_a_smiles_is_added_with_its_sequence(self):
        cooling = run_fields("cooling", "Iso", ("35", "N"), lowest="0")

        response = self.send(cooling, "save", follow=True, smiles=FIVE_CB, name="5CB")

        five_cb = Material.objects.get(name="5CB")
        self.assertEqual(five_cb.smiles, FIVE_CB)
        self.assertEqual(self.messages(response), ["Added 5CB, with a phase sequence.", WAITING])  # no LCR ID
        run = five_cb.phase_sequences.get()
        self.assertEqual((run.standard_sequence, run.lowest_temperature_c), ("Iso 35 N", 0))

    def test_a_sequence_from_another_reference_is_kept_alongside_this_ones(self):
        PhaseSequence.objects.create(material=self.rm734, reference=self.patent, sequence="K 140 N 188 I")

        self.send(RM734_COOLING, "save", material="RM734")

        self.assertEqual(
            sorted((run.reference.identifier, run.direction) for run in self.rm734.phase_sequences.all()),
            [("10.1002/chem.201702742", "cooling"), ("EP3260450A1", "summary")],
        )

    def test_a_summary_keeps_a_melting_point_above_a_phase_seen_only_on_cooling(self):
        summary = run_fields("summary", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="139.8")

        self.send(summary, "save", material="RM734")

        run = PhaseSequence.objects.get()
        self.assertEqual((run.standard_sequence, run.t_melting_c), ("Cr 139.8 NF 132.7 N 187.9 Iso", 139.8))

    def test_the_label_is_kept_as_an_alias_of_a_material_in_ffdb(self):
        self.client.force_login(self.admin)  # a curator's label waits (test_waiting_saves)
        response = self.send(RM734_COOLING, "save", follow=True, material="RM734", name="MB-1")

        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "MB-1")
        self.assertIn("MB-1 is kept as one of its aliases.", self.messages(response)[0])

    def test_a_run_whose_temperatures_go_up_and_down_is_refused_and_nothing_is_saved(self):
        # Down to NF at 132.7, then up to the solid at 150: "Iso 187.9 N 132.7 NF 150 Cr".
        up_and_down = run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="150")

        response = self.send(up_and_down, "save", smiles=FIVE_CB, name="5CB")

        self.assertEqual(response.status_code, 200)  # the card again, not a redirect
        self.assertContains(response, "Transition temperatures must all fall (cooling) or all rise (heating).")
        self.assertEqual(Material.objects.count(), 1)
        self.assertFalse(PhaseSequence.objects.exists())

    def test_a_card_with_no_sequence_and_no_values_is_refused(self):
        response = self.send(None, "save", material="RM734", name="MB-1")

        self.assertContains(response, "Add a phase sequence or at least one block of values.")
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")

    def test_the_stereochemistry_warning_once_saved_names_the_new_material_only_for_an_admin(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC", listed=True)
        warning = f"has the same connectivity as {s_octanol.accession}"

        # A curator's: the warning Check showed, with no LCR ID for the material added.
        response = self.send(FIVE_CB_HEATING, "save", follow=True, smiles="CC(O)CCCCCC", name="rac-2-octanol")
        self.assertTrue(self.messages(response)[1].startswith(f"This structure {warning}"))

        self.client.force_login(self.admin)
        response = self.send(FIVE_CB_HEATING, "save", follow=True, smiles="C[C@@H](O)CCCCCC", name="R-2-octanol")
        new = Material.objects.get(name="R-2-octanol")
        message = self.messages(response)[1]
        # An admin's warning counts every material, the curator's waiting rac-2-octanol too, and
        # which comes first depends on how the database sorts names, so check only that it is there.
        self.assertTrue(message.startswith(f"{new.accession} has the same connectivity as "))
        self.assertIn(f"{s_octanol.accession} (S-2-octanol)", message)


class AccessTests(CardTestCase):
    def test_the_add_data_page_has_an_empty_card_with_one_phase_sequence_and_one_block(self):
        response = visible(self.client.get(self.add_page(self.article)))

        self.assertContains(response, f'<form method="post" action="{self.page}"')
        self.assertContains(response, "<legend>Phase sequence</legend>", count=1, html=True)
        self.assertContains(response, 'name="card-0-run-phase_7"')  # every step, without JavaScript
        self.assertNotContains(response, "card-0-run-TOTAL_FORMS")  # one sequence: nothing to count
        self.assertNotContains(response, "Add a run")
        self.assertContains(response, 'name="card-0-block-TOTAL_FORMS" value="1"')
        self.assertContains(response, "+ Add a block of values</button>")

    def test_a_logged_out_visitor_cant_save(self):
        self.client.logout()

        response = self.send(RM734_COOLING, "save", material="RM734")

        self.assertRedirects(response, f"{LOGIN}?next={self.page}")
        self.assertFalse(PhaseSequence.objects.exists())

    def test_a_logged_in_user_who_isnt_a_curator_is_refused(self):
        self.client.force_login(User.objects.create_user("reader", password="a-test-password"))

        self.assertEqual(self.send(RM734_COOLING, "save", material="RM734").status_code, 403)
        self.assertFalse(PhaseSequence.objects.exists())


class PhaseBuilderAndDrawTests(CardTestCase):
    """What Django can see of the phase builder and Draw on a card: the buttons, hidden until
    the scripts show them, the suggestions and the scripts, on both pages a card is on. The
    scripts themselves are checked in a browser."""

    def pages(self):
        """The Add data page, with an empty card, and the add-a-compound page, as Check shows it."""
        return {
            "Add data": self.client.get(self.add_page(self.article)),
            "Check": self.send(RM734_COOLING, material="RM734"),
        }

    def test_both_pages_have_the_cards_draw_button_the_editors_dialog_and_the_scripts(self):
        for name, response in self.pages().items():
            with self.subTest(name):
                self.assertContains(
                    response,
                    '<button type="button" class="secondary-button card-draw" data-draw data-smiles="id_card-0-smiles" '
                    "hidden>Draw the structure</button>",
                    html=True,
                )
                self.assertContains(response, '<dialog class="draw-dialog" id="draw-dialog"', count=1)
                self.assertContains(response, '<div class="entry-form__field" data-draw-label>')
                self.assertContains(response, '<script src="/static/repo/draw.js" defer></script>', html=True)
                self.assertContains(response, '<script src="/static/repo/phase_steps.js" defer></script>', html=True)

    def test_the_phase_sequence_has_add_a_phase_and_remove_on_each_step_after_the_second_all_hidden(self):
        for name, response in self.pages().items():
            response = visible(response)
            with self.subTest(name):
                self.assertContains(response, "data-phase-builder", count=1)
                self.assertContains(response, "<li class=\"entry-form__row\" data-phase-step>", count=RUN_PHASES)
                self.assertContains(
                    response,
                    '<button type="button" class="secondary-button phase-steps__add" data-add-phase hidden>'
                    "+ Add a phase</button>",
                    count=1, html=True,
                )
                self.assertContains(response, "data-remove-phase", count=RUN_PHASES - 2)
                self.assertContains(response, 'aria-label="Remove phase 3"', count=1)
                self.assertNotContains(response, 'aria-label="Remove phase 2"')

    def test_the_phase_boxes_suggest_the_vocabulary_without_the_solids(self):
        for name, response in self.pages().items():
            response = visible(response)
            with self.subTest(name):
                self.assertContains(response, 'list="phase-labels"', count=RUN_PHASES)
                self.assertContains(response, '<datalist id="phase-labels">', count=1)
                self.assertContains(response, '<option value="NF">Ferroelectric nematic</option>', html=True)
                self.assertContains(response, '<option value="N_F">stored as NF</option>', html=True)
                self.assertNotContains(response, '<option value="K">')

    def test_suggestions_are_each_phase_by_its_code_then_its_other_labels(self):
        suggestions = CompoundCards(None, self.article, self.curator).phase_suggestions()

        self.assertEqual(
            suggestions[:4],
            [("Iso", "Isotropic liquid"), ("I", "stored as Iso"), ("IL", "stored as Iso"), ("N", "Nematic")],
        )
        labels = {label for label, _ in suggestions}
        self.assertFalse(labels & {"Cr", "K", "X", "Cry", "g", "glass", "Tg"})

    def test_the_phase_builders_script_is_in_the_static_files(self):
        # {% static %} writes the address whether or not the file is there, so this checks it is.
        self.assertTrue(finders.find("repo/phase_steps.js"))
