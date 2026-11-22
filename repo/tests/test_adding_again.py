"""A compound whose structure was a retired ID's is refused, naming the old ID: in the admin,
added or with its SMILES changed, and in the compound table on Add data. Otherwise one
structure would have two IDs. The import skips it too (test_import.py). A material that was
never public is the exception: it leaves no trace, so its structure can be added again."""

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse

from repo.models import Material, RetiredID, accession
from repo.tests.test_add_data import FIVE_CB
from repo.tests.test_compound_table import CompoundTableTestCase
from repo.tests.test_merge import WRONG_RM734
from repo.tests.test_values import DIO


class AddingAgainTests(CompoundTableTestCase):
    """RM734, and two IDs retired: 5CB, deleted, and RM-734, a duplicate of RM734 entered with
    the wrong structure, merged into it. The curator is logged in for the table, and the
    admin tests log the admin in."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_superuser("admin", password="pw")
        cb5 = Material.objects.create(name="5CB", smiles=FIVE_CB, listed=True)
        cls.retired_number = cb5.pk  # before retiring, which takes its pk
        cls.retired = accession(cls.retired_number)
        cb5.retire(cls.admin)
        duplicate = Material.objects.create(name="RM-734", smiles=WRONG_RM734, listed=True)
        cls.merged = accession(duplicate.pk)
        duplicate.merge_into(cls.rm734, cls.admin)
        cls.retired_message = f"This structure was {cls.retired} (5CB), now retired: an admin can restore {cls.retired}."
        cls.merged_message = (
            f"This structure was {cls.merged} (RM-734), merged into {cls.rm734.accession}: add the data there."
        )

    def admin_form(self, material, name, smiles):
        """Fill in the admin's Add material form, or material's Change form, and press Save.
        A compound with no sequence or component rows."""
        url = reverse("admin:repo_material_change", args=[material.pk]) if material else reverse("admin:repo_material_add")
        data = {
            "name": name, "aliases": "", "smiles": smiles, "notes": "",
            "kind": "compound", "composition_status": "listed", "other_components": "",
            "components-TOTAL_FORMS": "0", "components-INITIAL_FORMS": "0",
            "phase_sequences-TOTAL_FORMS": "0", "phase_sequences-INITIAL_FORMS": "0",
        }
        self.client.force_login(self.admin)
        return self.client.post(url, data, follow=True)

    def test_the_admin_refuses_a_retired_or_merged_structure_naming_the_old_id(self):
        before = Material.objects.count()
        for smiles, message in [(FIVE_CB, self.retired_message), (WRONG_RM734, self.merged_message)]:
            with self.subTest(message):
                response = self.admin_form(None, "Again", smiles)

                self.assertContains(response, message)
                self.assertEqual(Material.objects.count(), before)

    def test_the_admin_refuses_a_smiles_changed_to_a_retired_structure_but_not_a_name_change(self):
        dio = Material.objects.create(name="DIO", smiles=DIO)

        response = self.admin_form(dio, "DIO", FIVE_CB)

        self.assertContains(response, self.retired_message)
        dio.refresh_from_db()
        self.assertEqual(dio.smiles, DIO)

        # A compound that has a retired structure already, as one added before this check
        # could: only a change of structure is refused, so its name can still be changed.
        again = Material.objects.create(name="5CB again", smiles=FIVE_CB)

        self.admin_form(again, "5CB, added again", FIVE_CB)

        again.refresh_from_db()
        self.assertEqual(again.name, "5CB, added again")

    def test_the_table_makes_a_retired_or_merged_structure_a_problem_line(self):
        table = f"5CB\t{FIVE_CB}\nRM-734\t{WRONG_RM734}\nDIO\t{DIO}"

        lines = self.send(table).context["lines"]

        self.assertEqual([line.status for line in lines], ["problem", "problem", "new"])
        self.assertEqual([lines[0].problem, lines[1].problem], [self.retired_message, self.merged_message])

    def test_a_structure_retired_while_hidden_can_be_added_again_and_then_not_restored(self):
        hidden = Material.objects.create(name="DIO", smiles=DIO)  # no data and not listed: hidden
        number = hidden.pk
        hidden.retire(self.admin)

        self.assertEqual(self.line(f"DIO\t{DIO}").status, "new")  # nothing names the old ID
        self.admin_form(None, "DIO again", DIO)

        again = Material.objects.get(name="DIO again")
        with self.assertRaisesMessage(ValidationError, f"its structure is now in FFDB as {again.accession}"):
            RetiredID.objects.get(number=number).restore()

    def test_once_restored_the_structure_is_found_in_ffdb_under_its_old_id(self):
        RetiredID.objects.get(number=self.retired_number).restore()

        line = self.line(f"5CB\t{FIVE_CB}")

        self.assertEqual((line.status, line.match.accession), ("in_ffdb", self.retired))
