from __future__ import annotations

import csv
from datetime import date

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.accounts.context import get_current_station
from apps.accounts.models import ServiceStation

from .metrics import build_report


def _check_access(user) -> None:
    # spec 2: reports are for the owner (and the service owner)
    if not (user.is_owner or user.is_superadmin):
        raise PermissionDenied


def _params(request: HttpRequest):
    stations = ServiceStation.objects.for_user(request.user).order_by("name")
    current = get_current_station(request)
    today = current.local_today() if current else date.today()
    choice = request.GET.get("station", "all")
    if choice != "all" and choice.isdigit() and stations.filter(pk=int(choice)).exists():
        selected = [int(choice)]
    else:
        choice = "all"
        selected = list(stations.values_list("id", flat=True))
    try:
        date_from = date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        date_from = today.replace(day=1)
    try:
        date_to = date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        date_to = today
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return stations, choice, selected, date_from, date_to, today


@login_required
def results(request: HttpRequest) -> HttpResponse:
    _check_access(request.user)
    stations, choice, selected, date_from, date_to, today = _params(request)
    report = build_report(selected, date_from, date_to, today)
    return render(
        request,
        "reports/results.html",
        {"report": report, "stations": stations, "choice": choice,
         "query": request.GET.urlencode()},
    )  # fmt: skip


@login_required
def results_csv(request: HttpRequest) -> HttpResponse:
    _check_access(request.user)
    stations, choice, selected, date_from, date_to, today = _params(request)
    report = build_report(selected, date_from, date_to, today)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="vozvrat-report-{date_from}-{date_to}.csv"'
    )
    response.write("﻿")  # BOM: Excel opens UTF-8 correctly
    writer = csv.writer(response, delimiter=";")
    c = report.comparison
    writer.writerow(["Раздел", "Показатель", "Значение"])
    writer.writerow(["Период", "С", date_from.isoformat()])
    writer.writerow(["Период", "По", date_to.isoformat()])
    writer.writerow(["Период", "Точки", "все" if choice == "all" else
                     stations.get(pk=selected[0]).name])  # fmt: skip
    for label, value in report.funnel.steps():
        writer.writerow(["Воронка", label, value])
    rows = [
        ("Отправлено", c.sent),
        ("Приехали из рассылки", c.sent_visited),
        ("Доля приехавших, рассылка, %", f"{c.sent_rate * 100:.1f}"),
        ("Контрольная группа", c.control),
        ("Приехали из контрольной", c.control_visited),
        ("Доля приехавших, контроль, %", f"{c.control_rate * 100:.1f}"),
        ("Разница, п.п.", f"{c.uplift_pp:.1f}"),
        ("Средний чек приехавших, ₽", c.avg_check),
        ("Дополнительная выручка (оценка), ₽", c.extra_revenue),
        ("Статистически значимо (95%)", "да" if c.significant else "нет"),
        ("Окно атрибуции ещё открыто", c.open_windows),
    ]
    for label, value in rows:
        writer.writerow(["Сравнение с контролем", label, value])
    for row in report.money:
        writer.writerow(["Деньги на столе", f"{row.title} (рекомендаций: {row.count})",
                         row.amount])  # fmt: skip
    writer.writerow(["Деньги на столе", "Итого", report.money_total])
    return response
