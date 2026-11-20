"""Rule-based recommendations from work order lines (spec 6.1, 6.4). No LLM here."""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Avg, F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.catalog.matching import WorkTypeMatcher, normalize_name
from apps.catalog.models import WorkType
from apps.workorders.models import WorkOrder, WorkOrderLine

from .models import Recommendation

logger = logging.getLogger(__name__)

RULES_VERSION = "rules-v1"
DECLINED_DUE_DAYS = 14
UNMATCHED_CONFIDENCE = 0.5
PRICE_HISTORY_DAYS = 365


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _visit_date_expr(prefix: str = ""):
    return Coalesce(F(f"{prefix}closed_at"), F(f"{prefix}opened_at"))


class _Context:
    """Per-station caches for one rules run."""

    def __init__(self, station_id: int) -> None:
        self.station_id = station_id
        self.matcher = WorkTypeMatcher(station_id)
        self.avg_price: dict[int, Decimal] = {}

    def load_prices(self) -> None:
        since = timezone.localdate() - timedelta(days=PRICE_HISTORY_DAYS)
        rows = (
            WorkOrderLine.objects.filter(
                work_order__station_id=self.station_id,
                work_order__opened_at__gte=since,
                kind=WorkOrderLine.Kind.LABOR,
                executed=True,
                work_type__isnull=False,
                amount__gt=0,
            )
            .values("work_type_id")
            .annotate(avg=Avg("amount"))
        )
        self.avg_price = {r["work_type_id"]: Decimal(r["avg"]).quantize(Decimal("1")) for r in rows}


_ACTIVE_UNREVIEWED = (Recommendation.Status.OPEN, Recommendation.Status.NEEDS_REVIEW)


class _VehicleState:
    """In-memory recommendations + executed work history of one vehicle."""

    def __init__(self) -> None:
        self.recs: list[Recommendation] = []
        # (visit_date, work_order_id, work_type_id) of executed labor in closed orders
        self.executed: list[tuple[date, int, int]] = []


class _Run:
    def __init__(self) -> None:
        self.stats: dict[str, int] = defaultdict(int)
        self.new: list[Recommendation] = []
        self.dirty: dict[int, Recommendation] = {}
        self.deleted: set[int] = set()

    def touch(self, rec: Recommendation) -> None:
        if rec.pk:
            self.dirty[rec.pk] = rec

    def delete(self, state: _VehicleState, rec: Recommendation) -> None:
        state.recs.remove(rec)
        if rec.pk:
            self.deleted.add(rec.pk)
            self.dirty.pop(rec.pk, None)
        else:
            self.new.remove(rec)
        self.stats["removed"] += 1


_UPDATE_FIELDS = [
    "source_work_order", "source", "source_text", "due_km", "due_date", "urgency",
    "est_price", "confidence", "status", "closed_by_work_order", "extractor_version",
    "updated_at",
]  # fmt: skip


