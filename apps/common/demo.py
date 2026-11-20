"""Demo data for the cabinet (``manage.py load_demo``).

Everything goes through the real pipeline — import, rules, LLM extraction, queue, sending,
replies, a second import with visits, attribution — with offline stand-ins for the outside
world: the heuristic LLM and the console message backend. Dates are relative to today, so the
cabinet always looks "alive". All people, phones, VINs and plates are made up.
"""

from __future__ import annotations

import random
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.core.files.base import ContentFile
from django.db import transaction

DEMO_ORG = "Демо-сеть автосервисов"

MALE = [("Иванов", "Сергей", "Петрович"), ("Смирнов", "Алексей", "Иванович"),
        ("Кузнецов", "Дмитрий", "Олегович"), ("Попов", "Андрей", "Николаевич"),
        ("Васильев", "Максим", "Сергеевич"), ("Соколов", "Игорь", "Владимирович"),
        ("Михайлов", "Павел", "Андреевич"), ("Новиков", "Роман", "Юрьевич"),
        ("Фёдоров", "Евгений", "Викторович"), ("Морозов", "Артём", "Денисович"),
        ("Волков", "Николай", "Александрович"), ("Лебедев", "Кирилл", "Михайлович")]  # fmt: skip
FEMALE = [("Петрова", "Анна", "Сергеевна"), ("Козлова", "Мария", "Алексеевна"),
          ("Новикова", "Елена", "Игоревна"), ("Морозова", "Ольга", "Викторовна"),
          ("Волкова", "Наталья", "Павловна"), ("Соловьёва", "Ирина", "Андреевна")]  # fmt: skip
CARS = [("Lada", "Vesta"), ("Lada", "Granta"), ("Kia", "Rio"), ("Hyundai", "Solaris"),
        ("Toyota", "Camry"), ("Skoda", "Octavia"), ("Volkswagen", "Polo"), ("Renault", "Logan"),
        ("Haval", "Jolion"), ("Chery", "Tiggo 7 Pro"), ("Nissan", "Qashqai"), ("Kia", "Sportage")]  # fmt: skip
EXECUTED = [("Замена масла ДВС", 1500), ("Замена воздушного фильтра", 600),
            ("Замена салонного фильтра", 500), ("Диагностика ходовой", 1000),
            ("Замена передних тормозных колодок", 3500), ("Шиномонтаж R16 комплекс", 2400),
            ("Замена свечей зажигания", 1800), ("Развал-схождение", 2000),
            ("Заправка кондиционера", 2500)]  # fmt: skip
DECLINED = [("Замена ремня ГРМ", 6500), ("Замена стоек стабилизатора", 2800),
            ("Замена задних тормозных колодок", 3200), ("Замена тормозной жидкости", 1800),
            ("Замена передних амортизаторов", 7500), ("Замена антифриза", 2200),
            ("Замена подшипника ступицы передней", 4200), ("Замена комплекта сцепления", 24000),
            ("Покраска бампера", 9000), ("Ремонт обивки сиденья", 3500)]  # fmt: skip
COMMENTS = ["Колодки пер. 30%, менять через 5 т.км",
            "Рекомендуется замена ремня ГРМ через 10 тыс. км",
            "Течь масла из-под клапанной крышки, рекомендована замена прокладки",
            "Шаровые опоры люфт, замена в ближайшее время", "АКБ слабый, рекомендована замена",
            "", "", "", "", ""]  # fmt: skip
REPLIES = ["Давайте в субботу утром", "Можно завтра после 18?", "Уже поменял в другом сервисе",
           "Сколько это будет стоить?", "Машину продал", "А вы кто?", "Перезвоните мне",
           "Спасибо, подумаю", "СТОП"]  # fmt: skip
HEADER = ("Номер;Дата;Клиент;Телефон;Марка;Модель;Год;VIN;Госномер;Пробег;Тип;Работа;Выполнено;"
          "Сумма;Итого;Рекомендации\n")  # fmt: skip
COLUMN_MAP = {
    "order_number": "Номер", "opened_at": "Дата", "client_name": "Клиент", "phone": "Телефон",
    "make": "Марка", "model": "Модель", "year": "Год", "vin": "VIN", "plate": "Госномер",
    "mileage": "Пробег", "line_kind": "Тип", "line_name": "Работа", "executed": "Выполнено",
    "line_amount": "Сумма", "order_total": "Итого", "recommendations_text": "Рекомендации",
}  # fmt: skip
VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
PLATE_LETTERS = "АВЕКМНОРСТУХ"


@dataclass
class DemoClient:
    n: int
    name: str
    phone: str
    make: str
    model: str
    year: int
    vin: str
    plate: str
    mileage: int
    km_per_day: int
    last_visit: date = None
    rows: list[str] = field(default_factory=list)


