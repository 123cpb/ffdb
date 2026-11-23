from django.test import SimpleTestCase


class RobotsTxtTests(SimpleTestCase):
    """/robots.txt, as a crawler asks for it: by that address, and not logged in. A SimpleTestCase
    refuses any database query, so these also show that the view doesn't touch the database."""

    def test_a_visitor_who_is_not_logged_in_gets_it_as_plain_text(self):
        response = self.client.get("/robots.txt")

        # A curators' page would send this visitor to log in (302) instead.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")

    def test_it_asks_every_crawler_to_stay_off_the_whole_site(self):
        response = self.client.get("/robots.txt")

        self.assertEqual(response.content, b"User-agent: *\nDisallow: /\n")
