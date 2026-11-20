import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.workorders.models import ImportBatch, ImportMappingProfile, WorkOrder
from tests.workorders.helpers import FIXTURES, flat_profile, run_import

pytestmark = pytest.mark.django_db


def _upload(client, name, source="autodealer", **extra):
    data = {"file": SimpleUploadedFile(name, (FIXTURES / name).read_bytes()),
            "source_system": source, **extra}  # fmt: skip
    return client.post(reverse("workorders:import_upload"), data)


def _mapping_post(headers_map, **extra):
    data = {f"col_{k}": v for k, v in headers_map.items()}
    data.update({"profile_name": "Мой профиль", "layout": "flat", "date_format": "%d.%m.%Y"})
    data.update(extra)
    return data


def test_full_flow_new_profile(client, manager_a, station_a, django_capture_on_commit_callbacks):
    client.force_login(manager_a)
    resp = _upload(client, "autodealer_flat.csv")
    batch = ImportBatch.objects.get()
    assert resp["Location"] == reverse("workorders:import_mapping", args=[batch.pk])
    assert batch.station == station_a and batch.uploaded_by == manager_a

    page = client.get(resp["Location"])
    html = page.content.decode()
    assert "ЗН-1001" in html  # preview rows
    # auto-guessed mapping is preselected
    assert page.context["form"].initial["col_order_number"] == "Номер ЗН"

    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(
            reverse("workorders:import_mapping", args=[batch.pk]),
            _mapping_post(
                {
                    k.removeprefix("col_"): v
                    for k, v in page.context["form"].initial.items()
                    if k.startswith("col_")
                }  # fmt: skip
            ),
        )
    assert resp["Location"] == reverse("workorders:import_detail", args=[batch.pk])
    batch.refresh_from_db()
    assert batch.status == "parsed", batch.error_log
    assert WorkOrder.objects.count() == 3
    detail = client.get(resp["Location"]).content.decode()
    assert "Готово" in detail and "Строка 8: нет номера ЗН" in detail


def test_mapping_requires_mandatory_fields(client, manager_a, station_a):
    client.force_login(manager_a)
    _upload(client, "autodealer_flat.csv")
    batch = ImportBatch.objects.get()
    resp = client.post(
        reverse("workorders:import_mapping", args=[batch.pk]),
        _mapping_post({"order_number": "Номер ЗН", "opened_at": "Дата"}),
    )
    assert resp.status_code == 200
    assert "Не выбраны обязательные поля" in resp.content.decode()
    resp = client.post(
        reverse("workorders:import_mapping", args=[batch.pk]),
        _mapping_post(
            {
                "order_number": "Номер ЗН",
                "opened_at": "Номер ЗН",
                "line_name": "Наименование",
                "phone": "Телефон",
            }
        ),  # fmt: skip
    )
    assert "Одна колонка выбрана для нескольких полей" in resp.content.decode()
    assert not ImportMappingProfile.objects.exists()


def test_saved_profile_is_reused(client, manager_a, station_a, django_capture_on_commit_callbacks):
    flat_profile(station_a)
    client.force_login(manager_a)
    with django_capture_on_commit_callbacks(execute=True):
        resp = _upload(client, "autodealer_flat.csv")
    batch = ImportBatch.objects.get()
    assert resp["Location"] == reverse("workorders:import_detail", args=[batch.pk])
    batch.refresh_from_db()
    assert batch.status == "parsed"
    # remap flag forces the mapping screen
    resp = _upload(client, "autodealer_flat.csv", remap="on")
    assert "/mapping/" in resp["Location"]


def test_profile_with_unknown_columns_goes_to_mapping(client, manager_a, station_a):
    flat_profile(station_a)
    client.force_login(manager_a)
    resp = _upload(client, "alfa_header.xlsx")
    assert "/mapping/" in resp["Location"]


def test_xlsx_sheet_selection(client, manager_a, station_a):
    client.force_login(manager_a)
    _upload(client, "alfa_header.xlsx", source="alfa_auto")
    batch = ImportBatch.objects.get()
    url = reverse("workorders:import_mapping", args=[batch.pk])
    page = client.get(url)
    assert page.context["sheet_names"] == ["Заказ-наряды", "Справка"]
    assert "№ заказ-наряда" in page.context["headers"]
    page = client.get(url + "?sheet=Справка")
    assert "№ заказ-наряда" not in page.context["headers"]


def test_upload_rejects_bad_files(client, manager_a, station_a):
    client.force_login(manager_a)
    resp = client.post(
        reverse("workorders:import_upload"),
        {"file": SimpleUploadedFile("x.txt", b"abc"), "source_system": "other"},
    )
    assert "Поддерживаются только" in resp.content.decode()
    resp = client.post(
        reverse("workorders:import_upload"),
        {"file": SimpleUploadedFile("x.xlsx", b"not excel"), "source_system": "other"},
    )
    assert "Не удалось открыть Excel-файл" in resp.content.decode()
    assert not ImportBatch.objects.exists()


def test_htmx_status_partial(client, manager_a, station_a):
    batch = run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    client.force_login(manager_a)
    resp = client.get(reverse("workorders:import_detail", args=[batch.pk]), HTTP_HX_REQUEST="true")
    html = resp.content.decode()
    assert html.lstrip().startswith('<section id="import-status"')
    assert "every 2s" not in html  # finished batch is not polled


class TestIsolation:
    def test_foreign_batch_is_404(self, client, manager_a, station_b):
        foreign = run_import(station_b, flat_profile(station_b), "autodealer_flat.csv")
        client.force_login(manager_a)
        for name in ("workorders:import_detail", "workorders:import_mapping"):
            assert client.get(reverse(name, args=[foreign.pk])).status_code == 404
        assert client.post(reverse("workorders:import_mapping", args=[foreign.pk]),
                           {}).status_code == 404  # fmt: skip

    def test_list_shows_only_own_station(self, client, manager_a, station_a, station_b):
        run_import(station_b, flat_profile(station_b), "autodealer_flat.csv")
        client.force_login(manager_a)
        resp = client.get(reverse("workorders:import_list"))
        assert list(resp.context["page"]) == []

    def test_foreign_profile_not_reused(self, client, manager_a, station_a, station_b):
        flat_profile(station_b)
        client.force_login(manager_a)
        resp = _upload(client, "autodealer_flat.csv")
        assert "/mapping/" in resp["Location"]

    def test_login_required(self, client):
        for name in ("workorders:import_list", "workorders:import_upload"):
            assert client.get(reverse(name)).status_code == 302

    def test_tenant_querysets(self, manager_a, station_a, station_b):
        from apps.customers.models import Client, Vehicle
        from apps.workorders.models import WorkOrderLine

        run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
        run_import(station_b, flat_profile(station_b), "autodealer_flat.csv")
        for model in (Client, Vehicle, WorkOrder, WorkOrderLine, ImportBatch,
                      ImportMappingProfile):  # fmt: skip
            visible = model.objects.for_user(manager_a)
            assert visible.exists()
            assert visible.count() * 2 == model.objects.count(), model.__name__
