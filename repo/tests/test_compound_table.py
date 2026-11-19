"""A reference's Add data page reads a pasted table of compounds, where a material already in
FFDB, such as a mixture, can be given by its LCR ID: Check shows what each line would add and
saves nothing; Save adds the lines without a problem and lists the others."""

from urllib.parse import urlencode

from django.test import SimpleTestCase
from django.urls import reverse

from repo.forms import table_cells, table_rows
from repo.models import Material, PhaseSequence, is_compound_number
from repo.tests.test_add_data import FIVE_CB, LOGIN, AddDataTestCase
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
        """Post the table with the Check or Save button, as summaries unless direction says otherwise."""
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
        self.assertContains(response, "Already in FFDB from this reference: not added again.")
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


class SaveTests(CompoundTableTestCase):
    def test_save_adds_the_lines_without_a_problem_and_lists_the_others(self):
        response = self.send(THREE_LINES, "save", follow=True)

        self.assertRedirects(response, self.add_page(self.article))
        five_cb = Material.objects.get(name="5CB")
        self.assertFalse(Material.objects.filter(name="10b").exists())
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "MB-1")
        sequences = PhaseSequence.objects.order_by("material_id")
        self.assertEqual(
            [(s.material, s.reference, s.standard_sequence, s.entered_by) for s in sequences],
            [
                (self.rm734, self.article, "Cr 139.8 NF 132.7 N 187.9 Iso", self.curator),
                (five_cb, self.article, "Cr 24 N 35.3 Iso", self.curator),
            ],
        )
        self.assertEqual(self.messages(response), [
            "Saved from the table: 1 new compound, 2 sequences, 1 alias.",
            f"Line 4 not saved: Phase sequence: Unknown phase label: NFF. Use a label from the phase "
            f"vocabulary, or add it there first. The line: 10b\t{DIO}\tK 80 NFF 120 I",
        ])

    def test_saving_the_same_table_again_adds_nothing(self):
        self.send(THREE_LINES, "save")

        response = self.send(THREE_LINES, "save", follow=True)

        self.assertEqual(Material.objects.count(), 2)
        self.assertEqual(PhaseSequence.objects.count(), 2)
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "MB-1")
        self.assertIn("Saved from the table: 0 new compounds, 0 sequences, 0 aliases.", self.messages(response))

    def test_a_cooling_run_and_a_heating_run_are_saved_as_they_were_observed(self):
        self.send(f"RM734\t{RM734}\tIso 187.9 N 132.7 NF 70 K", "save", direction="cooling", rate="5", method="DSC")
        self.send(f"RM734\t{RM734}\tK 75 NF 133 N 188 I", "save", direction="heating", rate="10", method="DSC")

        runs = self.rm734.phase_sequences.order_by("pk")
        self.assertEqual(
            [(run.direction, run.standard_sequence, run.rate_k_per_min, run.method) for run in runs],
            [("cooling", "Iso 187.9 N 132.7 NF 70 Cr", 5, "DSC"), ("heating", "Cr 75 NF 133 N 188 Iso", 10, "DSC")],
        )
        page = self.client.get(reverse("repo:material", args=[self.rm734.pk]))
        self.assertContains(page, "<td>Cooling, 5 K/min, DSC</td>", html=True)
        self.assertContains(page, "<td>Heating, 10 K/min, DSC</td>", html=True)

    def test_a_bare_compound_number_names_a_new_compound_but_isnt_kept_as_an_alias(self):
        self.send(f"10a\t{FIVE_CB}\n10b\t{RM734}", "save")

        self.assertTrue(Material.objects.filter(name="10a", smiles=FIVE_CB).exists())
        self.rm734.refresh_from_db()
        self.assertEqual(self.rm734.aliases, "")

    def test_the_stereochemistry_warning_names_the_new_material_once_saved(self):
        s_octanol = Material.objects.create(name="S-2-octanol", smiles="C[C@H](O)CCCCCC")

        response = self.send("rac-2-octanol\tCC(O)CCCCCC", "save", follow=True)

        new = Material.objects.get(name="rac-2-octanol")
        self.assertTrue(self.messages(response)[1].startswith(
            f"{new.accession} has the same connectivity as {s_octanol.accession}"
        ))

    def test_a_logged_out_visitor_cant_save(self):
        self.client.logout()

        response = self.send(THREE_LINES, "save")

        self.assertRedirects(response, f"{LOGIN}?next={self.add_page(self.article)}")
        self.assertEqual(Material.objects.count(), 1)
        self.assertFalse(PhaseSequence.objects.exists())


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

    def test_a_mixtures_run_goes_in_by_its_lcr_id_and_makes_it_one_of_the_references_materials(self):
        response = self.send(f"F7, {self.f7.accession}, Iso 120 N 95 NF 40 Cr", "save", follow=True, direction="cooling")

        self.assertIn("Saved from the table: 0 new compounds, 1 sequence, 0 aliases.", self.messages(response))
        [run] = self.f7.phase_sequences.all()
        self.assertEqual(
            (run.reference, run.direction, run.standard_sequence), (self.article, "cooling", "Iso 120 N 95 NF 40 Cr")
        )
        # Back on the Add data page, F7 is listed, and both menus offer it.
        self.assertEqual(list(response.context["materials"]), [self.f7])
        self.assertEqual(list(response.context["choice"].fields["material"].queryset), [self.f7])
        self.assertEqual(list(response.context["curve_choice"].fields["material"].queryset), [self.f7])
        page = self.client.get(reverse("repo:material", args=[self.f7.pk]))
        self.assertContains(page, "<td>Iso 120 N 95 NF 40 Cr</td>", html=True)
        self.assertContains(page, "<td>Cooling</td>", html=True)

    def test_a_ps_value_for_the_mixture_shows_on_the_ps_page_under_its_name(self):
        PhaseSequence.objects.create(
            material=self.f7, reference=self.article, sequence="Iso 120 N 95 NF 40 Cr", direction="cooling"
        )
        # Chosen from the menu (material=), which offers F7 now it has a run from the article.
        query = urlencode({"material": self.f7.pk, "property": "ps", "kind": "measured", "temperature": "yes"})
        boxes = {"qualifier": "exact", "value": "4.4", "temperature_c": "30", "method": "current reversal",
                 "condition_frequency_Hz": "50", "condition_cell_gap_um": "8", "taken_from": "table", "location": "Table 1"}

        response = self.client.post(f"{reverse('repo:add_value', args=[self.article.pk])}?{query}", boxes, follow=True)

        self.assertEqual(self.messages(response), [f"Saved a Ps value for {self.f7.accession}."])
        page = self.client.get(reverse("repo:property", args=["ps"]))
        link = reverse("repo:material", args=[self.f7.pk])
        self.assertContains(page, f'<a href="{link}">{self.f7.accession}</a>', html=True)
        self.assertContains(page, '<span class="result__name">F7</span>', html=True)
