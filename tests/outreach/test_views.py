from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.common.models import AuditEvent
from apps.outreach.models import Outreach
from apps.outreach.texts import compose_text, recommendation_summary
from apps.recommendations.models import Recommendation as Rec
from tests.factories import ClientFactory, VehicleFactory, WorkOrderFactory

pytestmark = pytest.mark.django_db


def make_outreach(
    station, est=None, status="awaiting_approval", rec_status="scheduled", **client_kw
):
    client = ClientFactory(station=station, full_name="Тестов Иван Петрович", **client_kw)
    vehicle = VehicleFactory(client=client, make="Lada", model="Vesta")
    wo = WorkOrderFactory(vehicle=vehicle, opened_at=date(2026, 3, 10))
    rec = Rec.objects.create(station=station, vehicle=vehicle, source_work_order=wo,
                             source="free_text", work_type_raw="Замена передних колодок",
                             source_text="колодки пер. 30%", status=rec_status,
                             due_date=date(2026, 6, 1), est_price=est)  # fmt: skip
    o = Outreach.objects.create(
        station=station, client=client, vehicle=vehicle, planned_for=date(2026, 6, 1),
        status=status, est_amount=est,
    )  # fmt: skip
    o.recommendations.add(rec)
    return o


def test_text_template(station_a):
    station_a.sender_name = "СТО «Мотор»"
    station_a.booking_url = "https://example.com/book"
    station_a.save()
    o = make_outreach(station_a)
    text = compose_text(o)
    assert text == (
        "Иван, это СТО «Мотор». В марте мастер отметил: замена передних колодок на вашем "
        "Lada Vesta. По пробегу пора проверить. Записаться: https://example.com/book. "
        "Отписаться: СТОП"
    )
    o.custom_text = "Свой текст"
    assert compose_text(o) == "Свой текст"


def test_template_cannot_reach_models(station_a):
    station_a.settings = {"message_template": "{{ station.organization }}|{{ station.name }}"}
    station_a.save()
    o = make_outreach(station_a)
    assert compose_text(o) == f"|{station_a.name}"


def test_summary_join():
    class R:
        def __init__(self, t, urgency="wear"):
            self.title, self.urgency = t, urgency

    assert recommendation_summary([R("Замена масла"), R("Замена ГРМ"), R("Замена масла")]) == (
        "замена масла и замена ГРМ"
    )
    # safety first, at most two listed, the rest counted with the right plural form
    recs = [R("А"), R("Колодки", "safety"), R("В", "comfort"), R("Г")]
    assert recommendation_summary(recs) == "колодки, а и ещё 2 работы"
    assert recommendation_summary(recs[:3]) == "колодки, а и ещё 1 работа"
    assert recommendation_summary([R(str(i)) for i in range(7)]) == "0, 1 и ещё 5 работ"
    assert recommendation_summary([]) == ""


def test_today_page(client, manager_a, station_a):
    make_outreach(station_a)
    make_outreach(station_a, status="planned")  # control/planned is not shown
    client.force_login(manager_a)
    resp = client.get(reverse("accounts:today"))
    html = resp.content.decode()
    assert resp.status_code == 200
    assert len(resp.context["rows"]) == 1
    assert "«колодки пер. 30%»" in html
    assert "По пробегу пора проверить" in html
    phone = resp.context["rows"][0]["o"].client.phone
    assert phone not in html and f"{phone[:4]}***{phone[-2:]}" in html  # masked phone only


def test_approve(client, manager_a, station_a):
    o = make_outreach(station_a)
    client.force_login(manager_a)
    resp = client.post(reverse("outreach:approve", args=[o.pk]))
    assert "одобрено к отправке по SMS" in resp.content.decode()
    o.refresh_from_db()
    assert (o.status, o.channel, o.approved_by) == ("approved", "sms", manager_a)
    assert AuditEvent.objects.get().action == "outreach.approve"


def test_approve_telegram_channel(client, manager_a, station_a):
    o = make_outreach(station_a, telegram_chat_id=123)
    client.force_login(manager_a)
    client.post(reverse("outreach:approve", args=[o.pk]))
    o.refresh_from_db()
    assert o.channel == "telegram"


def test_approve_obsolete_is_cancelled(client, manager_a, station_a):
    o = make_outreach(station_a, rec_status="done")
    client.force_login(manager_a)
    resp = client.post(reverse("outreach:approve", args=[o.pk]))
    assert "работы уже выполнены" in resp.content.decode()
    o.refresh_from_db()
    assert o.status == "cancelled"


