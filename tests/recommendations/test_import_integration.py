from datetime import date

import pytest
from django.urls import reverse

from apps.recommendations.models import Recommendation as Rec
from apps.workorders.models import WorkOrderLine
from tests.workorders.helpers import flat_profile, header_profile, run_import

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def long_ttl(station_a):
    """Fixture data is from 2024; keep recommendations from expiring regardless of today."""
    station_a.settings = {"recommendation_ttl_days": 100000}
    station_a.save()


def test_old_history_expires_with_default_ttl(station_a, monkeypatch):
    from apps.recommendations import rules

    monkeypatch.setattr(rules.timezone, "localdate", lambda: date(2026, 11, 20))
    station_a.settings = {}
    station_a.save()
    run_import(station_a, header_profile(station_a), "alfa_header.xlsx")
    # on 2026-11-20 the 2024 oil change is past due + 540 days...
    assert Rec.objects.get(work_type__code="engine_oil", status__in=["open", "expired"]).status == (
        "expired"
    )
    # ...but brake fluid (every 24 months from 03.2024) is not yet
    assert Rec.objects.get(work_type__code="brake_fluid").status == "open"


def test_import_creates_rule_recommendations(station_a):
    batch = run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    assert batch.stats["rules"]["created"] >= 2
    oil = Rec.objects.get(work_type__code="engine_oil")
    assert oil.source == "regular_interval"
    assert (oil.due_km, oil.due_date) == (55000, date(2025, 1, 15))  # 45000 + 10000, +12 months
    belt = Rec.objects.get(work_type__code="timing_belt")
    assert belt.source == "declined_line"
    assert belt.due_date == date(2024, 1, 29)
    assert belt.est_price == 4500
    assert WorkOrderLine.objects.filter(work_type__isnull=False).exists()


def test_later_import_closes_recommendation(station_a):
    profile = header_profile(station_a)
    run_import(station_a, profile, "alfa_header.xlsx")
    # А-501 (Feb) oil change is superseded by А-504 (Sep) oil change
    oil = Rec.objects.filter(work_type__code="engine_oil").order_by("id")
    assert [(r.source_work_order.external_number, r.status) for r in oil] == [
        ("А-501", "done"),
        ("А-504", "open"),
    ]
    assert oil[0].closed_by_work_order.external_number == "А-504"
    # declined spark plugs from А-501
    plugs = Rec.objects.get(work_type__code="spark_plugs")
    assert plugs.source == "declined_line" and plugs.status == "open"


def test_second_file_closes_declined_work(station_a):
    """ЗН-1006 (in the second export) replaces the timing belt on the Rio, not on the Vesta."""
    profile = flat_profile(station_a)
    run_import(station_a, profile, "autodealer_flat.csv")
    run_import(station_a, profile, "autodealer_flat_2.csv")
    vesta_belt = Rec.objects.get(work_type__code="timing_belt", source="declined_line")
    assert vesta_belt.status == "open"
    rio_belt = Rec.objects.get(work_type__code="timing_belt", source="regular_interval")
    assert rio_belt.source_work_order.external_number == "ЗН-1006"
    assert rio_belt.due_km == 170000


def test_reimport_does_not_duplicate_recommendations(station_a):
    profile = flat_profile(station_a)
    run_import(station_a, profile, "autodealer_flat.csv")
    count = Rec.objects.count()
    run_import(station_a, profile, "autodealer_flat.csv")
    assert Rec.objects.count() == count


class TestListView:
    def test_list_and_filters(self, client, manager_a, station_a):
        run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
        client.force_login(manager_a)
        url = reverse("recommendations:list")
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.context["page"].paginator.count == Rec.objects.active().count()
        resp = client.get(url, {"source": "declined_line"})
        assert {r.source for r in resp.context["page"]} == {"declined_line"}
        resp = client.get(url, {"urgency": "comfort", "status": "bogus"})
        assert resp.status_code == 200

    def test_isolation(self, client, manager_a, station_a, station_b):
        run_import(station_b, flat_profile(station_b), "autodealer_flat.csv")
        client.force_login(manager_a)
        resp = client.get(reverse("recommendations:list"), {"status": "all"})
        assert resp.context["page"].paginator.count == 0
        assert Rec.objects.for_user(manager_a).count() == 0
        assert Rec.objects.count() > 0

    def test_login_required(self, client):
        assert client.get(reverse("recommendations:list")).status_code == 302
