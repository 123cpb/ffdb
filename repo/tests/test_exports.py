import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from repo import chem
from repo.models import Material, MixtureComponent, PhaseSequence, Series
from repo.tests.test_property_pages import PS_CONDITIONS, PropertyPagesTestCase
from repo.tests.test_values import add

REFERENCE_COLUMNS = ["reference", "reference_kind", "reference_identifier", "reference_link"]
MIXTURE = Material.Kind.MIXTURE
MOL, WT = MixtureComponent.Unit.MOL, MixtureComponent.Unit.WT


class ExportTestCase(PropertyPagesTestCase):
    """The three Ps curves of PropertyPagesTestCase, with phase sequences. DIO has two, one
    from each reference: Chen 2022's reports NX, which the vocabulary calls NS. RM734 has
    one, from Chen 2022. 5CB's has no NF, so a search for NF leaves 5CB out."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.dio.aliases = "DIO-1, Compound 4"
        cls.dio.save()
        for material, reference, sequence in [
            (cls.dio, cls.chen, "K 34.4 NF 68.8 NX 84.5 N 173.6 I"),
            (cls.dio, cls.other, "K 34 NF 69 N 174 I"),
            (cls.rm734, cls.chen, "K 139.8 NF 132.7 N 187.9 I"),
            (cls.five_cb, cls.other, "K 24 N 35.3 I"),
        ]:
            PhaseSequence.objects.create(material=material, reference=reference, sequence=sequence)

    def download(self, url, **params):
        """A download's header and its rows, each a dict keyed by the header."""
        # utf-8-sig reads the byte-order mark as a mark, not as part of the first heading.
        text = self.client.get(url, params).content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return reader.fieldnames, list(reader)


class FileTests(ExportTestCase):
    def test_a_download_is_a_csv_file_that_starts_with_the_byte_order_mark(self):
        response = self.client.get(reverse("repo:property_values_csv", args=["ps"]))

        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="ffdb-ps-values.csv"')
        self.assertTrue(response.content.startswith("﻿".encode()))
        self.assertIn("µC/cm²", response.content.decode("utf-8-sig"))

    def test_every_row_says_when_it_was_exported(self):
        _, rows = self.download(reverse("repo:materials_csv"))

        [stamp] = {row["exported_at"] for row in rows}
        self.assertLess(timezone.now() - datetime.fromisoformat(stamp), timedelta(minutes=1))


class MaterialsFileTests(ExportTestCase):
    url = reverse("repo:materials_csv")

    def test_one_row_per_material_in_lcr_order(self):
        header, rows = self.download(self.url)

        self.assertEqual(header, [
            "material_id", "name", "aliases", "material_kind", "composition_status", "components",
            "other_components", "smiles", "inchikey", "formula", "molar_mass_g_mol", "lc_phases",
            "n_references", "exported_at",
        ])
        self.assertEqual([row["name"] for row in rows], ["DIO", "RM734", "5CB"])

    def test_a_row_gives_the_structure_phases_and_number_of_references(self):
        rows = {row["name"]: row for row in self.download(self.url)[1]}

        dio = rows["DIO"]
        formula, mass = chem.formula_and_mass(self.dio.canonical_smiles)
        self.assertEqual(dio["material_id"], self.dio.accession)
        self.assertEqual(dio["aliases"], "DIO-1; Compound 4")
        # A compound has no composition: its stored status, listed, is the default, not a fact.
        self.assertEqual(
            [dio[key] for key in ("material_kind", "composition_status", "components", "other_components")],
            ["compound", "", "", ""],
        )
        self.assertEqual((dio["smiles"], dio["inchikey"]), (self.dio.canonical_smiles, self.dio.inchikey))
        self.assertEqual((dio["formula"], dio["molar_mass_g_mol"]), (formula, str(round(mass, 3))))
        self.assertEqual(dio["lc_phases"], "N; NS; NF")  # the vocabulary's order, each once
        # Chen 2022 gives DIO a sequence and a curve, but counts once; the other reference
        # gives a sequence.
        self.assertEqual(dio["n_references"], "2")
        self.assertEqual((rows["5CB"]["lc_phases"], rows["5CB"]["n_references"]), ("N", "1"))

    def test_it_holds_the_materials_the_search_finds(self):
        for params, names in [
            ({"phase": "NF"}, ["DIO", "RM734"]),
            ({"q": "5CB"}, ["5CB"]),
            ({"sub": "((("}, []),  # RDKit can't read it: the header, and no rows
        ]:
            with self.subTest(params=params):
                self.assertEqual([row["name"] for row in self.download(self.url, **params)[1]], names)

    def test_more_materials_take_no_more_queries(self):
        with CaptureQueriesContext(connection) as before:
            self.download(self.url)
        extra = Material.objects.create(name="6CB", smiles="CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1")
        PhaseSequence.objects.create(material=extra, reference=self.other, sequence="K 14.5 N 29 I")
        with CaptureQueriesContext(connection) as after:
            self.download(self.url)

        self.assertEqual(len(after), len(before))


