"""Shared regexes for personal data (used by log scrubbing and LLM anonymization)."""

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# VIN: 17 chars, no I/O/Q; must contain at least one digit to avoid long words
VIN = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*\d)[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
# Russian phones: +7 / 8 / 7 prefix with any separators, or bare 10 digits starting with 9
PHONE = re.compile(
    r"(?<!\d)(?:(?:\+7|8|7)[\s\-()]*)?\(?9\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
    r"|(?<!\d)(?:\+7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)
_PLATE_LETTERS = "АВЕКМНОРСТУХABEKMHOPCTYX"
PLATE = re.compile(
    rf"(?<![\wА-Яа-яЁё])[{_PLATE_LETTERS}]\s?\d{{3}}\s?[{_PLATE_LETTERS}]{{2}}\s?\d{{2,3}}(?![\wА-Яа-яЁё])",
    re.IGNORECASE,
)