@transaction.atomic
def apply_rules(work_order_ids: Iterable[int]) -> dict[str, int]:
    """Match lines to work types and create/close/update rule-based recommendations.

    Works in memory per vehicle and writes in bulk. Idempotent: re-running on the same
    work orders yields the same state.
    """
    ids = list(set(work_order_ids))
    run = _Run()
    if not ids:
        return {}
    work_orders = list(
        WorkOrder.objects.filter(id__in=ids)
        .select_related("vehicle", "station")
        .prefetch_related("lines__work_type")
    )
    contexts: dict[int, _Context] = {}

    # pass 1: map lines to work types (all statuses)
    changed_lines = []
    for wo in work_orders:
        ctx = contexts.setdefault(wo.station_id, _Context(wo.station_id))
        for line in wo.lines.all():
            wt = ctx.matcher.match(line.name)
            new_id = wt.id if wt else None
            if line.work_type_id != new_id:
                line.work_type = wt
                changed_lines.append(line)
    WorkOrderLine.objects.bulk_update(changed_lines, ["work_type"], batch_size=1000)
    run.stats["lines_matched"] = len(changed_lines)
    for ctx in contexts.values():
        ctx.load_prices()

    # load vehicle state once
    vehicle_ids = {wo.vehicle_id for wo in work_orders}
    states: dict[int, _VehicleState] = {vid: _VehicleState() for vid in vehicle_ids}
    for rec in (
        Recommendation.objects.filter(vehicle_id__in=vehicle_ids)
        .select_related("work_type")
        .annotate(src_visit=_visit_date_expr("source_work_order__"))
    ):
        states[rec.vehicle_id].recs.append(rec)
    executed_rows = (
        WorkOrderLine.objects.filter(
            work_order__vehicle_id__in=vehicle_ids,
            work_order__status=WorkOrder.Status.CLOSED,
            executed=True,
            kind=WorkOrderLine.Kind.LABOR,
            work_type__isnull=False,
        )
        .annotate(visit=_visit_date_expr("work_order__"))
        .values_list("work_order__vehicle_id", "visit", "work_order_id", "work_type_id")
    )
    for vid, visit, wo_id, wt_id in executed_rows:
        states[vid].executed.append((visit, wo_id, wt_id))
    for state in states.values():
        state.executed.sort()

    # pass 2: rules, chronologically per vehicle
    work_orders.sort(key=lambda w: (w.vehicle_id, w.visit_date, w.id))
    for wo in work_orders:
        _process(wo, contexts[wo.station_id], states[wo.vehicle_id], run)

    # write
    now = timezone.now()
    if run.deleted:
        Recommendation.objects.filter(id__in=run.deleted).delete()
    for rec in run.dirty.values():
        rec.updated_at = now
    Recommendation.objects.bulk_update(list(run.dirty.values()), _UPDATE_FIELDS, batch_size=500)
    Recommendation.objects.bulk_create(run.new, batch_size=500)
    return dict(run.stats)


def _process(wo: WorkOrder, ctx: _Context, state: _VehicleState, run: _Run) -> None:
    own_rule_recs = [
        r
        for r in state.recs
        if r.source_work_order_id == wo.id
        and r.source in Recommendation.RULE_SOURCES
        and r.status in _ACTIVE_UNREVIEWED
    ]
    if wo.status != WorkOrder.Status.CLOSED:
        for rec in own_rule_recs:
            run.delete(state, rec)
        return

    lines = [ln for ln in wo.lines.all() if ln.kind == WorkOrderLine.Kind.LABOR]
    executed_types: dict[int, WorkType] = {
        ln.work_type_id: ln.work_type for ln in lines if ln.executed and ln.work_type_id
    }
    visit = wo.visit_date

    # 6.4: executed work closes earlier open recommendations of the same type
    for rec in state.recs:
        if (
            rec.work_type_id in executed_types
            and rec.status in Recommendation.ACTIVE_STATUSES
            and rec.source_work_order_id != wo.id
            and rec.src_visit <= visit
        ):
            rec.status = Recommendation.Status.DONE
            rec.closed_by_work_order = wo
            run.touch(rec)
            run.stats["closed"] += 1

    produced: list[Recommendation] = []

    # 6.1: regular maintenance → next due by interval
    for wt in executed_types.values():
        if not wt.is_regular:
            continue
        due_km = (
            wo.mileage + wt.default_interval_km if wo.mileage and wt.default_interval_km else None
        )
        due_date = (
            add_months(visit, wt.default_interval_months) if wt.default_interval_months else None
        )
        if due_km is None and due_date is None:
            continue
        line = next(ln for ln in lines if ln.executed and ln.work_type_id == wt.id)
        rec = _upsert(
            wo, wt, None, state, run,
            source=Recommendation.Source.REGULAR_INTERVAL,
            source_text=line.name,
            due_km=due_km,
            due_date=due_date,
            urgency=wt.urgency_default,
            est_price=ctx.avg_price.get(wt.id),
            status=Recommendation.Status.OPEN,
            confidence=1.0,
        )  # fmt: skip
        if rec is not None:
            produced.append(rec)

    # 6.1: declined lines → "as soon as possible"
    seen: set = set()
    for line in lines:
        if line.executed:
            continue
        wt = line.work_type
        if wt is not None and wt.id in executed_types:
            continue  # the same work was done in this visit
        key = wt.id if wt else normalize_name(line.name)
        if key in seen:
            continue
        seen.add(key)
        rec = _upsert(
            wo, wt, line.name, state, run,
            source=Recommendation.Source.DECLINED_LINE,
            source_text=line.name,
            due_km=None,
            due_date=visit + timedelta(days=DECLINED_DUE_DAYS),
            urgency=wt.urgency_default if wt else "wear",
            est_price=line.amount or (ctx.avg_price.get(wt.id) if wt else None),
            status=Recommendation.Status.OPEN if wt else Recommendation.Status.NEEDS_REVIEW,
            confidence=1.0 if wt else UNMATCHED_CONFIDENCE,
        )  # fmt: skip
        if rec is not None:
            produced.append(rec)

    # recommendations this WO produced before but no longer does (WO was edited)
    for rec in own_rule_recs:
        if (
            rec in state.recs
            and rec.status in _ACTIVE_UNREVIEWED
            and not any(rec is p for p in produced)
        ):
            run.delete(state, rec)


