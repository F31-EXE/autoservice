from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.outreach.models import Outreach, QueueRun
from apps.outreach.planner import (
    SkipReason,
    build_queue,
    compute_planned_for,
    in_control_group,
    recompute_planned_for,
    run_daily_queues,
)
from apps.recommendations.models import Recommendation as Rec
from tests.factories import ClientFactory, ServiceStationFactory, VehicleFactory, WorkOrderFactory

pytestmark = pytest.mark.django_db

TODAY = date(2026, 6, 15)


@pytest.fixture
def station(db):
    # no control group by default so that queue assertions are deterministic
    return ServiceStationFactory(settings={"control_group_share": 0})


def vehicle_for(station, **kw):
    client_kw = {k[7:]: kw.pop(k) for k in list(kw) if k.startswith("client_")}
    defaults = dict(last_visit_at=TODAY - timedelta(days=100), last_known_mileage=50000,
                    km_per_day_estimate=50)  # fmt: skip
    defaults.update(kw)
    return VehicleFactory(client=ClientFactory(station=station, **client_kw), **defaults)


def rec_for(vehicle, **kw):
    wo = kw.pop("wo", None) or WorkOrderFactory(vehicle=vehicle, opened_at=vehicle.last_visit_at)
    defaults = dict(station=vehicle.client.station, vehicle=vehicle, source_work_order=wo,
                    source="declined_line", work_type_raw="работа", status="open")  # fmt: skip
    defaults.update(kw)
    return Rec.objects.create(**defaults)


# ---------------------------------------------------------------- 7.2 planned_for


class TestPlannedFor:
    def test_date_only(self, station):
        r = rec_for(vehicle_for(station), due_date=date(2026, 9, 1))
        assert compute_planned_for(r, 14) == date(2026, 8, 18)

    def test_mileage_only(self, station):
        # 50000 → 55000 km at 50 km/day = 100 days after the last visit
        v = vehicle_for(station, last_visit_at=date(2026, 1, 1))
        r = rec_for(v, due_km=55000)
        assert compute_planned_for(r, 14) == date(2026, 4, 11) - timedelta(days=14)

    def test_min_of_date_and_mileage(self, station):
        v = vehicle_for(station, last_visit_at=date(2026, 1, 1))
        assert compute_planned_for(rec_for(v, due_km=55000, due_date=date(2026, 2, 1)), 0) == date(
            2026, 2, 1
        )
        assert compute_planned_for(rec_for(v, due_km=55000, due_date=date(2027, 1, 1)), 0) == date(
            2026, 4, 11
        )

    def test_station_default_speed_and_clamping(self, station):
        station.avg_km_per_day_default = 1000  # clamped to 300
        station.save()
        v = vehicle_for(station, last_visit_at=date(2026, 1, 1), km_per_day_estimate=None)
        r = rec_for(v, due_km=56000)
        assert compute_planned_for(r, 0) == date(2026, 1, 21)  # 6000 km / 300

    def test_no_dates_means_not_planned(self, station):
        v = vehicle_for(station, last_known_mileage=None)
        assert compute_planned_for(rec_for(v, due_km=55000), 14) is None
        assert compute_planned_for(rec_for(v), 14) is None

    def test_remind_before_days_setting(self, station):
        station.settings = {"remind_before_days": 3}
        station.save()
        r = rec_for(vehicle_for(station), due_date=date(2026, 9, 10))
        recompute_planned_for(station)
        r.refresh_from_db()
        assert r.planned_for == date(2026, 9, 7)

    def test_mileage_already_passed_gives_past_date(self, station):
        v = vehicle_for(station, last_visit_at=date(2026, 1, 1), last_known_mileage=60000)
        r = rec_for(v, due_km=55000)
        assert compute_planned_for(r, 14) < date(2026, 1, 1)


# ---------------------------------------------------------------- 7.4 queue


def queue(station, today=TODAY):
    return build_queue(station, today)


