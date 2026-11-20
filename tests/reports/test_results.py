"""Stage 7 acceptance: import → reminders → new import with visits → correct report."""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.messaging.services import handle_incoming_text, send_touch
from apps.outreach.models import Outreach
from apps.outreach.planner import build_queue
from apps.recommendations.models import Recommendation as Rec
from apps.reports.attribution import attribute_visits
from apps.reports.metrics import Comparison, build_report
from apps.workorders.models import ImportMappingProfile
from tests.workorders.helpers import run_import

pytestmark = pytest.mark.django_db

HEADER = "Номер;Дата;Клиент;Телефон;Марка;Работа;Выполнено;Сумма;Итого\n"
SENT_AT = datetime(2026, 5, 10, 7, 0, tzinfo=UTC)  # 12:00 in Yekaterinburg
TODAY = date(2026, 11, 20)


def row(number, day, n, work, done, amount, total=""):
    return f"{number};{day};Клиент {n};8900000000{n};Lada;{work};{done};{amount};{total}\n"


@pytest.fixture
def scenario(station_a):
    station_a.settings = {"control_group_share": 0, "recommendation_ttl_days": 100000}
    station_a.save()
    profile = ImportMappingProfile.objects.create(
        station=station_a, name="p", source_system="other",
        column_map={"order_number": "Номер", "opened_at": "Дата", "client_name": "Клиент",
                    "phone": "Телефон", "make": "Марка", "line_name": "Работа",
                    "executed": "Выполнено", "line_amount": "Сумма", "order_total": "Итого"},
    )  # fmt: skip
    # 1) first export: six clients declined the timing belt on 01.05.2026
    first = HEADER + "".join(
        row(f"A{n}", "01.05.2026", n, "Замена ремня ГРМ", "нет", 5000) for n in range(1, 7)
    )
    run_import(station_a, profile, "first.csv", first.encode())

    # 2) the daily queue; clients 5 and 6 are the control group
    build_queue(station_a, date(2026, 5, 10))
    by_client = {o.client.full_name: o for o in Outreach.objects.select_related("client")}
    assert len(by_client) == 6
    for name in ("Клиент 5", "Клиент 6"):
        Outreach.objects.filter(pk=by_client[name].pk).update(
            is_control_group=True, status="planned"
        )
    # 3) the others are approved and sent; client 1 asks to book
    for n in range(1, 5):
        o = by_client[f"Клиент {n}"]
        Outreach.objects.filter(pk=o.pk).update(status="approved", channel="sms")
        assert send_touch(o.pk, SENT_AT) == "sent"
    handle_incoming_text(by_client["Клиент 1"].client, "sms", "Давайте в субботу")

    # 4) second export with visits
    second = HEADER + "".join([
        row("B1", "20.05.2026", 1, "Замена ремня ГРМ", "да", 5000, 5000),   # in window
        row("B2", "01.07.2026", 2, "Замена ремня ГРМ", "да", 9000, 9000),   # after the window
        row("B3", "25.05.2026", 3, "Замена ремня ГРМ", "да", 7000, 7000),   # in window
        row("B4", "09.06.2026", 4, "Мойка", "да", 3000, 3000),              # last day of window
        row("B5", "25.05.2026", 5, "Замена ремня ГРМ", "да", 6000, 6000),   # control, in window
    ])  # fmt: skip
    batch = run_import(station_a, profile, "second.csv", second.encode())
    return station_a, batch


def test_attribution_after_import(scenario):
    station, batch = scenario
    assert batch.stats["attribution"]["visited"] == 4
    visited = dict(
        Outreach.objects.filter(status="visited").values_list("client__full_name",
                                                              "attributed_amount")
    )  # fmt: skip
    assert visited == {"Клиент 1": Decimal("5000.00"), "Клиент 3": Decimal("7000.00"),
                       "Клиент 4": Decimal("3000.00"), "Клиент 5": Decimal("6000.00")}  # fmt: skip
    # client 2 came after the 30-day window → not attributed
    assert Outreach.objects.get(client__full_name="Клиент 2").status == "sent"
    # visited reminders free the vehicle for future reminders
    assert not Outreach.objects.active().filter(client__full_name="Клиент 1").exists()


