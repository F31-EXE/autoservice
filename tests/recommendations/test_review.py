from datetime import date

import pytest
from django.urls import reverse

from apps.catalog.models import WorkType
from apps.common.models import AuditEvent
from apps.recommendations.models import Recommendation as Rec
from tests.factories import ClientFactory, VehicleFactory, WorkOrderFactory

pytestmark = pytest.mark.django_db


def make_rec(station, **kw):
    vehicle = VehicleFactory(client=ClientFactory(station=station))
    wo = WorkOrderFactory(vehicle=vehicle)
    defaults = dict(station=station, vehicle=vehicle, source_work_order=wo, source="free_text",
                    source_text="колодки пер. 30%", work_type_raw="колодки", status="needs_review",
                    confidence=0.6, due_date=date(2024, 3, 1))  # fmt: skip
    defaults.update(kw)
    return Rec.objects.create(**defaults)


def post(client, rec, action, **data):
    payload = {"action": action, **{f"r{rec.pk}-{k}": v for k, v in data.items()}}
    return client.post(reverse("recommendations:review_action", args=[rec.pk]), payload)


def test_review_list_shows_quote(client, manager_a, station_a):
    make_rec(station_a)
    make_rec(station_a, status="open")
    client.force_login(manager_a)
    resp = client.get(reverse("recommendations:review"))
    assert resp.status_code == 200
    assert len(resp.context["items"]) == 1
    assert "«колодки пер. 30%»" in resp.content.decode()


def test_approve(client, manager_a, station_a):
    rec = make_rec(station_a)
    client.force_login(manager_a)
    resp = post(client, rec, "approve")
    assert "подтверждена" in resp.content.decode()
    rec.refresh_from_db()
    assert rec.status == "open"
    assert rec.review_decision_by == manager_a and rec.review_decision_at
    event = AuditEvent.objects.get()
    assert event.action == "recommendation.approve" and event.actor == manager_a


def test_edit(client, manager_a, station_a):
    rec = make_rec(station_a)
    client.force_login(manager_a)
    wt = WorkType.objects.get(code="brake_pads_front")
    post(client, rec, "edit", work_type=wt.pk, urgency="safety", due_date="", due_km="50000")
    rec.refresh_from_db()
    assert (rec.status, rec.work_type, rec.due_km, rec.urgency) == ("open", wt, 50000, "safety")
    event = AuditEvent.objects.get(action="recommendation.edit")
    assert event.data["before"]["work_type"] is None
    assert event.data["after"]["work_type"] == "brake_pads_front"


def test_edit_requires_deadline(client, manager_a, station_a):
    rec = make_rec(station_a)
    client.force_login(manager_a)
    resp = post(client, rec, "edit", work_type="", urgency="wear", due_date="", due_km="")
    assert resp.status_code == 422
    assert "Укажите срок" in resp.content.decode()
    rec.refresh_from_db()
    assert rec.status == "needs_review"


def test_reject(client, manager_a, station_a):
    rec = make_rec(station_a)
    client.force_login(manager_a)
    post(client, rec, "reject")
    rec.refresh_from_db()
    assert rec.status == "rejected"
    assert AuditEvent.objects.filter(action="recommendation.reject").exists()


def test_only_needs_review_can_be_decided(client, manager_a, station_a):
    rec = make_rec(station_a, status="open")
    client.force_login(manager_a)
    assert post(client, rec, "reject").status_code == 404


def test_unknown_action(client, manager_a, station_a):
    rec = make_rec(station_a)
    client.force_login(manager_a)
    assert post(client, rec, "explode").status_code == 400


def test_isolation(client, manager_a, station_a, station_b):
    foreign = make_rec(station_b)
    client.force_login(manager_a)
    assert post(client, foreign, "approve").status_code == 404
    foreign.refresh_from_db()
    assert foreign.status == "needs_review"
    resp = client.get(reverse("recommendations:review"))
    assert resp.context["items"] == []


def test_get_not_allowed_and_login_required(client, manager_a, station_a):
    rec = make_rec(station_a)
    url = reverse("recommendations:review_action", args=[rec.pk])
    assert client.post(url, {"action": "approve"}).status_code == 302  # anonymous → login
    client.force_login(manager_a)
    assert client.get(url).status_code == 405