class SequencesFileTests(ExportTestCase):
    url = reverse("repo:sequences_csv")

    def test_one_row_per_sequence_as_reported_and_standardised_with_its_reference(self):
        header, rows = self.download(self.url)

        self.assertEqual(header, [
            "material_id", "material", "material_kind", "sequence_reported", "sequence_standard",
            "direction", "rate_k_per_min", "method", "lowest_temperature_c", "clearing_point_c",
            "melting_point_c", *REFERENCE_COLUMNS, "exported_at",
        ])
        # By LCR ID, then by reference identifier: 10.1039/... comes before 10.1080/...
        self.assertEqual([row["material"] for row in rows], ["DIO", "DIO", "RM734", "5CB"])
        chen = rows[1]
        del chen["exported_at"]
        self.assertEqual(chen, {
            "material_id": self.dio.accession,
            "material": "DIO",
            "material_kind": "compound",
            "sequence_reported": "K 34.4 NF 68.8 NX 84.5 N 173.6 I",
            "sequence_standard": "Cr 34.4 NF 68.8 NS 84.5 N 173.6 Iso",
            "direction": "summary",
            "rate_k_per_min": "",
            "method": "",
            "lowest_temperature_c": "",
            "clearing_point_c": "173.6",
            "melting_point_c": "34.4",
            "reference": "Chen 2022, Liq. Cryst.",
            "reference_kind": "article",
            "reference_identifier": "10.1080/02678292.2022.2058101",
            "reference_link": "https://doi.org/10.1080/02678292.2022.2058101",
        })

    def test_it_gives_every_sequence_of_the_materials_the_search_finds(self):
        rows = self.download(self.url, phase="NF")[1]

        self.assertEqual([row["material"] for row in rows], ["DIO", "DIO", "RM734"])


# DIO's two sequences as a values file lists them: by reference identifier, so 10.1039/...
# comes before Chen 2022's 10.1080/...; standardised, so Chen's NX is NS; and each with how it
# was observed and its reference. The second reference has no author or year, so its label
# is its identifier.
DIO_SEQUENCES = (
    "Cr 34 NF 69 N 174 Iso (Summary, 10.1039/d2sm01298g); "
    "Cr 34.4 NF 68.8 NS 84.5 N 173.6 Iso (Summary, Chen 2022, Liq. Cryst.)"
)


