from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel
from apps.common.tenancy import TenantQuerySet


class Channel(models.TextChoices):
    SMS = "sms", "SMS"
    MAX = "max", "MAX"
    MANUAL_CALL = "manual_call", "Звонок"


class Intent(models.TextChoices):
    BOOK = "book", "Хочет записаться"
    PRICE_QUESTION = "price_question", "Вопрос о цене"
    ALREADY_DONE = "already_done", "Уже сделал"
    SOLD_CAR = "sold_car", "Продал машину"
    UNSUBSCRIBE = "unsubscribe", "Отписка"
    NOT_RELEVANT = "not_relevant", "Неактуально"
    OTHER = "other", "Другое"


class MessageQuerySet(TenantQuerySet):
    station_path = "client__station"


class Message(TimeStampedModel):
    class Direction(models.TextChoices):
        OUT = "out", "Исходящее"
        IN = "in", "Входящее"

    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        SENDING = "sending", "Отправляется"
        SENT = "sent", "Отправлено"
        DELIVERED = "delivered", "Доставлено"
        FAILED = "failed", "Ошибка"
        RECEIVED = "received", "Получено"

    outreach = models.ForeignKey(
        "outreach.Outreach", on_delete=models.CASCADE, null=True, blank=True,
        related_name="messages",
    )  # fmt: skip
    client = models.ForeignKey(
        "customers.Client", on_delete=models.CASCADE, related_name="messages"
    )
    channel = models.CharField(max_length=12, choices=Channel.choices)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    text = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices)
    # set for reminder touches only (1st, 2nd …); replies and service messages have none
    touch_number = models.PositiveSmallIntegerField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    error = models.CharField(max_length=255, blank=True)  # PII-free
    attempts = models.PositiveSmallIntegerField(default=0)
    intent = models.CharField(max_length=16, choices=Intent.choices, blank=True)
    intent_confidence = models.FloatField(null=True, blank=True)
    handled_by_ai = models.BooleanField(default=False)
    needs_attention = models.BooleanField(default=False, db_index=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = MessageQuerySet.as_manager()

    class Meta:
        verbose_name = "сообщение"
        verbose_name_plural = "сообщения"
        ordering = ["created_at", "id"]
        constraints = [
            # spec 11: no double send of the same reminder touch
            models.UniqueConstraint(
                fields=["outreach", "touch_number"],
                condition=Q(direction="out", touch_number__isnull=False),
                name="message_one_send_per_touch",
            ),
            # inbound dedup: providers may redeliver webhooks
            models.UniqueConstraint(
                fields=["channel", "provider_message_id"],
                condition=Q(direction="in") & ~Q(provider_message_id=""),
                name="message_inbound_dedup",
            ),
        ]

    def __str__(self) -> str:
        return f"Message #{self.pk}"


class BookingRequest(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        CONTACTED = "contacted", "Связались"
        BOOKED = "booked", "Записан"
        CANCELLED = "cancelled", "Отменена"

    outreach = models.ForeignKey(
        "outreach.Outreach", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="booking_requests",
    )  # fmt: skip
    client = models.ForeignKey(
        "customers.Client", on_delete=models.CASCADE, related_name="booking_requests"
    )
    preferred_time_text = models.CharField("желаемое время (со слов клиента)", max_length=255,
                                           blank=True)  # fmt: skip
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    objects = MessageQuerySet.as_manager()

    class Meta:
        verbose_name = "заявка на запись"
        verbose_name_plural = "заявки на запись"
        ordering = ["-created_at"]


def _token() -> str:
    return secrets.token_urlsafe(9)  # 12 url-safe chars


class MaxLink(TimeStampedModel):
    """Deep-link token that binds a MAX chat to a client (`?start=<token>`)."""

    client = models.ForeignKey(
        "customers.Client", on_delete=models.CASCADE, related_name="max_links"
    )
    token = models.CharField(max_length=32, unique=True, default=_token)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "ссылка на бота MAX"
        verbose_name_plural = "ссылки на бота MAX"
