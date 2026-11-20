"""A reference's Add data page reads a pasted table of compounds, where a material already in
FFDB, such as a mixture, can be given by its LCR ID: Check shows what each line would add and
saves nothing. The table adds nothing itself: it makes cards (test_table_cards.py)."""

from django.test import SimpleTestCase
from django.urls import reverse

from repo.forms import CompoundTableForm, table_cells, table_rows
from repo.models import Material, PhaseSequence, is_compound_number
from repo.tests.test_add_data import FIVE_CB, AddDataTestCase
from repo.tests.test_values import DIO, RM734

SEQUENCE = "K 139.8 NF 132.7 N 187.9 I"
# 5CB new, RM734 already in FFDB under another label, and DIO with a typo in its sequence.
THREE_LINES = f"Label\tSMILES\tPhases\n5CB\t{FIVE_CB}\tK 24 N 35.3 I\nMB-1\t{RM734}\t{SEQUENCE}\n10b\t{DIO}\tK 80 NFF 120 I"


class TableRuleTests(SimpleTestCase):
    def test_cells_are_split_on_tabs_or_else_commas_or_else_spaces(self):
        cases = {
            "5CB\tCCC\tK 24 N 35 I": ["5CB", "CCC", "K 24 N 35 I"],
            "compound 3\tCCC": ["compound 3", "CCC"],
            # The sequence keeps its own commas and spaces.
            "5CB, CCC, K 24, N 35, I": ["5CB", "CCC", "K 24, N 35, I"],
            "5CB   CCC  K 24 N 35 I": ["5CB", "CCC", "K 24 N 35 I"],
            "5CB\tCCC\t\t": ["5CB", "CCC"],  # empty cells at the end are dropped
        }
        for line, cells in cases.items():
            with self.subTest(line):
                self.assertEqual(table_cells(line), cells)

    def test_only_a_first_line_without_digits_is_a_header(self):
        header, rows = table_rows("Label\tSMILES\n\n5CB\tC1CC1\nDIO only\n")

        self.assertEqual(header, 1)
        # Line numbers are the pasted text's, blank lines counted.
        self.assertEqual([(number, cells) for number, _, cells in rows], [(3, ["5CB", "C1CC1"]), (4, ["DIO", "only"])])
        self.assertEqual(table_rows("5CB\tC1CC1")[0], None)


class AliasRuleTests(SimpleTestCase):
    def test_bare_compound_numbers(self):
        for name in ["3", "10a", "4B", " 12 ", "(3)", "(10a)", "compound 3", "Compound 10a", "cpd122", "cpd 151"]:
            with self.subTest(name):
                self.assertTrue(is_compound_number(name))
        for name in ["5CB", "M5", "12-0-1", "RM734", "SCUT10a", "II-4", "compound"]:
            with self.subTest(name):
                self.assertFalse(is_compound_number(name))

    def test_which_names_can_be_kept_as_aliases(self):
        material = Material(name="RM734", aliases="MB-1")  # not saved: the rule needs no database

        self.assertEqual(material.alias_problem("NT3A-5"), "")
        for name, problem in [
            ("rm-734", "already one of its names"),
            ("MB1", "already one of its names"),
            ("10a", "a bare compound number"),
            ("A, B", "can't contain a comma"),
            ("—", "no letters or digits"),
            ("X" * 500, "its aliases are full"),
        ]:
            with self.subTest(name):
                self.assertIn(problem, material.alias_problem(name))


