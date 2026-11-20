from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.context import get_current_station

from .forms import MappingForm, UploadForm
from .importing.fields import guess_column_map, missing_required
from .importing.readers import ImportFileError, read_table
from .models import ImportBatch, ImportMappingProfile, SourceSystem
from .tasks import parse_import_batch

PREVIEW_ROWS = 5


def _station_or_404(request):
    station = get_current_station(request)
    if station is None:
        raise Http404("Нет доступной точки")
    return station


def _read_preview(batch: ImportBatch, sheet_name: str = ""):
    with batch.file.open("rb") as fh:
        return read_table(fh, batch.original_filename, sheet_name, limit=PREVIEW_ROWS)


def _start_parsing(batch: ImportBatch) -> None:
    transaction.on_commit(lambda: parse_import_batch.delay(batch.pk))


@login_required
def import_list(request: HttpRequest) -> HttpResponse:
    station = _station_or_404(request)
    qs = (
        ImportBatch.objects.for_user(request.user)
        .for_station(station)
        .select_related("uploaded_by")
    )
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "workorders/import_list.html", {"page": page, "station": station})


@login_required
def import_upload(request: HttpRequest) -> HttpResponse:
    station = _station_or_404(request)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        source_system = form.cleaned_data["source_system"]
        batch = ImportBatch(
            station=station,
            uploaded_by=request.user,
            source_system=source_system,
            original_filename=upload.name[:255],
        )
        batch.file.save(upload.name, upload, save=False)
        try:
            table = _read_preview(batch)
        except ImportFileError as exc:
            batch.file.delete(save=False)
            form.add_error("file", str(exc))
        else:
            batch.save()
            profile = (
                ImportMappingProfile.objects.for_station(station)
                .filter(source_system=source_system)
                .order_by("-updated_at")
                .first()
            )
            if profile and not form.cleaned_data["remap"] and _profile_fits(profile, table):
                batch.mapping_profile = profile
                batch.save(update_fields=["mapping_profile", "updated_at"])
                _start_parsing(batch)
                messages.success(request, f"Файл загружен, используется профиль «{profile.name}».")
                return redirect("workorders:import_detail", pk=batch.pk)
            return redirect("workorders:import_mapping", pk=batch.pk)
    return render(request, "workorders/import_upload.html", {"form": form, "station": station})


def _profile_fits(profile: ImportMappingProfile, table) -> bool:
    if profile.sheet_name and profile.sheet_name not in (table.sheet_names or []):
        return False
    used = [h for h in profile.column_map.values() if h]
    return all(h in table.headers for h in used) and not missing_required(profile.column_map)


@login_required
def import_mapping(request: HttpRequest, pk: int) -> HttpResponse:
    batch = get_object_or_404(ImportBatch.objects.for_user(request.user), pk=pk)
    if batch.mapping_profile_id or batch.status != ImportBatch.Status.PENDING:
        return redirect("workorders:import_detail", pk=batch.pk)

    sheet = request.POST.get("sheet_name", request.GET.get("sheet", ""))
    try:
        table = _read_preview(batch, sheet)
    except ImportFileError as exc:
        messages.error(request, str(exc))
        sheet = ""
        table = _read_preview(batch)

    last_profile = (
        ImportMappingProfile.objects.for_station(batch.station)
        .filter(source_system=batch.source_system)
        .order_by("-updated_at")
        .first()
    )
    if request.method == "POST":
        form = MappingForm(request.POST, headers=table.headers)
        if form.is_valid():
            profile, _ = ImportMappingProfile.objects.update_or_create(
                station=batch.station,
                source_system=batch.source_system,
                name=form.cleaned_data["profile_name"],
                defaults={
                    "layout": form.cleaned_data["layout"],
                    "column_map": form.column_map(),
                    "date_format": form.cleaned_data["date_format"],
                    "sheet_name": sheet,
                },
            )
            batch.mapping_profile = profile
            batch.save(update_fields=["mapping_profile", "updated_at"])
            _start_parsing(batch)
            messages.success(request, "Сопоставление сохранено, файл обрабатывается.")
            return redirect("workorders:import_detail", pk=batch.pk)
    else:
        guess = guess_column_map(table.headers)
        if last_profile:
            guess.update({k: v for k, v in last_profile.column_map.items() if v in table.headers})
        initial = {f"col_{k}": v for k, v in guess.items()}
        initial.update(
            profile_name=last_profile.name
            if last_profile
            else f"{SourceSystem(batch.source_system).label} — основной",
            layout=last_profile.layout if last_profile else "flat",
            date_format=last_profile.date_format if last_profile else "%d.%m.%Y",
        )
        form = MappingForm(initial=initial, headers=table.headers)

    preview = [[row.get(h) for h in table.headers] for _, row in table.rows[:PREVIEW_ROWS]]
    return render(
        request,
        "workorders/import_mapping.html",
        {
            "batch": batch,
            "form": form,
            "headers": table.headers,
            "preview": preview,
            "sheet_names": table.sheet_names,
            "sheet": sheet,
        },
    )


@login_required
def import_detail(request: HttpRequest, pk: int) -> HttpResponse:
    batch = get_object_or_404(
        ImportBatch.objects.for_user(request.user).select_related("mapping_profile"), pk=pk
    )
    template = (
        "workorders/_import_status.html"
        if request.headers.get("HX-Request")
        else "workorders/import_detail.html"
    )
    errors = batch.error_log.splitlines() if batch.error_log else []
    return render(request, template, {"batch": batch, "errors": errors})
