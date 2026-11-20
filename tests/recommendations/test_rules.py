from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.catalog.models import WorkType
from apps.recommendations.models import Recommendation as Rec
from apps.recommendations.rules import add_months, apply_rules, expire_recommendations
from tests.factories import VehicleFactory, WorkOrderFactory, WorkOrderLineFactory

pytestmark = pytest.mark.django_db


def wo_with(vehicle, lines, **kwargs):
    wo = WorkOrderFactory(vehicle=vehicle, **kwargs)
    for spec in lines:
        if isinstance(spec, str):
            spec = {"name": spec}
        WorkOrderLineFactory(work_order=wo, **spec)
    return wo


@pytest.fixture
def vehicle(db):
    return VehicleFactory()


def test_add_months():
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2024, 11, 15), 14) == date(2026, 1, 15)


def test_regular_interval_recommendation(vehicle):
    wo = wo_with(vehicle, ["Замена масла ДВС", "Замена салонного фильтра", "Мойка"],
                 opened_at=date(2024, 3, 10), mileage=45000)  # fmt: skip
    apply_rules([wo.id])
    oil = Rec.objects.get(work_type__code="engine_oil")
    assert oil.source == "regular_interval"
    assert oil.due_km == 55000
    assert oil.due_date == date(2025, 3, 10)
    assert oil.status == "open" and oil.confidence == 1.0
    assert oil.source_work_order == wo and oil.station == wo.station
    assert oil.extractor_version == "rules-v1"
    cabin = Rec.objects.get(work_type__code="cabin_filter")
    assert cabin.urgency == "comfort"
    assert Rec.objects.count() == 2  # "Мойка" is unmatched, brake pads are not regular


def test_regular_without_mileage_uses_date_only(vehicle):
    wo = wo_with(vehicle, ["Замена масла ДВС"], mileage=None, opened_at=date(2024, 3, 10))
    apply_rules([wo.id])
    rec = Rec.objects.get()
    assert rec.due_km is None and rec.due_date == date(2025, 3, 10)


def test_regular_km_only_type(vehicle):
    wo = wo_with(vehicle, ["Замена масла АКПП"], mileage=80000)
    apply_rules([wo.id])
    rec = Rec.objects.get()
    assert rec.due_km == 140000 and rec.due_date is None


def test_non_regular_executed_work_creates_nothing(vehicle):
    wo = wo_with(vehicle, ["Замена передних тормозных колодок"])
    apply_rules([wo.id])
    assert not Rec.objects.exists()


def test_declined_line(vehicle):
    belt = {"name": "Замена ремня ГРМ", "executed": False, "amount": Decimal("4500")}
    wo = wo_with(vehicle, [belt], opened_at=date(2024, 1, 15))
    apply_rules([wo.id])
    rec = Rec.objects.get()
    assert rec.source == "declined_line"
    assert rec.work_type.code == "timing_belt"
    assert rec.due_date == date(2024, 1, 29)
    assert rec.due_km is None
    assert rec.urgency == "wear"
    assert rec.est_price == Decimal("4500")
    assert rec.status == "open"
    assert rec.source_text == "Замена ремня ГРМ"


def test_declined_safety_urgency(vehicle):
    wo = wo_with(vehicle, [{"name": "Замена передних колодок", "executed": False}])
    apply_rules([wo.id])
    assert Rec.objects.get().urgency == "safety"


def test_unmatched_declined_line_goes_to_review(vehicle):
    wo = wo_with(vehicle, [{"name": "Покраска бампера", "executed": False}])
    apply_rules([wo.id])
    rec = Rec.objects.get()
    assert rec.work_type is None and rec.work_type_raw == "Покраска бампера"
    assert rec.status == "needs_review" and rec.confidence == 0.5


def test_declined_and_executed_same_type_in_one_visit(vehicle):
    wo = wo_with(
        vehicle, ["Замена масла ДВС", {"name": "Замена масла двигателя", "executed": False}]
    )
    apply_rules([wo.id])
    assert list(Rec.objects.values_list("source", flat=True)) == ["regular_interval"]


