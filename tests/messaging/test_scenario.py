"""Stage 6 acceptance: approve → send → «давайте в субботу» → BookingRequest; «СТОП» →
unsubscribe; control group is never sent. ConsoleBackend + offline intent heuristic."""

from datetime import date, timedelta
from unittest import mock

import pytest
from django.urls import reverse

from apps.messaging.models import BookingRequest, Message
from apps.messaging.services import dispatch_due
from apps.outreach.models import Outreach
from apps.outreach.planner import build_queue
from apps.recommendations.models import Recommendation as Rec
from tests.factories import ClientFactory, VehicleFactory, WorkOrderFactory
from tests.messaging.conftest import IN_WINDOW

pytestmark = pytest.mark.django_db


def sms_in(client, phone, text, msg_id):
    return client.post(
        reverse("messaging:sms_inbound"),
        data={"phone": phone, "text": text, "id": msg_id},
        content_type="application/json",
        HTTP_X_VOZVRAT_SECRET="test-sms-secret",
    )


def test_full_scenario(client, manager_a, station_a, outbox, django_capture_on_commit_callbacks):
    # control group share 0 for the "real" customer, 1 for the control one (two stations' worth)
    station_a.settings = {"control_group_share": 0}
    station_a.save()
    customer = ClientFactory(station=station_a, full_name="Тестов Иван Петрович")
    vehicle = VehicleFactory(client=customer, make="Lada", model="Vesta",
                             last_visit_at=date.today() - timedelta(days=60),
                             last_known_mileage=50000)  # fmt: skip
    wo = WorkOrderFactory(vehicle=vehicle, opened_at=date.today() - timedelta(days=60))
    Rec.objects.create(station=station_a, vehicle=vehicle, source_work_order=wo,
                       source="declined_line", work_type_raw="Замена ремня ГРМ",
                       status="open", due_date=date.today())  # fmt: skip
    build_queue(station_a)
    outreach = Outreach.objects.get(vehicle=vehicle)
    assert outreach.status == "awaiting_approval"

    # 1) approve in the cabinet → sent immediately when inside the send window
    client.force_login(manager_a)
    with mock.patch("django.utils.timezone.now", return_value=IN_WINDOW), \
            django_capture_on_commit_callbacks(execute=True):  # fmt: skip
        client.post(reverse("outreach:approve", args=[outreach.pk]))
    outreach.refresh_from_db()
    assert outreach.status == "sent" and outreach.touch_count == 1
    assert len(outbox) == 1 and outbox[0].channel == "sms" and outbox[0].to == customer.phone
    assert "СТОП — отписка" in outbox[0].text
    sent = Message.objects.get(direction="out", touch_number=1)
    assert sent.status == "sent" and sent.provider_message_id.startswith("console-")

    # 2) the client answers «давайте в субботу» → BookingRequest, polite reply, no time promised
    with django_capture_on_commit_callbacks(execute=True):
        assert sms_in(client, customer.phone, "Давайте в субботу утром", "sms-1").status_code == 200
    booking = BookingRequest.objects.get()
    assert booking.preferred_time_text == "в субботу утром"
    assert booking.outreach == outreach and booking.status == "new"
    outreach.refresh_from_db()
    assert outreach.status == "replied"
    reply = outbox[-1].text
    assert "администратор" in reply.lower() and "суббот" not in reply.lower()
    inbound = Message.objects.get(direction="in")
    assert (inbound.intent, inbound.handled_by_ai, inbound.needs_attention) == ("book", True, False)

    # the booking shows up on «Сегодня» (re-login: the session was created under a mocked clock)
    client.force_login(manager_a)
    assert "в субботу утром" in client.get(reverse("accounts:today")).content.decode()

    # 3) «СТОП» → unsubscribed immediately, confirmation sent
    with django_capture_on_commit_callbacks(execute=True):
        sms_in(client, customer.phone, "СТОП", "sms-2")
    customer.refresh_from_db()
    assert customer.unsubscribed_at is not None
    assert "отписались" in outbox[-1].text
    assert Message.objects.filter(direction="in", intent="unsubscribe").exists()


def test_control_group_is_never_sent(station_a, make_outreach, outbox):
    control = make_outreach(status="planned", control=True)
    # even if someone approves it by mistake, the sender refuses
    Outreach.objects.filter(pk=control.pk).update(status="approved")
    stats = dispatch_due(now=IN_WINDOW)
    assert stats == {"control_group": 1} or "control_group" not in stats  # excluded or refused
    assert outbox == []
    assert not Message.objects.filter(outreach=control).exists()


def test_webhook_redelivery_is_ignored(client, station_a, make_outreach, outbox,
                                       django_capture_on_commit_callbacks):  # fmt: skip
    o = make_outreach(status="sent")
    with django_capture_on_commit_callbacks(execute=True):
        sms_in(client, o.client.phone, "Давайте завтра", "dup-1")
        sms_in(client, o.client.phone, "Давайте завтра", "dup-1")
    assert Message.objects.filter(direction="in").count() == 1
    assert BookingRequest.objects.count() == 1