class ValuesFileTests(ExportTestCase):
    ps_url = reverse("repo:property_values_csv", args=["ps"])

    def test_ps_has_one_row_per_point_of_its_three_curves(self):
        header, rows = self.download(self.ps_url)

        self.assertEqual(header, [
            "material_id", "material", "material_kind", "smiles", "phase_sequences", "property",
            "symbol", "series_id", "qualifier", "value", "unit", "uncertainty", "temperature_c",
            "variable", "x", "x_unit", "kind", "method", "direction", "conditions",
            "conditions_json", "taken_from", "location", "digitised", *REFERENCE_COLUMNS,
            "exported_at",
        ])
        curves = [self.curves[name].pk for name in ("DIO", "RM734", "5CB")]
        self.assertEqual([int(row["series_id"]) for row in rows], [pk for pk in curves for _ in range(3)])
        self.assertEqual([row["temperature_c"] for row in rows[:3]], ["60.0", "75.0", "89.86"])

    def test_a_row_gives_the_point_its_conditions_and_reference(self):
        row = self.download(self.ps_url)[1][0]
        del row["exported_at"]

        self.assertEqual(row, {
            "material_id": self.dio.accession, "material": "DIO", "material_kind": "compound",
            "smiles": self.dio.canonical_smiles, "phase_sequences": DIO_SEQUENCES,
            "property": "Spontaneous polarisation", "symbol": "Ps", "series_id": str(self.curves["DIO"].pk),
            "qualifier": "exact", "value": "6.08", "unit": "µC/cm²", "uncertainty": "",
            "temperature_c": "60.0", "variable": "temperature", "x": "60.0", "x_unit": "°C",
            "kind": "measured", "method": "current reversal", "direction": "unspecified",
            "conditions": "cell gap: 8 µm, frequency: 50 Hz",
            "conditions_json": '{"cell_gap_um": 8, "frequency_Hz": 50}',
            "taken_from": "", "location": "", "digitised": "no",
            "reference": "Chen 2022, Liq. Cryst.", "reference_kind": "article",
            "reference_identifier": "10.1080/02678292.2022.2058101",
            "reference_link": "https://doi.org/10.1080/02678292.2022.2058101",
        })

    def test_x_is_the_variable_and_is_blank_for_a_value_against_nothing(self):
        sweep = add(
            self.dio, self.chen, "eps-parallel", "frequency",
            conditions={"cell_gap_um": 10, "electrode_coating": "ITO"},
        )
        sweep.points.create(value=5000, temperature_c=80, x=1000)
        computed = add(
            self.five_cb, self.other, "dipole-moment", "none", Series.Kind.COMPUTED,
            method="DFT", conditions={"conformer_protocol": "lowest", "solvent": "gas phase"},
        )
        computed.points.create(value=5.9)

        rows = {row["symbol"]: row for row in self.download(reverse("repo:values_csv"))[1]}

        self.assertEqual([rows["ε∥"][key] for key in ("temperature_c", "x", "x_unit")], ["80.0", "1000.0", "Hz"])
        self.assertEqual([rows["μ"][key] for key in ("temperature_c", "x", "x_unit")], ["", "", ""])
        self.assertEqual((rows["μ"]["variable"], rows["μ"]["kind"]), ("none", "computed"))

    def test_the_materials_page_gives_every_value_of_the_materials_it_finds(self):
        dn = add(self.rm734, self.chen, "birefringence", conditions={"wavelength_nm": 550})
        dn.points.create(value=0.2, temperature_c=140)

        nf = self.download(reverse("repo:values_csv"), phase="NF")[1]
        rm734 = self.download(reverse("repo:values_csv"), q="RM734")[1]

        self.assertEqual([row["material"] for row in nf], ["DIO"] * 3 + ["RM734"] * 4)
        self.assertEqual([row["symbol"] for row in rm734], ["Ps"] * 3 + ["Δn"])

    def test_with_a_property_chosen_the_materials_page_gives_only_its_values(self):
        dn = add(self.rm734, self.chen, "birefringence", conditions={"wavelength_nm": 550})
        dn.points.create(value=0.2, temperature_c=140)
        add(self.dio, self.chen, "birefringence").points.create(value=0.3, temperature_c=100)

        materials = self.download(reverse("repo:materials_csv"), prop="birefringence")[1]
        values = self.download(reverse("repo:values_csv"), prop="birefringence")[1]

        self.assertEqual([row["name"] for row in materials], ["DIO", "RM734"])
        self.assertEqual([(row["material"], row["symbol"]) for row in values], [("DIO", "Δn"), ("RM734", "Δn")])

    def test_both_values_files_give_every_sequence_of_each_rows_material(self):
        for url in (self.ps_url, reverse("repo:values_csv")):
            with self.subTest(url=url):
                cells = {row["material"]: row["phase_sequences"] for row in self.download(url)[1]}
                self.assertEqual(cells["DIO"], DIO_SEQUENCES)
                self.assertEqual(cells["RM734"], "Cr 139.8 NF 132.7 N 187.9 Iso (Summary, Chen 2022, Liq. Cryst.)")

    def test_a_run_says_how_it_was_observed(self):
        PhaseSequence.objects.create(
            material=self.five_cb, reference=self.chen, direction=PhaseSequence.Direction.COOLING,
            sequence="I 35 N 20 K", rate_k_per_min=5, method="DSC",
        )

        rows = self.download(self.ps_url)[1]

        self.assertEqual(rows[-1]["phase_sequences"], (
            "Cr 24 N 35.3 Iso (Summary, 10.1039/d2sm01298g); "
            "Iso 35 N 20 Cr (Cooling, 5 K/min, DSC, Chen 2022, Liq. Cryst.)"
        ))

    def test_a_material_with_no_sequence_has_a_blank_cell(self):
        six_cb = Material.objects.create(name="6CB", smiles="CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1")
        add(six_cb, self.other, "ps", **PS_CONDITIONS).points.create(value=1.0, temperature_c=30)

        rows = self.download(self.ps_url)[1]

        self.assertEqual([row["phase_sequences"] for row in rows if row["material"] == "6CB"], [""])

    def test_more_materials_with_sequences_take_no_more_queries(self):
        with CaptureQueriesContext(connection) as before:
            self.download(self.ps_url)
        six_cb = Material.objects.create(name="6CB", smiles="CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1")
        add(six_cb, self.other, "ps", **PS_CONDITIONS).points.create(value=1.0, temperature_c=30)
        for reference in (self.chen, self.other):
            PhaseSequence.objects.create(material=six_cb, reference=reference, sequence="K 14 N 29 I")
        with CaptureQueriesContext(connection) as after:
            self.download(self.ps_url)

        self.assertEqual(len(after), len(before))

    def test_an_unknown_property_is_not_found(self):
        response = self.client.get(reverse("repo:property_values_csv", args=["no-such-property"]))

        self.assertEqual(response.status_code, 404)


