import os
import re
import subprocess
import sys

from django.conf import settings
from django.core.management.utils import get_random_secret_key
from django.test import SimpleTestCase
from django.urls import reverse

HOST = "ffdb.example.org"

# Two requests through SecurityMiddleware alone, with a stand-in view: one as the proxy passes on
# a plain http request, one with the header it adds for https. No database is used.
PROXY_SCRIPT = """
import django
django.setup()
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory
middleware = SecurityMiddleware(lambda request: HttpResponse())
requests = RequestFactory(SERVER_NAME="%s")
plain = middleware(requests.get("/"))
proxied = middleware(requests.get("/", headers={"x-forwarded-proto": "https"}))
print(plain.status_code, plain["Location"], proxied.status_code,
      proxied["Strict-Transport-Security"])
""" % HOST


def run_with_https_on(*args):
    """Runs Python in a new process, with the variables as on Render: HTTPS on, DEBUG off, a strong
    key and a host. settings.py reads them once, when it starts, so this process can't switch
    them. They win over .env, because load_dotenv leaves alone a variable that is already set."""
    env = os.environ | {
        "HTTPS": "True",
        "DEBUG": "False",
        "SECRET_KEY": get_random_secret_key(),
        "ALLOWED_HOSTS": HOST,
        "DJANGO_SETTINGS_MODULE": "config.settings",
    }
    return subprocess.run(
        [sys.executable, *args], cwd=settings.BASE_DIR, env=env, capture_output=True, text=True
    )


class HttpsOnTests(SimpleTestCase):
    def test_check_deploy_finds_only_the_hsts_subdomain_and_preload_warnings(self):
        result = run_with_https_on("manage.py", "check", "--deploy")
        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        # Each issue is printed with its ID in brackets, such as "(security.W005)".
        found = set(re.findall(r"\((\w+\.[EW]\d+)\)", output))
        self.assertEqual(found, {"security.W005", "security.W021"}, output)

    def test_django_takes_the_proxy_header_for_https(self):
        result = run_with_https_on("-c", PROXY_SCRIPT)

        self.assertEqual(result.returncode, 0, result.stderr)
        # Plain http is sent to https; https, as the header says, is answered, with HSTS.
        self.assertEqual(
            result.stdout.split(), ["301", f"https://{HOST}/", "200", "max-age=3600"]
        )


class HttpsOffTests(SimpleTestCase):
    """HTTPS is off in this process, as in every other test and on the laptop."""

    def test_a_plain_request_is_not_redirected(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
