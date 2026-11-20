"""Current-station selection for the web cabinet (stored in session)."""

from __future__ import annotations

from django.http import HttpRequest

from .models import ServiceStation

SESSION_KEY = "current_station_id"


def available_stations(request: HttpRequest):
    return ServiceStation.objects.for_user(request.user).select_related("organization")


def get_current_station(request: HttpRequest) -> ServiceStation | None:
    stations = available_stations(request)
    station_id = request.session.get(SESSION_KEY)
    station = stations.filter(pk=station_id).first() if station_id else None
    if station is None:
        station = stations.first()
        if station is not None:
            request.session[SESSION_KEY] = station.pk
    return station


def station_context(request: HttpRequest) -> dict:
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {
        "user_stations": available_stations(request),
        "current_station": get_current_station(request),
    }
