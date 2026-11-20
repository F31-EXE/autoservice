from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .context import SESSION_KEY
from .models import ServiceStation


@login_required
def station_list(request: HttpRequest) -> HttpResponse:
    stations = ServiceStation.objects.for_user(request.user).select_related("organization")
    return render(request, "accounts/station_list.html", {"stations": stations})


@login_required
def station_detail(request: HttpRequest, pk: int) -> HttpResponse:
    station = get_object_or_404(ServiceStation.objects.for_user(request.user), pk=pk)
    return render(request, "accounts/station_detail.html", {"station": station})


@login_required
@require_POST
def switch_station(request: HttpRequest) -> HttpResponse:
    station = get_object_or_404(
        ServiceStation.objects.for_user(request.user), pk=request.POST.get("station_id")
    )
    request.session[SESSION_KEY] = station.pk
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("accounts:today")
