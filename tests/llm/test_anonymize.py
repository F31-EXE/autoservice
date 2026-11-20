import pytest

from apps.llm.anonymize import anonymize


@pytest.mark.parametrize(
    "text,secret",
    [
        ("Перезвонить клиенту по +7 (912) 345-67-89", "345-67-89"),
        ("тел 89123456789 после 18", "89123456789"),
        ("телефон 9123456789", "9123456789"),
        ("8 343 123-45-67 — рабочий", "123-45-67"),
        ("почта ivan.petrov@mail.ru", "ivan.petrov@mail.ru"),
        ("VIN XTA21099012345678 проверить", "XTA21099012345678"),
        ("vin xta21099012345678", "xta21099012345678"),
        ("гос номер А123ВС196", "А123ВС196"),
        ("госномер a 123 bc 96", "a 123 bc 96"),
        ("Мастер: Петров И.И.", "Петров"),
        ("Принял И.И. Петров", "Петров"),
        ("Клиент Иван Сергеевич просил позвонить", "Иван"),
        ("со слов Сидорова Анна Павловна, стук справа", "Сидорова"),
        ("передал ключи Кузнецов Дмитрий", "Кузнецов"),
    ],
)
def test_pii_removed(text, secret):
    out = anonymize(text)
    assert secret.lower() not in out.lower(), out


def test_known_client_name_removed_in_any_case_and_form():
    out = anonymize("Звонил Тестов, просил передать тестову ответ", ["Тестов Иван Петрович"])
    assert "тестов" not in out.lower()
    assert "[ФИО]" in out


def test_technical_text_is_preserved():
    text = (
        "Колодки пер. 30%, менять через 5-7 т.км. Рекомендована замена ремня ГРМ "
        "и помпы. Течь масла из-под клапанной крышки. АКПП пинается при переключении. "
        "Lada Vesta: подтекает ШРУС. Стойки стабилизатора стучат."
    )
    assert anonymize(text) == text


def test_mileage_and_prices_are_not_phones():
    text = "пробег 123456 км, сумма 15000 руб, через 10000 км, ремень 2024 г."
    assert anonymize(text) == text


def test_empty():
    assert anonymize("") == ""
    assert anonymize(None) == ""


def test_short_name_parts_ignored():
    # names shorter than 3 letters would destroy normal words
    assert anonymize("ок, по ТО", ["Ли Ок"]) == "ок, по ТО"
