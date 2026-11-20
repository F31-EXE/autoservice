from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.db import models

from apps.catalog.models import Urgency
from apps.common.models import TimeStampedModel
from apps.common.tenancy import TenantQuerySet


class RecommendationQuerySet(TenantQuerySet):
    station_path = "station"

    def active(self) -> RecommendationQuerySet:
        return self.filter(status__in=Recommendation.ACTIVE_STATUSES)


class Recommendation(TimeStampedModel):
    class Source(models.TextChoices):
        REGULAR_INTERVAL = "regular_interval", "Регламент"
        EXPLICIT_FIELD = "explicit_field", "Поле «Рекомендации»"
        FREE_TEXT = "free_text", "Текст мастера"
        DECLINED_LINE = "declined_line", "Отказ клиента"

    class Status(models.TextChoices):
        NEEDS_REVIEW = "needs_review", "На проверке"
        OPEN = "open", "Открыта"
        SCHEDULED = "scheduled", "Запланировано напоминание"
        DONE = "done", "Выполнена"
        DECLINED = "declined", "Клиент отказался"
        EXPIRED = "expired", "Истекла"
        REJECTED = "rejected", "Отклонена"

    ACTIVE_STATUSES = (Status.NEEDS_REVIEW, Status.OPEN, Status.SCHEDULED)
    RULE_SOURCES = (Source.REGULAR_INTERVAL, Source.DECLINED_LINE)

    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, related_name="recommendations"
    )
    vehicle = models.ForeignKey(
        "customers.Vehicle", on_delete=models.CASCADE, related_name="recommendations"
    )
    source_work_order = models.ForeignKey(
        "workorders.WorkOrder", on_delete=models.CASCADE, related_name="recommendations"
    )
    work_type = models.ForeignKey(
        "catalog.WorkType",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recommendations",
    )
    work_type_raw = models.CharField("работа (как в ЗН)", max_length=512, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices)
    source_text = models.TextField("цитата из ЗН", blank=True)
    due_km = models.PositiveIntegerField("срок, км", null=True, blank=True)
    due_date = models.DateField("срок, дата", null=True, blank=True)
    urgency = models.CharField(max_length=8, choices=Urgency.choices, default=Urgency.WEAR)
    est_price = models.DecimalField(
        "оценка стоимости", max_digits=12, decimal_places=2, null=True, blank=True
    )
    confidence = models.FloatField(default=1.0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    review_decision_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    review_decision_at = models.DateTimeField(null=True, blank=True)
    closed_by_work_order = models.ForeignKey(
        "workorders.WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_recommendations",
    )
    extractor_version = models.CharField(max_length=128, blank=True)
    # when to remind (spec 7.2): expected due date minus remind_before_days
    planned_for = models.DateField("напомнить", null=True, blank=True, db_index=True)

    objects = RecommendationQuerySet.as_manager()

    class Meta:
        verbose_name = "рекомендация"
        verbose_name_plural = "рекомендации"
        ordering = ["due_date", "id"]
        indexes = [
            models.Index(fields=["vehicle", "status"]),
            models.Index(fields=["station", "status"]),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def title(self) -> str:
        return self.work_type.name if self.work_type else self.work_type_raw

    def expected_date(self) -> date | None:
        """When the work is expected to be due: min(due_date, date by mileage) — spec 7.2."""
        candidates = []
        if self.due_date:
            candidates.append(self.due_date)
        vehicle = self.vehicle
        if self.due_km and vehicle.last_visit_at and vehicle.last_known_mileage is not None:
            km_left = self.due_km - vehicle.last_known_mileage
            days = km_left / vehicle.effective_km_per_day()
            candidates.append(vehicle.last_visit_at + timedelta(days=round(days)))
        return min(candidates) if candidates else None
