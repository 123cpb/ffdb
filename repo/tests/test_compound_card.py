"""A compound card on a reference's Add data page: its material, in FFDB or new, and its runs,
each built from phase steps. Check shows what Save would add, saving nothing; Save adds all of
it, or nothing while anything on the card has a problem."""

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from django.urls import reverse

from repo import chem
from repo.forms import RUN_PHASES, RunForm, steps_text
from repo.models import Material, PhaseSequence, RetiredID
from repo.tests.test_add_data import FIVE_CB, LOGIN, AddDataTestCase
from repo.tests.test_values import RM734


def run_fields(direction, top, *below, solid="", t_solid="", rate="", method="", lowest=""):
    """One run's boxes as the page sends them. below is (temperature, phase) pairs, hottest
    first; the steps after them are sent empty, as the page sends them."""
    fields = {
        "direction": direction, "rate": rate, "method": method, "lowest": lowest,
        "phase_0": top, "solid": solid, "t_solid": t_solid,
    }
    for i in range(1, RUN_PHASES):
        fields[f"t_{i}"], fields[f"phase_{i}"] = below[i - 1] if i <= len(below) else ("", "")
    return fields


EMPTY_RUN = run_fields("", "Iso")  # a run left as the page shows it
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
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)

    def setUp(self):
        super().setUp()
        self.page = reverse("repo:add_compound", args=[self.article.pk])

    def send(self, runs=(), button="check", follow=False, **material):
        """Post the card with the Check or Save button: material is its boxes, by name (material,
        smiles, name), and runs fills the two runs, the rest left empty."""
        data = {"run-TOTAL_FORMS": "2", "run-INITIAL_FORMS": "0", button: ""}
        data.update({f"card-{key}": value for key, value in material.items()})
        for i, run in enumerate([*runs, *[EMPTY_RUN] * (2 - len(runs))]):
            data.update({f"run-{i}-{key}": value for key, value in run.items()})
        return self.client.post(self.page, data, follow=follow)

    def card(self, response):
        return response.context["card"]


