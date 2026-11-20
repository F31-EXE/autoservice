from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.common.models import TimeStampedModel
from apps.common.tenancy import TenantQuerySet


class ClientQuerySet(TenantQuerySet):
    station_path = "station"


class Client(TimeStampedModel):
    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, related_name="clients"
    )
    external_id = models.CharField("ID в системе СТО", max_length=64, null=True, blank=True)  # noqa: DJ001 - NULL needed for conditional unique
    full_name = models.CharField("ФИО", max_length=255, blank=True)
    phone = models.CharField("телефон (E.164)", max_length=20, null=True, blank=True)  # noqa: DJ001
    telegram_chat_id = models.BigIntegerField(null=True, blank=True)
    consent_service = models.BooleanField("согласие на сервисные уведомления", default=True)
    consent_marketing = models.BooleanField("согласие на рекламу", default=False)
    unsubscribed_at = models.DateTimeField("отписан", null=True, blank=True)
    notes = models.TextField("заметки", blank=True)

    objects = ClientQuerySet.as_manager()

    class Meta:
        verbose_name = "клиент"
        verbose_name_plural = "клиенты"
        indexes = [models.Index(fields=["phone"])]
        constraints = [
            models.UniqueConstraint(
                fields=["station", "phone"],
                condition=Q(phone__isnull=False),
                name="client_unique_station_phone",
            ),
            models.UniqueConstraint(
                fields=["station", "external_id"],
                condition=Q(phone__isnull=True, external_id__isnull=False),
                name="client_unique_station_external_id_without_phone",
            ),
            models.CheckConstraint(
                condition=Q(phone__isnull=False) | Q(external_id__isnull=False),
                name="client_has_phone_or_external_id",
            ),
        ]

    def __str__(self) -> str:
        return f"Client #{self.pk}"

    @property
    def first_name(self) -> str:
        """Best-effort first name from "Фамилия Имя Отчество" or "Имя"."""
        parts = self.full_name.split()
        if len(parts) >= 2:
            return parts[1]
        return parts[0] if parts else ""

    @property
    def masked_phone(self) -> str:
        if not self.phone:
            return ""
        return f"{self.phone[:4]}***{self.phone[-2:]}"


class VehicleQuerySet(TenantQuerySet):
    station_path = "client__station"


class Vehicle(TimeStampedModel):
    MIN_KM_PER_DAY = 5
    MAX_KM_PER_DAY = 300

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="vehicles")
    vin = models.CharField("VIN", max_length=17, null=True, blank=True)  # noqa: DJ001 - NULL = unknown
    plate = models.CharField("госномер", max_length=16, null=True, blank=True)  # noqa: DJ001
    make = models.CharField("марка", max_length=64, blank=True)
    model = models.CharField("модель", max_length=64, blank=True)
    year = models.PositiveSmallIntegerField("год", null=True, blank=True)
    last_known_mileage = models.PositiveIntegerField("последний пробег", null=True, blank=True)
    last_visit_at = models.DateField("последний визит", null=True, blank=True)
    is_sold = models.BooleanField("продана", default=False)
    km_per_day_estimate = models.FloatField("пробег в день (оценка)", null=True, blank=True)

    objects = VehicleQuerySet.as_manager()

    class Meta:
        verbose_name = "автомобиль"
        verbose_name_plural = "автомобили"
        indexes = [models.Index(fields=["vin"]), models.Index(fields=["plate"])]

    def __str__(self) -> str:
        return f"{self.make} {self.model}".strip() or f"Vehicle #{self.pk}"

    @property
    def make_model(self) -> str:
        return f"{self.make} {self.model}".strip()

    def effective_km_per_day(self) -> float:
        """Spec 7.1: computed estimate, else station default; always clamped to 5..300."""
        value = self.km_per_day_estimate
        if value is None:
            value = float(self.client.station.avg_km_per_day_default)
        return float(min(max(value, self.MIN_KM_PER_DAY), self.MAX_KM_PER_DAY))
