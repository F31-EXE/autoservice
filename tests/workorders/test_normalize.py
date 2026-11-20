from datetime import date, datetime
from decimal import Decimal

import pytest

from apps.workorders.importing import normalize as n


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("8 (912) 345-67-89", "+79123456789"),
        ("+7 912 345 67 89", "+79123456789"),
        ("79123456789", "+79123456789"),
        ("9123456789", "+79123456789"),
        (79123456789, "+79123456789"),
        (89123456789.0, "+79123456789"),
        ("12345", None),
        ("", None),
        (None, None),
        ("нет", None),
    ],
)
def test_phone(raw, expected):
    assert n.normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw,fmt,expected",
    [
        ("15.01.2024", "%d.%m.%Y", date(2024, 1, 15)),
        ("15.01.2024 14:30", "%d.%m.%Y", date(2024, 1, 15)),
        ("2024-01-15", "%d.%m.%Y", date(2024, 1, 15)),
        ("01/15/2024", "%m/%d/%Y", date(2024, 1, 15)),
        (datetime(2024, 1, 15, 10), "%d.%m.%Y", date(2024, 1, 15)),
        (date(2024, 1, 15), "%d.%m.%Y", date(2024, 1, 15)),
        (45306, "%d.%m.%Y", date(2024, 1, 15)),  # Excel serial
        ("", "%d.%m.%Y", None),
    ],
)
def test_date(raw, fmt, expected):
    assert n.normalize_date(raw, fmt) == expected


@pytest.mark.parametrize("raw", ["32.13.2024", "вчера", 12])
def test_bad_date(raw):
    with pytest.raises(n.ValueError_):
        n.normalize_date(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3 200,00", Decimal("3200.00")),
        ("3 200,5", Decimal("3200.50")),
        ("1.234,50", Decimal("1234.50")),
        ("1,234.50", Decimal("1234.50")),
        ("1500 руб.", Decimal("1500.00")),
        ("1500 ₽", Decimal("1500.00")),
        ("99.9", Decimal("99.90")),
        (1500, Decimal("1500.00")),
        (99.95, Decimal("99.95")),
        ("", None),
    ],
)
def test_decimal(raw, expected):
    assert n.normalize_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "nan", "inf", True])
def test_bad_decimal(raw):
    with pytest.raises(n.ValueError_):
        n.normalize_decimal(raw)


def test_int_and_year():
    assert n.normalize_int("123 456 км") == 123456
    assert n.normalize_int(45000.0) == 45000
    assert n.normalize_int("") is None
    assert n.normalize_year("2019") == 2019
    assert n.normalize_year("1800") is None
    assert n.normalize_year("abc") is None


def test_vin_and_plate():
    assert n.normalize_vin(" xta 00000000000001 ") == "XTA00000000000001"
    assert n.normalize_vin("XTA0000000000000I") is None  # I is not allowed in VIN
    assert n.normalize_vin("short") is None
    assert n.normalize_plate("а 001 аа 96") == "А001АА96"
    assert n.normalize_plate("A001AA96") == "А001АА96"  # latin look-alikes -> cyrillic
    assert n.normalize_plate("") is None


@pytest.mark.parametrize(
    "raw,expected",
    [("да", True), ("", True), (None, True), ("1", True), ("Выполнено", True),
     ("нет", False), ("0", False), ("Отказ", False), ("не выполнено", False), (False, False)],
)  # fmt: skip
def test_executed(raw, expected):
    assert n.normalize_executed(raw) is expected


def test_kind_status_split():
    assert n.normalize_kind("Товар") == "part"
    assert n.normalize_kind("Запчасти") == "part"
    assert n.normalize_kind("Работа") == "labor"
    assert n.normalize_order_status("Отменён") == "cancelled"
    assert n.normalize_order_status("В работе") == "open"
    assert n.normalize_order_status("") == "closed"
    assert n.split_lines("а; б\nв;;") == ["а", "б", "в"]
