import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core import mail

from apps.catalog.models import WorkType
from apps.llm.client import FakeLLMClient, LLMError, cost_rub, model_uri, set_client
from apps.llm.models import LLMBudgetAlert, LLMCall
from apps.recommendations.llm_extract import (
    ExtractionInput,
    build_input,
    extract,
    extract_pending,
    save_extraction,
)
from apps.recommendations.llm_schema import InvalidAnswer, parse_answer
from apps.recommendations.models import Recommendation as Rec
from apps.recommendations.rules import apply_rules
from apps.workorders.models import WorkOrder
from tests.factories import ClientFactory, VehicleFactory, WorkOrderFactory, WorkOrderLineFactory

pytestmark = pytest.mark.django_db

TODAY = date.today()


def answer(*recs):
    return json.dumps({"recommendations": list(recs)}, ensure_ascii=False)


def rec(code="brake_pads_front", text="колодки пер. 30%", kind="future", km=5000, months=None,
        conf=0.9, urgency="safety", raw="передние колодки"):  # fmt: skip
    return {"work_type_code": code, "work_type_raw": raw, "source_text": text, "kind": kind,
            "due_km_delta": km, "due_months_delta": months, "urgency": urgency,
            "confidence": conf}  # fmt: skip


@pytest.fixture
def fake():
    client = FakeLLMClient()
    set_client(client)
    yield client
    set_client(None)


@pytest.fixture
def wo(db):
    client = ClientFactory(full_name="Тестов Иван Петрович", phone="+79000000001")
    vehicle = VehicleFactory(client=client, make="Lada", model="Vesta", year=2019)
    order = WorkOrderFactory(
        vehicle=vehicle,
        opened_at=TODAY - timedelta(days=10),
        mileage=45000,
        recommendations_text="Колодки пер. 30%, менять через 5-7 т.км",
        mechanic_comment="Звонил Тестов по тел. +7 900 000-00-01, VIN XTA00000000000001",
    )
    WorkOrderLineFactory(work_order=order, name="Замена масла ДВС")
    apply_rules([order.id])
    return order


# ---------------------------------------------------------------- parsing


class TestParse:
    def test_valid(self):
        parsed = parse_answer(answer(rec()))
        assert parsed.recommendations[0].work_type_code == "brake_pads_front"

    def test_code_fences_and_chatter(self):
        text = "Вот ответ:\n```json\n" + answer(rec()) + "\n```\nГотово."
        assert len(parse_answer(text).recommendations) == 1

    def test_null_code_strings(self):
        assert parse_answer(answer(rec(code="null"))).recommendations[0].work_type_code is None

    @pytest.mark.parametrize(
        "text",
        ["", "нет рекомендаций", "{not json}", '{"items": []}',
         answer({**rec(), "kind": "maybe"}), answer({**rec(), "confidence": 1.5}),
         answer({**rec(), "source_text": ""}), answer({**rec(), "due_km_delta": -5})],
    )  # fmt: skip
    def test_invalid(self, text):
        with pytest.raises(InvalidAnswer):
            parse_answer(text)


# ---------------------------------------------------------------- pure extraction


def _input(**kw):
    base = {
        "vehicle": "Lada Vesta 2019",
        "mileage": 45000,
        "executed_works": ["Замена масла ДВС"],
        "mechanic_comment": "",
        "recommendations_text": "Колодки пер. 30%, менять через 5-7 т.км",
    }
    base.update(kw)
    return ExtractionInput(**base)


