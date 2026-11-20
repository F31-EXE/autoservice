import json

import pytest
from django.urls import reverse

from apps.common.models import AuditEvent
from apps.messaging.max_updates import process_update
from apps.messaging.models import BookingRequest, MaxLink, Message
from apps.messaging.services import send_touch
from tests.messaging.conftest import IN_WINDOW

pytestmark = pytest.mark.django_db


def started(user_id, payload):
    return {"update_type": "bot_started", "timestamp": 1, "chat_id": 1,
            "user": {"user_id": user_id, "first_name": "И"}, "payload": payload}  # fmt: skip


def callback(user_id, payload, cid="cb-1"):
    return {"update_type": "message_callback", "timestamp": 1,
            "callback": {"timestamp": 1, "callback_id": cid, "payload": payload,
                         "user": {"user_id": user_id}}}  # fmt: skip


def text_update(user_id, text, mid="mid-1"):
    return {"update_type": "message_created", "timestamp": 1,
            "message": {"sender": {"user_id": user_id, "is_bot": False},
                        "recipient": {"chat_id": 5, "user_id": None},
                        "body": {"mid": mid, "seq": 1, "text": text}}}  # fmt: skip


class TestMaxUpdates:
    def test_deep_link_binds_client_and_resends_reminder_with_buttons(self, make_outreach, outbox):
        o = make_outreach()
        send_touch(o.pk, IN_WINDOW)  # SMS with the invitation link
        link = MaxLink.objects.get(client=o.client)
        assert link.token in outbox[0].text
        assert process_update(started(555, link.token)) == "linked"
        o.client.refresh_from_db()
        link.refresh_from_db()
        assert o.client.max_user_id == 555 and link.used_at
        greeting, reminder = outbox[1], outbox[2]
        assert greeting.channel == "max" and "сюда" in greeting.text
        assert [b.text for b in reminder.buttons] == ["Записаться", "Уже сделал", "Неактуально"]
        # the repeated reminder is not a new touch
        assert Message.objects.filter(outreach=o, touch_number__isnull=False).count() == 1

    def test_start_without_or_with_bad_token(self, outbox):
        assert process_update(started(1, "")) == "no_token"
        assert process_update(started(1, "nope")) == "no_token"
        assert "ссылку из SMS" in outbox[-1].text

    def test_buttons(self, make_outreach, outbox):
        o = make_outreach(max_user_id=555, channel="max")
        send_touch(o.pk, IN_WINDOW)
        assert process_update(callback(555, f"book:{o.pk}")) == "book"
        assert BookingRequest.objects.get().outreach == o
        assert process_update(callback(555, f"book:{o.pk}")) == "duplicate"  # same callback_id
        assert process_update(callback(555, f"done:{o.pk}", "cb-2")) == "done"
        assert o.recommendations.get().status == "done"
        assert process_update(callback(555, f"nr:{o.pk}", "cb-3")) == "nr"
        intents = list(Message.objects.filter(direction="in").values_list("intent", flat=True))
        assert intents == ["book", "already_done", "not_relevant"]

    def test_foreign_button_is_ignored(self, make_outreach):
        o = make_outreach(max_user_id=555, channel="max")
        assert process_update(callback(999, f"book:{o.pk}")) == "foreign"
        assert process_update(callback(555, "book:abc")) == "bad_update"
        assert process_update(callback(555, "hack:1")) == "bad_update"
        assert not BookingRequest.objects.exists()

    def test_text_message(self, make_outreach, outbox):
        o = make_outreach(max_user_id=555, channel="max")
        send_touch(o.pk, IN_WINDOW)
        assert process_update(text_update(555, "Давайте завтра вечером")) == "handled"
        assert process_update(text_update(555, "Давайте завтра вечером")) == "duplicate"
        assert BookingRequest.objects.get().preferred_time_text == "завтра вечером"
        assert outbox[-1].channel == "max"
        assert process_update(text_update(12345, "привет", "m2")) == "unknown_user"
        assert process_update(text_update(555, "/start abc", "m3")) == "ignored"
        assert process_update({"update_type": "dialog_muted"}) == "ignored"