class CompoundTableTestCase(AddDataTestCase):
    """RM734 is in FFDB, with nothing yet from the article."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)

    def send(self, table, button="check", follow=False, direction="summary", rate="", method=""):
        """Post the table with the Check button, or the one button names, as summaries unless
        direction says otherwise."""
        data = {"table": table, "direction": direction, "rate": rate, "method": method, button: ""}
        return self.client.post(self.add_page(self.article), data, follow=follow)

    def line(self, table, **fields):
        """The last line Check read."""
        return self.send(table, **fields).context["lines"][-1]


class CheckTests(CompoundTableTestCase):
    def test_a_new_structure_is_new_with_its_drawing_and_standard_sequence(self):
        response = self.send(f"5CB\t{FIVE_CB}\tK 24 N 35.3 I")

        [line] = response.context["lines"]
        self.assertEqual((line.status, line.standard_sequence, line.stereo_warning), ("new", "Cr 24 N 35.3 Iso", ""))
        self.assertContains(response, '<div role="img" aria-label="Structure of 5CB"><svg')

    def test_a_structure_in_ffdb_is_shown_as_that_material_with_what_happens_to_its_label(self):
        response = self.send(f"MB-1\t{RM734}")

        self.assertEqual(response.context["lines"][0].match, self.rm734)
        link = reverse("repo:material", args=[self.rm734.pk])
        self.assertContains(response, f'<a href="{link}">{self.rm734.accession}</a>', html=True)
        self.assertContains(response, "MB-1 will be kept as an alias.")

        line = self.line(f"10a\t{RM734}")
        self.assertIn("a bare compound number", line.alias_problem)

    def test_each_kind_of_problem_line_says_why(self):
        for bad, problem in [
            ("5CB", "Expected a label, a SMILES or LCR ID and, optionally, a phase sequence, but found 1 cell."),
            ("5CB\tC1CC1\tK 24 N 35 I\tmore", "but found 4 cells."),
            ("\tC1CC1", "Give the label as the reference prints it."),
            ("X" * 201 + "\tC1CC1", "longer than the 200 characters a name can have"),
            ("9z\tC1CC1x", "RDKit could not read “C1CC1x” as a SMILES"),
            ("9z\tLCR-09999", "No material has the LCR ID LCR-09999."),
            (f"9z\t{FIVE_CB}", "Same material as line 1: give each material once."),
            ("10b\tC1CC1\tK 80 NFF 120 I", "Phase sequence: Unknown phase label: NFF."),
            ("10b\tC1CC1\tK 50 NF 120 N 80 I", "Phase sequence: In a summary, transition temperatures must rise"),
        ]:
            with self.subTest(bad):
                # A first line, so the bad one is never read as a header.
                line = self.line(f"5CB\t{FIVE_CB}\n{bad}")

                self.assertEqual(line.status, "problem")
                self.assertIn(problem, line.problem)

    def test_a_new_structure_with_the_connectivity_of_one_in_ffdb_is_warned_about(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")

        line = self.line("rac-2-octanol\tCC(O)CCCCCC")

        self.assertEqual(line.status, "new")
        self.assertTrue(line.stereo_warning.startswith(f"This structure has the same connectivity as {s_octanol.accession}"))

    def test_a_sequence_already_there_from_this_reference_is_marked(self):
        PhaseSequence.objects.create(material=self.rm734, reference=self.article, sequence=SEQUENCE)

        response = self.send(f"RM734\t{RM734}\tK 139.8 Nf 132.7 N 187.9 I")  # Nf is NF

        self.assertTrue(response.context["lines"][0].sequence_already_there)
        self.assertContains(response, "Already in FFDB from this reference: Make cards leaves it off the card.")
        self.assertContains(response, "RM734 won't be kept as an alias: it is already one of its names.")

    def test_a_run_that_goes_up_and_down_is_a_problem_line(self):
        line = self.line(f"RM734\t{RM734}\tIso 187.9 N 192 NF 70 K", direction="cooling")

        self.assertEqual(line.status, "problem")
        self.assertIn("Enter heating and cooling runs as separate sequences.", line.problem)

    def test_a_table_with_sequences_needs_a_direction_and_one_without_doesnt(self):
        response = self.send(f"5CB\t{FIVE_CB}\tK 24 N 35.3 I", direction="")

        self.assertContains(response, "This table has phase sequences: choose what they are")
        self.assertEqual(response.context["lines"], [])

        line = self.line(f"5CB\t{FIVE_CB}", direction="")
        self.assertEqual(line.status, "new")

    def test_a_heating_run_written_as_a_summary_already_there_is_not_the_same_sequence(self):
        PhaseSequence.objects.create(material=self.rm734, reference=self.article, sequence="K 70 NF 132.7 N 187.9 I")

        line = self.line(f"RM734\t{RM734}\tK 70 NF 132.7 N 187.9 I", direction="heating")

        self.assertFalse(line.sequence_already_there)

    def test_a_table_with_no_lines_or_too_many_is_refused_whole(self):
        for table, error in [
            ("", "Paste at least one line"),
            ("Label\tSMILES\n\n", "Paste at least one line"),
            (f"5CB\t{FIVE_CB}\n" * 101, "A table can have at most 100 lines, and this one has 101: split it."),
        ]:
            with self.subTest(table[:20]):
                response = self.send(table)

                self.assertContains(response, error)
                self.assertEqual(response.context["lines"], [])

    def test_check_saves_nothing(self):
        response = self.send(THREE_LINES)

        statuses = [line.status for line in response.context["lines"]]
        self.assertEqual(statuses, ["new", "in_ffdb", "problem"])
        self.assertContains(response, "Line 1 has no digits, so it was read as a header.")
        self.assertEqual(Material.objects.count(), 1)
        self.assertFalse(PhaseSequence.objects.exists())
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")


class LcrIdLineTests(CompoundTableTestCase):
    """RM734 is in FFDB, and so is F7, a mixture whose composition isn't disclosed. Nothing is
    from the article yet."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.f7 = Material.objects.create(
            kind=Material.Kind.MIXTURE, name="F7", composition_status=Material.CompositionStatus.NOT_DISCLOSED
        )

    def test_a_material_in_ffdb_can_be_given_by_its_lcr_id_in_place_of_a_smiles(self):
        # Typed any way pk_from_accession() reads: RM734's in lower case, without the dash.
        response = self.send(f"MB-1\tlcr{self.rm734.pk}\nF7\t{self.f7.accession}")

        compound, mixture = response.context["lines"]
        self.assertEqual((compound.status, compound.match), ("in_ffdb", self.rm734))
        self.assertEqual((mixture.status, mixture.match), ("in_ffdb", self.f7))
        # The compound is drawn from the structure FFDB has. The mixture has none.
        self.assertContains(response, '<div role="img" aria-label="Structure of MB-1"><svg')
        self.assertNotContains(response, 'aria-label="Structure of F7"')
        self.assertContains(response, '<span class="result__mixture">Mixture</span>', html=True)
        self.assertContains(response, "MB-1 will be kept as an alias.")
        self.assertContains(response, "F7 won't be kept as an alias: it is already one of its names.")

    def test_the_same_material_twice_is_a_problem_line_by_smiles_or_by_lcr_id(self):
        for table in [f"RM734\t{RM734}\nMB-1\t{self.rm734.accession}", f"F7\t{self.f7.accession}\nF7\t{self.f7.accession}"]:
            with self.subTest(table):
                line = self.line(table)

                self.assertEqual(line.problem, "Same material as line 1: give each material once.")

    def test_a_mixtures_line_makes_a_card_whose_run_makes_it_one_of_the_references_materials(self):
        table = {"table": f"F7, {self.f7.accession}, Iso 120 N 95 NF 40 Cr", "direction": "cooling", "rate": "", "method": ""}
        cards = CompoundTableForm(table, reference=self.article).cards_data()  # the cards, as Make cards sends them back

        response = self.client.post(reverse("repo:add_compound", args=[self.article.pk]), {**cards, "save": ""}, follow=True)

        self.assertEqual(self.messages(response), [f"Saved 1 run for {self.f7}."])
        [run] = self.f7.phase_sequences.all()
        self.assertEqual(
            (run.reference, run.direction, run.standard_sequence), (self.article, "cooling", "Iso 120 N 95 NF 40 Cr")
        )
        # Back on the Add data page, F7 is listed.
        self.assertEqual(list(response.context["materials"]), [self.f7])
        page = self.client.get(reverse("repo:material", args=[self.f7.pk]))
        self.assertContains(page, '<p class="ribbon-row__text">Iso 120 N 95 N<sub>F</sub> 40 Cr</p>', html=True)
        self.assertContains(page, '<span class="ribbon-row__run">Cooling</span>', html=True)
