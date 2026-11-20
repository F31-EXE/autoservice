from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel
from apps.common.tenancy import TenantQuerySet


class SourceSystem(models.TextChoices):
    AUTODEALER = "autodealer", "АвтоДилер"
    ALFA_AUTO = "alfa_auto", "1С Альфа-Авто"
    YCLIENTS = "yclients", "YCLIENTS"
    OTHER = "other", "Другое"


class Layout(models.TextChoices):
    FLAT = "flat", "Одна строка — одна позиция ЗН"
    HEADER = "header", "Одна строка — один ЗН, работы в одной ячейке"


class StationScopedQuerySet(TenantQuerySet):
    station_path = "station"


class ImportMappingProfile(TimeStampedModel):
    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, related_name="mapping_profiles"
    )
    name = models.CharField("название", max_length=128)
    source_system = models.CharField(max_length=16, choices=SourceSystem.choices)
    layout = models.CharField("формат", max_length=8, choices=Layout.choices, default=Layout.FLAT)
    column_map = models.JSONField("сопоставление колонок", default=dict)
    date_format = models.CharField("формат даты", max_length=32, default="%d.%m.%Y")
    sheet_name = models.CharField("лист", max_length=128, blank=True)

    objects = StationScopedQuerySet.as_manager()

    class Meta:
        verbose_name = "профиль маппинга"
        verbose_name_plural = "профили маппинга"
        constraints = [
            models.UniqueConstraint(
                fields=["station", "source_system", "name"], name="mapping_profile_unique_name"
            )
        ]

    def __str__(self) -> str:
        return self.name


class ImportBatch(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        PARSING = "parsing", "Обрабатывается"
        PARSED = "parsed", "Готово"
        FAILED = "failed", "Ошибка"

    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, related_name="import_batches"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    file = models.FileField(upload_to="imports/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    source_system = models.CharField(max_length=16, choices=SourceSystem.choices)
    mapping_profile = models.ForeignKey(
        ImportMappingProfile, on_delete=models.SET_NULL, null=True, blank=True
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    stats = models.JSONField(default=dict, blank=True)
    error_log = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = StationScopedQuerySet.as_manager()

    class Meta:
        verbose_name = "загрузка"
        verbose_name_plural = "загрузки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Import #{self.pk}"

    @property
    def is_running(self) -> bool:
        return self.status in (self.Status.PENDING, self.Status.PARSING) and bool(
            self.mapping_profile_id
        )


class WorkOrder(TimeStampedModel):
    class Status(models.TextChoices):
        CLOSED = "closed", "Закрыт"
        OPEN = "open", "Открыт"
        CANCELLED = "cancelled", "Отменён"

    class ExtractionStatus(models.TextChoices):
        PENDING = "pending", "Ожидает"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Пропущено"

    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, related_name="work_orders"
    )
    vehicle = models.ForeignKey(
        "customers.Vehicle", on_delete=models.CASCADE, related_name="work_orders"
    )
    external_number = models.CharField("номер ЗН", max_length=64)
    opened_at = models.DateField("дата открытия")
    closed_at = models.DateField("дата закрытия", null=True, blank=True)
    mileage = models.PositiveIntegerField("пробег", null=True, blank=True)
    total_amount = models.DecimalField("сумма", max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CLOSED)
    mechanic_comment = models.TextField("комментарий мастера", blank=True)
    recommendations_text = models.TextField("рекомендации", blank=True)
    raw = models.JSONField(default=list, blank=True)
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="work_orders"
    )
    content_hash = models.CharField(max_length=64, blank=True)
    extraction_status = models.CharField(
        max_length=8, choices=ExtractionStatus.choices, default=ExtractionStatus.PENDING
    )

    objects = StationScopedQuerySet.as_manager()

    class Meta:
        verbose_name = "заказ-наряд"
        verbose_name_plural = "заказ-наряды"
        ordering = ["-opened_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["station", "external_number"], name="workorder_unique_station_number"
            )
        ]
        indexes = [models.Index(fields=["vehicle", "opened_at"])]

    def __str__(self) -> str:
        return f"ЗН {self.external_number}"

    @property
    def visit_date(self):
        return self.closed_at or self.opened_at


class WorkOrderLineQuerySet(TenantQuerySet):
    station_path = "work_order__station"


class WorkOrderLine(TimeStampedModel):
    class Kind(models.TextChoices):
        LABOR = "labor", "Работа"
        PART = "part", "Запчасть"

    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.LABOR)
    name = models.CharField("наименование", max_length=512)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    executed = models.BooleanField("выполнено", default=True)
    work_type = models.ForeignKey(
        "catalog.WorkType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_order_lines",
        verbose_name="вид работ",
    )

    objects = WorkOrderLineQuerySet.as_manager()

    class Meta:
        verbose_name = "позиция ЗН"
        verbose_name_plural = "позиции ЗН"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.name