def _csv_row(wo, day, c, kind, work, done, amount, total="", recs=""):
    return (f"{wo};{day:%d.%m.%Y};{c.name};{c.phone};{c.make};{c.model};{c.year};{c.vin};"
            f"{c.plate};{c.mileage};{kind};{work};{done};{amount};{total};{recs}\n")  # fmt: skip


def _local_noon(station, day: date) -> datetime:
    return datetime.combine(day, time(12, 0), tzinfo=station.tz)


def remove_demo() -> bool:
    from apps.accounts.models import Organization, User
    from apps.workorders.models import ImportBatch

    org = Organization.objects.filter(name=DEMO_ORG).first()
    if org is None:
        return False
    for batch in ImportBatch.objects.filter(station__organization=org).exclude(file=""):
        batch.file.delete(save=False)
    User.objects.filter(organization=org).delete()
    org.delete()
    return True


def build_demo(clients: int = 80, password: str = "demo12345", seed: int = 7, today=None) -> dict:
    from apps.accounts.models import Organization, Role, ServiceStation, User
    from apps.llm.client import FakeLLMClient, set_client
    from apps.messaging.backends import ConsoleBackend
    from apps.messaging.intent import INTENT_MARKER, heuristic_responder
    from apps.recommendations.llm_mock import HeuristicResponder

    rnd = random.Random(seed)
    with ExitStack() as stack:
        # Never reach real LLMs / SMS / MAX and don't hand work to a Celery worker. The patches
        # wrap the transaction, so on_commit callbacks also fire while they are active.
        for target in ("apps.messaging.services.get_backend",
                       "apps.messaging.max_updates.get_backend"):  # fmt: skip
            stack.enter_context(mock.patch(target, lambda channel: ConsoleBackend(channel)))
        stack.enter_context(mock.patch("apps.recommendations.tasks.extract_pending_task.delay"))
        stack.enter_context(mock.patch("apps.messaging.tasks.send_touch_task.delay"))
        stack.enter_context(transaction.atomic())

        remove_demo()
        org = Organization.objects.create(name=DEMO_ORG)
        station = ServiceStation.objects.create(
            organization=org, name="Мотор на Ленина", city="Екатеринбург",
            timezone="Asia/Yekaterinburg", sender_name="СТО «Мотор»", phone="+73430000000",
            booking_url="https://motor.example/zapis",
        )  # fmt: skip
        today = today or station.local_today()
        owner = User.objects.create_user("demo", password=password, organization=org,
                                         role=Role.OWNER, first_name="Владелец")  # fmt: skip
        manager = User.objects.create_user("manager", password=password, organization=org,
                                           role=Role.MANAGER, first_name="Администратор")  # fmt: skip
        manager.stations.set([station])

        extraction = HeuristicResponder()

        def route(messages):
            content = messages[-1]["content"]
            if INTENT_MARKER in content:
                return heuristic_responder(messages)
            return extraction(messages)

        set_client(FakeLLMClient(route))
        stack.callback(set_client, None)
        return _populate(station, owner, manager, clients, rnd, today)


def _make_clients(n: int, rnd: random.Random) -> list[DemoClient]:
    people = []
    for i in range(1, n + 1):
        surname, first, middle = rnd.choice(MALE if rnd.random() < 0.75 else FEMALE)
        make, model = rnd.choice(CARS)
        plate = (f"{rnd.choice(PLATE_LETTERS)}{rnd.randint(100, 999)}"
                 f"{rnd.choice(PLATE_LETTERS)}{rnd.choice(PLATE_LETTERS)}{rnd.choice([96, 196, 66])}")  # fmt: skip
        people.append(DemoClient(
            n=i, name=f"{surname} {first} {middle}", phone=f"+7900{i:07d}", make=make,
            model=model, year=rnd.randint(2012, 2023),
            vin="X" + "".join(rnd.choice(VIN_CHARS) for _ in range(16)), plate=plate,
            mileage=rnd.randint(20_000, 180_000), km_per_day=rnd.randint(20, 90),
        ))  # fmt: skip
    return people


def _history_csv(people, rnd, today) -> str:
    rows = []
    for c in people:
        # an older service visit, then the last visit with declined works / comments
        first_day = today - timedelta(days=rnd.randint(330, 520))
        last_day = today - timedelta(days=rnd.choice([rnd.randint(5, 24), rnd.randint(30, 200)]))
        c.mileage -= c.km_per_day * (last_day - first_day).days
        c.mileage = max(c.mileage, 5_000)
        rows.append(_csv_row(f"Д-{c.n}-1", first_day, c, "Работа", "Замена масла ДВС", "да",
                             1500, 1500))  # fmt: skip
        c.mileage += c.km_per_day * (last_day - first_day).days
        works = rnd.sample(EXECUTED, rnd.randint(1, 3))
        declined = rnd.sample(DECLINED, rnd.choice([0, 1, 1, 2]))
        total = sum(p for _, p in works)
        comment = rnd.choice(COMMENTS)
        for i, (work, price) in enumerate(works):
            rows.append(_csv_row(f"Д-{c.n}-2", last_day, c, "Работа", work, "да", price, total,
                                 comment if i == 0 else ""))  # fmt: skip
        for work, price in declined:
            rows.append(_csv_row(f"Д-{c.n}-2", last_day, c, "Работа", work, "нет", price, total))
        c.last_visit = last_day
    return HEADER + "".join(rows)


