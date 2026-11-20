"""Screen "Сегодня" (spec 7.5): reminders awaiting approval and actions on them."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.context import get_current_station
from apps.common.models import audit
from apps.recommendations.models import Recommendation

from .forms import SkipForm, TextForm
from .models import Outreach
from .planner import build_queue, cancel_obsolete, release_recommendations
from .texts import call_script, compose_text, default_channel, sms_segments


def _queue(request, station):
    return (
        Outreach.objects.for_user(request.user)
        .for_station(station)
        .filter(status=Outreach.Status.AWAITING_APPROVAL)
        .select_related("client", "vehicle", "station")
        .prefetch_related("recommendations__work_type", "recommendations__source_work_order")
        .order_by("planned_for", "id")
    )


def _row_context(outreach: Outreach, **extra) -> dict:
    from django.conf import settings

    channel = default_channel(outreach.client) or "sms"
    text = compose_text(outreach, channel)
    segments = sms_segments(text)[0] if channel == "sms" else None
    return {
        "o": outreach,
        "text": text,
        "channel": channel,
        "segments": segments,
        "too_long": bool(segments and segments > int(settings.SMS_MAX_SEGMENTS)),
        **extra,
    }


@login_required
def today(request: HttpRequest) -> HttpResponse:
    station = get_current_station(request)
    if station is None:
        return render(request, "dashboard/today.html", {"station": None})
    page = Paginator(_queue(request, station), 25).get_page(request.GET.get("page"))
    local_today = station.local_today()
    todays = (
        Outreach.objects.for_user(request.user).for_station(station).filter(planned_for=local_today)
    )
    return render(
        request,
        "dashboard/today.html",
        {
            "station": station,
            "page": page,
            "rows": [_row_context(o) for o in page],
            "querystring": "",
            "control_today": todays.filter(is_control_group=True).count(),
            "approved_today": todays.filter(status=Outreach.Status.APPROVED).count(),
            "skipped_today": todays.filter(status=Outreach.Status.SKIPPED).count(),
            **_inbox(request, station),
        },
    )


def _inbox(request, station) -> dict:
    from apps.messaging.models import BookingRequest, Message

    attention = (
        Message.objects.for_user(request.user)
        .filter(client__station=station, needs_attention=True)
        .select_related("client")
        .order_by("-created_at")
    )
    return {
        "bookings": list(
            BookingRequest.objects.for_user(request.user)
            .filter(client__station=station, status__in=["new", "contacted"])
            .select_related("client")[:50]
        ),
        "attention": list(attention[:10]),
        "attention_count": attention.count(),
        "calls": list(
            Outreach.objects.for_user(request.user)
            .for_station(station)
            .filter(status=Outreach.Status.APPROVED, channel=Outreach.Channel.MANUAL_CALL)
            .select_related("client", "vehicle")
        ),
    }


def _get_pending(request, pk) -> Outreach:
    return get_object_or_404(
        Outreach.objects.for_user(request.user).select_related("client", "vehicle", "station"),
        pk=pk,
        status=Outreach.Status.AWAITING_APPROVAL,
    )


def _still_relevant(outreach: Outreach) -> bool:
    return outreach.recommendations.filter(status__in=Recommendation.ACTIVE_STATUSES).exists()


def _approve(request, outreach: Outreach, channel: str) -> None:
    outreach.status = Outreach.Status.APPROVED
    outreach.channel = channel
    outreach.approved_by = request.user
    outreach.approved_at = timezone.now()
    outreach.save(update_fields=["status", "channel", "approved_by", "approved_at", "updated_at"])
    audit(request.user, "outreach.approve", outreach, station=outreach.station, channel=channel)
    if channel != Outreach.Channel.MANUAL_CALL:
        from apps.messaging.tasks import send_touch_task

        outreach_id = outreach.pk
        # sends now if inside the send window; otherwise the periodic dispatcher does it later
        transaction.on_commit(lambda: send_touch_task.delay(outreach_id))


def _default_channel(outreach: Outreach) -> str:
    return default_channel(outreach.client) or Outreach.Channel.SMS


@login_required
@require_POST
def approve(request: HttpRequest, pk: int) -> HttpResponse:
    with transaction.atomic():
        outreach = _get_pending(request, pk)
        if not _still_relevant(outreach):
            cancel_obsolete(outreach.station)
            return render(
                request, "outreach/_row_done.html", {"o": outreach, "result": "cancelled"}
            )
        _approve(request, outreach, _default_channel(outreach))
    return render(request, "outreach/_row_done.html", {"o": outreach, "result": "approved"})


@login_required
@require_POST
def call(request: HttpRequest, pk: int) -> HttpResponse:
    """High-value reminders are handled by a phone call: approve as a call task + script."""
    with transaction.atomic():
        outreach = _get_pending(request, pk)
        _approve(request, outreach, Outreach.Channel.MANUAL_CALL)
    return render(
        request,
        "outreach/_row_done.html",
        {"o": outreach, "result": "call", "script": call_script(outreach)},
    )


@login_required
@require_POST
def skip(request: HttpRequest, pk: int) -> HttpResponse:
    outreach = _get_pending(request, pk)
    form = SkipForm(request.POST)
    if not form.is_valid():
        return render(
            request, "outreach/_row.html", _row_context(outreach, skip_error=True), status=422
        )
    with transaction.atomic():
        outreach.status = Outreach.Status.SKIPPED
        outreach.skip_reason = form.cleaned_data["reason"]
        outreach.save(update_fields=["status", "skip_reason", "updated_at"])
        release_recommendations(outreach)
        # the reason is free text typed by a manager — may contain PII, so keep only its length
        audit(request.user, "outreach.skip", outreach, station=outreach.station,
              reason_length=len(outreach.skip_reason))  # fmt: skip
    return render(request, "outreach/_row_done.html", {"o": outreach, "result": "skipped"})


@login_required
def edit_text(request: HttpRequest, pk: int) -> HttpResponse:
    outreach = _get_pending(request, pk)
    if request.method == "POST":
        form = TextForm(request.POST)
        if form.is_valid():
            old = compose_text(outreach)
            new = form.cleaned_data["text"].strip()
            outreach.custom_text = new
            outreach.save(update_fields=["custom_text", "updated_at"])
            audit(request.user, "outreach.edit_text", outreach, station=outreach.station,
                  old_length=len(old), new_length=len(new))  # fmt: skip
            return render(request, "outreach/_row.html", _row_context(outreach))
    else:
        form = TextForm(initial={"text": compose_text(outreach)})
    return render(request, "outreach/_text_form.html", {"o": outreach, "form": form})


@login_required
def row(request: HttpRequest, pk: int) -> HttpResponse:
    """Re-render a row (used by "cancel" in the text editor)."""
    return render(request, "outreach/_row.html", _row_context(_get_pending(request, pk)))


@login_required
@require_POST
def bulk_approve(request: HttpRequest) -> HttpResponse:
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    approved = 0
    with transaction.atomic():
        for outreach in (
            Outreach.objects.for_user(request.user)
            .filter(id__in=ids, status=Outreach.Status.AWAITING_APPROVAL)
            .select_related("client", "station")
            .select_for_update(of=("self",))
        ):
            if outreach.needs_call or not _still_relevant(outreach):
                continue  # calls and obsolete reminders are handled one by one
            _approve(request, outreach, _default_channel(outreach))
            approved += 1
    messages.success(request, f"Одобрено к отправке: {approved}")
    return redirect("accounts:today")


@login_required
@require_POST
def rebuild(request: HttpRequest) -> HttpResponse:
    """Build today's queue now (e.g. right after uploading a new export)."""
    station = get_current_station(request)
    if station is None:
        raise Http404
    stats = build_queue(station)
    messages.success(
        request,
        f"Очередь обновлена: новых к подтверждению — {stats.get('awaiting_approval', 0)}, "
        f"в контрольной группе — {stats.get('control', 0)}, пропущено — {stats.get('skipped', 0)}.",
    )
    return redirect("accounts:today")