class CheckTests(CardTestCase):
    def test_rm734_by_its_lcr_id_shows_it_in_ffdb_and_the_runs_ribbon_saving_nothing(self):
        response = self.send([RM734_COOLING], material=self.rm734.accession)

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
                self.assertEqual(self.card(self.send([RM734_COOLING], material=text)).material_form.found, self.rm734)

    def test_an_unknown_or_shared_name_says_why_it_found_nothing(self):
        Material.objects.create(name="Twin", smiles="CCO")
        Material.objects.create(name="Twin", smiles="CCCO")
        for text, problem in [
            ("XYZ", "Nothing in FFDB is called XYZ. Check it, or give the SMILES of a new compound."),
            ("Twin", "More than one material is called Twin"),
            ("LCR-09999", "No material has the LCR ID LCR-09999."),
        ]:
            with self.subTest(text):
                self.assertContains(self.send([RM734_COOLING], material=text), problem)

    def test_a_new_structure_is_shown_new_with_its_drawing(self):
        response = self.send([FIVE_CB_HEATING], smiles=FIVE_CB, name="5CB")

        self.assertContains(response, "New compound, named 5CB")
        self.assertContains(response, '<div role="img" aria-label="Structure of 5CB"><svg')
        self.assertContains(response, "Cr 24 N 35.3 Iso")
        self.assertEqual(Material.objects.count(), 1)

    def test_a_structure_already_in_ffdb_finds_that_material_and_says_what_happens_to_the_label(self):
        response = self.send([RM734_COOLING], smiles=RM734, name="MB-1")
        self.assertEqual(self.card(response).material_form.found, self.rm734)
        self.assertContains(response, "MB-1 will be kept as an alias.")

        response = self.send([RM734_COOLING], smiles=RM734, name="10b")
        self.assertContains(response, "10b won't be kept as an alias: a bare compound number")

    def test_a_new_structure_with_the_connectivity_of_one_in_ffdb_is_warned_about(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")

        response = self.send([FIVE_CB_HEATING], smiles="CC(O)CCCCCC", name="rac-2-octanol")

        self.assertContains(response, f"This structure has the same connectivity as {s_octanol.accession}")

    def test_a_retired_structure_is_refused_with_the_models_message(self):
        Material.objects.create(name="5CB", smiles=FIVE_CB).retire(self.curator)
        _, inchikey = chem.standardise(FIVE_CB)

        response = self.send([FIVE_CB_HEATING], smiles=FIVE_CB, name="5CB")

        self.assertEqual(self.card(response).material_form.errors["smiles"], [RetiredID.structure_problem(inchikey)])

    def test_the_material_boxes_need_one_way_to_find_it_and_a_new_compound_its_label(self):
        for material, problem in [
            ({"material": "RM734", "smiles": FIVE_CB}, "Give an LCR ID or name, or a SMILES, not both."),
            ({}, "Give the LCR ID or name of a material in FFDB, or the SMILES of a new compound."),
            ({"smiles": FIVE_CB}, "Give the label the reference prints for this compound: it becomes its name."),
            ({"smiles": "C1CC", "name": "X"}, "RDKit could not read"),
        ]:
            with self.subTest(material):
                self.assertContains(self.send([RM734_COOLING], **material), problem)

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
                form = self.card(self.send([run], material="RM734")).run_forms[0]
                self.assertEqual(form.errors[field], [problem])

    def test_a_label_the_vocabulary_doesnt_have_gets_the_parsers_message_and_the_steps_as_read(self):
        response = self.send([run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NFF"))], material="RM734")

        self.assertContains(response, "Unknown phase label: NFF.")
        self.assertContains(response, "The steps read as: Iso 187.9 N 132.7 NFF")

    def test_steps_left_empty_between_others_are_skipped(self):
        steps = run_fields("cooling", "Iso", ("187.9", "N"), ("", ""), ("132.7", "NF"))

        self.assertContains(self.send([steps], material="RM734"), "Iso 187.9 N 132.7 N<sub>F</sub>")


class SaveTests(CardTestCase):
    def test_save_adds_rm734s_cooling_run_and_its_page_shows_it(self):
        response = self.send([RM734_COOLING], "save", follow=True, material=self.rm734.accession)

        self.assertRedirects(response, self.add_page(self.article))
        self.assertEqual(self.messages(response), [f"Saved 1 run for {self.rm734.accession} RM734."])
        run = PhaseSequence.objects.get()
        self.assertEqual(
            (run.material, run.reference, run.direction, run.sequence, run.standard_sequence, run.rate_k_per_min,
             run.method, run.entered_by),
            (self.rm734, self.article, "cooling", "Iso 187.9 N 132.7 NF 70 Cr", "Iso 187.9 N 132.7 NF 70 Cr", 5, "DSC",
             self.curator),
        )
        page = self.client.get(reverse("repo:material", args=[self.rm734.pk]))
        self.assertContains(page, '<span class="ribbon-row__run">Cooling, 5 K/min, DSC</span>', html=True)

    def test_a_new_compound_typed_as_a_smiles_is_added_with_its_runs(self):
        cooling = run_fields("cooling", "Iso", ("35", "N"), lowest="0")

        response = self.send([FIVE_CB_HEATING, cooling], "save", follow=True, smiles=FIVE_CB, name="5CB")

        five_cb = Material.objects.get(name="5CB")
        self.assertEqual(five_cb.smiles, FIVE_CB)
        self.assertEqual(self.messages(response), [f"Added 5CB as {five_cb.accession}, with 2 runs."])
        runs = five_cb.phase_sequences.order_by("pk")
        self.assertEqual(
            [(run.standard_sequence, run.t_melting_c, run.lowest_temperature_c) for run in runs],
            [("Cr 24 N 35.3 Iso", 24, None), ("Iso 35 N", None, 0)],
        )

    def test_a_summary_keeps_a_melting_point_above_a_phase_seen_only_on_cooling(self):
        summary = run_fields("summary", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="139.8")

        self.send([summary], "save", material="RM734")

        run = PhaseSequence.objects.get()
        self.assertEqual((run.standard_sequence, run.t_melting_c), ("Cr 139.8 NF 132.7 N 187.9 Iso", 139.8))

    def test_the_label_is_kept_as_an_alias_of_a_material_in_ffdb(self):
        response = self.send([RM734_COOLING], "save", follow=True, material="RM734", name="MB-1")

        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "MB-1")
        self.assertIn("MB-1 is kept as one of its aliases.", self.messages(response)[0])

    def test_a_run_whose_temperatures_go_up_and_down_is_refused_and_nothing_is_saved(self):
        # Down to NF at 132.7, then up to the solid at 150: "Iso 187.9 N 132.7 NF 150 Cr".
        up_and_down = run_fields("cooling", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="150")

        response = self.send([up_and_down], "save", smiles=FIVE_CB, name="5CB")

        self.assertEqual(response.status_code, 200)  # the card again, not a redirect
        self.assertContains(response, "Transition temperatures must all fall (cooling) or all rise (heating).")
        self.assertEqual(Material.objects.count(), 1)
        self.assertFalse(PhaseSequence.objects.exists())

    def test_one_run_with_a_problem_keeps_the_others_from_being_saved_too(self):
        # Melting above NF forms, which only a summary can have: "Cr 150 NF 132.7 N 187.9 Iso".
        bad = run_fields("heating", "Iso", ("187.9", "N"), ("132.7", "NF"), solid="Cr", t_solid="150")

        response = self.send([RM734_COOLING, bad], "save", material="RM734")

        self.assertContains(response, "The steps read as: Cr 150 NF 132.7 N 187.9 Iso")
        self.assertFalse(PhaseSequence.objects.exists())
        self.assertContains(response, '<svg class="ribbon"', count=1)  # Check still shows the run that reads

    def test_a_card_with_no_runs_is_refused(self):
        response = self.send([], "save", material="RM734", name="MB-1")

        self.assertContains(response, "Add at least one run.")
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")

    def test_the_stereochemistry_warning_names_the_new_material_once_saved(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")

        response = self.send([FIVE_CB_HEATING], "save", follow=True, smiles="CC(O)CCCCCC", name="rac-2-octanol")

        new = Material.objects.get(name="rac-2-octanol")
        self.assertTrue(self.messages(response)[1].startswith(f"{new.accession} has the same connectivity as {s_octanol.accession}"))


class AccessTests(CardTestCase):
    def test_the_add_data_page_has_an_empty_card(self):
        response = self.client.get(self.add_page(self.article))

        self.assertContains(response, f'<form method="post" action="{self.page}"')
        self.assertContains(response, 'name="run-TOTAL_FORMS" value="2"')
        self.assertContains(response, 'name="run-1-phase_7"')  # every step of both runs, without JavaScript

    def test_a_logged_out_visitor_cant_save(self):
        self.client.logout()

        response = self.send([RM734_COOLING], "save", material="RM734")

        self.assertRedirects(response, f"{LOGIN}?next={self.page}")
        self.assertFalse(PhaseSequence.objects.exists())

    def test_a_logged_in_user_who_isnt_a_curator_is_refused(self):
        self.client.force_login(User.objects.create_user("reader", password="a-test-password"))

        self.assertEqual(self.send([RM734_COOLING], "save", material="RM734").status_code, 403)
        self.assertFalse(PhaseSequence.objects.exists())
