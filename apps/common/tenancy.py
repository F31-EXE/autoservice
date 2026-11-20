"""Tenant isolation at the QuerySet level.

Every business model gets a QuerySet subclass of ``TenantQuerySet`` with ``station_path``
pointing at its ``ServiceStation`` FK (``"station"``, ``"vehicle__client__station"`` ...).
Views must always start from ``Model.objects.for_user(request.user)``; the unscoped
``objects.all()`` is reserved for background tasks and Django admin (superadmin only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from apps.accounts.models import User


class TenantQuerySet(models.QuerySet):
    #: lookup path from this model to ServiceStation; "" means the model *is* ServiceStation
    station_path: str = "station"

    def _station_lookup(self, suffix: str) -> str:
        return f"{self.station_path}__{suffix}" if self.station_path else suffix

    def for_user(self, user: User | None) -> TenantQuerySet:
        if user is None or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.is_superadmin:
            return self.all()
        if user.organization_id is None:
            return self.none()
        qs = self.filter(**{self._station_lookup("organization_id"): user.organization_id})
        if user.is_owner:
            return qs
        station_ids = list(user.stations.values_list("id", flat=True))
        return qs.filter(**{self._station_lookup("id__in"): station_ids})

    def for_station(self, station) -> TenantQuerySet:
        return self.filter(**{self._station_lookup("id"): getattr(station, "pk", station)})