def _import(station, profile, owner, name: str, content: str):
    from apps.workorders.models import ImportBatch
    from apps.workorders.tasks import parse_import_batch

    batch = ImportBatch(station=station, uploaded_by=owner, source_system=profile.source_system,
                        mapping_profile=profile, original_filename=name)  # fmt: skip
    batch.file.save(name, ContentFile(content.encode("utf-8-sig")), save=True)
    parse_import_batch(batch.pk)
    batch.refresh_from_db()
    return batch


def _populate(station, owner, manager, n, rnd, today) -> dict:
    from apps.customers.models import Client
    from apps.messaging.models import BookingRequest
    from apps.messaging.services import handle_incoming_text, send_touch
    from apps.outreach.models import Outreach
    from apps.outreach.planner import build_queue
    from apps.recommendations.llm_extract import extract_pending
    from apps.workorders.models import ImportMappingProfile

    people = _make_clients(n, rnd)
    profile = ImportMappingProfile.objects.create(
        station=station, name="АвтоДилер — основной", source_system="autodealer",
        column_map=COLUMN_MAP,
    )  # fmt: skip

    # 1) history export → rules; free-text extraction with the offline LLM
    _import(station, profile, owner, "autodealer-history.csv", _history_csv(people, rnd, today))
    extract_pending(station.id, limit=10_000)

    # some clients already use the MAX bot
    all_clients = list(Client.objects.filter(station=station).order_by("id"))
    for client in rnd.sample(all_clients, n // 6):
        client.max_user_id = 900_000 + client.pk
        client.save(update_fields=["max_user_id"])

    # 2) a campaign 25 days ago: queue, approval, sending
    campaign_day = today - timedelta(days=25)
    build_queue(station, campaign_day)
    sent_at = _local_noon(station, campaign_day)
    sent = []
    for outreach in Outreach.objects.filter(station=station, status="awaiting_approval"):
        channel = "max" if outreach.client.max_user_id else "sms"
        if outreach.needs_call:
            channel = "manual_call"
        Outreach.objects.filter(pk=outreach.pk).update(
            status="approved", channel=channel, approved_by=manager, approved_at=sent_at
        )
        if channel != "manual_call" and send_touch(outreach.pk, sent_at) == "sent":
            sent.append(outreach)

    # 3) client replies (offline intent heuristic)
    for i, outreach in enumerate(rnd.sample(sent, min(len(sent), max(6, len(sent) // 3)))):
        reply = REPLIES[i % len(REPLIES)]
        channel = "max" if outreach.client.max_user_id else "sms"
        handle_incoming_text(outreach.client, channel, reply, f"demo-in-{outreach.pk}")
    for booking in BookingRequest.objects.filter(client__station=station)[:2]:
        booking.status = "booked"
        booking.assigned_to = manager
        booking.save(update_fields=["status", "assigned_to"])
        if booking.outreach:
            Outreach.objects.filter(pk=booking.outreach_id).update(status="booked")

    # 4) second export: visits after the campaign (more often for those who got a message)
    by_phone = {c.phone: c for c in people}
    rows = []
    for outreach in Outreach.objects.filter(station=station, planned_for=campaign_day):
        if outreach.status in ("skipped", "cancelled"):
            continue
        chance = 0.22 if outreach.is_control_group else 0.45
        if outreach.status == "booked":
            chance = 0.95
        if rnd.random() >= chance:
            continue
        c = by_phone[outreach.client.phone]
        day = campaign_day + timedelta(days=rnd.randint(2, 22))
        c.mileage += c.km_per_day * (day - c.last_visit).days
        works = [r.source_text or r.title for r in outreach.recommendations.all()][:2] or [
            "Диагностика ходовой"
        ]
        total = rnd.choice([3500, 5200, 7800, 9600, 12400, 18000])
        for work in works:
            rows.append(_csv_row(f"В-{c.n}", day, c, "Работа", work, "да",
                                 total // len(works), total))  # fmt: skip
    _import(station, profile, owner, "autodealer-visits.csv", HEADER + "".join(rows))

    # 5) today's queue: new reminders and second touches waiting for approval
    build_queue(station, today)

    return {
        "station": station.name,
        "clients": Client.objects.filter(station=station).count(),
        "to_approve": Outreach.objects.filter(station=station, status="awaiting_approval").count(),
        "sent": len(sent),
        "visited": Outreach.objects.filter(station=station, status="visited").count(),
        "bookings": BookingRequest.objects.filter(client__station=station).count(),
    }


def load(clients: int = 80, password: str = "demo12345", seed: int = 7) -> dict:
    return build_demo(clients=clients, password=password, seed=seed)