class TestExtract:
    def test_postprocessing(self):
        client = FakeLLMClient(lambda m: answer(
            rec(),
            rec(code="not_a_code", text="колодки пер. 30%", raw="что-то"),  # unknown code → null
            rec(code="engine_oil", text="масло"),  # done in this visit → dropped
            rec(code="timing_belt", text="ремень ГРМ менять", conf=0.95),  # not a quote → 0.3
        ))  # fmt: skip
        result = extract(_input(), client, "m", list(WorkType.objects.all()), {"engine_oil"})
        got = {(r.work_type_code, r.confidence) for r in result.recommendations}
        assert got == {("brake_pads_front", 0.9), (None, 0.9), ("timing_belt", 0.3)}
        assert result.attempts == 1 and not result.error

    def test_prompt_contains_catalog_and_context(self):
        client = FakeLLMClient(lambda m: answer())
        extract(_input(), client, "m", list(WorkType.objects.all()))
        prompt = client.calls[0]["messages"][0]["content"]
        assert "brake_pads_front — Замена передних тормозных колодок" in prompt
        assert "Lada Vesta 2019" in prompt and "45000 км" in prompt
        assert "Замена масла ДВС" in prompt
        assert "Колодки пер. 30%" in prompt

    def test_retry_once_then_fail(self):
        answers = iter(["oops", answer(rec())])
        client = FakeLLMClient(lambda m: next(answers))
        result = extract(_input(), client, "m", list(WorkType.objects.all()))
        assert result.attempts == 2 and not result.error and len(result.recommendations) == 1

        client = FakeLLMClient(lambda m: "still not json")
        result = extract(_input(), client, "m", list(WorkType.objects.all()))
        assert result.attempts == 2 and result.error.startswith("invalid")
        assert len(client.calls) == 2

    def test_llm_error(self):
        client = FakeLLMClient(lambda m: LLMError("APITimeoutError"))
        result = extract(_input(), client, "m", list(WorkType.objects.all()))
        assert result.error == "llm: APITimeoutError" and result.attempts == 1


# ---------------------------------------------------------------- anonymization end-to-end


def test_no_pii_reaches_llm(wo, fake):
    fake.responder = lambda m: answer()
    extract_pending(wo.station_id)
    sent = json.dumps(fake.calls, ensure_ascii=False)
    for secret in ("Тестов", "+7 900 000-00-01", "XTA00000000000001", "Иван", "79000000001"):
        assert secret not in sent
    assert "[ФИО]" in sent and "[ТЕЛЕФОН]" in sent and "[VIN]" in sent


def test_build_input_uses_only_allowed_fields(wo):
    data = build_input(wo)
    assert set(data.to_dict()) == {"vehicle", "mileage", "executed_works", "mechanic_comment",
                                   "recommendations_text"}  # fmt: skip
    assert data.vehicle == "Lada Vesta 2019"


# ---------------------------------------------------------------- persistence


