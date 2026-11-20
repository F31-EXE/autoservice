"""Reminder planning and the daily queue (spec 7). Deterministic code only, no LLM."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import ServiceStation
from apps.recommendations.models import Recommendation

from .models import Outreach, QueueRun

logger = logging.getLogger(__name__)


class SkipReason:
    UNSUBSCRIBED = "Клиент отписался от сообщений"
    NO_CONSENT = "Нет согласия на сервисные уведомления"
    SOLD = "Автомобиль продан"
    INACTIVE = "Нет визитов дольше {days} дней"
    NO_CONTACT = "Нет телефона и MAX"
    WORK_DONE = "Работы уже выполнены или рекомендации закрыты"


# ---------------------------------------------------------------- 7.2 planned_for


def target_date(rec: Recommendation) -> date | None:
    """min(due_date, date_by_km) over non-empty values (spec 7.2)."""
    return rec.expected_date()


def compute_planned_for(rec: Recommendation, remind_before_days: int) -> date | None:
    target = target_date(rec)
    return target - timedelta(days=remind_before_days) if target else None


def recompute_planned_for(station: ServiceStation, vehicle_ids: Iterable[int] | None = None) -> int:
    """Store planned_for for active recommendations of a station. Returns rows changed.

    planned_for is the raw computed date; one in the past means "send on the nearest send
    day" and is handled by the queue (``planned_for <= today + 1``).
    """
    before = int(station.conf["remind_before_days"])
    qs = Recommendation.objects.filter(
        station=station, status__in=Recommendation.ACTIVE_STATUSES
    ).select_related("vehicle__client__station")
    if vehicle_ids is not None:
        qs = qs.filter(vehicle_id__in=list(vehicle_ids))
    changed = []
    for rec in qs:
        value = compute_planned_for(rec, before)
        if value != rec.planned_for:
            rec.planned_for = value
            changed.append(rec)
    Recommendation.objects.bulk_update(changed, ["planned_for"], batch_size=500)
    return len(changed)


# ---------------------------------------------------------------- 7.4 control group


def in_control_group(outreach_id: int, share: float) -> bool:
    """Deterministic assignment by a hash of the outreach id (spec 7.4)."""
    digest = hashlib.sha256(f"outreach:{outreach_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    return bucket < round(float(share) * 10_000)


# ---------------------------------------------------------------- 7.4 skip rules


def skip_reason_for(vehicle, client, station: ServiceStation, today: date) -> str | None:
    conf = station.conf
    if client.unsubscribed_at:
        return SkipReason.UNSUBSCRIBED
    if not client.consent_service:
        return SkipReason.NO_CONSENT
    if vehicle.is_sold:
        return SkipReason.SOLD
    inactive_days = int(conf["inactive_vehicle_days"])
    if vehicle.last_visit_at and (today - vehicle.last_visit_at).days > inactive_days:
        return SkipReason.INACTIVE.format(days=inactive_days)
    if not client.phone and not client.max_user_id:
        return SkipReason.NO_CONTACT
    return None


# ---------------------------------------------------------------- housekeeping


def release_recommendations(outreach: Outreach) -> None:
    outreach.recommendations.filter(status=Recommendation.Status.SCHEDULED).update(
        status=Recommendation.Status.OPEN, updated_at=timezone.now()
    )


def cancel_obsolete(station: ServiceStation) -> int:
    """Cancel not-yet-sent reminders whose recommendations were all closed meanwhile.

    Control-group reminders are never cancelled: they are an observation, and a visit that
    closed their recommendations is exactly what attribution must count (otherwise the
    control group would lose its visits and the uplift would be overstated).
    """
    cancelled = 0
    pending = Outreach.objects.filter(
        station=station,
        is_control_group=False,
        status__in=(Outreach.Status.PLANNED, Outreach.Status.AWAITING_APPROVAL,
                    Outreach.Status.APPROVED),
    ).prefetch_related("recommendations")  # fmt: skip
    for outreach in pending:
        recs = list(outreach.recommendations.all())
        if recs and not any(r.status in Recommendation.ACTIVE_STATUSES for r in recs):
            outreach.status = Outreach.Status.CANCELLED
            outreach.skip_reason = SkipReason.WORK_DONE
            outreach.save(update_fields=["status", "skip_reason", "updated_at"])
            cancelled += 1
    return cancelled


def close_finished_control(station: ServiceStation, today: date) -> int:
    """Control reminders are never sent; once their observation window is over they end."""
    window = int(station.conf["attribution_window_days"])
    finished = Outreach.objects.filter(
        station=station,
        is_control_group=True,
        status=Outreach.Status.PLANNED,
        planned_for__lt=today - timedelta(days=window),
    )
    count = 0
    for outreach in finished:
        outreach.status = Outreach.Status.NO_RESPONSE
        outreach.save(update_fields=["status", "updated_at"])
        release_recommendations(outreach)
        count += 1
    return count


def schedule_next_touches(station: ServiceStation, now=None) -> dict[str, int]:
    """Spec 8.5: no answer after N days → second touch (again via approval); after the
    limit → no_response."""
    now = now or timezone.now()
    conf = station.conf
    waited = now - timedelta(days=int(conf["second_touch_after_days"]))
    stats = {"second_touch": 0, "no_response": 0}
    for outreach in Outreach.objects.filter(
        station=station, status=Outreach.Status.SENT, last_touch_at__lte=waited
    ):
        if outreach.touch_count < int(conf["max_touches"]):
            outreach.status = Outreach.Status.AWAITING_APPROVAL
            outreach.custom_text = ""  # the second touch uses its own template
            outreach.approved_by, outreach.approved_at = None, None
            outreach.save(update_fields=["status", "custom_text", "approved_by", "approved_at",
                                         "updated_at"])  # fmt: skip
            stats["second_touch"] += 1
        else:
            outreach.status = Outreach.Status.NO_RESPONSE
            outreach.save(update_fields=["status", "updated_at"])
            release_recommendations(outreach)
            stats["no_response"] += 1
    return stats


# ---------------------------------------------------------------- 7.4 queue


@transaction.atomic
def build_queue(station: ServiceStation, today: date | None = None) -> dict[str, int]:
    """Create today's reminders for a station. Safe to run many times a day."""
    today = today or station.local_today()
    conf = station.conf
    stats: dict[str, int] = defaultdict(int)
    stats["planned_updated"] = recompute_planned_for(station)
    stats["obsolete_cancelled"] = cancel_obsolete(station)
    stats["control_closed"] = close_finished_control(station, today)
    stats.update(schedule_next_touches(station))

    merge_until = today + timedelta(days=int(conf["merge_window_days"]))
    due_vehicle_ids = set(
        Recommendation.objects.filter(
            station=station,
            status=Recommendation.Status.OPEN,
            planned_for__isnull=False,
            planned_for__lte=today + timedelta(days=1),
        ).values_list("vehicle_id", flat=True)
    )
    if not due_vehicle_ids:
        return dict(stats)

    busy = set(
        Outreach.objects.filter(vehicle_id__in=due_vehicle_ids)
        .active()
        .values_list("vehicle_id", flat=True)
    )
    cooldown_since = timezone.now() - timedelta(days=int(conf["skip_cooldown_days"]))
    cooling = set(
        Outreach.objects.filter(
            vehicle_id__in=due_vehicle_ids,
            status__in=Outreach.COOLDOWN_STATUSES,
            updated_at__gte=cooldown_since,
        ).values_list("vehicle_id", flat=True)
    )
    stats["already_active"] = len(due_vehicle_ids & busy)
    stats["cooldown"] = len((due_vehicle_ids - busy) & cooling)

    # all open recommendations of these vehicles within the merge window → one reminder
    recs_by_vehicle: dict[int, list[Recommendation]] = defaultdict(list)
    for rec in (
        Recommendation.objects.filter(
            station=station,
            vehicle_id__in=due_vehicle_ids - busy - cooling,
            status=Recommendation.Status.OPEN,
            planned_for__isnull=False,
            planned_for__lte=merge_until,
        )
        .select_related("vehicle__client")
        .order_by("planned_for", "id")
    ):
        recs_by_vehicle[rec.vehicle_id].append(rec)

    share = float(conf["control_group_share"])
    for recs in recs_by_vehicle.values():
        vehicle = recs[0].vehicle
        client = vehicle.client
        reason = skip_reason_for(vehicle, client, station, today)
        outreach = Outreach(
            station=station,
            client=client,
            vehicle=vehicle,
            planned_for=today,
            est_amount=sum((r.est_price for r in recs if r.est_price), Decimal(0)) or None,
        )
        if reason:
            outreach.status = Outreach.Status.SKIPPED
            outreach.skip_reason = reason
        try:
            with transaction.atomic():
                outreach.save()
        except IntegrityError:  # a concurrent run created one for this vehicle
            stats["already_active"] += 1
            continue
        outreach.recommendations.set(recs)
        if reason:
            stats["skipped"] += 1
            continue
        if in_control_group(outreach.pk, share):
            outreach.is_control_group = True
            outreach.status = Outreach.Status.PLANNED
            stats["control"] += 1
        else:
            outreach.status = Outreach.Status.AWAITING_APPROVAL
            stats["awaiting_approval"] += 1
        outreach.save(update_fields=["is_control_group", "status", "updated_at"])
        Recommendation.objects.filter(id__in=[r.id for r in recs]).update(
            status=Recommendation.Status.SCHEDULED, updated_at=timezone.now()
        )
    return dict(stats)


def run_daily_queues(hour: int = 7) -> dict[int, dict]:
    """Hourly entry point: build the queue for each station whose local time is past ``hour``
    and that has no queue run today (catches up if the worker was down at 07:00)."""
    results = {}
    for station in ServiceStation.objects.filter(organization__is_active=True):
        now = station.local_now()
        if now.hour < hour:
            continue
        try:
            with transaction.atomic():
                run = QueueRun.objects.create(station=station, date=now.date())
        except IntegrityError:
            continue
        stats = build_queue(station, now.date())
        run.stats = stats
        run.save(update_fields=["stats", "updated_at"])
        results[station.id] = stats
        logger.info("Queue built for station %s: %s", station.id, stats)
    return results