def test_declined_parts_are_ignored(vehicle):
    wo = wo_with(vehicle, [{"name": "Колодки тормозные", "executed": False, "kind": "part"}])
    apply_rules([wo.id])
    assert not Rec.objects.exists()


def test_open_and_cancelled_orders_produce_nothing(vehicle):
    open_wo = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}], status="open")
    apply_rules([open_wo.id])
    assert not Rec.objects.exists()
    # but lines are still matched
    assert open_wo.lines.get().work_type.code == "timing_belt"


def test_later_work_closes_recommendation(vehicle):
    first = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}],
                    opened_at=date(2024, 1, 15))  # fmt: skip
    apply_rules([first.id])
    later = wo_with(vehicle, ["Ремень ГРМ - замена"], opened_at=date(2024, 5, 1))
    apply_rules([later.id])
    rec = Rec.objects.get(source="declined_line")
    assert rec.status == "done"
    assert rec.closed_by_work_order == later
    # timing belt is regular → a new interval recommendation from the later visit
    new = Rec.objects.get(source="regular_interval")
    assert new.status == "open" and new.source_work_order == later


def test_regular_rec_is_replaced_by_next_service(vehicle):
    a = wo_with(vehicle, ["Замена масла ДВС"], opened_at=date(2024, 1, 10), mileage=40000)
    b = wo_with(vehicle, ["Замена масла ДВС"], opened_at=date(2024, 9, 10), mileage=49000)
    apply_rules([a.id, b.id])  # whole history in one run, processed chronologically
    old, new = Rec.objects.order_by("id")
    assert old.status == "done" and old.closed_by_work_order == b
    assert new.status == "open" and new.due_km == 59000


def test_other_vehicle_does_not_close(vehicle):
    wo_a = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}])
    other = wo_with(VehicleFactory(client=vehicle.client), ["Замена ремня ГРМ"],
                    opened_at=date(2024, 6, 1))  # fmt: skip
    apply_rules([wo_a.id, other.id])
    assert Rec.objects.get(source="declined_line").status == "open"


def test_backfill_older_order_imported_after_newer(vehicle):
    """History is uploaded newest-first: older visits must not override newer information."""
    newer = wo_with(vehicle, ["Замена масла ДВС"], opened_at=date(2024, 9, 1), mileage=50000)
    belt_done = wo_with(vehicle, ["Замена ремня ГРМ"], opened_at=date(2024, 2, 1), mileage=45000)
    apply_rules([newer.id, belt_done.id])
    older = wo_with(vehicle, ["Замена масла ДВС", {"name": "Замена ремня ГРМ", "executed": False},
                              {"name": "Замена передних колодок", "executed": False}],
                    opened_at=date(2023, 9, 1), mileage=40000)  # fmt: skip
    pads_done = wo_with(vehicle, ["Замена передних тормозных колодок"], opened_at=date(2023, 12, 1))
    apply_rules([pads_done.id])
    apply_rules([older.id])
    # the newer oil / belt recommendations stay authoritative, nothing from the old visit wins
    assert Rec.objects.get(work_type__code="engine_oil", status="open").source_work_order == newer
    belt = Rec.objects.get(work_type__code="timing_belt", status="open")
    assert belt.source_work_order == belt_done and belt.source == "regular_interval"
    # the declined pads from the older visit were already replaced later
    pads = Rec.objects.get(work_type__code="brake_pads_front")
    assert pads.status == "done" and pads.closed_by_work_order == pads_done


def test_repeat_decline_updates_existing(vehicle):
    a = wo_with(
        vehicle, [{"name": "Замена ремня ГРМ", "executed": False}], opened_at=date(2024, 1, 1)
    )
    b = wo_with(
        vehicle, [{"name": "Замена ремня ГРМ", "executed": False}], opened_at=date(2024, 6, 1)
    )
    apply_rules([a.id])
    apply_rules([b.id])
    rec = Rec.objects.get()
    assert rec.source_work_order == b
    assert rec.due_date == date(2024, 6, 15)


