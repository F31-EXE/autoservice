from datetime import UTC, date, datetime

import pytest

from apps.messaging.backends import ConsoleBackend
from apps.outreach.models import Outreach
from apps.recommendations.models import Recommendation as Rec
from tests.factories import ClientFactory, VehicleFactory, WorkOrderFactory

# 12:00 in Yekaterinburg (UTC+5) — inside the default 10:00–20:00 window
IN_WINDOW = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
OUT_OF_WINDOW = datetime(2026, 6, 15, 17, 0, tzinfo=UTC)  # 22:00 local


@pytest.fixture(autouse=True)
def clean_outbox():
    ConsoleBackend.reset()
    yield
    ConsoleBackend.reset()


@pytest.fixture
def outbox():
    return ConsoleBackend.outbox


@pytest.fixture
def make_outreach(station_a):
    def make(status="approved", channel="sms", est=None, control=False, **client_kw):
        client_kw.setdefault("full_name", "Тестов Иван Петрович")
        client = ClientFactory(station=station_a, **client_kw)
        vehicle = VehicleFactory(client=client, make="Lada", model="Vesta")
        wo = WorkOrderFactory(vehicle=vehicle, opened_at=date(2026, 3, 10))
        rec = Rec.objects.create(
            station=station_a, vehicle=vehicle, source_work_order=wo, source="declined_line",
            work_type_raw="Замена ремня ГРМ", source_text="Замена ремня ГРМ",
            status="scheduled", due_date=date(2026, 6, 1), est_price=est,
        )  # fmt: skip
        o = Outreach.objects.create(
            station=station_a, client=client, vehicle=vehicle, planned_for=date(2026, 6, 15),
            status=status, channel=channel, est_amount=est, is_control_group=control,
        )  # fmt: skip
        o.recommendations.add(rec)
        return o

    return make
