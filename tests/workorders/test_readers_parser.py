import io

import pytest

from apps.workorders.importing.fields import guess_column_map, missing_required
from apps.workorders.importing.parser import parse_table
from apps.workorders.importing.readers import ImportFileError, read_table
from tests.workorders.helpers import FIXTURES, FLAT_MAP, HEADER_MAP


def _read(name, sheet=""):
    with open(FIXTURES / name, "rb") as fh:
        return read_table(fh, name, sheet)


def test_read_cp1251_semicolon_csv():
    table = _read("autodealer_flat.csv")
    assert table.headers[0] == "Номер ЗН"
    assert len(table.rows) == 8
    assert table.rows[0][0] == 2  # file row numbers are 1-based and include the header


def test_read_xlsx_skips_title_rows_and_reports_sheets():
    table = _read("alfa_header.xlsx")
    assert table.headers[0] == "№ заказ-наряда"
    assert table.sheet_names == ["Заказ-наряды", "Справка"]
    assert len(table.rows) == 4
    assert table.rows[0][0] == 4


def test_read_unknown_sheet_and_bad_files():
    with pytest.raises(ImportFileError):
        _read("alfa_header.xlsx", "Нет такого")
    with pytest.raises(ImportFileError):
        read_table(io.BytesIO(b"not excel"), "x.xlsx")
    with pytest.raises(ImportFileError):
        read_table(io.BytesIO(b"a;b"), "x.txt")


def test_guess_column_map():
    guess = guess_column_map(_read("autodealer_flat.csv").headers)
    assert guess["order_number"] == "Номер ЗН"
    assert guess["opened_at"] == "Дата"
    assert guess["phone"] == "Телефон"
    assert guess["line_name"] == "Наименование"
    assert guess["vin"] == "VIN"
    assert not missing_required(guess)
    guess = guess_column_map(_read("alfa_header.xlsx").headers)
    assert guess["order_number"] == "№ заказ-наряда"
    assert guess["line_name"] == "Работы"
    assert guess["declined_lines"] == "Отказы"


def test_missing_required():
    assert "Телефон или ID клиента" in missing_required({"order_number": "a"})
    assert (
        missing_required(
            {"order_number": "a", "opened_at": "b", "line_name": "c", "client_external_id": "d"}
        )
        == []
    )


def test_parse_flat():
    result = parse_table(_read("autodealer_flat.csv"), FLAT_MAP, "flat", "%d.%m.%Y")
    by_number = {wo.number: wo for wo in result.work_orders}
    assert set(by_number) == {"ЗН-1001", "ЗН-1002", "ЗН-1004"}
    wo = by_number["ЗН-1001"]
    assert wo.client.phone == "+79000000001"
    assert wo.vehicle.plate == "А001АА96"
    assert wo.mileage == 45000
    assert len(wo.lines) == 3
    assert [ln.executed for ln in wo.lines] == [True, True, False]
    assert wo.lines[1].kind == "part"
    assert str(wo.total_amount) == "4000.00"  # declined line is not counted
    assert wo.recommendations_text.startswith("Колодки передние")
    assert wo.closed_at == wo.opened_at
    # skipped: row without number, ЗН-1003 without phone/id, ЗН-1005 bad date
    assert result.rows_skipped == 3
    joined = "\n".join(result.errors)
    assert "Строка 8: нет номера ЗН" in joined
    assert "Строка 6: нет телефона и ID клиента" in joined
    assert "Строка 9: некорректная дата" in joined
    # error log must not contain PII
    assert "Без Телефона" not in joined and "89000000005" not in joined


def test_parse_header_layout():
    table = _read("alfa_header.xlsx", "Заказ-наряды")
    result = parse_table(table, HEADER_MAP, "header", "%d.%m.%Y")
    by_number = {wo.number: wo for wo in result.work_orders}
    wo = by_number["А-501"]
    assert [ln.name for ln in wo.lines] == [
        "Замена масла ДВС",
        "Замена воздушного фильтра",
        "Замена салонного фильтра",
        "Замена свечей зажигания",
    ]
    assert [ln.executed for ln in wo.lines] == [True, True, True, False]
    assert str(wo.total_amount) == "9800.00"
    assert wo.client.external_id == "C-11"
    assert by_number["А-502"].client.phone is None
    assert by_number["А-503"].status == "cancelled"
    assert by_number["А-503"].closed_at is None


def test_content_hash_is_stable_and_sensitive():
    table = _read("autodealer_flat.csv")
    a = parse_table(table, FLAT_MAP, "flat", "%d.%m.%Y").work_orders[0]
    b = parse_table(table, FLAT_MAP, "flat", "%d.%m.%Y").work_orders[0]
    assert a.content_hash() == b.content_hash()
    b.lines[0].amount = b.lines[0].amount + 1
    assert a.content_hash() != b.content_hash()
