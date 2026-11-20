from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.common.models import AuditEvent
from apps.customers.models import Client, Vehicle
from apps.messaging.models import Message
from apps.outreach.models import Outreach
from apps.recommendations.models import Recommendation
from apps.workorders.models import ImportBatch, WorkOrder, WorkOrderLine
from tests.workorders.helpers import flat_profile, run_import

pytestmark = pytest.mark.django_db


@pytest.fixture
def imported(station_a):
    station_a.settings = {"recommendation_ttl_days": 100000}
    station_a.save()
    batch = run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    client = Client.objects.get(phone="+79000000001")
    vehicle = client.vehicles.get()
    o = Outreach.objects.create(station=station_a, client=client, vehicle=vehicle,
                                planned_for="2026-06-01", status="sent")  # fmt: skip
    Message.objects.create(client=client, outreach=o, channel="sms", direction="in",
                           text="Перезвоните", status="received")  # fmt: skip
    return client, batch


def test_dry_run_changes_nothing(imported):
    client, _ = imported
    out = StringIO()
    call_command("delete_client_data", client_id=client.pk, stdout=out)
    text = out.getvalue()
    assert "work_orders: 2" in text and "Dry run" in text
    assert Client.objects.filter(pk=client.pk).exists()


def test_delete_everything(imported):
    client, batch = imported
    other = Client.objects.exclude(pk=client.pk).get()
    other_wos = WorkOrder.objects.filter(vehicle__client=other).count()
    assert batch.file
    call_command("delete_client_data", client_id=client.pk, yes=True, stdout=StringIO())
    assert not Client.objects.filter(pk=client.pk).exists()
    assert not Vehicle.objects.filter(client_id=client.pk).exists()
    assert not WorkOrder.objects.filter(external_number__in=["ЗН-1001", "ЗН-1004"]).exists()
    assert not WorkOrderLine.objects.filter(work_order__external_number="ЗН-1001").exists()
    assert not Recommendation.objects.filter(vehicle__client_id=client.pk).exists()
    assert not Outreach.objects.exists() and not Message.objects.exists()
    # other clients are untouched; the shared import file is removed, stats are kept
    assert WorkOrder.objects.filter(vehicle__client=other).count() == other_wos
    batch.refresh_from_db()
    assert not batch.file and batch.stats["work_orders"] == 3
    event = AuditEvent.objects.get(action="client.data_deleted")
    assert event.data["client_id"] == client.pk and "Тестов" not in str(event.data)


def test_unknown_client():
    with pytest.raises(CommandError):
        call_command("delete_client_data", client_id=999999)


def test_import_files_purged_after_retention(station_a, settings):
    from datetime import timedelta

    from django.utils import timezone

    from apps.workorders.tasks import purge_import_files_task

    batch = run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    assert purge_import_files_task() == 0
    ImportBatch.objects.filter(pk=batch.pk).update(finished_at=timezone.now() - timedelta(days=31))
    assert purge_import_files_task() == 1
    batch.refresh_from_db()
    assert not batch.file and batch.status == "parsed"