class MixtureFileTests(ExportTestCase):
    """F7, a mixture of DIO and RM734 at 70 and 30 mol%, with a cooling run and a Ps value
    from Chen 2022, and M5, whose composition isn't disclosed and which has no data."""

    url = reverse("repo:materials_csv")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.f7 = Material.objects.create(kind=MIXTURE, name="F7")
        # The smaller first, so the file's largest-first order isn't just the order added.
        MixtureComponent.objects.create(mixture=cls.f7, component=cls.rm734, fraction=30, unit=MOL)
        MixtureComponent.objects.create(mixture=cls.f7, component=cls.dio, fraction=70, unit=MOL)
        PhaseSequence.objects.create(
            material=cls.f7, reference=cls.chen, direction=PhaseSequence.Direction.COOLING,
            sequence="Iso 120 N 95 NF 40 Cr",
        )
        add(cls.f7, cls.chen, "ps", **PS_CONDITIONS).points.create(value=4.0, temperature_c=50)
        cls.m5 = Material.objects.create(
            kind=MIXTURE, name="M5", composition_status=Material.CompositionStatus.NOT_DISCLOSED
        )

    def material_row(self, material):
        """material's row in the materials file."""
        rows = {row["material_id"]: row for row in self.download(self.url)[1]}
        return rows[material.accession]

    def test_a_mixtures_row_gives_its_components_largest_first_and_no_structure(self):
        f7 = self.material_row(self.f7)

        self.assertEqual((f7["material_kind"], f7["composition_status"]), ("mixture", "listed"))
        self.assertEqual(
            f7["components"], f"{self.dio.accession} DIO, 70 mol%; {self.rm734.accession} RM734, 30 mol%"
        )
        for key in ("other_components", "smiles", "inchikey", "formula", "molar_mass_g_mol"):
            self.assertEqual(f7[key], "", key)
        # Its phases and references are found as a compound's are.
        self.assertEqual((f7["lc_phases"], f7["n_references"]), ("N; NF", "1"))

    def test_a_composition_that_is_not_disclosed_has_no_components(self):
        m5 = self.material_row(self.m5)

        self.assertEqual(
            [m5[key] for key in ("composition_status", "components", "other_components")],
            ["not_disclosed", "", ""],
        )

    def test_partly_known_gives_the_other_components_too(self):
        f8 = Material.objects.create(
            kind=MIXTURE, name="F8", composition_status=Material.CompositionStatus.PARTLY_KNOWN,
            other_components="A nematic host, 90 wt%",
        )
        MixtureComponent.objects.create(mixture=f8, component=self.rm734, fraction=10, unit=WT)

        row = self.material_row(f8)

        self.assertEqual(
            [row[key] for key in ("composition_status", "components", "other_components")],
            ["partly_known", f"{self.rm734.accession} RM734, 10 wt%", "A nematic host, 90 wt%"],
        )

    def test_a_component_with_no_name_is_its_lcr_id(self):
        unnamed = Material.objects.create(smiles="CCCCCCc1ccc(-c2ccc(C#N)cc2)cc1")
        f8 = Material.objects.create(kind=MIXTURE, name="F8")
        MixtureComponent.objects.create(mixture=f8, component=unnamed, fraction=12.5, unit=WT)

        self.assertEqual(self.material_row(f8)["components"], f"{unnamed.accession}, 12.5 wt%")

    def test_every_download_says_which_rows_are_mixtures(self):
        urls = [reverse(f"repo:{name}") for name in ("materials_csv", "sequences_csv", "values_csv")]
        for url in [*urls, reverse("repo:property_values_csv", args=["ps"])]:
            with self.subTest(url=url):
                kinds = defaultdict(set)  # every kind a material's rows give, each once
                for row in self.download(url)[1]:
                    kinds[row["material_id"]].add(row["material_kind"])
                self.assertEqual(kinds[self.f7.accession], {"mixture"})
                self.assertEqual(kinds[self.dio.accession], {"compound"})

    def test_more_mixtures_take_no_more_queries(self):
        with CaptureQueriesContext(connection) as before:
            self.download(self.url)
        f8 = Material.objects.create(kind=MIXTURE, name="F8")
        MixtureComponent.objects.create(mixture=f8, component=self.five_cb, fraction=50, unit=MOL)
        MixtureComponent.objects.create(mixture=f8, component=self.rm734, fraction=50, unit=MOL)
        with CaptureQueriesContext(connection) as after:
            self.download(self.url)

        self.assertEqual(len(after), len(before))


class LinkTests(ExportTestCase):
    def test_the_materials_page_downloads_keep_its_search(self):
        response = self.client.get(reverse("repo:materials"), {"phase": "NF", "page": 1})

        for name in ("materials_csv", "sequences_csv", "values_csv"):
            self.assertContains(response, f'href="{reverse(f"repo:{name}")}?phase=NF"')

    def test_they_keep_the_property_and_the_values_link_names_it(self):
        response = self.client.get(reverse("repo:materials"), {"prop": "ps"})

        for name in ("materials_csv", "sequences_csv"):
            self.assertContains(response, f'href="{reverse(f"repo:{name}")}?prop=ps"')
        self.assertContains(response, f'href="{reverse("repo:values_csv")}?prop=ps">Ps values</a>')

    def test_without_a_search_they_hold_every_material(self):
        response = self.client.get(reverse("repo:materials"))

        self.assertContains(response, f'href="{reverse("repo:values_csv")}"')

    def test_the_property_page_links_to_its_values(self):
        response = self.client.get(reverse("repo:property", args=["ps"]))

        self.assertContains(response, f'href="{reverse("repo:property_values_csv", args=["ps"])}"')