class TestQueue:
    def test_due_tomorrow_is_queued_day_after_is_not(self, station):
        r1 = rec_for(vehicle_for(station), due_date=TODAY + timedelta(days=15))  # planned +1
        r2 = rec_for(vehicle_for(station), due_date=TODAY + timedelta(days=16))  # planned +2
        stats = queue(station)
        assert stats["awaiting_approval"] == 1
        o = Outreach.objects.get()
        assert list(o.recommendations.all()) == [r1]
        assert o.status == "awaiting_approval" and o.planned_for == TODAY
        r1.refresh_from_db()
        r2.refresh_from_db()
        assert (r1.status, r2.status) == ("scheduled", "open")

    def test_overdue_is_queued_today(self, station):
        rec_for(vehicle_for(station), due_date=TODAY - timedelta(days=60))
        assert queue(station)["awaiting_approval"] == 1
        assert Outreach.objects.get().planned_for == TODAY

    def test_merge_window(self, station):
        v = vehicle_for(station)
        due = rec_for(v, due_date=TODAY + timedelta(days=10))
        soon = rec_for(v, due_date=TODAY + timedelta(days=14 + 29))  # planned within 30 days
        later = rec_for(v, due_date=TODAY + timedelta(days=14 + 31))  # outside the window
        queue(station)
        o = Outreach.objects.get()
        assert set(o.recommendations.all()) == {due, soon}
        later.refresh_from_db()
        assert later.status == "open"

    def test_only_upcoming_does_not_trigger(self, station):
        v = vehicle_for(station)
        rec_for(v, due_date=TODAY + timedelta(days=40))
        assert queue(station).get("awaiting_approval", 0) == 0

    def test_needs_review_and_closed_are_ignored(self, station):
        v = vehicle_for(station)
        rec_for(v, due_date=TODAY, status="needs_review")
        rec_for(v, due_date=TODAY, status="done")
        rec_for(v, due_date=TODAY, status="rejected")
        assert not Outreach.objects.exists() and queue(station).get("awaiting_approval", 0) == 0

    def test_est_amount_sum(self, station):
        v = vehicle_for(station)
        rec_for(v, due_date=TODAY, est_price=Decimal("4500"))
        rec_for(v, due_date=TODAY, est_price=Decimal("1500"))
        rec_for(v, due_date=TODAY)
        queue(station)
        assert Outreach.objects.get().est_amount == Decimal("6000")

    def test_idempotent_same_day(self, station):
        rec_for(vehicle_for(station), due_date=TODAY)
        queue(station)
        stats = queue(station)
        assert Outreach.objects.count() == 1
        assert stats.get("awaiting_approval", 0) == 0

    def test_existing_active_outreach_blocks(self, station):
        v = vehicle_for(station)
        rec_for(v, due_date=TODAY)
        Outreach.objects.create(station=station, client=v.client, vehicle=v, planned_for=TODAY,
                                status="sent")  # fmt: skip
        stats = queue(station)
        assert stats["already_active"] == 1
        assert Outreach.objects.count() == 1

    def test_cooldown_after_skip(self, station):
        v = vehicle_for(station)
        rec_for(v, due_date=TODAY)
        Outreach.objects.create(station=station, client=v.client, vehicle=v, planned_for=TODAY,
                                status="skipped", skip_reason="x")  # fmt: skip
        assert queue(station)["cooldown"] == 1
        Outreach.objects.update(updated_at=timezone.now() - timedelta(days=31))
        assert queue(station)["awaiting_approval"] == 1

    def test_other_station_untouched(self, station):
        other = ServiceStationFactory()
        rec_for(vehicle_for(other), due_date=TODAY)
        queue(station)
        assert not Outreach.objects.exists()


