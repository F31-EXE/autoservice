"""Erase all personal data of one client on request (spec 11, 152-FZ)."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from apps.common.models import audit
from apps.workorders.models import ImportBatch, WorkOrder

from .models import Client


@dataclass
class DeletionPlan:
    client_id: int
    station_id: int
    counts: dict[str, int] = field(default_factory=dict)
    batch_ids: list[int] = field(default_factory=list)


def plan_deletion(client: Client) -> DeletionPlan:
    work_orders = WorkOrder.objects.filter(vehicle__client=client)
    batch_ids = sorted(
        set(
            work_orders.exclude(import_batch__isnull=True).values_list("import_batch_id", flat=True)
        )
    )
    counts = {
        "vehicles": client.vehicles.count(),
        "work_orders": work_orders.count(),
        "recommendations": sum(v.recommendations.count() for v in client.vehicles.all()),
        "outreaches": client.outreaches.count(),
        "messages": client.messages.count(),
        "booking_requests": client.booking_requests.count(),
        "max_links": client.max_links.count(),
        "import_files": ImportBatch.objects.filter(id__in=batch_ids).exclude(file="").count(),
    }
    return DeletionPlan(client.id, client.station_id, counts, batch_ids)


@transaction.atomic
def delete_client_data(client: Client, actor=None) -> DeletionPlan:
    """Delete the client and everything derived from them.

    Import files that contained the client's rows are deleted too (the file mixes many
    clients, but it is only an audit copy of the STO's own export; batch stats remain).
    The audit log keeps only ids and counts.
    """
    plan = plan_deletion(client)
    station = client.station
    for batch in ImportBatch.objects.filter(id__in=plan.batch_ids).exclude(file=""):
        batch.file.delete(save=False)
        batch.file = ""
        batch.save(update_fields=["file", "updated_at"])
    # vehicles → work orders → lines / recommendations cascade; outreaches, messages,
    # bookings and MAX links cascade from the client
    WorkOrder.objects.filter(vehicle__client=client).delete()
    client.delete()
    audit(actor, "client.data_deleted", station, station=station,
          client_id=plan.client_id, counts=plan.counts)  # fmt: skip
    return plan