def _upsert(wo, wt, raw_name, state: _VehicleState, run: _Run, **fields):
    raw = (raw_name or "")[:512]
    if wt is not None:
        same = [r for r in state.recs if r.work_type_id == wt.id]
    else:
        same = [r for r in state.recs if r.work_type_id is None and r.work_type_raw == raw]

    # a manager already rejected this exact recommendation — don't resurrect it
    if any(
        r.source_work_order_id == wo.id and r.status == Recommendation.Status.REJECTED for r in same
    ):
        run.stats["kept_rejected"] += 1
        return None

    active = [r for r in same if r.status in Recommendation.ACTIVE_STATUSES]
    existing = max(active, key=lambda r: (r.src_visit, r.pk or 10**12), default=None)
    if existing is not None and existing.src_visit > wo.visit_date:
        run.stats["skipped_older"] += 1  # newer information already exists (backfill import)
        return None

    fields["extractor_version"] = RULES_VERSION
    status = fields.pop("status")
    if existing is not None:
        for name, value in fields.items():
            if name == "est_price" and value is None:
                continue
            setattr(existing, name, value)
        existing.source_work_order = wo
        existing.src_visit = wo.visit_date
        if existing.status == Recommendation.Status.NEEDS_REVIEW:
            existing.status = status
        run.touch(existing)
        run.stats["updated"] += 1
        rec = existing
    else:
        rec = Recommendation(
            station_id=wo.station_id,
            vehicle_id=wo.vehicle_id,
            source_work_order=wo,
            work_type=wt,
            work_type_raw=raw or (wt.name if wt else ""),
            status=status,
            **fields,
        )
        rec.src_visit = wo.visit_date
        state.recs.append(rec)
        run.new.append(rec)
        run.stats["created"] += 1

    if wt is not None:
        _close_if_done_later(rec, wo, state, run)
    return rec


def _close_if_done_later(rec, wo, state: _VehicleState, run: _Run) -> None:
    """Backfill: the work may already be done in a later visit that was imported earlier."""
    for visit, wo_id, wt_id in state.executed:  # sorted by visit
        if wt_id == rec.work_type_id and wo_id != wo.id and visit > wo.visit_date:
            rec.status = Recommendation.Status.DONE
            rec.closed_by_work_order_id = wo_id
            run.touch(rec)
            run.stats["closed"] += 1
            return


def expire_recommendations(station_ids: Iterable[int] | None = None) -> int:
    """Spec 6.4: recommendations past TTL become ``expired``.

    The TTL is counted from the expected due date (or the source visit when no due date),
    so long-interval maintenance (e.g. a 4-year timing belt) does not expire before it is due.
    """
    from apps.accounts.models import ServiceStation

    today = timezone.localdate()
    stations = ServiceStation.objects.all()
    if station_ids is not None:
        stations = stations.filter(id__in=list(station_ids))
    expired = 0
    for station in stations:
        ttl = timedelta(days=int(station.conf["recommendation_ttl_days"]))
        qs = (
            Recommendation.objects.filter(station=station)
            .filter(status__in=(Recommendation.Status.OPEN, Recommendation.Status.NEEDS_REVIEW))
            .annotate(src_visit=_visit_date_expr("source_work_order__"))
            .filter(Q(src_visit__lt=today - ttl))
            .select_related("vehicle__client__station")
        )
        to_expire = []
        for rec in qs.iterator(chunk_size=500):
            anchor = rec.expected_date() or rec.src_visit
            if anchor + ttl < today:
                to_expire.append(rec.id)
        expired += Recommendation.objects.filter(id__in=to_expire).update(
            status=Recommendation.Status.EXPIRED, updated_at=timezone.now()
        )
    return expired
