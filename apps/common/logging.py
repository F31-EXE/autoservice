"""Log filter that scrubs personal data (phones, e-mails, VINs, plates) from log records.

This is a safety net: code must not log PII in the first place.
"""

import logging
import re

from . import pii

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (pii.EMAIL, "<email>"),
    (pii.VIN, "<vin>"),
    (pii.PHONE, "<phone>"),
    (pii.PLATE, "<plate>"),
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
