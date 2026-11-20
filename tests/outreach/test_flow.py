from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command

from apps.outreach.models import Outreach
from apps.outreach.planner import build_queue
from apps.recommendations.models import Recommendation as Rec
from tests.workorders.helpers import flat_profile, run_import

pytestmark = pytest.mark.django_db


def test_import_rules_queue(station_a):
    station_a.settings = {"recommendation_ttl_days": 100000, "control_group_share": 0}
    station_a.save()
    run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    # planned_for is filled right after import
    assert (
        not Rec.objects.filter(status="open", planned_for__isnull=True)
        .exclude(due_date__isnull=True, due_km__isnull=True)
        .exists()
    )

    stats = build_queue(station_a, date(2025, 1, 5))
    vesta = Outreach.objects.get(vehicle__vin="XTA00000000000001")
    titles = sorted(r.work_type.code for r in vesta.recommendations.all())
    # overdue declined timing belt + oil change due 15.01.2025 → one merged reminder
    assert titles == ["engine_oil", "timing_belt"]
    assert vesta.status == "awaiting_approval"
    assert stats["awaiting_approval"] >= 1

    # a later visit where the belt and oil were done cancels the pending reminder
    content = (
        "Номер ЗН;Дата;Клиент;Телефон;Марка;Модель;Год выпуска;VIN;Госномер;Пробег;Тип;"
        "Наименование;Кол-во;Цена;Сумма;Выполнено;Рекомендации\n"
        "ЗН-2001;06.01.2025;Тестов Иван Петрович;89000000001;Lada;Vesta;2019;XTA00000000000001;"
        ";60000;Работа;Замена ремня ГРМ;1;5000;5000;да;\n"
        "ЗН-2001;06.01.2025;Тестов Иван Петрович;89000000001;Lada;Vesta;2019;XTA00000000000001;"
        ";60000;Работа;Замена масла ДВС;1;800;800;да;\n"
    ).encode()
    run_import(station_a, flat_profile_reuse(station_a), "next.csv", content)
    vesta.refresh_from_db()
    assert vesta.status == "cancelled"


def flat_profile_reuse(station):
    from apps.workorders.models import ImportMappingProfile

    return ImportMappingProfile.objects.get(station=station)


def test_build_queue_command(station_a):
    out = StringIO()
    call_command("build_queue", station=station_a.id, date="2025-01-05", stdout=out)
    assert "planned_updated" in out.getvalue()
