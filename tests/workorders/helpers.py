from pathlib import Path

from django.core.files.base import ContentFile

from apps.workorders.models import ImportBatch, ImportMappingProfile
from apps.workorders.tasks import parse_import_batch

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

FLAT_MAP = {
    "order_number": "Номер ЗН",
    "opened_at": "Дата",
    "client_name": "Клиент",
    "phone": "Телефон",
    "make": "Марка",
    "model": "Модель",
    "year": "Год выпуска",
    "vin": "VIN",
    "plate": "Госномер",
    "mileage": "Пробег",
    "line_kind": "Тип",
    "line_name": "Наименование",
    "line_quantity": "Кол-во",
    "line_price": "Цена",
    "line_amount": "Сумма",
    "executed": "Выполнено",
    "recommendations_text": "Рекомендации",
}

HEADER_MAP = {
    "order_number": "№ заказ-наряда",
    "opened_at": "Дата открытия",
    "closed_at": "Дата закрытия",
    "client_name": "ФИО",
    "phone": "Телефон",
    "client_external_id": "Код клиента",
    "make": "Марка",
    "model": "Модель",
    "vin": "VIN",
    "mileage": "Пробег, км",
    "line_name": "Работы",
    "declined_lines": "Отказы",
    "order_total": "Итого",
    "order_status": "Статус",
    "mechanic_comment": "Комментарий мастера",
}


def flat_profile(station):
    return ImportMappingProfile.objects.create(
        station=station, name="АД", source_system="autodealer", layout="flat", column_map=FLAT_MAP
    )


def header_profile(station):
    return ImportMappingProfile.objects.create(
        station=station,
        name="Альфа",
        source_system="alfa_auto",
        layout="header",
        column_map=HEADER_MAP,
        sheet_name="Заказ-наряды",
    )


def run_import(station, profile, filename, content: bytes | None = None) -> ImportBatch:
    if content is None:
        content = (FIXTURES / filename).read_bytes()
    batch = ImportBatch(
        station=station,
        source_system=profile.source_system,
        mapping_profile=profile,
        original_filename=filename,
    )
    batch.file.save(filename, ContentFile(content), save=True)
    parse_import_batch(batch.pk)
    batch.refresh_from_db()
    return batch
