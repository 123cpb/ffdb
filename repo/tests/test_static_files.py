import gzip
import tempfile

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings


class StaticFilesTests(SimpleTestCase):
    """What WhiteNoise serves on Render: the files collectstatic wrote, with DEBUG off as in every
    test. urls.py serves no static files, so only WhiteNoise can answer these requests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        folder = cls.enterClassContext(tempfile.TemporaryDirectory())
        # Autorefresh is on for the other tests (settings.py). Off, as on Render, WhiteNoise
        # learns what files there are once, when it starts.
        cls.enterClassContext(override_settings(STATIC_ROOT=folder, WHITENOISE_AUTOREFRESH=False))
        # Ketcher is left out: compressing its 30 MB file takes several seconds.
        call_command("collectstatic", interactive=False, verbosity=0, ignore_patterns=["ketcher"])
        with open(finders.find("repo/style.css"), "rb") as file:
            cls.style = file.read()

    def test_style_css_is_gzipped_for_a_browser_that_accepts_gzip(self):
        response = self.client.get("/static/repo/style.css", headers={"accept-encoding": "gzip"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Encoding"], "gzip")
        # Reading all of streaming_content also closes the file.
        self.assertEqual(gzip.decompress(b"".join(response.streaming_content)), self.style)

    def test_style_css_is_sent_as_it_is_otherwise(self):
        response = self.client.get("/static/repo/style.css")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Content-Encoding", response)
        self.assertEqual(b"".join(response.streaming_content), self.style)
