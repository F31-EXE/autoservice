"""Turn table rows + mapping profile into normalized work orders (pure, no DB)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from . import normalize as n
from .readers import Table, jsonable

MAX_ERRORS = 500


@dataclass
class ParsedLine:
    name: str
    kind: str = "labor"
    quantity: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    executed: bool = True


@dataclass
class ParsedClient:
    phone: str | None = None
    external_id: str | None = None
    full_name: str = ""


@dataclass
class ParsedVehicle:
    vin: str | None = None
    plate: str | None = None
    make: str = ""
    model: str = ""
    year: int | None = None


@dataclass
class ParsedWorkOrder:
    number: str
    opened_at: date
    closed_at: date | None
    status: str
    mileage: int | None
    total_amount: Decimal
    mechanic_comment: str
    recommendations_text: str
    client: ParsedClient
    vehicle: ParsedVehicle
    lines: list[ParsedLine]
    raw: list[dict[str, Any]] = field(default_factory=list)
    row_numbers: list[int] = field(default_factory=list)

    def content_hash(self) -> str:
        data = asdict(self)
        data.pop("raw")
        data.pop("row_numbers")
        payload = json.dumps(data, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class ParseResult:
    work_orders: list[ParsedWorkOrder]
    errors: list[str]
    rows_total: int
    rows_skipped: int
    warnings: int = 0


class _Errors:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.count = 0

    def add(self, rows: list[int] | int, message: str) -> None:
        self.count += 1
        if len(self.messages) < MAX_ERRORS:
            rows = [rows] if isinstance(rows, int) else rows
            label = "Строка" if len(rows) == 1 else "Строки"
            shown = ", ".join(str(r) for r in rows[:10]) + ("…" if len(rows) > 10 else "")
            self.messages.append(f"{label} {shown}: {message}")


def _first(rows: list[dict[str, Any]], header: str | None) -> Any:
    if not header:
        return None
    for row in rows:
        value = row.get(header)
        if not n.is_empty(value):
            return value
    return None


def parse_table(
    table: Table, column_map: dict[str, str], layout: str, date_format: str
) -> ParseResult:
    col = {k: v for k, v in column_map.items() if v}
    errors = _Errors()
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    skipped = 0

    for rownum, row in table.rows:
        number = n.clean_str(row.get(col["order_number"]))
        if not number:
            errors.add(rownum, "нет номера ЗН — строка пропущена")
            skipped += 1
            continue
        groups.setdefault(number, []).append((rownum, row))

    result: list[ParsedWorkOrder] = []
    warnings = 0
    for number, items in groups.items():
        rownums = [r for r, _ in items]
        rows = [row for _, row in items]
        try:
            wo, row_warnings = _parse_group(number, rows, rownums, col, layout, date_format)
        except n.ValueError_ as exc:
            errors.add(rownums, f"{exc} — ЗН пропущен")
            skipped += len(rows)
            continue
        for message in row_warnings:
            errors.add(rownums, message)
        warnings += len(row_warnings)
        result.append(wo)

    return ParseResult(
        work_orders=result,
        errors=errors.messages,
        rows_total=len(table.rows),
        rows_skipped=skipped,
        warnings=warnings,
    )


def _parse_group(number, rows, rownums, col, layout, date_format):
    warnings: list[str] = []

    opened_at = n.normalize_date(_first(rows, col.get("opened_at")), date_format)
    if opened_at is None:
        raise n.ValueError_("нет даты ЗН")
    try:
        closed_at = n.normalize_date(_first(rows, col.get("closed_at")), date_format)
    except n.ValueError_:
        closed_at = None
        warnings.append("некорректная дата закрытия — проигнорирована")
    status = n.normalize_order_status(_first(rows, col.get("order_status")))
    if closed_at is None and status == "closed":
        closed_at = opened_at

    raw_phone = _first(rows, col.get("phone"))
    phone = n.normalize_phone(raw_phone)
    if raw_phone is not None and phone is None:
        warnings.append("некорректный телефон")
    external_id = n.clean_str(_first(rows, col.get("client_external_id"))) or None
    if phone is None and external_id is None:
        raise n.ValueError_("нет телефона и ID клиента")
    client = ParsedClient(
        phone=phone,
        external_id=external_id,
        full_name=n.clean_str(_first(rows, col.get("client_name")))[:255],
    )

    raw_vin = _first(rows, col.get("vin"))
    vin = n.normalize_vin(raw_vin)
    if raw_vin is not None and vin is None:
        warnings.append("некорректный VIN — проигнорирован")
    vehicle = ParsedVehicle(
        vin=vin,
        plate=n.normalize_plate(_first(rows, col.get("plate"))),
        make=n.clean_str(_first(rows, col.get("make")))[:64],
        model=n.clean_str(_first(rows, col.get("model")))[:64],
        year=n.normalize_year(_first(rows, col.get("year"))),
    )

    try:
        mileage = n.normalize_int(_first(rows, col.get("mileage")))
    except n.ValueError_:
        mileage = None
        warnings.append("некорректный пробег — проигнорирован")

    lines: list[ParsedLine] = []
    for row in rows:
        lines.extend(_parse_lines(row, col, layout, warnings))
    declined_seen = {ln.name.lower() for ln in lines if not ln.executed}
    for row in rows:
        for name in (
            n.split_lines(row.get(col["declined_lines"])) if col.get("declined_lines") else []
        ):
            if name.lower() not in declined_seen:
                declined_seen.add(name.lower())
                lines.append(ParsedLine(name=name[:512], executed=False))

    try:
        total = n.normalize_decimal(_first(rows, col.get("order_total")))
    except n.ValueError_:
        total = None
        warnings.append("некорректная сумма ЗН — посчитана по строкам")
    if total is None:
        total = sum((ln.amount or Decimal(0) for ln in lines if ln.executed), Decimal("0.00"))

    texts = {}
    for key in ("mechanic_comment", "recommendations_text"):
        values = []
        for row in rows:
            value = n.clean_str(row.get(col[key])) if col.get(key) else ""
            if value and value not in values:
                values.append(value)
        texts[key] = "\n".join(values)

    return (
        ParsedWorkOrder(
            number=number[:64],
            opened_at=opened_at,
            closed_at=closed_at,
            status=status,
            mileage=mileage,
            total_amount=total,
            mechanic_comment=texts["mechanic_comment"],
            recommendations_text=texts["recommendations_text"],
            client=client,
            vehicle=vehicle,
            lines=lines,
            raw=[{k: jsonable(v) for k, v in row.items()} for row in rows],
            row_numbers=rownums,
        ),
        warnings,
    )


def _parse_lines(row, col, layout, warnings) -> list[ParsedLine]:
    cell = row.get(col["line_name"])
    if layout == "header":
        return [ParsedLine(name=name[:512]) for name in n.split_lines(cell)]
    name = n.clean_str(cell)
    if not name:
        return []
    numbers = {}
    for key in ("line_quantity", "line_price", "line_amount"):
        try:
            numbers[key] = n.normalize_decimal(row.get(col[key])) if col.get(key) else None
        except n.ValueError_:
            numbers[key] = None
            warnings.append("некорректное число в позиции — проигнорировано")
    amount = numbers["line_amount"]
    if amount is None and numbers["line_price"] is not None:
        amount = (numbers["line_price"] * (numbers["line_quantity"] or 1)).quantize(Decimal("0.01"))
    return [
        ParsedLine(
            name=name[:512],
            kind=n.normalize_kind(row.get(col["line_kind"])) if col.get("line_kind") else "labor",
            quantity=numbers["line_quantity"],
            price=numbers["line_price"],
            amount=amount,
            executed=n.normalize_executed(row.get(col["executed"]))
            if col.get("executed")
            else True,
        )
    ]