class TestSkipReasons:
    @pytest.mark.parametrize(
        "vehicle_kw,reason",
        [
            ({"client_unsubscribed_at": timezone.now()}, SkipReason.UNSUBSCRIBED),
            ({"client_consent_service": False}, SkipReason.NO_CONSENT),
            ({"is_sold": True}, SkipReason.SOLD),
            ({"last_visit_at": TODAY - timedelta(days=731)}, "Нет визитов дольше 730 дней"),
            ({"client_phone": None, "client_external_id": "X1"}, SkipReason.NO_CONTACT),
        ],
    )
    def test_reason(self, station, vehicle_kw, reason):
        v = vehicle_for(station, **vehicle_kw)
        r = rec_for(v, due_date=TODAY)
        stats = queue(station)
        assert stats["skipped"] == 1
        o = Outreach.objects.get()
        assert (o.status, o.skip_reason) == ("skipped", reason)
        assert list(o.recommendations.all()) == [r]
        r.refresh_from_db()
        assert r.status == "open"  # not scheduled; cooldown prevents daily duplicates
        assert queue(station).get("skipped", 0) == 0

    def test_max_only_client_is_contactable(self, station):
        v = vehicle_for(station, client_phone=None, client_external_id="X", client_max_user_id=5)
        rec_for(v, due_date=TODAY)
        assert queue(station)["awaiting_approval"] == 1

    def test_inactive_threshold_is_setting(self, station):
        station.settings = {"inactive_vehicle_days": 50, "control_group_share": 0}
        station.save()
        rec_for(vehicle_for(station), due_date=TODAY)  # last visit 100 days ago
        assert queue(station)["skipped"] == 1


class TestControlGroup:
    def test_share_on_1000(self):
        hits = sum(in_control_group(i, 0.12) for i in range(1, 1001))
        assert 100 <= hits <= 140  # 12% ± 2 p.p.

    def test_deterministic(self):
        assert [in_control_group(i, 0.12) for i in range(50)] == [
            in_control_group(i, 0.12) for i in range(50)
        ]
        assert not any(in_control_group(i, 0) for i in range(1000))
        assert all(in_control_group(i, 1) for i in range(1000))

    def test_queue_assigns_control_and_never_awaits_approval(self):
        station = ServiceStationFactory(settings={"control_group_share": 1})
        rec = rec_for(vehicle_for(station), due_date=TODAY)
        stats = queue(station)
        assert stats["control"] == 1
        o = Outreach.objects.get()
        assert (o.status, o.is_control_group) == ("planned", True)
        rec.refresh_from_db()
        assert rec.status == "scheduled"  # not queued again while observed

    def test_control_share_in_queue(self):
        station = ServiceStationFactory(settings={"control_group_share": 0.12})
        for _ in range(300):
            rec_for(vehicle_for(station), due_date=TODAY)
        queue(station)
        share = Outreach.objects.filter(is_control_group=True).count() / 300
        assert 0.07 <= share <= 0.17  # sampling noise on 300 units

    def test_control_closes_after_window(self):
        station = ServiceStationFactory(settings={"control_group_share": 1})
        rec = rec_for(vehicle_for(station), due_date=TODAY)
        queue(station)
        queue(station, TODAY + timedelta(days=30))
        assert Outreach.objects.get().status == "planned"
        stats = queue(station, TODAY + timedelta(days=31))
        assert stats["control_closed"] == 1
        o = Outreach.objects.order_by("id").first()
        assert o.status == "no_response"
        rec.refresh_from_db()
        assert rec.status in ("open", "scheduled")  # released (and maybe queued again)


class TestHousekeeping:
    def test_obsolete_outreach_cancelled(self, station):
        v = vehicle_for(station)
        r = rec_for(v, due_date=TODAY)
        queue(station)
        Rec.objects.filter(pk=r.pk).update(status="done")
        stats = queue(station)
        assert stats["obsolete_cancelled"] == 1
        o = Outreach.objects.get()
        assert o.status == "cancelled" and o.skip_reason == SkipReason.WORK_DONE


class TestDailyRun:
    def test_runs_once_after_seven_local(self, station, monkeypatch):
        from apps.accounts.models import ServiceStation

        rec_for(vehicle_for(station), due_date=date.today())

        def fake_now(hour):
            from datetime import datetime

            return lambda self: datetime(2026, 6, 15, hour, 0, tzinfo=self.tz)

        monkeypatch.setattr(ServiceStation, "local_now", fake_now(6))
        assert run_daily_queues() == {}
        monkeypatch.setattr(ServiceStation, "local_now", fake_now(9))
        results = run_daily_queues()
        assert station.id in results
        assert QueueRun.objects.filter(station=station, date=date(2026, 6, 15)).exists()
        assert run_daily_queues() == {}  # already done today

    def test_local_today_uses_station_timezone(self, station):
        station.timezone = "Asia/Vladivostok"
        assert station.local_now().utcoffset() == timedelta(hours=10)
