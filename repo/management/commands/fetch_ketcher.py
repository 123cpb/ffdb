"""manage.py fetch_ketcher: download the Ketcher structure editor (EPAM, Apache License 2.0)
from its GitHub release, check every file against the SHA-256 below, and only then unpack it
into repo/static/repo/ketcher/, with Ketcher's licence and notice. Git ignores that folder,
so run this once after cloning, after an upgrade, and in a deployment's build step before
collectstatic. FFDB serves the editor from its own address because its pages talk to it
inside an iframe, which browsers allow only within one site."""

import hashlib
import io
import shutil
import zipfile
from urllib.error import URLError
from urllib.request import urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# To upgrade, change the version and every SHA-256. GitHub lists each release file's SHA-256
# on the release page. For LICENSE and NOTICE, hash the files at the new tag.
VERSION = "3.18.0"
ZIP_URL = f"https://github.com/epam/ketcher/releases/download/v{VERSION}/ketcher-standalone-{VERSION}.zip"
ZIP_SHA256 = "484e7f10a0e74808ae5f43f0e6448db6f4d884bdd2189aa271108474167a3e7e"
# The release zip has no licence or notice file, so these come from Ketcher's source at the
# same tag. Apache 2.0 asks for both to go wherever the editor goes.
NOTICE_URL = f"https://raw.githubusercontent.com/epam/ketcher/v{VERSION}/{{}}"
NOTICES = {
    "LICENSE": "3d54b234fa88c11d1d96380ff5fe29f3c5d8e1078ef914dfe8185ce4b1fbeac7",
    "NOTICE": "ab82986438fc66fa2b40836415b734ff018b1e42ee3a18434b27bdf1c42eb368",
}

FOLDER = settings.BASE_DIR / "repo" / "static" / "repo" / "ketcher"
# Release files FFDB doesn't use. Each of the duo, popup and closable entry points is a second
# copy of the whole editor (about 30 MB). iframe.html is a demo page, and serve.json is a
# setting for a Node web server.
UNUSED = ("duo.", "popup.", "closable.", "iframe.html", "serve.json")


def download(url):
    with urlopen(url, timeout=120) as response:
        return response.read()


def check(data, sha256, name):
    """Refuse a file that isn't byte for byte the one this command was written for."""
    if hashlib.sha256(data).hexdigest() != sha256:
        raise CommandError(
            f"{name} doesn't match its SHA-256 in fetch_ketcher.py, so it isn't the file this "
            "command expects. Nothing was written."
        )


def unpack(data, folder):
    """Empty folder, so no file from an older version is left behind, then write the zip's
    files into it, apart from the unused ones. Returns how many files were written."""
    if folder.exists():
        shutil.rmtree(folder)
    written = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or entry.filename.rsplit("/", 1)[-1].startswith(UNUSED):
                continue
            # extract() makes the folders on the way, and keeps a name with ../ in it inside folder.
            archive.extract(entry, folder)
            written += 1
    return written


class Command(BaseCommand):
    help = f"Download Ketcher {VERSION}, check it, and unpack it into repo/static/repo/ketcher/."

    def handle(self, *args, **options):
        try:
            data = download(ZIP_URL)
            notices = {name: download(NOTICE_URL.format(name)) for name in NOTICES}
        except (URLError, TimeoutError) as exc:
            raise CommandError(f"Couldn't download Ketcher from GitHub: {exc}. Nothing was written.")

        check(data, ZIP_SHA256, ZIP_URL)
        for name, sha256 in NOTICES.items():
            check(notices[name], sha256, name)

        # Only now, with every file checked, is the old folder replaced.
        written = unpack(data, FOLDER)
        for name, text in notices.items():
            (FOLDER / name).write_bytes(text)
        self.stdout.write(f"Ketcher {VERSION}: {written + len(notices)} files in {FOLDER}")