def test_idempotent_rerun(vehicle):
    wo = wo_with(vehicle, ["Замена масла ДВС", {"name": "Замена ремня ГРМ", "executed": False},
                           {"name": "Покраска", "executed": False}])  # fmt: skip
    apply_rules([wo.id])
    snapshot = list(Rec.objects.order_by("id").values("id", "status", "due_date", "due_km"))
    apply_rules([wo.id])
    assert list(Rec.objects.order_by("id").values("id", "status", "due_date", "due_km")) == snapshot


def test_edited_order_removes_stale_recommendation(vehicle):
    wo = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}])
    apply_rules([wo.id])
    wo.lines.all().delete()
    WorkOrderLineFactory(work_order=wo, name="Мойка")
    apply_rules([wo.id])
    assert not Rec.objects.exists()


def test_rejected_recommendation_is_not_resurrected(vehicle):
    wo = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}])
    apply_rules([wo.id])
    Rec.objects.update(status="rejected")
    apply_rules([wo.id])
    assert list(Rec.objects.values_list("status", flat=True)) == ["rejected"]


def test_est_price_from_station_history(vehicle):
    for price in (1000, 2000):
        wo_with(vehicle, [{"name": "Замена масла ДВС", "amount": Decimal(price)}],
                opened_at=date.today() - timedelta(days=30))  # fmt: skip
    from apps.workorders.models import WorkOrder

    ids = list(WorkOrder.objects.values_list("id", flat=True))
    apply_rules(ids)
    assert Rec.objects.get(status="open").est_price == Decimal("1500")


def test_expiry_by_ttl(vehicle):
    old = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}],
                  opened_at=date.today() - timedelta(days=600))  # fmt: skip
    fresh = wo_with(VehicleFactory(client=vehicle.client),
                    [{"name": "Замена ремня ГРМ", "executed": False}],
                    opened_at=date.today() - timedelta(days=100))  # fmt: skip
    apply_rules([old.id, fresh.id])
    assert expire_recommendations() == 1
    assert Rec.objects.get(source_work_order=old).status == "expired"
    assert Rec.objects.get(source_work_order=fresh).status == "open"


def test_long_interval_not_expired_before_due(vehicle):
    vehicle.last_visit_at = date.today() - timedelta(days=700)
    vehicle.last_known_mileage = 60000
    vehicle.save()
    wo = wo_with(vehicle, ["Замена ремня ГРМ"], opened_at=vehicle.last_visit_at, mileage=60000)
    apply_rules([wo.id])
    # due in 48 months / +60000 km (~3.3 years at 50 km/day) → not expired
    assert expire_recommendations() == 0


def test_ttl_is_station_setting(vehicle):
    station = vehicle.client.station
    station.settings = {"recommendation_ttl_days": 30}
    station.save()
    wo = wo_with(vehicle, [{"name": "Замена ремня ГРМ", "executed": False}],
                 opened_at=date.today() - timedelta(days=60))  # fmt: skip
    apply_rules([wo.id])
    assert expire_recommendations([station.id]) == 1


def test_expected_date_uses_mileage(vehicle):
    vehicle.last_visit_at = date(2024, 1, 1)
    vehicle.last_known_mileage = 50000
    vehicle.km_per_day_estimate = 100
    vehicle.save()
    wo = wo_with(vehicle, ["Замена масла ДВС"], opened_at=date(2024, 1, 1), mileage=50000)
    apply_rules([wo.id])
    rec = Rec.objects.get()
    # 10000 km at 100 km/day = 100 days, earlier than the 12-month date
    assert rec.expected_date() == date(2024, 4, 10)


def test_work_type_catalog_used(vehicle):
    assert WorkType.objects.filter(is_regular=True).count() >= 15
