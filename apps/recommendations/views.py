from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.context import get_current_station
from apps.catalog.models import Urgency, WorkType
from apps.common.models import audit

from .forms import ReviewForm
from .models import Recommendation

STATUS_FILTERS = {
    "active": ("Активные", Recommendation.ACTIVE_STATUSES),
    **{s.value: (s.label, (s.value,)) for s in Recommendation.Status},
    "all": ("Все", None),
}


@login_required
def recommendation_list(request: HttpRequest) -> HttpResponse:
    station = get_current_station(request)
    if station is None:
        raise Http404
    qs = (
        Recommendation.objects.for_user(request.user)
        .for_station(station)
        .select_related("work_type", "vehicle__client", "source_work_order")
    )
    status = request.GET.get("status", "active")
    if status not in STATUS_FILTERS:
        status = "active"
    statuses = STATUS_FILTERS[status][1]
    if statuses:
        qs = qs.filter(status__in=statuses)
    urgency = request.GET.get("urgency", "")
    if urgency in Urgency.values:
        qs = qs.filter(urgency=urgency)
    source = request.GET.get("source", "")
    if source in Recommendation.Source.values:
        qs = qs.filter(source=source)
    work_type = request.GET.get("work_type", "")
    if work_type.isdigit():
        qs = qs.filter(work_type_id=int(work_type))

    page = Paginator(qs.order_by("due_date", "id"), 50).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "recommendations/list.html",
        {
            "page": page,
            "station": station,
            "status": status,
            "status_filters": [(k, v[0]) for k, v in STATUS_FILTERS.items()],
            "urgency": urgency,
            "urgencies": Urgency.choices,
            "source": source,
            "sources": Recommendation.Source.choices,
            "work_type": work_type,
            "work_types": WorkType.objects.filter(recommendations__station=station).distinct(),
            "querystring": params.urlencode(),
            "review_count": Recommendation.objects.for_user(request.user)
            .for_station(station)
            .filter(status=Recommendation.Status.NEEDS_REVIEW)
            .count(),
        },
    )


@login_required
def review_list(request: HttpRequest) -> HttpResponse:
    """Tab "На проверке": low-confidence or unmatched recommendations (spec 6.2, screen 4)."""
    station = get_current_station(request)
    if station is None:
        raise Http404
    qs = (
        Recommendation.objects.for_user(request.user)
        .for_station(station)
        .filter(status=Recommendation.Status.NEEDS_REVIEW)
        .select_related("work_type", "vehicle__client", "source_work_order")
        .order_by("-source_work_order__opened_at", "id")
    )
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    items = [(rec, ReviewForm(instance=rec, prefix=f"r{rec.pk}")) for rec in page]
    return render(
        request,
        "recommendations/review.html",
        {"page": page, "items": items, "station": station, "querystring": ""},
    )


@login_required
@require_POST
def review_action(request: HttpRequest, pk: int) -> HttpResponse:
    rec = get_object_or_404(
        Recommendation.objects.for_user(request.user).select_related("work_type"),
        pk=pk,
        status=Recommendation.Status.NEEDS_REVIEW,
    )
    action = request.POST.get("action")
    form = ReviewForm(request.POST, instance=rec, prefix=f"r{rec.pk}")
    before = _snapshot(rec)
    if action == "reject":
        rec.status = Recommendation.Status.REJECTED
    elif action in ("approve", "edit"):
        if action == "edit":
            if not form.is_valid():
                return render(request, "recommendations/_review_item.html",
                              {"rec": rec, "form": form}, status=422)  # fmt: skip
            rec = form.save(commit=False)
        rec.status = Recommendation.Status.OPEN
    else:
        return HttpResponseBadRequest("unknown action")
    rec.review_decision_by = request.user
    rec.review_decision_at = timezone.now()
    rec.save()
    from apps.outreach.planner import recompute_planned_for

    recompute_planned_for(rec.station, [rec.vehicle_id])
    after = _snapshot(rec)
    audit(request.user, f"recommendation.{action}", rec, station=rec.station,
          before=before, after=after)  # fmt: skip
    return render(request, "recommendations/_review_done.html", {"rec": rec, "action": action})


def _snapshot(rec: Recommendation) -> dict:
    return {
        "work_type": rec.work_type.code if rec.work_type else None,
        "due_date": str(rec.due_date) if rec.due_date else None,
        "due_km": rec.due_km,
        "urgency": rec.urgency,
    }
