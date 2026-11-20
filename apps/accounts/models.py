from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from apps.common.models import TimeStampedModel
from apps.common.tenancy import TenantQuerySet

from .station_settings import merged_settings


class OrganizationQuerySet(models.QuerySet):
    def for_user(self, user) -> OrganizationQuerySet:
        if user is None or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.is_superadmin:
            return self.all()
        return self.filter(pk=user.organization_id)


class Organization(TimeStampedModel):
    name = models.CharField("название", max_length=255)
    is_active = models.BooleanField("активна", default=True)

    objects = OrganizationQuerySet.as_manager()

    class Meta:
        verbose_name = "организация"
        verbose_name_plural = "организации"

    def __str__(self) -> str:
        return self.name


class ServiceStationQuerySet(TenantQuerySet):
    station_path = ""


class ServiceStation(TimeStampedModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="stations", verbose_name="организация"
    )
    name = models.CharField("название", max_length=255)
    city = models.CharField("город", max_length=255, blank=True)
    timezone = models.CharField("часовой пояс", max_length=64, default="Asia/Yekaterinburg")
    sender_name = models.CharField("подпись в сообщениях", max_length=64, blank=True)
    phone = models.CharField("телефон", max_length=32, blank=True)
    booking_url = models.URLField("ссылка на запись", blank=True)
    avg_km_per_day_default = models.PositiveIntegerField("средний пробег в день, км", default=50)
    settings = models.JSONField("настройки", default=dict, blank=True)

    objects = ServiceStationQuerySet.as_manager()

    class Meta:
        verbose_name = "СТО"
        verbose_name_plural = "СТО"
        ordering = ["organization_id", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def conf(self) -> dict[str, Any]:
        """Station settings merged with defaults from spec 7.3."""
        return merged_settings(self.settings)


class Role(models.TextChoices):
    SUPERADMIN = "superadmin", "Суперадмин"
    OWNER = "owner", "Владелец"
    MANAGER = "manager", "Администратор СТО"


class UserQuerySet(models.QuerySet):
    def for_user(self, user) -> UserQuerySet:
        if user is None or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.is_superadmin:
            return self.all()
        return self.filter(organization_id=user.organization_id)


class VozvratUserManager(UserManager.from_queryset(UserQuerySet)):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.SUPERADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="организация",
    )
    role = models.CharField("роль", max_length=16, choices=Role.choices, default=Role.MANAGER)
    stations = models.ManyToManyField(
        ServiceStation, blank=True, related_name="users", verbose_name="доступ к точкам"
    )

    objects = VozvratUserManager()

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"

    @property
    def is_superadmin(self) -> bool:
        return self.role == Role.SUPERADMIN or self.is_superuser

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def is_manager(self) -> bool:
        return self.role == Role.MANAGER

    def save(self, *args, **kwargs):
        # Django admin is for superadmin only.
        self.is_staff = self.is_superadmin
        super().save(*args, **kwargs)
