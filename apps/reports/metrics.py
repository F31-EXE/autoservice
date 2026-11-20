"""Owner report (spec 9.2). Pure aggregation over the DB, deterministic."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from apps.messaging.models import BookingRequest, Message
from apps.outreach.models import Outreach
from apps.recommendations.models import Recommendation

MIN_GROUP = 30  # below this, the comparison is shown with a "too few data" warning


@dataclass
class Funnel:
    found: int = 0
    to_send: int = 0
    sent: int = 0
    replied: int = 0
    bookings: int = 0
    visited: int = 0
    revenue: Decimal = Decimal(0)

    def steps(self) -> list[tuple[str, int | Decimal]]:
        return [
            ("Найдено рекомендаций", self.found),
            ("К отправке", self.to_send),
            ("Отправлено", self.sent),
            ("Ответили", self.replied),
            ("Заявки на запись", self.bookings),
            ("Приехали", self.visited),
            ("Выручка, ₽", self.revenue),
        ]


@dataclass
class Comparison:
    sent: int = 0
    sent_visited: int = 0
    control: int = 0
    control_visited: int = 0
    avg_check: Decimal = Decimal(0)
    open_windows: int = 0  # reminders whose attribution window is not over yet

    @property
    def sent_rate(self) -> float:
        return self.sent_visited / self.sent if self.sent else 0.0

    @property
    def control_rate(self) -> float:
        return self.control_visited / self.control if self.control else 0.0

    @property
    def uplift_pp(self) -> float:
        return (self.sent_rate - self.control_rate) * 100

    @property
    def extra_revenue(self) -> Decimal:
        """(share in mailing − share in control) × sent × average check of visitors."""
        if not self.control:
            return Decimal(0)
        value = Decimal(str(self.sent_rate - self.control_rate)) * self.sent * self.avg_check
        return value.quantize(Decimal("1"))

    @property
    def ci95_pp(self) -> tuple[float, float] | None:
        """95% confidence interval of the difference in shares (normal approximation)."""
        if not self.sent or not self.control:
            return None
        p1, p2 = self.sent_rate, self.control_rate
        se = math.sqrt(p1 * (1 - p1) / self.sent + p2 * (1 - p2) / self.control)
        diff = p1 - p2
        return ((diff - 1.96 * se) * 100, (diff + 1.96 * se) * 100)

    @property
    def significant(self) -> bool:
        ci = self.ci95_pp
        return bool(ci) and (ci[0] > 0 or ci[1] < 0)

    @property
    def enough_data(self) -> bool:
        return self.sent >= MIN_GROUP and self.control >= MIN_GROUP


@dataclass
class MoneyRow:
    title: str
    count: int
    priced: int
    amount: Decimal


@dataclass
class Report:
    date_from: date
    date_to: date
    funnel: Funnel
    comparison: Comparison
    money: list[MoneyRow] = field(default_factory=list)

    @property
    def money_total(self) -> Decimal:
        return sum((r.amount for r in self.money), Decimal(0))

    @property
    def money_count(self) -> int:
        return sum(r.count for r in self.money)


def build_report(station_ids: list[int], date_from: date, date_to: date, today: date) -> Report:
    period = Q(planned_for__gte=date_from, planned_for__lte=date_to)
    outreaches = Outreach.objects.filter(period, station_id__in=station_ids)
    mailing = outreaches.filter(is_control_group=False).exclude(
        status__in=(Outreach.Status.SKIPPED,)
    )
    sent = mailing.filter(first_touch_at__isnull=False)
    sent_ids = list(sent.values_list("id", flat=True))
    replied = (
        Message.objects.filter(outreach_id__in=sent_ids, direction=Message.Direction.IN)
        .values("outreach_id").distinct().count()
    )  # fmt: skip
    visited = sent.filter(status=Outreach.Status.VISITED)
    funnel = Funnel(
        found=Recommendation.objects.filter(station_id__in=station_ids)
        .annotate(visit=Coalesce("source_work_order__closed_at", "source_work_order__opened_at"))
        .filter(visit__gte=date_from, visit__lte=date_to)
        .count(),
        to_send=mailing.count(),
        sent=len(sent_ids),
        replied=replied,
        bookings=BookingRequest.objects.filter(outreach_id__in=sent_ids)
        .values("outreach_id").distinct().count(),
        visited=visited.count(),
        revenue=visited.aggregate(s=Sum("attributed_amount"))["s"] or Decimal(0),
    )  # fmt: skip

    control = outreaches.filter(is_control_group=True)
    avg = visited.aggregate(s=Sum("attributed_amount"), n=Count("id"))
    comparison = Comparison(
        sent=funnel.sent,
        sent_visited=funnel.visited,
        control=control.count(),
        control_visited=control.filter(status=Outreach.Status.VISITED).count(),
        avg_check=(avg["s"] / avg["n"]).quantize(Decimal("1")) if avg["n"] else Decimal(0),
        open_windows=_open_windows(outreaches, today),
    )
    return Report(date_from, date_to, funnel, comparison, money_on_table(station_ids))


def _open_windows(outreaches, today: date) -> int:
    count = 0
    for o in outreaches.filter(
        status__in=(Outreach.Status.SENT, Outreach.Status.REPLIED, Outreach.Status.BOOKED,
                    Outreach.Status.PLANNED, Outreach.Status.NO_RESPONSE)
    ).select_related("station"):  # fmt: skip
        if o.is_control_group:
            start = o.planned_for
        elif o.first_touch_at:
            start = o.first_touch_at.astimezone(o.station.tz).date()
        else:
            continue
        if start + timedelta(days=int(o.station.conf["attribution_window_days"])) >= today:
            count += 1
    return count


def money_on_table(station_ids: list[int]) -> list[MoneyRow]:
    """Open recommendations and their estimated value, by work type (current state)."""
    rows = (
        Recommendation.objects.filter(
            station_id__in=station_ids, status__in=Recommendation.ACTIVE_STATUSES
        )
        .values("work_type__name", "work_type_raw", "work_type_id")
        .annotate(
            count=Count("id"),
            priced=Count("id", filter=Q(est_price__isnull=False)),
            amount=Coalesce(Sum("est_price"), Decimal(0)),
        )
    )
    merged: dict[str, MoneyRow] = {}
    for row in rows:
        title = row["work_type__name"] or "Нет в справочнике"
        item = merged.setdefault(title, MoneyRow(title, 0, 0, Decimal(0)))
        item.count += row["count"]
        item.priced += row["priced"]
        item.amount += row["amount"]
    return sorted(merged.values(), key=lambda r: (-r.amount, -r.count, r.title))