def test_high_value_requires_call(client, manager_a, station_a):
    o = make_outreach(station_a, est=Decimal("20000"))
    client.force_login(manager_a)
    html = client.get(reverse("accounts:today")).content.decode()
    assert "Позвонить" in html
    resp = client.post(reverse("outreach:call", args=[o.pk]))
    body = resp.content.decode()
    assert "Скрипт звонка" in body and "20000 ₽" in body
    o.refresh_from_db()
    assert (o.status, o.channel) == ("approved", "manual_call")


def test_skip_requires_reason_and_releases_recs(client, manager_a, station_a):
    o = make_outreach(station_a)
    client.force_login(manager_a)
    assert client.post(reverse("outreach:skip", args=[o.pk]), {"reason": ""}).status_code == 422
    client.post(reverse("outreach:skip", args=[o.pk]), {"reason": "уже записан по телефону"})
    o.refresh_from_db()
    assert (o.status, o.skip_reason) == ("skipped", "уже записан по телефону")
    assert o.recommendations.get().status == "open"
    event = AuditEvent.objects.get(action="outreach.skip")
    assert "записан" not in str(event.data)  # no free text in the audit log


def test_edit_text(client, manager_a, station_a):
    o = make_outreach(station_a)
    client.force_login(manager_a)
    form = client.get(reverse("outreach:edit_text", args=[o.pk])).content.decode()
    assert "По пробегу пора проверить" in form
    resp = client.post(reverse("outreach:edit_text", args=[o.pk]), {"text": "Иван, пора на ТО!"})
    assert "Иван, пора на ТО!" in resp.content.decode() and "(изменён)" in resp.content.decode()
    o.refresh_from_db()
    assert o.custom_text == "Иван, пора на ТО!"
    event = AuditEvent.objects.get(action="outreach.edit_text")
    assert event.data == {"old_length": event.data["old_length"], "new_length": 17}
    assert client.get(reverse("outreach:row", args=[o.pk])).status_code == 200


def test_bulk_approve_skips_calls_and_obsolete(client, manager_a, station_a):
    a = make_outreach(station_a)
    b = make_outreach(station_a)
    call = make_outreach(station_a, est=Decimal("50000"))
    obsolete = make_outreach(station_a, rec_status="done")
    client.force_login(manager_a)
    resp = client.post(reverse("outreach:bulk_approve"),
                       {"ids": [a.pk, b.pk, call.pk, obsolete.pk]})  # fmt: skip
    assert resp.status_code == 302
    statuses = dict(Outreach.objects.values_list("pk", "status"))
    assert statuses[a.pk] == statuses[b.pk] == "approved"
    assert statuses[call.pk] == statuses[obsolete.pk] == "awaiting_approval"


def test_rebuild_button(client, manager_a, station_a):
    client.force_login(manager_a)
    resp = client.post(reverse("outreach:rebuild"), follow=True)
    assert "Очередь обновлена" in resp.content.decode()


class TestIsolation:
    def test_foreign_outreach_actions_404(self, client, manager_a, station_b):
        o = make_outreach(station_b)
        client.force_login(manager_a)
        for name in ("outreach:approve", "outreach:call", "outreach:skip", "outreach:edit_text"):
            assert (
                client.post(reverse(name, args=[o.pk]), {"reason": "x", "text": "x"}).status_code
                == 404
            )
        assert client.get(reverse("outreach:row", args=[o.pk])).status_code == 404
        client.post(reverse("outreach:bulk_approve"), {"ids": [o.pk]})
        o.refresh_from_db()
        assert o.status == "awaiting_approval"

    def test_today_hides_foreign(self, client, manager_a, station_a, station_b):
        make_outreach(station_b)
        client.force_login(manager_a)
        assert client.get(reverse("accounts:today")).context["rows"] == []

    def test_already_decided_is_404(self, client, manager_a, station_a):
        o = make_outreach(station_a, status="approved")
        client.force_login(manager_a)
        assert client.post(reverse("outreach:approve", args=[o.pk])).status_code == 404

    def test_get_not_allowed(self, client, manager_a, station_a):
        o = make_outreach(station_a)
        client.force_login(manager_a)
        assert client.get(reverse("outreach:approve", args=[o.pk])).status_code == 405
