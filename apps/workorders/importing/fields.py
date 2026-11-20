"""Logical fields a source file can be mapped to (spec section 5.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    required: bool = False
    synonyms: tuple[str, ...] = ()


FIELDS: tuple[FieldDef, ...] = (
    FieldDef("order_number", "Номер ЗН", True, ("номер зн", "номер заказ-наряда", "№ зн", "номер заказа", "заказ-наряд", "номер документа", "зн")),
    FieldDef("opened_at", "Дата ЗН (открытия)", True, ("дата", "дата зн", "дата открытия", "дата заказ-наряда", "дата документа", "дата визита", "дата начала")),
    FieldDef("line_name", "Работа / товар", True, ("наименование", "работа", "работы", "услуга", "услуги", "наименование работы", "работа/товар", "номенклатура", "работы и товары", "состав")),
    FieldDef("phone", "Телефон клиента", False, ("телефон", "телефон клиента", "моб. телефон", "мобильный", "тел", "тел.")),
    FieldDef("client_external_id", "ID клиента", False, ("id клиента", "код клиента", "клиент id", "код контрагента")),
    FieldDef("client_name", "ФИО клиента", False, ("фио", "клиент", "фио клиента", "заказчик", "контрагент", "владелец")),
    FieldDef("closed_at", "Дата закрытия", False, ("дата закрытия", "дата выдачи", "дата окончания")),
    FieldDef("make", "Марка", False, ("марка", "марка авто", "марка автомобиля")),
    FieldDef("model", "Модель", False, ("модель", "модель авто", "модель автомобиля")),
    FieldDef("year", "Год выпуска", False, ("год", "год выпуска")),
    FieldDef("vin", "VIN", False, ("vin", "vin-код", "вин", "vin код")),
    FieldDef("plate", "Госномер", False, ("госномер", "гос. номер", "гос номер", "гос.номер", "номер авто", "рег. номер")),
    FieldDef("mileage", "Пробег", False, ("пробег", "пробег, км", "пробег км", "одометр")),
    FieldDef("line_kind", "Тип позиции (работа/товар)", False, ("тип", "тип позиции", "вид", "вид позиции")),
    FieldDef("line_quantity", "Количество", False, ("кол-во", "количество", "кол.")),
    FieldDef("line_price", "Цена", False, ("цена", "цена за ед.")),
    FieldDef("line_amount", "Сумма строки", False, ("сумма", "сумма строки", "стоимость", "итого по строке")),
    FieldDef("executed", "Признак выполнения", False, ("выполнено", "выполнена", "статус работы", "признак выполнения", "отказ")),
    FieldDef("declined_lines", "Отказы клиента (работы)", False, ("отказы", "отказ от работ", "не выполнено", "отложенные работы")),
    FieldDef("order_total", "Сумма ЗН", False, ("сумма зн", "итого", "сумма заказа", "итого по зн", "сумма документа")),
    FieldDef("order_status", "Статус ЗН", False, ("статус", "статус зн", "состояние")),
    FieldDef("mechanic_comment", "Комментарий мастера", False, ("комментарий", "комментарий мастера", "примечание", "заключение")),
    FieldDef("recommendations_text", "Рекомендации", False, ("рекомендации", "рекомендации мастера", "рекомендации клиенту", "советы")),
)  # fmt: skip

FIELDS_BY_KEY = {f.key: f for f in FIELDS}
REQUIRED_KEYS = tuple(f.key for f in FIELDS if f.required)
CLIENT_ID_KEYS = ("phone", "client_external_id")  # at least one is required

DATE_FORMATS = (
    ("%d.%m.%Y", "31.12.2024"),
    ("%d.%m.%Y %H:%M", "31.12.2024 14:30"),
    ("%d.%m.%Y %H:%M:%S", "31.12.2024 14:30:00"),
    ("%d.%m.%y", "31.12.24"),
    ("%Y-%m-%d", "2024-12-31"),
    ("%Y-%m-%d %H:%M:%S", "2024-12-31 14:30:00"),
    ("%d/%m/%Y", "31/12/2024"),
)


def _norm_header(value: str) -> str:
    value = value.strip().lower().replace("ё", "е").replace("№", "номер ")
    return re.sub(r"\s+", " ", value).strip()


def guess_column_map(headers: list[str]) -> dict[str, str]:
    """Auto-suggest mapping by header synonyms. Each header is used at most once."""
    normalized = {_norm_header(h): h for h in headers if h}
    result: dict[str, str] = {}
    used: set[str] = set()
    for field in FIELDS:
        for synonym in field.synonyms:
            header = normalized.get(_norm_header(synonym))
            if header and header not in used:
                result[field.key] = header
                used.add(header)
                break
    return result


def missing_required(column_map: dict[str, str]) -> list[str]:
    """Return labels of required fields that are not mapped."""
    missing = [FIELDS_BY_KEY[k].label for k in REQUIRED_KEYS if not column_map.get(k)]
    if not any(column_map.get(k) for k in CLIENT_ID_KEYS):
        missing.append("Телефон или ID клиента")
    return missing
