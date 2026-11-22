"""The References list's actions: Mark published, refused for unpublished work, and Mark not
published, its undo. Only admins are offered them, and each change goes in the reference's
admin history."""

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from repo.models import Material, PhaseSequence, Reference
from repo.tests.test_import import ARTICLE, FIVE_CB, RM734, RM734_SUMMARY
from repo.tests.test_switches import UNPUBLISHED_WORK

REFERENCES = reverse("admin:repo_reference_changelist")
FIVE_CB_SUMMARY = "K 22.5 N 35 I"
# The DOI the unpublished work's paper is given, as an admin might paste it.
ITS_PAPER = "https://doi.org/10.1039/D6CP01234A"


class MakePublicTestCase(TestCase):
    """RM734, whose only data is an approved sequence from an article that isn't published yet;
    5CB, whose only data is an approved sequence from unpublished work; and an admin logged in."""

    @classmethod
    def setUpTestData(cls):
        cls.rm734 = Material.objects.create(name="RM734", smiles=RM734)
        cls.article = Reference.objects.create(
            kind=Reference.Kind.ARTICLE, identifier=ARTICLE, first_author="Mandle", year=2017
        )
        PhaseSequence.objects.create(material=cls.rm734, reference=cls.article, sequence=RM734_SUMMARY, approved=True)
        cls.five_cb = Material.objects.create(name="5CB", smiles=FIVE_CB)
        cls.work = Reference.objects.create(kind=Reference.Kind.UNPUBLISHED, identifier=UNPUBLISHED_WORK)
        PhaseSequence.objects.create(material=cls.five_cb, reference=cls.work, sequence=FIVE_CB_SUMMARY, approved=True)
        cls.admin = User.objects.create_superuser("admin", password="a-test-password")

    def setUp(self):
        self.client.force_login(self.admin)

    def act(self, action, *references):
        """Tick the references on the References list and choose the action. The response is the
        list again, with the action's messages."""
        data = {"action": action, "_selected_action": [reference.pk for reference in references]}
        return self.client.post(REFERENCES, data, follow=True)

    def messages(self, response):
        return [str(message) for message in response.context["messages"]]

    def status(self, name, obj):
        """What a visitor gets at the public page of a material or reference."""
        self.client.logout()
        code = self.client.get(reverse(f"repo:{name}", args=[obj.pk])).status_code
        self.client.force_login(self.admin)
        return code

    def published(self, reference):
        reference.refresh_from_db()
        return reference.published


class MarkPublishedTests(MakePublicTestCase):
    def test_it_makes_an_articles_approved_data_public(self):
        self.assertEqual((self.status("reference", self.article), self.status("material", self.rm734)), (404, 404))

        response = self.act("mark_published", self.article)

        self.assertTrue(self.published(self.article))
        self.assertEqual(self.messages(response), ["Marked published: 1 reference. Their approved data is public."])
        self.assertEqual((self.status("reference", self.article), self.status("material", self.rm734)), (200, 200))

    def test_unpublished_work_is_refused_and_the_others_are_still_marked(self):
        response = self.act("mark_published", self.article, self.work)

        self.assertFalse(self.published(self.work))
        self.assertTrue(self.published(self.article))
        self.assertEqual(self.messages(response), [
            f"{UNPUBLISHED_WORK} (unpublished) wasn't marked published: it is unpublished work. "
            "Change its kind and identifier to its paper's on its page first.",
            "Marked published: 1 reference. Their approved data is public.",
        ])
        self.assertEqual(self.status("material", self.five_cb), 404)

    def test_unpublished_work_edited_into_its_paper_goes_public_with_its_data(self):
        # As the reference's admin page saves it: its checks, which tidy the DOI, then save().
        self.work.kind, self.work.identifier = Reference.Kind.ARTICLE, ITS_PAPER
        self.work.full_clean()
        self.work.save()

        self.act("mark_published", self.work)

        self.assertTrue(self.published(self.work))
        self.assertEqual((self.status("reference", self.work), self.status("material", self.five_cb)), (200, 200))


class MarkNotPublishedTests(MakePublicTestCase):
    def test_it_hides_a_reference_and_a_material_with_nothing_public_left(self):
        self.act("mark_published", self.article)

        response = self.act("mark_not_published", self.article)

        self.assertFalse(self.published(self.article))
        self.assertEqual(self.messages(response), ["Marked not published: 1 reference. Their data is hidden."])
        self.assertEqual((self.status("reference", self.article), self.status("material", self.rm734)), (404, 404))


class WhoMayUseThemTests(MakePublicTestCase):
    def test_a_curator_isnt_offered_them_and_cant_use_them(self):
        curator = User.objects.create_user("curator", is_staff=True)
        curator.groups.add(Group.objects.get(name="Curators"))
        self.client.force_login(curator)

        offered = dict(self.client.get(REFERENCES).context["action_form"].fields["action"].choices)
        self.act("mark_published", self.article)

        self.assertNotIn("mark_published", offered)
        self.assertNotIn("mark_not_published", offered)
        self.assertFalse(self.published(self.article))

    def test_each_change_is_in_the_references_history_and_nothing_else(self):
        self.act("mark_published", self.article)
        self.act("mark_published", self.article)  # already published: no change to record
        self.act("mark_not_published", self.article)

        history = LogEntry.objects.filter(object_id=str(self.article.pk)).order_by("pk")
        self.assertEqual(
            [(entry.user, entry.change_message) for entry in history],
            [(self.admin, "Marked published."), (self.admin, "Marked not published.")],
        )
