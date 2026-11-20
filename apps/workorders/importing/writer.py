"""Persist parsed work orders idempotently (spec 5.5)."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.customers.models import Client, Vehicle
from apps.workorders.models import ImportBatch, WorkOrder, WorkOrderLine

from .parser import ParsedClient, ParsedVehicle, ParsedWorkOrder


@dataclass
class WriteStats:
    work_orders_created: int = 0
    work_orders_updated: int = 0
    work_orders_unchanged: int = 0
    clients_created: int = 0
    clients_updated: int = 0
    vehicles_created: int = 0
    changed_work_order_ids: list[int] = field(default_factory=list)
    vehicle_ids: set[int] = field(default_factory=set)


class _ClientResolver:
    def __init__(self, station, parsed: list[ParsedWorkOrder]) -> None:
        self.station = station
        phones = {p.client.phone for p in parsed if p.client.phone}
        ext_ids = {p.client.external_id for p in parsed if p.client.external_id}
        qs = Client.objects.filter(station=station)
        self.by_phone = {c.phone: c for c in qs.filter(phone__in=phones)}
        self.by_ext = {}
        for c in qs.filter(external_id__in=ext_ids).order_by("id"):
            self.by_ext.setdefault(c.external_id, c)
        self.stats: WriteStats | None = None

    def resolve(self, pc: ParsedClient, stats: WriteStats) -> Client:
        client = None
        if pc.phone:
            client = self.by_phone.get(pc.phone)
        if client is None and pc.external_id:
            candidate = self.by_ext.get(pc.external_id)
            # reuse only if phones don't conflict (client without phone, or same phone)
            if candidate and (not candidate.phone or not pc.phone):
                client = candidate

        if client is None:
            client = Client.objects.create(
                station=self.station,
                phone=pc.phone,
                external_id=pc.external_id,
                full_name=pc.full_name,
            )
            stats.clients_created += 1
        else:
            changed = []
            if pc.phone and not client.phone:
                client.phone = pc.phone
                changed.append("phone")
            if pc.external_id and not client.external_id:
                client.external_id = pc.external_id
                changed.append("external_id")
            if pc.full_name and pc.full_name != client.full_name:
                client.full_name = pc.full_name
                changed.append("full_name")
            if changed:
                client.save(update_fields=[*changed, "updated_at"])
                stats.clients_updated += 1
        if client.phone:
            self.by_phone[client.phone] = client
        if client.external_id:
            self.by_ext.setdefault(client.external_id, client)
        return client


class _VehicleResolver:
    def __init__(self, client_ids: set[int]) -> None:
        self.by_client: dict[int, list[Vehicle]] = {}
        for v in Vehicle.objects.filter(client_id__in=client_ids).order_by("id"):
            self.by_client.setdefault(v.client_id, []).append(v)

    def resolve(self, client: Client, pv: ParsedVehicle, stats: WriteStats) -> Vehicle:
        vehicles = self.by_client.setdefault(client.id, [])
        vehicle = None
        if pv.vin:
            vehicle = next((v for v in vehicles if v.vin == pv.vin), None)
        if vehicle is None and pv.plate:
            vehicle = next(
                (v for v in vehicles if v.plate == pv.plate and (not pv.vin or not v.vin)), None
            )
        if vehicle is None and not pv.vin and not pv.plate:
            same_model = [
                v
                for v in vehicles
                if pv.make
                and v.make.lower() == pv.make.lower()
                and (not pv.model or v.model.lower() == pv.model.lower())
            ]
            if len(same_model) == 1:
                vehicle = same_model[0]
            elif not pv.make and len(vehicles) == 1:
                vehicle = vehicles[0]

        if vehicle is None:
            vehicle = Vehicle.objects.create(
                client=client,
                vin=pv.vin,
                plate=pv.plate,
                make=pv.make,
                model=pv.model,
                year=pv.year,
            )
            vehicles.append(vehicle)
            stats.vehicles_created += 1
            return vehicle

        changed = []
        for attr in ("vin", "plate", "make", "model", "year"):
            new = getattr(pv, attr)
            if new and not getattr(vehicle, attr):
                setattr(vehicle, attr, new)
                changed.append(attr)
        if changed:
            vehicle.save(update_fields=[*changed, "updated_at"])
        return vehicle


def _line_objects(work_order: WorkOrder, parsed: ParsedWorkOrder) -> list[WorkOrderLine]:
    return [
        WorkOrderLine(
            work_order=work_order,
            kind=line.kind,
            name=line.name,
            quantity=line.quantity,
            price=line.price,
            amount=line.amount,
            executed=line.executed,
        )
        for line in parsed.lines
    ]


@transaction.atomic
def write_work_orders(batch: ImportBatch, parsed: list[ParsedWorkOrder]) -> WriteStats:
    station = batch.station
    stats = WriteStats()
    clients = _ClientResolver(station, parsed)
    existing = {
        wo.external_number: wo
        for wo in WorkOrder.objects.filter(
            station=station, external_number__in=[p.number for p in parsed]
        )
    }
    resolved = [(p, clients.resolve(p.client, stats)) for p in parsed]
    vehicles = _VehicleResolver({c.id for _, c in resolved})

    new_lines: list[WorkOrderLine] = []
    replaced_wo_ids: list[int] = []
    now = timezone.now()
    for p, client in resolved:
        vehicle = vehicles.resolve(client, p.vehicle, stats)
        stats.vehicle_ids.add(vehicle.id)
        content_hash = p.content_hash()
        wo = existing.get(p.number)
        if wo is not None and wo.content_hash == content_hash and wo.vehicle_id == vehicle.id:
            stats.work_orders_unchanged += 1
            continue

        is_new = wo is None
        if is_new:
            wo = WorkOrder(station=station, external_number=p.number)
        else:
            stats.vehicle_ids.add(wo.vehicle_id)  # vehicle may have changed
        wo.vehicle = vehicle
        wo.opened_at = p.opened_at
        wo.closed_at = p.closed_at
        wo.status = p.status
        wo.mileage = p.mileage
        wo.total_amount = p.total_amount
        wo.mechanic_comment = p.mechanic_comment
        wo.recommendations_text = p.recommendations_text
        wo.raw = p.raw
        wo.import_batch = batch
        wo.content_hash = content_hash
        wo.extraction_status = WorkOrder.ExtractionStatus.PENDING
        wo.updated_at = now
        wo.save()
        existing[p.number] = wo
        if is_new:
            stats.work_orders_created += 1
        else:
            stats.work_orders_updated += 1
            replaced_wo_ids.append(wo.id)
        stats.changed_work_order_ids.append(wo.id)
        new_lines.extend(_line_objects(wo, p))

    if replaced_wo_ids:
        WorkOrderLine.objects.filter(work_order_id__in=replaced_wo_ids).delete()
    WorkOrderLine.objects.bulk_create(new_lines, batch_size=1000)
    return stats
