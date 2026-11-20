from datetime import date
from decimal import Decimal

import pytest

from apps.customers.models import Client, Vehicle
from apps.workorders.models import ImportBatch, WorkOrder, WorkOrderLine
from apps.workorders.signals import work_orders_imported
from tests.workorders.helpers import flat_profile, header_profile, run_import

pytestmark = pytest.mark.django_db


def test_flat_csv_import(station_a):
    batch = run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    assert batch.status == ImportBatch.Status.PARSED, batch.error_log
    s = batch.stats
    assert s["rows"] == 8
    assert s["work_orders"] == 3
    assert s["work_orders_created"] == 3
    assert s["clients_created"] == 2
    assert s["vehicles_created"] == 2
    assert s["errors"] == 3

    assert set(Client.objects.values_list("phone", flat=True)) == {"+79000000001", "+79000000002"}
    wo = WorkOrder.objects.get(external_number="ЗН-1001")
    assert wo.station == station_a
    assert wo.total_amount == Decimal("4000.00")
    assert wo.extraction_status == "pending"
    assert wo.raw[0]["Номер ЗН"] == "ЗН-1001"
    assert wo.lines.filter(executed=False).get().name == "Замена ремня ГРМ"
    # ЗН-1001 and ЗН-1004 are the same car (same VIN), plate normalized
    vehicle = Vehicle.objects.get(vin="XTA00000000000001")
    assert vehicle.work_orders.count() == 2
    assert vehicle.plate == "А001АА96"


def test_reimport_same_file_creates_no_duplicates(station_a):
    profile = flat_profile(station_a)
    run_import(station_a, profile, "autodealer_flat.csv")
    counts = (Client.objects.count(), Vehicle.objects.count(), WorkOrder.objects.count(),
              WorkOrderLine.objects.count())  # fmt: skip
    batch = run_import(station_a, profile, "autodealer_flat.csv")
    assert batch.stats["work_orders_created"] == 0
    assert batch.stats["work_orders_unchanged"] == 3
    assert batch.stats["clients_created"] == 0
    assert (Client.objects.count(), Vehicle.objects.count(), WorkOrder.objects.count(),
            WorkOrderLine.objects.count()) == counts  # fmt: skip


def test_overlapping_period_updates_only_changed(station_a):
    profile = flat_profile(station_a)
    run_import(station_a, profile, "autodealer_flat.csv")
    received = {}

    def handler(sender, batch, changed_work_order_ids, vehicle_ids, **kwargs):
        received["changed"] = changed_work_order_ids

    work_orders_imported.connect(handler)
    try:
        batch = run_import(station_a, profile, "autodealer_flat_2.csv")
    finally:
        work_orders_imported.disconnect(handler)

    assert batch.stats["work_orders_created"] == 1  # ЗН-1006
    assert batch.stats["work_orders_updated"] == 1  # ЗН-1004 price changed
    assert batch.stats["work_orders_unchanged"] == 1  # ЗН-1002
    assert WorkOrder.objects.count() == 4
    wo = WorkOrder.objects.get(external_number="ЗН-1004")
    assert wo.total_amount == Decimal("1700.00")
    assert wo.lines.count() == 1
    assert set(received["changed"]) == set(
        WorkOrder.objects.filter(external_number__in=["ЗН-1004", "ЗН-1006"]).values_list(
            "id", flat=True
        )
    )


def test_header_xlsx_import_and_external_id(station_a):
    batch = run_import(station_a, header_profile(station_a), "alfa_header.xlsx")
    assert batch.status == "parsed", batch.error_log
    assert batch.stats["work_orders_created"] == 4
    assert batch.stats["clients_created"] == 2
    no_phone = Client.objects.get(external_id="C-12")
    assert no_phone.phone is None
    wo = WorkOrder.objects.get(external_number="А-501")
    assert wo.lines.count() == 4
    assert wo.lines.filter(executed=False).count() == 1
    assert wo.mechanic_comment.startswith("Рекомендую")
    assert WorkOrder.objects.get(external_number="А-503").status == "cancelled"
    batch2 = run_import(station_a, header_profile_reuse(station_a), "alfa_header.xlsx")
    assert batch2.stats["work_orders_unchanged"] == 4


