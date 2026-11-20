"""Screen 5 "Клиент": search and client card."""

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.context import get_current_station
from apps.common.models import audit
from apps.outreach.planner import release_recommendations
from apps.recommendations.models import Recommendation

from .models import Client, Vehicle


@login_required
def client_list(request: HttpRequest) -> HttpResponse:
    station = get_current_station(request)
    if station is None:
        raise Http404
    qs = Client.objects.for_user(request.user).for_station(station)
    q = request.GET.get("q", "").strip()
    if q:
        digits = re.sub(r"\D", "", q)
        plate = re.sub(r"[\s-]", "", q).upper()
        cond = Q(full_name__icontains=q) | Q(external_id__iexact=q)
        if len(digits) >= 4:
            cond |= Q(phone__contains=digits[-10:])
        if len(plate) >= 4:
            cond |= Q(vehicles__plate__icontains=plate) | Q(vehicles__vin__icontains=plate)
        qs = qs.filter(cond).distinct()
    qs = qs.annotate(last_visit=Max("vehicles__last_visit_at")).order_by("-last_visit", "full_name")
    page = Paginator(qs.prefetch_related("vehicles"), 30).get_page(request.GET.get("page"))
    return render(request, "customers/list.html", {
        "page": page, "q": q, "station": station,
        "querystring": f"q={q}" if q else "",
    })  # fmt: skip


def _client(request, pk) -> Client:
    return get_object_or_404(Client.objects.for_user(request.user).select_related("station"), pk=pk)


@login_required
def client_detail(request: HttpRequest, pk: int) -> HttpResponse:
    client = _client(request, pk)
    vehicles = client.vehicles.order_by("-last_visit_at")
    from apps.workorders.models import WorkOrder

    work_orders = (
        WorkOrder.objects.filter(vehicle__client=client)
        .select_related("vehicle")
        .prefetch_related("lines")
        .order_by("-opened_at", "-id")[:50]
    )
    recommendations = (
        Recommendation.objects.filter(vehicle__client=client)
        .select_related("work_type", "vehicle", "source_work_order", "closed_by_work_order")
        .order_by("-created_at")
    )
    outreaches = client.outreaches.select_related("vehicle").order_by("-planned_for", "-id")[:20]
    return render(request, "customers/detail.html", {
        "client": client,
        "vehicles": vehicles,
        "work_orders": work_orders,
        "recommendations": recommendations,
        "outreaches": outreaches,
        "bookings": client.booking_requests.order_by("-created_at")[:10],
        "last_messages": client.messages.order_by("-created_at")[:5],
    })  # fmt: skip


@login_required
@require_POST
def client_action(request: HttpRequest, pk: int) -> HttpResponse:
    client = _client(request, pk)
    action = request.POST.get("action")
    if action == "unsubscribe":
        from apps.outreach.models import Outreach

        client.unsubscribed_at = timezone.now()
        client.save(update_fields=["unsubscribed_at", "updated_at"])
        for o in Outreach.objects.filter(
            client=client, status__in=["planned", "awaiting_approval", "approved"]
        ):
            o.status = Outreach.Status.CANCELLED
            o.skip_reason = "Клиент отписан менеджером"
            o.save(update_fields=["status", "skip_reason", "updated_at"])
            release_recommendations(o)
        messages.success(request, "Клиент отписан от сообщений.")
    elif action == "resubscribe":
        if request.POST.get("confirm") != "yes":
            messages.error(
                request, "Подтвердите, что клиент сам попросил снова присылать сообщения."
            )
            return redirect("customers:detail", pk=client.pk)
        client.unsubscribed_at = None
        client.save(update_fields=["unsubscribed_at", "updated_at"])
        messages.success(request, "Подписка восстановлена.")
    elif action == "notes":
        client.notes = request.POST.get("notes", "")[:5000]
        client.save(update_fields=["notes", "updated_at"])
        messages.success(request, "Заметки сохранены.")
        action = "notes"
    else:
        return HttpResponse(status=400)
    audit(request.user, f"client.{action}", client, station=client.station)
    return redirect("customers:detail", pk=client.pk)


@login_required
@require_POST
def vehicle_sold(request: HttpRequest, pk: int) -> HttpResponse:
    vehicle = get_object_or_404(Vehicle.objects.for_user(request.user).select_related("client"),
                                pk=pk)  # fmt: skip
    vehicle.is_sold = request.POST.get("sold") == "1"
    vehicle.save(update_fields=["is_sold", "updated_at"])
    audit(request.user, "vehicle.sold" if vehicle.is_sold else "vehicle.not_sold", vehicle,
          station=vehicle.client.station)  # fmt: skip
    messages.success(request, "Отмечено: машина продана." if vehicle.is_sold
                     else "Отметка о продаже снята.")  # fmt: skip
    return redirect("customers:detail", pk=vehicle.client_id)
