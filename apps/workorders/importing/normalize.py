"""Value normalizers for imported cells. They never log values (PII)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import phonenumbers


class ValueError_(ValueError):
    """Raised when a cell cannot be normalized. Message is PII-free."""


def is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def clean_str(value) -> str:
    if is_empty(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"[ \t ]+", " ", str(value)).strip()


def normalize_phone(value, region: str = "RU") -> str | None:
    """Return E.164 phone or None if empty/invalid."""
    raw = clean_str(value)
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10 and digits.startswith("9"):
        raw = "+7" + digits
    elif len(digits) == 11 and digits[0] in "78":
        raw = "+7" + digits[1:]
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


_EXCEL_EPOCH = datetime(1899, 12, 30)
_FALLBACK_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%y",
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y",
)


def normalize_date(value, date_format: str = "%d.%m.%Y") -> date | None:
    if is_empty(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        if 20000 < value < 80000:  # Excel serial date
            return (_EXCEL_EPOCH + timedelta(days=float(value))).date()
        raise ValueError_("некорректная дата")
    text = clean_str(value)
    for fmt in (date_format, *_FALLBACK_DATE_FORMATS):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError_("некорректная дата")


def normalize_decimal(value) -> Decimal | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        raise ValueError_("некорректное число")
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError_("некорректное число")
        return number.quantize(Decimal("0.01"))
    text = clean_str(value).lower()
    text = re.sub(r"(руб\.?|р\.|₽|р\b)", "", text)
    text = re.sub(r"[\s\u00a0']", "", text)
    if "," in text and "." in text:
        # the last separator is the decimal one: "1.234,50" / "1,234.50"
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(",") == 1:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError_("некорректное число") from exc
    if not number.is_finite():
        raise ValueError_("некорректное число")
    return number.quantize(Decimal("0.01"))


def normalize_int(value) -> int | None:
    if is_empty(value):
        return None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value)
    text = re.sub(r"[^\d,.]", "", clean_str(value))
    text = re.split(r"[,.]", text)[0] if text else ""
    if not text:
        raise ValueError_("некорректное целое число")
    return int(text)


def normalize_year(value) -> int | None:
    try:
        year = normalize_int(value)
    except ValueError_:
        return None
    if year is None or not 1950 <= year <= date.today().year + 1:
        return None
    return year


_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def normalize_vin(value) -> str | None:
    text = re.sub(r"[\s\-]", "", clean_str(value)).upper()
    if not text:
        return None
    return text if _VIN_RE.match(text) else None


_LAT_TO_CYR = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")


def normalize_plate(value) -> str | None:
    text = re.sub(r"[\s\-]", "", clean_str(value)).upper().translate(_LAT_TO_CYR)
    return text[:16] or None


_FALSE_WORDS = {"нет", "0", "false", "no", "-", "отказ", "не выполнено", "невыполнено",
                "отказано", "отклонено", "не выполнена", "отложено", "n"}  # fmt: skip


def normalize_executed(value) -> bool:
    """Default is True: a line counts as executed unless explicitly marked otherwise."""
    if is_empty(value):
        return True
    if isinstance(value, bool):
        return value
    text = clean_str(value).lower()
    return text not in _FALSE_WORDS


def normalize_kind(value) -> str:
    text = clean_str(value).lower()
    if any(w in text for w in ("товар", "запчаст", "деталь", "материал", "part")):
        return "part"
    return "labor"


def normalize_order_status(value) -> str:
    text = clean_str(value).lower()
    if not text:
        return "closed"
    if "отмен" in text or "аннул" in text:
        return "cancelled"
    if any(w in text for w in ("закрыт", "выполнен", "выдан", "оплачен", "завершен", "завершён")):
        return "closed"
    if any(w in text for w in ("открыт", "в работе", "черновик", "ожида")):
        return "open"
    return "closed"


_SPLIT_RE = re.compile(r"[;\n\r]+")


def split_lines(value) -> list[str]:
    if is_empty(value):
        return []
    return [part.strip(" \t-•") for part in _SPLIT_RE.split(str(value)) if part.strip(" \t-•")]
