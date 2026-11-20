import pytest
from django.urls import reverse

from apps.common.models import AuditEvent
from apps.customers.models import Client
from apps.outreach.models import Outreach
from tests.workorders.helpers import flat_profile, run_import

pytestmark = pytest.mark.django_db


@pytest.fixture
def data(station_a, station_b):
    station_a.settings = {"recommendation_ttl_days": 100000}
    station_a.save()
    run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    run_import(station_b, flat_profile(station_b), "autodealer_flat.csv")
    return Client.objects.get(station=station_a, phone="+79000000001")


@pytest.mark.parametrize("q", ["Тестов", "+7 900 000-00-01", "89000000001", "а001аа96",
                               "XTA00000000000001"])  # fmt: skip
def test_search(client, manager_a, data, q):
    client.force_login(manager_a)
    page = client.get(reverse("customers:list"), {"q": q}).context["page"]
    assert [c.pk for c in page] == [data.pk]


def test_list_only_own_station(client, manager_a, data):
    client.force_login(manager_a)
    page = client.get(reverse("customers:list")).context["page"]
    assert {c.station_id for c in page} == {data.station_id}


def test_card(client, manager_a, data):
    client.force_login(manager_a)
    html = client.get(reverse("customers:detail", args=[data.pk])).content.decode()
    for text in ("Тестов Иван Петрович", "Lada Vesta", "А001АА96", "ЗН-1001", "Замена ремня ГРМ",
                 "отказ", "Отказ клиента"):  # fmt: skip
        assert text in html


def test_card_isolation(client, manager_a, station_b, data):
    foreign = Client.objects.get(station=station_b, phone="+79000000001")
    client.force_login(manager_a)
    assert client.get(reverse("customers:detail", args=[foreign.pk])).status_code == 404
    url = reverse("customers:action", args=[foreign.pk])
    assert client.post(url, {"action": "unsubscribe"}).status_code == 404
    vehicle = foreign.vehicles.first()
    assert client.post(reverse("customers:vehicle_sold", args=[vehicle.pk]),
                       {"sold": "1"}).status_code == 404  # fmt: skip


def test_unsubscribe_and_resubscribe(client, manager_a, data):
    vehicle = data.vehicles.first()
    o = Outreach.objects.create(station=data.station, client=data, vehicle=vehicle,
                                planned_for="2026-06-01", status="awaiting_approval")  # fmt: skip
    client.force_login(manager_a)
    url = reverse("customers:action", args=[data.pk])
    client.post(url, {"action": "unsubscribe"})
    data.refresh_from_db()
    o.refresh_from_db()
    assert data.unsubscribed_at and o.status == "cancelled"
    client.post(url, {"action": "resubscribe"})  # without confirmation
    data.refresh_from_db()
    assert data.unsubscribed_at
    client.post(url, {"action": "resubscribe", "confirm": "yes"})
    data.refresh_from_db()
    assert data.unsubscribed_at is None
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert "client.unsubscribe" in actions and "client.resubscribe" in actions


def test_notes_and_sold(client, manager_a, data):
    client.force_login(manager_a)
    client.post(reverse("customers:action", args=[data.pk]),
                {"action": "notes", "text": "", "notes": "Звонить после 18"})  # fmt: skip
    data.refresh_from_db()
    assert data.notes == "Звонить после 18"
    assert "Звонить" not in str(AuditEvent.objects.get(action="client.notes").data)
    vehicle = data.vehicles.first()
    client.post(reverse("customers:vehicle_sold", args=[vehicle.pk]), {"sold": "1"})
    vehicle.refresh_from_db()
    assert vehicle.is_sold
    assert client.post(reverse("customers:action", args=[data.pk]),
                       {"action": "nope"}).status_code == 400  # fmt: skip
