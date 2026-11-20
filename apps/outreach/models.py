from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum

from apps.common.models import TimeStampedModel
from apps.common.tenancy import TenantQuerySet


class OutreachQuerySet(TenantQuerySet):
    station_path = "station"

    def active(self) -> OutreachQuerySet:
        return self.filter(status__in=Outreach.ACTIVE_STATUSES)


class Outreach(TimeStampedModel):
    """One reminder = one vehicle + one or more recommendations (spec 4, outreach)."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Запланировано"
        AWAITING_APPROVAL = "awaiting_approval", "Ждёт подтверждения"
        APPROVED = "approved", "Одобрено"
        SENT = "sent", "Отправлено"
        REPLIED = "replied", "Ответил"
        BOOKED = "booked", "Записался"
        VISITED = "visited", "Приехал"
        NO_RESPONSE = "no_response", "Нет ответа"
        CANCELLED = "cancelled", "Отменено"
        SKIPPED = "skipped", "Пропущено"

    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        MAX = "max", "MAX"
        MANUAL_CALL = "manual_call", "Звонок"

    ACTIVE_STATUSES = (
        Status.PLANNED, Status.AWAITING_APPROVAL, Status.APPROVED,
        Status.SENT, Status.REPLIED, Status.BOOKED,
    )  # fmt: skip
    COOLDOWN_STATUSES = (Status.SKIPPED, Status.CANCELLED, Status.NO_RESPONSE)

    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, related_name="outreaches"
    )
    client = models.ForeignKey(
        "customers.Client", on_delete=models.CASCADE, related_name="outreaches"
    )
    vehicle = models.ForeignKey(
        "customers.Vehicle", on_delete=models.CASCADE, related_name="outreaches"
    )
    recommendations = models.ManyToManyField(
        "recommendations.Recommendation", related_name="outreaches", blank=True
    )
    planned_for = models.DateField("дата отправки")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    is_control_group = models.BooleanField("контрольная группа", default=False)
    skip_reason = models.TextField("причина пропуска", null=True, blank=True)  # noqa: DJ001
    channel = models.CharField(max_length=12, choices=Channel.choices, blank=True)
    custom_text = models.TextField("текст, изменённый менеджером", blank=True)
    est_amount = models.DecimalField(
        "оценка суммы", max_digits=12, decimal_places=2, null=True, blank=True
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    touch_count = models.PositiveSmallIntegerField(default=0)
    first_touch_at = models.DateTimeField(null=True, blank=True)
    last_touch_at = models.DateTimeField(null=True, blank=True)
    attributed_work_order = models.ForeignKey(
        "workorders.WorkOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attributed_outreaches",
    )
    attributed_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    objects = OutreachQuerySet.as_manager()

    class Meta:
        verbose_name = "напоминание"
        verbose_name_plural = "напоминания"
        ordering = ["planned_for", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(
                    status__in=[
                        "planned",
                        "awaiting_approval",
                        "approved",
                        "sent",
                        "replied",
                        "booked",
                    ]
                ),  # fmt: skip
                name="outreach_one_active_per_vehicle",
            )
        ]
        indexes = [models.Index(fields=["station", "status", "planned_for"])]

    def __str__(self) -> str:
        return f"Outreach #{self.pk}"

    def compute_est_amount(self):
        return self.recommendations.aggregate(s=Sum("est_price"))["s"]

    @property
    def needs_call(self) -> bool:
        threshold = self.station.conf["high_value_call_threshold"]
        return bool(self.est_amount and threshold and self.est_amount > threshold)


class QueueRun(TimeStampedModel):
    """Daily queue build marker (one per station per local day) — makes the job idempotent."""

    station = models.ForeignKey("accounts.ServiceStation", on_delete=models.CASCADE)
    date = models.DateField()
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["station", "date"], name="queue_run_once")]
