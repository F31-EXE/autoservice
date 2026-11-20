"""Derived vehicle fields recomputed after each import (spec 5.6, 7.1)."""

from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q

from .models import Vehicle

MIN_DAYS_FOR_ESTIMATE = 30


def estimate_km_per_day(visits: list[tuple]) -> float | None:
    """visits: (date, mileage) sorted by date. Returns clamped km/day or None if unknown."""
    with_mileage = [(d, m) for d, m in visits if m is not None]
    if len(with_mileage) < 2:
        return None
    (first_date, first_km), (last_date, last_km) = with_mileage[0], with_mileage[-1]
    days = (last_date - first_date).days
    if days < MIN_DAYS_FOR_ESTIMATE or last_km <= first_km:
        return None
    value = (last_km - first_km) / days
    return round(min(max(value, Vehicle.MIN_KM_PER_DAY), Vehicle.MAX_KM_PER_DAY), 2)


def recompute_vehicle_stats(vehicle_ids: Iterable[int]) -> None:
    from apps.workorders.models import WorkOrder

    ids = list(set(vehicle_ids))
    if not ids:
        return
    visits_by_vehicle: dict[int, list[tuple]] = {vid: [] for vid in ids}
    rows = (
        WorkOrder.objects.filter(vehicle_id__in=ids)
        .filter(~Q(status=WorkOrder.Status.CANCELLED))
        .values_list("vehicle_id", "opened_at", "closed_at", "mileage")
        .order_by("vehicle_id", "opened_at", "id")
    )
    for vehicle_id, opened_at, closed_at, mileage in rows:
        visits_by_vehicle[vehicle_id].append((closed_at or opened_at, mileage))

    vehicles = list(Vehicle.objects.filter(id__in=ids))
    for vehicle in vehicles:
        visits = sorted(visits_by_vehicle.get(vehicle.id, []), key=lambda v: v[0])
        vehicle.last_visit_at = visits[-1][0] if visits else None
        mileages = [m for _, m in visits if m is not None]
        vehicle.last_known_mileage = mileages[-1] if mileages else None
        vehicle.km_per_day_estimate = estimate_km_per_day(visits)
    Vehicle.objects.bulk_update(
        vehicles, ["last_visit_at", "last_known_mileage", "km_per_day_estimate", "updated_at"]
    )