def test_report_numbers(scenario):
    station, _ = scenario
    report = build_report([station.id], date(2026, 5, 1), date(2026, 5, 31), TODAY)
    f = report.funnel
    assert (f.to_send, f.sent, f.replied, f.bookings, f.visited) == (4, 4, 1, 1, 3)
    assert f.revenue == Decimal("15000.00")  # 5000 + 7000 + 3000, not client 2's 9000
    # found: 6 declined belts (01.05) + new belt intervals from the May visits of 1, 3 and 5
    assert f.found == 9 == Rec.objects.filter(
        source_work_order__opened_at__month=5).count()  # fmt: skip

    c = report.comparison
    assert (c.sent, c.sent_visited, c.control, c.control_visited) == (4, 3, 2, 1)
    assert c.sent_rate == 0.75 and c.control_rate == 0.5
    assert c.uplift_pp == pytest.approx(25.0)
    assert c.avg_check == Decimal("5000")
    assert c.extra_revenue == Decimal("5000")  # 0.25 × 4 × 5000
    assert not c.enough_data and c.open_windows == 0


def test_report_period_filter(scenario):
    station, _ = scenario
    report = build_report([station.id], date(2026, 6, 1), date(2026, 6, 30), TODAY)
    assert report.funnel.sent == 0 and report.funnel.revenue == 0


def test_money_on_table(scenario):
    station, _ = scenario
    report = build_report([station.id], date(2026, 5, 1), date(2026, 5, 31), TODAY)
    active = Rec.objects.active()
    assert report.money_count == active.count()
    assert report.money_total == sum(r.est_price or 0 for r in active)
    assert report.money[0].title == "Замена ремня ГРМ"


def test_attribution_is_idempotent(scenario):
    station, _ = scenario
    assert attribute_visits([station.id])["visited"] == 0


def test_comparison_math():
    c = Comparison(sent=400, sent_visited=120, control=60, control_visited=9,
                   avg_check=Decimal("8000"))  # fmt: skip
    assert c.uplift_pp == pytest.approx(15.0)
    low, high = c.ci95_pp
    assert low > 0 and high < 30 and c.significant and c.enough_data
    assert c.extra_revenue == Decimal("480000")
    empty = Comparison(sent=10, sent_visited=5)
    assert empty.ci95_pp is None and empty.extra_revenue == 0 and not empty.significant


class TestViews:
    def test_owner_sees_report_and_csv(self, client, owner_a, scenario):
        client.force_login(owner_a)
        url = reverse("reports:results")
        resp = client.get(url, {"from": "2026-05-01", "to": "2026-05-31"})
        html = resp.content.decode()
        assert resp.status_code == 200
        assert "15 000" in html and "+25,0 п.п." in html
        resp = client.get(reverse("reports:results_csv"),
                          {"from": "2026-05-01", "to": "2026-05-31"})  # fmt: skip
        assert resp["Content-Type"].startswith("text/csv")
        text = resp.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        assert ["Воронка", "Выручка, ₽", "15000.00"] in rows
        assert ["Сравнение с контролем", "Разница, п.п.", "25.0"] in rows
        assert "Клиент 1" not in text and "+7900" not in text  # aggregates only, no PII

    def test_defaults_and_bad_params(self, client, owner_a, station_a):
        client.force_login(owner_a)
        resp = client.get(reverse("reports:results"), {"from": "bad", "to": "x", "station": "999"})
        assert resp.status_code == 200 and resp.context["choice"] == "all"
        r = resp.context["report"]
        assert r.date_from.day == 1

    def test_manager_forbidden(self, client, manager_a):
        client.force_login(manager_a)
        assert client.get(reverse("reports:results")).status_code == 403
        assert client.get(reverse("reports:results_csv")).status_code == 403

    def test_isolation(self, client, station_b, scenario):
        from apps.accounts.models import Role
        from tests.factories import UserFactory

        stranger = UserFactory(organization=station_b.organization, role=Role.OWNER)
        client.force_login(stranger)
        station_a = scenario[0]
        params = {"from": "2026-05-01", "to": "2026-05-31", "station": station_a.pk}
        resp = client.get(reverse("reports:results"), params)
        r = resp.context["report"]
        assert r.funnel.sent == 0 and r.money_count == 0
