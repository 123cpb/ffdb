from django.test import SimpleTestCase

from config.settings import database_settings


class DatabaseSettingsTests(SimpleTestCase):
    def test_a_neon_address_keeps_its_query_string_and_its_password_is_unquoted(self):
        # Made up, in the form Neon gives: postgresql://, no port, and its two query keys. The
        # password is "p@ss/w0rd", with the @ and / percent-encoded, as they must be in a URL.
        url = (
            "postgresql://neondb_owner:p%40ss%2Fw0rd@ep-quiet-sky-a1b2c3d4.eu-central-1"
            ".aws.neon.tech/neondb?sslmode=require&channel_binding=require"
        )

        self.assertEqual(
            database_settings(url),
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "neondb",
                "USER": "neondb_owner",
                "PASSWORD": "p@ss/w0rd",
                "HOST": "ep-quiet-sky-a1b2c3d4.eu-central-1.aws.neon.tech",
                "PORT": "",
                "OPTIONS": {"sslmode": "require", "channel_binding": "require"},
            },
        )

    def test_an_address_without_a_query_string_has_no_options(self):
        # The form of .env.example's address and CI's.
        url = "postgres://ffdb:ffdb@localhost:5432/ffdb"

        self.assertEqual(
            database_settings(url),
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "ffdb",
                "USER": "ffdb",
                "PASSWORD": "ffdb",
                "HOST": "localhost",
                "PORT": 5432,
                "OPTIONS": {},
            },
        )
