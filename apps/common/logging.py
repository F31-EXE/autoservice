"""Log filter that scrubs personal data (phones, e-mails, VINs, plates) from log records.

This is a safety net: code must not log PII in the first place.
"""

import logging
import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # VIN: 17 chars, no I/O/Q
    (re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE), "<vin>"),
    # Russian phone numbers in common notations
    (re.compile(r"(?:\+7|\b8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b"), "<phone>"),
    # Russian plates (Cyrillic or look-alike Latin letters): А123ВС77 / A123BC777
    (
        re.compile(
            r"\b[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}\b",
            re.IGNORECASE,
        ),
        "<plate>",
    ),
]


def scrub(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class PiiScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        record.msg = scrub(message)
        record.args = None
        return True