class TestWebhooks:
    def post_max(self, client, body, secret="test-max-secret"):
        headers = {"HTTP_X_MAX_BOT_API_SECRET": secret} if secret else {}
        return client.post(reverse("messaging:max_webhook"), data=body,
                           content_type="application/json", **headers)  # fmt: skip

    def test_max_secret(self, client):
        assert self.post_max(client, "{}", secret=None).status_code == 403
        assert self.post_max(client, "{}", secret="wrong").status_code == 403
        assert self.post_max(client, "not json").status_code == 400
        assert self.post_max(client, "[]").status_code == 400

    def test_max_dispatches(self, client, make_outreach, django_capture_on_commit_callbacks):
        o = make_outreach(max_user_id=555, channel="max")
        send_touch(o.pk, IN_WINDOW)
        with django_capture_on_commit_callbacks(execute=True):
            resp = self.post_max(client, json.dumps(callback(555, f"book:{o.pk}")))
        assert resp.status_code == 200
        assert BookingRequest.objects.exists()

    def test_sms_secret_and_payload(self, client, settings):
        url = reverse("messaging:sms_inbound")
        assert client.post(url, "{}", content_type="application/json").status_code == 403
        bad = client.post(url, "{}", content_type="application/json",
                          HTTP_X_VOZVRAT_SECRET="test-sms-secret")  # fmt: skip
        assert bad.status_code == 400
        settings.SMS_INBOUND_SECRET = ""  # not configured → closed
        assert client.post(url, "{}", content_type="application/json",
                           HTTP_X_VOZVRAT_SECRET="").status_code == 403  # fmt: skip

    def test_get_not_allowed(self, client):
        assert client.get(reverse("messaging:max_webhook")).status_code == 405


class TestCabinet:
    def test_dialogs_and_reply(self, client, manager_a, make_outreach, outbox):
        o = make_outreach()
        send_touch(o.pk, IN_WINDOW)
        from apps.messaging.services import handle_incoming_text

        handle_incoming_text(o.client, "sms", "А вы кто?")
        client.force_login(manager_a)
        page = client.get(reverse("messaging:dialogs"))
        assert [c.pk for c in page.context["page"]] == [o.client.pk]
        url = reverse("messaging:conversation", args=[o.client.pk])
        assert "А вы кто?" in client.get(url).content.decode()
        client.post(url, {"action": "reply", "text": "Это автосервис «Мотор», здравствуйте!"})
        assert outbox[-1].text == "Это автосервис «Мотор», здравствуйте!"
        assert not Message.objects.filter(needs_attention=True).exists()
        assert Message.objects.filter(sent_by=manager_a).exists()
        assert AuditEvent.objects.get(action="message.reply").data == {"length": 37}
        assert list(client.get(reverse("messaging:dialogs")).context["page"]) == []

    def test_booking_actions(self, client, manager_a, make_outreach):
        o = make_outreach(status="replied")
        b = BookingRequest.objects.create(client=o.client, outreach=o, preferred_time_text="сб")
        client.force_login(manager_a)
        assert "сб" in client.get(reverse("accounts:today")).content.decode()
        url = reverse("messaging:booking_action", args=[b.pk])
        assert client.post(url, {"status": "weird"}).status_code == 400
        client.post(url, {"status": "booked"})
        b.refresh_from_db()
        o.refresh_from_db()
        assert (b.status, b.assigned_to, o.status) == ("booked", manager_a, "booked")

    @pytest.mark.parametrize("outcome,status", [("booked", "booked"), ("no_answer", "sent"),
                                                ("declined", "replied")])  # fmt: skip
    def test_call_outcomes(self, client, manager_a, make_outreach, outcome, status):
        o = make_outreach(channel="manual_call")
        client.force_login(manager_a)
        assert o.client.full_name in client.get(reverse("accounts:today")).content.decode()
        client.post(reverse("messaging:call_outcome", args=[o.pk]), {"outcome": outcome})
        o.refresh_from_db()
        assert o.status == status and o.touch_count == 1 and o.first_touch_at
        assert Message.objects.get().channel == "manual_call"
        if outcome == "declined":
            assert o.recommendations.get().status == "declined"

    def test_isolation(self, client, manager_a, station_b):
        from apps.outreach.models import Outreach
        from tests.factories import ClientFactory, VehicleFactory

        foreign = ClientFactory(station=station_b)
        vehicle = VehicleFactory(client=foreign)
        o = Outreach.objects.create(station=station_b, client=foreign, vehicle=vehicle,
                                    planned_for="2026-06-01", status="approved",
                                    channel="manual_call")  # fmt: skip
        b = BookingRequest.objects.create(client=foreign)
        client.force_login(manager_a)
        assert client.get(reverse("messaging:conversation", args=[foreign.pk])).status_code == 404
        assert client.post(reverse("messaging:booking_action", args=[b.pk]),
                           {"status": "booked"}).status_code == 404  # fmt: skip
        assert client.post(reverse("messaging:call_outcome", args=[o.pk]),
                           {"outcome": "booked"}).status_code == 404  # fmt: skip
        assert client.get(reverse("accounts:today")).context["bookings"] == []
