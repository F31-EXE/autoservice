import json
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from apps.messaging.backends import Button, ConsoleBackend, SendError, get_backend
from apps.messaging.backends.max import MaxBackend
from apps.messaging.models import Message
from apps.messaging.services import dispatch_due, in_send_window, send_touch
from apps.outreach.planner import schedule_next_touches
from tests.messaging.conftest import IN_WINDOW, OUT_OF_WINDOW

pytestmark = pytest.mark.django_db


def test_send_window(station_a):
    assert in_send_window(station_a, IN_WINDOW)
    assert not in_send_window(station_a, OUT_OF_WINDOW)
    station_a.settings = {"send_window": ["09:00", "23:00"]}
    assert in_send_window(station_a, OUT_OF_WINDOW)
    station_a.timezone = "Europe/Kaliningrad"  # 19:00 local → inside 10–20
    station_a.settings = {}
    assert in_send_window(station_a, OUT_OF_WINDOW)


def test_outside_window_waits_for_dispatcher(make_outreach, outbox):
    o = make_outreach()
    assert send_touch(o.pk, OUT_OF_WINDOW) == "outside_window"
    assert outbox == [] and not Message.objects.exists()
    assert dispatch_due(IN_WINDOW) == {"sent": 1}
    o.refresh_from_db()
    assert o.status == "sent" and o.first_touch_at == IN_WINDOW


def test_no_double_send(make_outreach, outbox):
    o = make_outreach()
    assert send_touch(o.pk, IN_WINDOW) == "sent"
    assert send_touch(o.pk, IN_WINDOW) == "not_approved"
    # even if the status is reset by mistake, the (outreach, touch) row blocks a resend
    type(o).objects.filter(pk=o.pk).update(status="approved", touch_count=0)
    assert send_touch(o.pk, IN_WINDOW) == "duplicate"
    assert len(outbox) == 1


def test_crash_while_sending_is_not_resent(make_outreach, outbox):
    o = make_outreach()
    Message.objects.create(outreach=o, client=o.client, channel="sms", direction="out",
                           text="x", touch_number=1, status="sending", attempts=1)  # fmt: skip
    assert send_touch(o.pk, IN_WINDOW) == "duplicate"
    assert outbox == []


def test_failure_retries_then_gives_up(make_outreach, outbox):
    o = make_outreach()
    for _ in range(2):
        ConsoleBackend.fail_next = True
        assert send_touch(o.pk, IN_WINDOW) == "failed"
    o.refresh_from_db()
    assert o.status == "approved"
    ConsoleBackend.fail_next = True
    assert send_touch(o.pk, IN_WINDOW) == "failed"
    o.refresh_from_db()
    assert o.status == "cancelled" and "Не удалось отправить" in o.skip_reason
    assert o.recommendations.get().status == "open"
    msg = Message.objects.get()
    assert (msg.attempts, msg.status) == (3, "failed")


def test_failure_then_success(make_outreach, outbox):
    o = make_outreach()
    ConsoleBackend.fail_next = True
    send_touch(o.pk, IN_WINDOW)
    assert send_touch(o.pk, IN_WINDOW) == "sent"
    assert Message.objects.get().attempts == 2


def test_max_channel_with_buttons_and_fallback(make_outreach, outbox):
    o = make_outreach(channel="max", max_user_id=777)
    send_touch(o.pk, IN_WINDOW)
    item = outbox[0]
    assert (item.channel, item.to) == ("max", "777")
    assert [b.text for b in item.buttons] == ["Записаться", "Уже сделал", "Неактуально"]
    assert item.buttons[0].payload == f"book:{o.pk}"
    assert "Нажмите «Записаться»" in item.text
    # MAX chosen but the client never linked the bot → SMS
    o2 = make_outreach(channel="max")
    send_touch(o2.pk, IN_WINDOW)
    assert outbox[-1].channel == "sms" and outbox[-1].buttons == []


def test_unsubscribed_meanwhile_is_cancelled(make_outreach, outbox):
    o = make_outreach()
    o.client.unsubscribed_at = timezone.now()
    o.client.save()
    assert send_touch(o.pk, IN_WINDOW) == "unsubscribed"
    o.refresh_from_db()
    assert o.status == "cancelled" and outbox == []


def test_manual_call_is_not_sent(make_outreach, outbox):
    o = make_outreach(channel="manual_call")
    assert send_touch(o.pk, IN_WINDOW) == "manual_call"
    assert dispatch_due(IN_WINDOW) == {}


