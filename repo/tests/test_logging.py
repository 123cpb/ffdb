import io
import logging

from django.test import Client, SimpleTestCase, override_settings
from django.urls import path


def broken(request):
    raise RuntimeError("a test error")


# The addresses for these tests alone, so a view can fail on purpose.
urlpatterns = [path("broken/", broken)]


@override_settings(ROOT_URLCONF=__name__)
class LoggingTests(SimpleTestCase):
    """What settings.LOGGING's console prints. DEBUG is off in every test, as on Render, so it
    prints here."""

    def setUp(self):
        # Write what the console prints to a string instead of the terminal, to read it back.
        self.output = io.StringIO()
        console = logging.getHandlerByName("console")
        self.addCleanup(console.setStream, console.setStream(self.output))

    def test_a_server_error_prints_an_error_with_its_traceback(self):
        # The test client usually raises a view's exception in the test: this one answers 500.
        response = Client(raise_request_exception=False).get("/broken/")

        self.assertEqual(response.status_code, 500)
        printed = self.output.getvalue()
        self.assertTrue(printed.startswith("ERROR django.request: Internal Server Error: /broken/\n"))
        self.assertIn("Traceback (most recent call last):", printed)
        self.assertIn("RuntimeError: a test error", printed)

    def test_a_404_prints_nothing(self):
        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.output.getvalue(), "")