class TestPersistence:
    def test_creates_open_recommendation(self, wo, fake):
        fake.responder = lambda m: answer(rec(text="Колодки пер. 30%, менять через 5-7 т.км"))
        stats = extract_pending(wo.station_id)
        assert stats["done"] == 1 and stats["created"] == 1
        r = Rec.objects.get(work_type__code="brake_pads_front")
        assert r.source == "explicit_field"
        assert r.status == "open"
        assert r.due_km == 50000 and r.due_date is None
        assert r.extractor_version == "extract_v1+"
        wo.refresh_from_db()
        assert wo.extraction_status == "done"

    def test_threshold_and_unknown_type_go_to_review(self, wo, fake):
        fake.responder = lambda m: answer(
            rec(code="timing_belt", text="Звонил", conf=0.65, km=None),
            rec(code=None, text="Колодки пер. 30%", raw="покраска", conf=0.95, km=None),
        )
        extract_pending(wo.station_id)
        statuses = dict(Rec.objects.filter(source__in=["free_text", "explicit_field"])
                        .values_list("work_type_raw", "status"))  # fmt: skip
        assert statuses == {"передние колодки": "needs_review", "покраска": "needs_review"}

    def test_free_text_source_and_default_deadlines(self, wo, fake):
        fake.responder = lambda m: answer(
            rec(code="timing_belt", text="Звонил", kind="future", km=None),
            rec(code="spark_plugs", text="Звонил", kind="declined", km=None, urgency="wear"),
        )
        extract_pending(wo.station_id)
        belt = Rec.objects.get(work_type__code="timing_belt")
        assert belt.source == "free_text"
        assert belt.due_date == wo.visit_date + timedelta(days=90)
        assert Rec.objects.get(work_type__code="spark_plugs").due_date == wo.visit_date + timedelta(
            days=14
        )

    def test_rules_take_precedence_in_same_visit(self, wo, fake):
        # rules already created engine_oil (regular) from this visit; LLM duplicate is ignored
        fake.responder = lambda m: answer(rec(code="engine_oil", text="Колодки пер. 30%"))
        data = build_input(wo)
        from apps.recommendations.llm_extract import ExtractionResult

        result = ExtractionResult(
            recommendations=parse_answer(fake.responder(None)).recommendations
        )
        save_extraction(wo, data, result, "m")
        assert Rec.objects.filter(work_type__code="engine_oil").count() == 1
        assert Rec.objects.get(work_type__code="engine_oil").source == "regular_interval"

    def test_reextraction_is_idempotent_and_respects_rejection(self, wo, fake):
        fake.responder = lambda m: answer(rec(text="Колодки пер. 30%"))
        extract_pending(wo.station_id)
        Rec.objects.filter(work_type__code="brake_pads_front").update(status="rejected")
        WorkOrder.objects.filter(pk=wo.pk).update(extraction_status="pending")
        extract_pending(wo.station_id)
        assert list(Rec.objects.filter(work_type__code="brake_pads_front")
                    .values_list("status", flat=True)) == ["rejected"]  # fmt: skip

    def test_closed_by_later_visit(self, wo, fake):
        later = WorkOrderFactory(vehicle=wo.vehicle, opened_at=TODAY - timedelta(days=2))
        WorkOrderLineFactory(work_order=later, name="Замена передних тормозных колодок")
        apply_rules([later.id])
        fake.responder = lambda m: answer(rec(text="Колодки пер. 30%"))
        extract_pending(wo.station_id)
        r = Rec.objects.get(work_type__code="brake_pads_front")
        assert r.status == "done" and r.closed_by_work_order == later

    def test_failed_answer_marks_failed(self, wo, fake):
        fake.responder = lambda m: "garbage"
        stats = extract_pending(wo.station_id)
        wo.refresh_from_db()
        assert stats["failed"] == 1 and wo.extraction_status == "failed"
        calls = LLMCall.objects.all()
        assert calls.count() == 2 and all(c.payload is None for c in calls)
        assert not calls.last().success or not calls.first().success


