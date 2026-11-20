import logging

from apps.common.logging import PiiScrubFilter, scrub


def test_scrub_masks_pii():
    text = "Клиент +7 (912) 345-67-89, 89123456789, a@b.ru, VIN XTA21099012345678, А123ВС196"
    out = scrub(text)
    for secret in ["912", "89123456789", "a@b.ru", "XTA21099012345678", "А123ВС196"]:
        assert secret not in out


def test_filter_rewrites_record():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "phone %s", ("+79123456789",), None)
    PiiScrubFilter().filter(record)
    assert record.getMessage() == "phone <phone>"
