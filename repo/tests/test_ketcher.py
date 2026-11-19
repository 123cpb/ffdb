import hashlib
import io
import tempfile
import zipfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.staticfiles import finders
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from repo.management.commands import fetch_ketcher
from repo.tests.test_add_data import AddDataTestCase

COMMAND = "repo.management.commands.fetch_ketcher"

# A stand-in for the release zip, laid out the same way, with a few bytes in each file.
KEPT = [
    "index.html", "favicon.ico", "static/css/main.96b8.css", "static/js/main.e47c.js",
    "static/js/main.e47c.js.LICENSE.txt", "static/js/127.aa15.chunk.js",
]
DROPPED = [
    "duo.html", "popup.html", "closable.html", "iframe.html", "serve.json",
    "static/css/duo.5970.css", "static/js/popup.3926.js", "static/js/closable.4741.js.LICENSE.txt",
]


def make_zip(names):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, f"contents of {name}")
    return buffer.getvalue()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def files_in(folder):
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())


class FetchKetcherTests(SimpleTestCase):
    """The command without the network: download() is replaced by a dictionary lookup."""

    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / "ketcher"
        self.zip = make_zip(KEPT + DROPPED)
        self.downloads = {
            fetch_ketcher.ZIP_URL: self.zip,
            fetch_ketcher.NOTICE_URL.format("LICENSE"): b"Apache License 2.0",
            fetch_ketcher.NOTICE_URL.format("NOTICE"): b"Ketcher, EPAM Systems",
        }
        self.enterContext(mock.patch(f"{COMMAND}.download", side_effect=self.downloads.__getitem__))
        self.enterContext(mock.patch(f"{COMMAND}.FOLDER", self.folder))
        self.enterContext(mock.patch(f"{COMMAND}.ZIP_SHA256", sha256(self.zip)))
        self.enterContext(mock.patch.dict(f"{COMMAND}.NOTICES", {
            "LICENSE": sha256(b"Apache License 2.0"), "NOTICE": sha256(b"Ketcher, EPAM Systems"),
        }))

    def test_unpack_keeps_the_editor_and_drops_the_other_entry_points(self):
        self.folder.mkdir()
        (self.folder / "main.old.js").write_text("left by an older version")

        written = fetch_ketcher.unpack(self.zip, self.folder)

        self.assertEqual(files_in(self.folder), sorted(KEPT))
        self.assertEqual(written, len(KEPT))

    def test_command_writes_the_editor_with_its_licence_and_notice(self):
        out = StringIO()

        call_command("fetch_ketcher", stdout=out)

        self.assertEqual(files_in(self.folder), sorted(KEPT + ["LICENSE", "NOTICE"]))
        self.assertEqual((self.folder / "LICENSE").read_bytes(), b"Apache License 2.0")
        self.assertIn(f"Ketcher {fetch_ketcher.VERSION}: 8 files", out.getvalue())

    def test_a_file_that_fails_its_checksum_stops_everything(self):
        self.folder.mkdir()
        (self.folder / "index.html").write_text("the version already here")
        self.downloads[fetch_ketcher.NOTICE_URL.format("NOTICE")] = b"something else"

        with self.assertRaisesMessage(CommandError, "NOTICE doesn't match its SHA-256"):
            call_command("fetch_ketcher", stdout=StringIO())

        # The zip passed its check, but nothing was written: the old folder is untouched.
        self.assertEqual(files_in(self.folder), ["index.html"])


class EditorFilesTests(SimpleTestCase):
    def test_the_editor_and_its_licence_are_in_the_static_files(self):
        for path in ("repo/ketcher/index.html", "repo/ketcher/LICENSE", "repo/ketcher/NOTICE"):
            self.assertTrue(
                finders.find(path), f"{path} is missing: run .venv/bin/python manage.py fetch_ketcher"
            )

    def test_the_draw_script_is_in_the_static_files(self):
        # {% static %} writes the address whether or not the file is there, so this checks it is.
        self.assertTrue(finders.find("repo/draw.js"))


class CreditTests(TestCase):
    def test_pages_credit_ketcher(self):
        response = self.client.get(reverse("repo:home"))

        self.assertContains(
            response, 'Structure editor: <a href="https://github.com/epam/ketcher">Ketcher</a>'
        )


class DrawButtonTests(AddDataTestCase):
    """What Django can see of the Draw button. Drawing itself is checked in a browser."""

    def test_curators_get_a_hidden_draw_button_its_dialog_and_its_script(self):
        response = self.client.get(self.add_page(self.article))

        # hidden until draw.js shows it, so without JavaScript the page is as it was.
        self.assertContains(
            response,
            '<button type="button" class="secondary-button" data-draw data-table="id_table" hidden>'
            "Draw a compound</button>",
            html=True,
        )
        self.assertContains(response, '<dialog class="draw-dialog" id="draw-dialog"')
        self.assertContains(response, 'data-src="/static/repo/ketcher/index.html"')
        self.assertContains(response, '<script src="/static/repo/draw.js" defer></script>', html=True)
