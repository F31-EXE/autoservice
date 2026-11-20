from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render

from apps.accounts.context import get_current_station
from apps.catalog.models import Urgency, WorkType

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
        },
    )