def header_profile_reuse(station):
    from apps.workorders.models import ImportMappingProfile

    return ImportMappingProfile.objects.get(station=station, name="Альфа")


def test_vehicle_stats_after_import(station_a):
    run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    vesta = Vehicle.objects.get(vin="XTA00000000000001")
    assert vesta.last_visit_at == date(2024, 7, 10)
    assert vesta.last_known_mileage == 52500
    # (52500 - 45000) / 177 days
    assert vesta.km_per_day_estimate == pytest.approx(42.37, abs=0.01)
    rio = Vehicle.objects.get(plate="В002ВВ196")
    assert rio.km_per_day_estimate is None  # single visit
    assert rio.effective_km_per_day() == 50  # station default


def test_cancelled_orders_ignored_in_vehicle_stats(station_a):
    run_import(station_a, header_profile(station_a), "alfa_header.xlsx")
    camry = Vehicle.objects.get(vin="JTN00000000000011")
    assert camry.last_visit_at == date(2024, 9, 21)
    assert camry.last_known_mileage == 135500


def test_same_numbers_in_different_stations_are_separate(station_a, station_b):
    run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    run_import(station_b, flat_profile(station_b), "autodealer_flat.csv")
    assert WorkOrder.objects.filter(external_number="ЗН-1001").count() == 2
    assert Client.objects.filter(phone="+79000000001").count() == 2


def test_missing_profile_columns_fails_cleanly(station_a):
    profile = flat_profile(station_a)
    profile.column_map = {**profile.column_map, "mileage": "Нет такой колонки"}
    profile.save()
    batch = run_import(station_a, profile, "autodealer_flat.csv")
    assert batch.status == "failed"
    assert "Нет такой колонки" in batch.error_log
    assert WorkOrder.objects.count() == 0


def test_task_is_idempotent_for_finished_batch(station_a):
    from apps.workorders.tasks import parse_import_batch

    batch = run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    stats = batch.stats
    parse_import_batch(batch.pk)
    batch.refresh_from_db()
    assert batch.stats == stats


def test_phone_conflict_with_external_id(station_a):
    """A client found by external_id but with another phone is not overwritten."""
    content = (
        "Номер ЗН;Дата;Телефон;Код;Работа\n"
        "1;01.02.2024;89000000001;K1;Мойка\n"
        "2;01.03.2024;89000000002;K1;Мойка\n"
        "3;01.04.2024;;K1;Мойка\n"
    ).encode()
    from apps.workorders.models import ImportMappingProfile

    profile = ImportMappingProfile.objects.create(
        station=station_a,
        name="p",
        source_system="other",
        column_map={
            "order_number": "Номер ЗН",
            "opened_at": "Дата",
            "phone": "Телефон",
            "client_external_id": "Код",
            "line_name": "Работа",
        },  # fmt: skip
    )
    batch = run_import(station_a, profile, "x.csv", content)
    assert batch.status == "parsed", batch.error_log
    assert set(Client.objects.values_list("phone", flat=True)) == {"+79000000001", "+79000000002"}
    # the order without phone attaches to the first client with that external id
    wo3 = WorkOrder.objects.get(external_number="3")
    assert wo3.vehicle.client.phone == "+79000000001"


@pytest.mark.parametrize("rows", [3000])
def test_bulk_import_speed(station_a, rows):
    import time

    lines = ["Номер ЗН;Дата;Телефон;Марка;Работа;Сумма;Пробег"]
    for i in range(rows):
        lines.append(
            f"N{i // 3};01.0{1 + i % 9}.2024;8900{i // 3 % 500:07d};Lada;Работа {i};100;{i}"
        )
    from apps.workorders.models import ImportMappingProfile

    profile = ImportMappingProfile.objects.create(
        station=station_a,
        name="p",
        source_system="other",
        column_map={
            "order_number": "Номер ЗН",
            "opened_at": "Дата",
            "phone": "Телефон",
            "make": "Марка",
            "line_name": "Работа",
            "line_amount": "Сумма",
            "mileage": "Пробег",
        },  # fmt: skip
    )
    started = time.monotonic()
    batch = run_import(station_a, profile, "big.csv", "\n".join(lines).encode())
    assert batch.status == "parsed"
    assert WorkOrderLine.objects.count() == rows
    assert time.monotonic() - started < 30
