"""Visit attribution (spec 9.1): a reminder "worked" if the vehicle came for a new work order
within ``attribution_window_days`` after the first touch (control group: after planned_for)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.outreach.models import Outreach
from apps.outreach.planner import release_recommendations
from apps.workorders.models import WorkOrder

# Reminders that were delivered (or deliberately not, for the control group) and may still
# turn into a visit. no_response is included: a client can come without answering.
CONTACTED = (Outreach.Status.SENT, Outreach.Status.REPLIED, Outreach.Status.BOOKED,
             Outreach.Status.NO_RESPONSE)  # fmt: skip
CONTROL = (Outreach.Status.PLANNED, Outreach.Status.NO_RESPONSE)


def window_start(outreach: Outreach):
    if outreach.is_control_group:
        return outreach.planned_for
    return outreach.first_touch_at.astimezone(outreach.station.tz).date()


@transaction.atomic
def attribute_visits(
    station_ids: Iterable[int] | None = None,
    vehicle_ids: Iterable[int] | None = None,
    today=None,
) -> dict[str, int]:
    today = today or timezone.localdate()
    qs = Outreach.objects.filter(attributed_work_order__isnull=True).filter(
        Q(is_control_group=False, status__in=CONTACTED, first_touch_at__isnull=False)
        | Q(is_control_group=True, status__in=CONTROL, planned_for__lte=today)
    )
    if station_ids is not None:
        qs = qs.filter(station_id__in=list(station_ids))
    if vehicle_ids is not None:
        qs = qs.filter(vehicle_id__in=list(vehicle_ids))
    outreaches = list(qs.select_related("station"))
    if not outreaches:
        return {"checked": 0, "visited": 0}

    used = set(
        Outreach.objects.filter(
            vehicle_id__in={o.vehicle_id for o in outreaches}, attributed_work_order__isnull=False
        ).values_list("attributed_work_order_id", flat=True)
    )
    # the most recent reminder gets the visit when windows overlap
    outreaches.sort(key=lambda o: window_start(o), reverse=True)
    visited = 0
    for outreach in outreaches:
        start = window_start(outreach)
        end = start + timedelta(days=int(outreach.station.conf["attribution_window_days"]))
        work_order = (
            WorkOrder.objects.filter(
                vehicle_id=outreach.vehicle_id, opened_at__gte=start, opened_at__lte=end
            )
            .exclude(status=WorkOrder.Status.CANCELLED)
            .exclude(id__in=used)
            .order_by("opened_at", "id")
            .first()
        )
        if work_order is None:
            continue
        used.add(work_order.id)
        outreach.status = Outreach.Status.VISITED
        outreach.attributed_work_order = work_order
        outreach.attributed_amount = work_order.total_amount
        outreach.save(update_fields=["status", "attributed_work_order", "attributed_amount",
                                     "updated_at"])  # fmt: skip
        release_recommendations(outreach)  # done ones were closed by the rules already
        visited += 1
    return {"checked": len(outreaches), "visited": visited}