def test_second_touch_and_no_response(station_a, make_outreach, outbox):
    o = make_outreach()
    send_touch(o.pk, IN_WINDOW)
    type(o).objects.filter(pk=o.pk).update(last_touch_at=timezone.now() - timedelta(days=4))
    stats = schedule_next_touches(station_a)
    o.refresh_from_db()
    assert stats["second_touch"] == 1 and o.status == "awaiting_approval"
    # approved again → touch 2 with the reminder template
    type(o).objects.filter(pk=o.pk).update(status="approved")
    assert send_touch(o.pk, IN_WINDOW) == "sent"
    o.refresh_from_db()
    assert o.touch_count == 2 and "напоминает" in outbox[-1].text
    # no answer after the second touch → no_response, recommendations released
    type(o).objects.filter(pk=o.pk).update(last_touch_at=timezone.now() - timedelta(days=4))
    assert schedule_next_touches(station_a)["no_response"] == 1
    o.refresh_from_db()
    assert o.status == "no_response" and o.recommendations.get().status == "open"


def test_second_touch_waits(station_a, make_outreach):
    o = make_outreach()
    send_touch(o.pk, IN_WINDOW)
    type(o).objects.filter(pk=o.pk).update(last_touch_at=timezone.now() - timedelta(days=2))
    assert schedule_next_touches(station_a) == {"second_touch": 0, "no_response": 0}


def test_unknown_sms_backend(settings):
    settings.MESSAGE_BACKEND_SMS = "nope"
    with pytest.raises(SendError):
        get_backend("sms")


def test_console_backend_logs_no_pii(caplog):
    with caplog.at_level("INFO"):
        ConsoleBackend("sms").send("+79001234567", "Иван, привет")
    assert "+7900" not in caplog.text and "Иван" not in caplog.text


class TestMaxHttp:
    """The real MAX backend against a fake HTTP transport (no network)."""

    @pytest.fixture
    def configured(self, settings):
        settings.MAX_BOT_TOKEN = "bot-token"
        settings.MAX_API_BASE_URL = "https://platform-api2.max.ru"

    def test_send_wire_format(self, configured):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"body": {"mid": "mid.123", "text": "x"}}})

        backend = MaxBackend(httpx.Client(transport=httpx.MockTransport(handler)))
        result = backend.send("42", "Привет", [Button("Записаться", "book:1")])
        assert result.ok and result.provider_message_id == "mid.123"
        assert seen["url"] == "https://platform-api2.max.ru/messages?user_id=42"
        assert seen["auth"] == "bot-token"
        assert seen["body"] == {
            "text": "Привет",
            "attachments": [{"type": "inline_keyboard", "payload": {"buttons": [[
                {"type": "callback", "text": "Записаться", "payload": "book:1"}]]}}],
        }  # fmt: skip

    def test_errors_and_answers(self, configured):
        def handler(request):
            if request.url.path == "/answers":
                assert request.url.params["callback_id"] == "cb1"
                assert json.loads(request.content) == {"notification": "Спасибо!"}
                return httpx.Response(200, json={"success": True})
            return httpx.Response(403, json={"code": "chat.denied", "message": "+79001234567"})

        backend = MaxBackend(httpx.Client(transport=httpx.MockTransport(handler)))
        result = backend.send("42", "x")
        assert not result.ok and result.error == "max: HTTP 403 chat.denied"
        assert backend.answer_callback("cb1", "Спасибо!")

    def test_requires_token(self, settings):
        settings.MAX_BOT_TOKEN = ""
        with pytest.raises(SendError):
            MaxBackend()


def test_max_setup_webhook_command(settings, monkeypatch):
    from io import StringIO

    from django.core.management import CommandError, call_command

    settings.MAX_BOT_TOKEN = "bot-token"
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    import apps.messaging.backends.max as max_module

    real_init = max_module.MaxApi.__init__
    monkeypatch.setattr(
        max_module.MaxApi, "__init__",
        lambda self, http_client=None: real_init(
            self, httpx.Client(transport=httpx.MockTransport(handler))),
    )  # fmt: skip
    out = StringIO()
    call_command("max_setup_webhook", url="https://vozvrat.example/messaging/max/webhook/",
                 stdout=out)  # fmt: skip
    assert "Subscribed" in out.getvalue()
    assert seen["body"] == {
        "url": "https://vozvrat.example/messaging/max/webhook/",
        "update_types": ["bot_started", "message_created", "message_callback"],
        "secret": "test-max-secret",
    }
    with pytest.raises(CommandError):
        call_command("max_setup_webhook", url="http://insecure.example/")