class TestOrchestration:
    def test_skips_without_text_old_and_open_orders(self, wo, fake):
        v = wo.vehicle
        empty = WorkOrderFactory(vehicle=v, opened_at=TODAY)
        old = WorkOrderFactory(
            vehicle=v, opened_at=TODAY - timedelta(days=900), mechanic_comment="x"
        )
        opened = WorkOrderFactory(vehicle=v, opened_at=TODAY, status="open", mechanic_comment="x")
        stats = extract_pending(wo.station_id)
        assert stats["skipped"] == 3
        for order in (empty, old, opened):
            order.refresh_from_db()
            assert order.extraction_status == "skipped"
        assert len(fake.calls) == 1

    def test_concurrency_batches(self, wo, fake, settings):
        settings.LLM_CONCURRENCY = 3
        for i in range(7):
            WorkOrderFactory(vehicle=wo.vehicle, opened_at=TODAY - timedelta(days=i),
                             mechanic_comment="Колодки пер. 30%")  # fmt: skip
        fake.responder = lambda m: answer()
        stats = extract_pending(wo.station_id)
        assert stats["done"] == 8 and len(fake.calls) == 8
        assert not WorkOrder.objects.filter(extraction_status="pending").exists()

    def test_batch_limit_reports_more(self, wo, fake, settings):
        settings.LLM_CONCURRENCY = 1
        WorkOrderFactory(vehicle=wo.vehicle, opened_at=TODAY, mechanic_comment="x")
        stats = extract_pending(wo.station_id, limit=1)
        assert stats["done"] == 1 and stats.get("more") == 1

    def test_cost_accounting(self, wo, fake, settings):
        settings.LLM_PRICE_PER_1K_INPUT_RUB = 0.4
        settings.LLM_PRICE_PER_1K_OUTPUT_RUB = 0.4
        fake.responder = lambda m: answer()
        extract_pending(wo.station_id)
        call = LLMCall.objects.get()
        assert call.purpose == "extract" and call.prompt_version == "extract_v1"
        assert call.input_tokens > 0 and call.cost_rub > 0
        assert call.station_id == wo.station_id

    def test_budget_pauses_and_alerts_once(self, wo, fake, settings, django_user_model):
        settings.LLM_MONTHLY_BUDGET_RUB = 10
        django_user_model.objects.create_user("root", "root@example.com", "x", role="superadmin")
        LLMCall.objects.create(station_id=wo.station_id, purpose="extract", model="m",
                               prompt_version="v", cost_rub=Decimal("10"))  # fmt: skip
        stats = extract_pending(wo.station_id)
        assert stats.get("budget_exceeded") == 1
        assert len(fake.calls) == 0
        wo.refresh_from_db()
        assert wo.extraction_status == "pending"  # paused, not failed
        assert LLMBudgetAlert.objects.count() == 1
        assert len(mail.outbox) == 1 and "бюджет LLM" in mail.outbox[0].subject
        extract_pending(wo.station_id)
        assert len(mail.outbox) == 1  # once per month

    def test_payload_logging_flag(self, wo, fake, settings):
        settings.LLM_LOG_PAYLOADS = True
        fake.responder = lambda m: answer()
        extract_pending(wo.station_id)
        payload = LLMCall.objects.get().payload
        assert "Тестов" not in json.dumps(payload, ensure_ascii=False)
        assert payload["input"]["mileage"] == 45000


def test_import_triggers_extraction(station_a, fake, django_capture_on_commit_callbacks):
    from tests.workorders.helpers import flat_profile, run_import

    station_a.settings = {"recommendation_ttl_days": 100000}
    station_a.save()
    fake.responder = lambda m: answer(rec(text="Колодки передние 30%"))
    with django_capture_on_commit_callbacks(execute=True):
        run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    # ЗН-1001 has a recommendations text; the others have none → skipped
    assert len(fake.calls) == 1
    assert Rec.objects.filter(source="explicit_field", work_type__code="brake_pads_front").exists()


def test_helpers(settings):
    settings.LLM_FOLDER_ID = "b1gfolder"
    assert model_uri("yandexgpt") == "gpt://b1gfolder/yandexgpt/latest"
    assert model_uri("qwen3-235b-a22b-fp8/latest") == "gpt://b1gfolder/qwen3-235b-a22b-fp8/latest"
    assert model_uri("gpt://x/y/latest") == "gpt://x/y/latest"
    settings.LLM_PRICE_PER_1K_INPUT_RUB = 0.4
    settings.LLM_PRICE_PER_1K_OUTPUT_RUB = 0.8
    assert cost_rub(2000, 500) == Decimal("1.2000")


def test_real_client_requires_key(settings):
    from apps.llm.client import OpenAICompatibleClient

    settings.LLM_API_KEY = ""
    with pytest.raises(LLMError):
        OpenAICompatibleClient()


def test_not_configured_leaves_orders_pending(wo, settings):
    settings.LLM_BACKEND = "openai"
    settings.LLM_API_KEY = ""
    stats = extract_pending(wo.station_id)
    assert stats["not_configured"] == 1
    wo.refresh_from_db()
    assert wo.extraction_status == "pending"
