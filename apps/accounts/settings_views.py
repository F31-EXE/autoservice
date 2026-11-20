"""Screen 8 "Настройки СТО": owner edits station data, rules (7.3), templates, users."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.common.models import audit

from .context import get_current_station
from .models import Role, ServiceStation, User
from .settings_forms import NewUserForm, RulesForm, StationForm, TemplatesForm
from .station_settings import merged_settings


def _owner_station(request) -> ServiceStation:
    if not (request.user.is_owner or request.user.is_superadmin):
        raise PermissionDenied
    station = get_current_station(request)
    if station is None:
        raise Http404
    return station


def _preview(station: ServiceStation) -> dict:
    """Render the templates on a made-up client so the owner sees the result and length."""
    from apps.outreach.texts import (
        MAX_TEMPLATE,
        SMS_TEMPLATES,
        _render,
        sms_segments,
    )  # fmt: skip

    conf = station.conf
    context = {
        "client_first_name": "Иван",
        "station": {"sender_name": station.sender_name or station.name, "name": station.name,
                    "phone": station.phone},
        "visit_month": "марте",
        "recommendation_summary": "замена ремня ГРМ",
        "vehicle_make_model": "Lada Vesta",
        "link": station.booking_url or "https://max.ru/bot?start=AbCdEfGhIjKl",
        "est_amount": "6500",
    }  # fmt: skip
    sms = _render(conf.get("sms_template") or SMS_TEMPLATES[0], context)
    max_text = _render(conf.get("message_template") or MAX_TEMPLATE, context)
    segments, encoding = sms_segments(sms)
    return {"sms": sms, "sms_len": len(sms), "sms_segments": segments, "max": max_text}


@login_required
def station_settings(request: HttpRequest) -> HttpResponse:
    station = _owner_station(request)
    conf = station.conf
    station_form = StationForm(instance=station, prefix="st")
    rules_form = RulesForm(initial=RulesForm.initial_from(conf), prefix="rules")
    templates_form = TemplatesForm(
        initial={"sms_template": conf.get("sms_template", ""),
                 "message_template": conf.get("message_template", "")},
        prefix="tpl",
    )  # fmt: skip

    if request.method == "POST":
        section = request.POST.get("section")
        if section == "station":
            station_form = StationForm(request.POST, instance=station, prefix="st")
            if station_form.is_valid():
                station_form.save()
                audit(request.user, "settings.station", station, station=station,
                      fields=sorted(station_form.changed_data))  # fmt: skip
                messages.success(request, "Данные точки сохранены.")
                return redirect("accounts:settings")
        elif section == "rules":
            rules_form = RulesForm(request.POST, prefix="rules")
            if rules_form.is_valid():
                new = rules_form.to_settings()
                changed = sorted(k for k, v in new.items() if conf.get(k) != v)
                station.settings = {**station.settings, **new}
                station.save(update_fields=["settings", "updated_at"])
                audit(request.user, "settings.rules", station, station=station, changed={
                    k: [conf.get(k), new[k]] for k in changed})  # fmt: skip
                messages.success(request, "Правила рассылки сохранены.")
                return redirect("accounts:settings")
        elif section == "templates":
            templates_form = TemplatesForm(request.POST, prefix="tpl")
            if templates_form.is_valid():
                station.settings = {**station.settings, **templates_form.cleaned_data}
                station.save(update_fields=["settings", "updated_at"])
                audit(request.user, "settings.templates", station, station=station,
                      fields=sorted(templates_form.changed_data))  # fmt: skip
                messages.success(request, "Шаблоны сохранены.")
                return redirect("accounts:settings")
        else:
            return HttpResponse(status=400)

    users = (
        User.objects.for_user(request.user)
        .filter(organization=station.organization)
        .prefetch_related("stations")
        .order_by("-is_active", "role", "username")
    )
    return render(request, "settings/station.html", {
        "station": station,
        "station_form": station_form,
        "rules_form": rules_form,
        "templates_form": templates_form,
        "preview": _preview(station),
        "users": users,
        "defaults": merged_settings({}),
    })  # fmt: skip


@login_required
def user_create(request: HttpRequest) -> HttpResponse:
    station = _owner_station(request)
    org_stations = ServiceStation.objects.for_user(request.user).filter(
        organization=station.organization
    )
    form = NewUserForm(request.POST or None, stations=org_stations, initial={"stations": [station]})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(
                username=d["username"], email=d["email"], password=d["password"],
                first_name=d["first_name"], organization=station.organization, role=Role.MANAGER,
            )  # fmt: skip
            user.stations.set(d["stations"])
            audit(request.user, "user.create", user, station=station,
                  stations=[s.pk for s in d["stations"]])  # fmt: skip
        messages.success(request, f"Пользователь {user.username} создан.")
        return redirect("accounts:settings")
    return render(request, "settings/user_form.html", {"form": form, "station": station})


@login_required
@require_POST
def user_update(request: HttpRequest, pk: int) -> HttpResponse:
    station = _owner_station(request)
    user = get_object_or_404(
        User.objects.for_user(request.user).filter(organization=station.organization), pk=pk
    )
    if user.pk == request.user.pk or user.is_owner or user.is_superadmin:
        messages.error(request, "Этого пользователя здесь изменить нельзя.")
        return redirect("accounts:settings")
    action = request.POST.get("action")
    if action in ("deactivate", "activate"):
        user.is_active = action == "activate"
        user.save(update_fields=["is_active"])
    elif action == "stations":
        allowed = ServiceStation.objects.for_user(request.user).filter(
            organization=station.organization, pk__in=request.POST.getlist("stations")
        )
        user.stations.set(allowed)
    else:
        return HttpResponse(status=400)
    audit(request.user, f"user.{action}", user, station=station)
    messages.success(request, "Сохранено.")
    return redirect("accounts:settings")
