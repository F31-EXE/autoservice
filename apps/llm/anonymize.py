"""Remove personal data from text before it is sent to an LLM (spec 6.2).

Client.full_name, phone, vin and plate must never reach the LLM. Replaced with placeholders
so that the text stays readable for the model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from apps.common import pii

PH_PHONE = "[ТЕЛЕФОН]"
PH_EMAIL = "[EMAIL]"
PH_VIN = "[VIN]"
PH_PLATE = "[ГОСНОМЕР]"
PH_NAME = "[ФИО]"

_UP = "А-ЯЁ"
_LO = "а-яё"
_WORD = rf"[{_UP}][{_LO}]+(?:-[{_UP}][{_LO}]+)?"
_INITIALS = rf"[{_UP}]\.\s?(?:[{_UP}]\.)?"

# "Иванов И.И." / "Иванов И. И." / "И.И. Иванов"
_INITIALS_SURNAME = re.compile(rf"(?<![{_UP}{_LO}]){_INITIALS}\s?{_WORD}")
_SURNAME_INITIALS = re.compile(rf"\b{_WORD}\s+{_INITIALS}")
# "Иван Иванович", "Иванов Иван Иванович" — the last word is a patronymic
_PATRONYMIC = re.compile(
    rf"\b(?:{_WORD}\s+){{1,2}}[{_UP}][{_LO}]+(?:ович|евич|ич|овна|евна|ична|инична)\b"
)
# two or three consecutive capitalized words that are not at a sentence start
_CAPITALIZED_RUN = re.compile(rf"(?<=[{_LO},:;)]\s){_WORD}(?:\s+{_WORD}){{1,2}}\b")

# Words that are often capitalized in work order texts but are not names
_NOT_NAMES = {
    "лада", "веста", "гранта", "приора", "нива", "калина", "ларгус", "газель", "соболь",
    "уаз", "патриот", "мастер", "клиент", "клиенту", "клиента", "рекомендуется",
}  # fmt: skip


def _name_parts(names: Iterable[str]) -> list[str]:
    parts = set()
    for name in names:
        for part in re.split(r"[\s.]+", name or ""):
            if len(part) >= 3 and part.lower() not in _NOT_NAMES:
                parts.add(part)
    return sorted(parts, key=len, reverse=True)


def _replace_run(match: re.Match[str]) -> str:
    words = match.group(0).split()
    if any(w.lower() in _NOT_NAMES for w in words):
        return match.group(0)
    return PH_NAME


def anonymize(text: str, known_names: Iterable[str] = ()) -> str:
    """Return text without phones, e-mails, VINs, plates and person names."""
    if not text:
        return ""
    text = pii.EMAIL.sub(PH_EMAIL, text)
    text = pii.VIN.sub(PH_VIN, text)
    text = pii.PHONE.sub(PH_PHONE, text)
    text = pii.PLATE.sub(PH_PLATE, text)
    for part in _name_parts(known_names):
        text = re.sub(rf"(?<![\w]){re.escape(part)}\w{{0,3}}(?![\w])", PH_NAME, text, flags=re.I)
    text = _PATRONYMIC.sub(PH_NAME, text)
    text = _INITIALS_SURNAME.sub(PH_NAME, text)
    text = _SURNAME_INITIALS.sub(PH_NAME, text)
    text = _CAPITALIZED_RUN.sub(_replace_run, text)
    text = re.sub(rf"(?:{re.escape(PH_NAME)}\s*)+", PH_NAME + " ", text)
    return re.sub(r" +([.,;:!?)])", r"\1", text).strip()
