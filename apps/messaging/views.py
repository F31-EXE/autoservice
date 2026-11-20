from __future__ import annotations

import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.context import get_current_station
from apps.common.models import audit
from apps.customers.models import Client
from apps.outreach.models import Outreach
from apps.outreach.planner import release_recommendations
from apps.recommendations.models import Recommendation

from . import services
from .models import BookingRequest, Channel, Message
from .tasks import process_incoming_sms_task, process_max_update_task

logger = logging.getLogger(__name__)


def _secret_ok(received: str | None, expected: str) -> bool:
    return bool(expected) and bool(received) and hmac.compare_digest(received, expected)


# ---------------------------------------------------------------- webhooks


@csrf_exempt
@require_POST
def max_webhook(request: HttpRequest) -> HttpResponse:
    """MAX sends updates here; the secret comes in the X-Max-Bot-Api-Secret header."""
    if not _secret_ok(request.headers.get("X-Max-Bot-Api-Secret"), settings.MAX_WEBHOOK_SECRET):
        return HttpResponse(status=403)
    try:
        update = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)
    if not isinstance(update, dict):
        return HttpResponse(status=400)
    transaction.on_commit(lambda: process_max_update_task.delay(update))
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def sms_inbound(request: HttpRequest) -> HttpResponse:
    """Generic inbound SMS hook: {"phone", "text", "id"}; a provider adapter maps to it."""
    if not _secret_ok(request.headers.get("X-Vozvrat-Secret"), settings.SMS_INBOUND_SECRET):
        return HttpResponse(status=403)
    try:
        data = json.loads(request.body)
        phone, text = str(data["phone"]), str(data["text"])
    except (ValueError, KeyError, TypeError):
        return HttpResponse(status=400)
    provider_id = str(data.get("id") or "")
    transaction.on_commit(lambda: process_incoming_sms_task.delay(phone, text, provider_id))
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------- dialogs (screen 6)


def _station(request):
    station = get_current_station(request)
    if station is None:
        raise Http404
    return station


@login_required
def dialogs(request: HttpRequest) -> HttpResponse:
    station = _station(request)
    clients = (
        Client.objects.for_user(request.user)
        .for_station(station)
        .filter(messages__isnull=False)
        .annotate(
            last_at=Max("messages__created_at"),
            attention=Count("messages", filter=Q(messages__needs_attention=True)),
        )
        .order_by("-attention", "-last_at")
    )
    if request.GET.get("all") != "1":
        clients = clients.filter(attention__gt=0)
    page = Paginator(clients, 30).get_page(request.GET.get("page"))
    return render(request, "messaging/dialogs.html",
                  {"page": page, "station": station, "show_all": request.GET.get("all") == "1",
                   "querystring": "all=1" if request.GET.get("all") == "1" else ""})  # fmt: skip


@login_required
def conversation(request: HttpRequest, client_id: int) -> HttpResponse:
    client = get_object_or_404(Client.objects.for_user(request.user), pk=client_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply":
            text = request.POST.get("text", "").strip()
            if text:
                outreach = services.latest_outreach(client)
                channel = services.default_channel(client) or Channel.SMS
                services.send_reply(client, channel, text, outreach, user=request.user)
                audit(request.user, "message.reply", client, station=client.station,
                      length=len(text))  # fmt: skip
        if action in ("reply", "handled"):
            Message.objects.filter(client=client, needs_attention=True).update(
                needs_attention=False, handled_by=request.user, updated_at=timezone.now()
            )
        return redirect("messaging:conversation", client_id=client.pk)
    history = client.messages.select_related("outreach").order_by("created_at", "id")
    return render(request, "messaging/conversation.html", {"client": client, "history": history})


# ---------------------------------------------------------------- bookings


@login_required
@require_POST
def booking_action(request: HttpRequest, pk: int) -> HttpResponse:
    booking = get_object_or_404(
        BookingRequest.objects.for_user(request.user).select_related("outreach", "client"), pk=pk
    )
    status = request.POST.get("status")
    if status not in (BookingRequest.Status.CONTACTED, BookingRequest.Status.BOOKED,
                      BookingRequest.Status.CANCELLED):  # fmt: skip
        return HttpResponse(status=400)
    with transaction.atomic():
        booking.status = status
        booking.assigned_to = request.user
        booking.save(update_fields=["status", "assigned_to", "updated_at"])
        outreach = booking.outreach
        if outreach is not None and status == BookingRequest.Status.BOOKED:
            outreach.status = Outreach.Status.BOOKED
            outreach.save(update_fields=["status", "updated_at"])
        audit(request.user, f"booking.{status}", booking, station=booking.client.station)
    return render(request, "messaging/_booking.html", {"b": booking})


# ---------------------------------------------------------------- phone calls


@login_required
@require_POST
def call_outcome(request: HttpRequest, pk: int) -> HttpResponse:
    outreach = get_object_or_404(
        Outreach.objects.for_user(request.user).select_related("client", "station"),
        pk=pk, status=Outreach.Status.APPROVED, channel=Outreach.Channel.MANUAL_CALL,
    )  # fmt: skip
    outcome = request.POST.get("outcome")
    labels = {"booked": "Записан", "no_answer": "Не дозвонились", "declined": "Отказался"}
    if outcome not in labels:
        return HttpResponse(status=400)
    now = timezone.now()
    with transaction.atomic():
        touch = outreach.touch_count + 1
        Message.objects.create(
            outreach=outreach, client=outreach.client, channel=Channel.MANUAL_CALL,
            direction=Message.Direction.OUT, status=Message.Status.SENT, touch_number=touch,
            text=f"Звонок: {labels[outcome]}", sent_by=request.user, attempts=1,
        )  # fmt: skip
        outreach.touch_count = touch
        outreach.first_touch_at = outreach.first_touch_at or now
        outreach.last_touch_at = now
        if outcome == "booked":
            outreach.status = Outreach.Status.BOOKED
            BookingRequest.objects.create(outreach=outreach, client=outreach.client,
                                          status=BookingRequest.Status.BOOKED,
                                          assigned_to=request.user)  # fmt: skip
        elif outcome == "declined":
            outreach.status = Outreach.Status.REPLIED
            outreach.recommendations.filter(status__in=Recommendation.ACTIVE_STATUSES).update(
                status=Recommendation.Status.DECLINED, updated_at=now
            )
        else:
            outreach.status = Outreach.Status.SENT  # second touch logic applies (spec 8.5)
        outreach.save(update_fields=["status", "touch_count", "first_touch_at", "last_touch_at",
                                     "updated_at"])  # fmt: skip
        if outcome == "declined":
            release_recommendations(outreach)
        audit(request.user, f"call.{outcome}", outreach, station=outreach.station)
    flash.success(request, f"Звонок отмечен: {labels[outcome]}")
    return redirect("accounts:today")
