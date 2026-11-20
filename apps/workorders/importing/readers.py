"""Read .xlsx / .csv into a header list + rows (dicts keyed by header)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import IO, Any

MAX_HEADER_SCAN_ROWS = 20


class ImportFileError(Exception):
    """Unreadable file. Message is user-facing and PII-free."""


@dataclass
class Table:
    headers: list[str]
    rows: list[tuple[int, dict[str, Any]]]  # (1-based row number in file, values)
    sheet_names: list[str] = field(default_factory=list)


def _dedupe_headers(raw: list[Any]) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for i, cell in enumerate(raw):
        name = str(cell).strip() if cell is not None and str(cell).strip() else f"Колонка {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def _build_table(raw_rows: list[tuple[int, list[Any]]], sheet_names: list[str]) -> Table:
    header_idx = None
    for idx, (_, values) in enumerate(raw_rows[:MAX_HEADER_SCAN_ROWS]):
        non_empty = [v for v in values if v is not None and str(v).strip()]
        if len(non_empty) >= 2:
            header_idx = idx
            break
    if header_idx is None:
        raise ImportFileError("Не удалось найти строку заголовков в файле")
    headers = _dedupe_headers(raw_rows[header_idx][1])
    rows = []
    for rownum, values in raw_rows[header_idx + 1 :]:
        if not any(v is not None and str(v).strip() for v in values):
            continue
        padded = list(values) + [None] * (len(headers) - len(values))
        rows.append((rownum, dict(zip(headers, padded, strict=False))))
    return Table(headers=headers, rows=rows, sheet_names=sheet_names)


def read_xlsx(fileobj: IO[bytes], sheet_name: str = "", limit: int | None = None) -> Table:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any openpyxl failure means unreadable file
        raise ImportFileError("Не удалось открыть Excel-файл") from exc
    try:
        sheet_names = list(wb.sheetnames)
        if sheet_name:
            if sheet_name not in sheet_names:
                raise ImportFileError("В файле нет выбранного листа")
            ws = wb[sheet_name]
        else:
            ws = wb[sheet_names[0]]
        raw_rows = []
        for rownum, values in enumerate(ws.iter_rows(values_only=True), start=1):
            raw_rows.append((rownum, list(values)))
            if limit is not None and len(raw_rows) >= limit + MAX_HEADER_SCAN_ROWS:
                break
        # trim trailing empty columns
        width = max(
            (max((i + 1 for i, v in enumerate(vals) if v not in (None, "")), default=0))
            for _, vals in raw_rows
        ) if raw_rows else 0  # fmt: skip
        raw_rows = [(n, vals[:width]) for n, vals in raw_rows]
    finally:
        wb.close()
    return _build_table(raw_rows, sheet_names)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileError("Не удалось определить кодировку CSV (ожидается UTF-8 или Windows-1251)")


def read_csv(fileobj: IO[bytes], limit: int | None = None) -> Table:
    text = _decode(fileobj.read())
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    raw_rows = []
    for rownum, values in enumerate(reader, start=1):
        raw_rows.append((rownum, values))
        if limit is not None and len(raw_rows) >= limit + MAX_HEADER_SCAN_ROWS:
            break
    return _build_table(raw_rows, [])


def read_table(fileobj: IO[bytes], filename: str, sheet_name: str = "", limit=None) -> Table:
    name = filename.lower()
    if name.endswith(".xlsx"):
        return read_xlsx(fileobj, sheet_name, limit)
    if name.endswith(".csv"):
        return read_csv(fileobj, limit)
    raise ImportFileError("Поддерживаются только файлы .xlsx и .csv")


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)
