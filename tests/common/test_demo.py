from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Organization, User
from apps.common.demo import DEMO_ORG
from apps.customers.models import Client
from apps.llm.models import LLMCall
from apps.messaging.backends import ConsoleBackend
from apps.messaging.models import BookingRequest, Message
from apps.outreach.models import Outreach
from apps.recommendations.models import Recommendation

pytestmark = pytest.mark.django_db


def run(*args, **kwargs):
    out = StringIO()
    call_command("load_demo", *args, stdout=out, **kwargs)
    return out.getvalue()


def test_refuses_without_debug():
    with pytest.raises(CommandError):
        run()


def test_demo_fills_every_screen(settings, client):
    settings.LLM_BACKEND = "openai"  # must not matter: the demo forces offline stand-ins
    settings.LLM_API_KEY = "would-fail-if-used"
    settings.MESSAGE_BACKEND_SMS = "nope"
    ConsoleBackend.reset()
    text = run("--force", clients=40)
    assert "is ready" in text and "demo12345" in text
    assert Client.objects.count() == 40
    assert Outreach.objects.filter(status="awaiting_approval").exists()
    assert Outreach.objects.filter(status="visited", attributed_amount__gt=0).exists()
    assert Outreach.objects.filter(is_control_group=True).exists()
    assert Recommendation.objects.filter(status="needs_review").exists()
    assert Recommendation.objects.filter(source="explicit_field").exists()
    assert BookingRequest.objects.exists()
    assert Message.objects.filter(direction="in").exists()
    assert ConsoleBackend.outbox, "messages go to the console backend only"
    assert all(c.model.startswith("fake") or c.model == "" for c in LLMCall.objects.all())

    client.login(username="demo", password="demo12345")
    for url in ("/", "/recommendations/review/", "/messaging/dialogs/?all=1", "/clients/",
                "/results/", "/settings/", "/imports/"):  # fmt: skip
        assert client.get(url).status_code == 200, url
    assert User.objects.get(username="manager").stations.count() == 1


def test_rerun_and_remove():
    run("--force", clients=10)
    run("--force", clients=10)
    assert Organization.objects.filter(name=DEMO_ORG).count() == 1
    assert Client.objects.count() == 10
    assert "removed" in run("--force", "--remove")
    assert not Organization.objects.filter(name=DEMO_ORG).exists()
    assert not User.objects.filter(username="demo").exists()
    assert "No demo data" in run("--force", "--remove")


def test_bad_size():
    with pytest.raises(CommandError):
        run("--force", clients=3)
