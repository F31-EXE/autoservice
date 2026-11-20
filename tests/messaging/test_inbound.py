from decimal import Decimal

import pytest

from apps.llm.client import FakeLLMClient, set_client
from apps.messaging.intent import classify, is_stop
from apps.messaging.models import BookingRequest, Message
from apps.messaging.services import find_client_by_phone, handle_incoming_text, send_touch
from apps.outreach.models import Outreach
from tests.messaging.conftest import IN_WINDOW

pytestmark = pytest.mark.django_db


@pytest.fixture
def sent(make_outreach):
    def make(**kw):
        o = make_outreach(**kw)
        send_touch(o.pk, IN_WINDOW)
        o.refresh_from_db()
        return o

    return make


@pytest.mark.parametrize("text", ["СТОП", "стоп", "Stop", " отписаться! ", "Отписка"])
def test_stop_words(text):
    assert is_stop(text)


@pytest.mark.parametrize("text", ["не стоп", "стоп-сигнал не горит", "остановка"])
def test_not_stop(text):
    assert not is_stop(text)


def test_stop_cancels_pending_reminders(sent, make_outreach):
    o = sent()
    pending = make_outreach(status="awaiting_approval")
    pending.client = o.client
    pending.save()
    handle_incoming_text(o.client, "sms", "СТОП")
    pending.refresh_from_db()
    assert pending.status == "cancelled"
    assert o.client.unsubscribed_at is not None


@pytest.mark.parametrize(
    "text,intent",
    [
        ("Давайте в пятницу после 18", "book"),
        ("Уже поменял в другом сервисе", "already_done"),
        ("Машину продал", "sold_car"),
        ("Больше не пишите мне", "unsubscribe"),
    ],
)
def test_intents_and_actions(sent, outbox, text, intent):
    o = sent()
    msg = handle_incoming_text(o.client, "sms", text, "id-1")
    assert msg.intent == intent and msg.handled_by_ai and not msg.needs_attention
    o.refresh_from_db()
    if intent == "book":
        assert BookingRequest.objects.get().preferred_time_text
    if intent == "already_done":
        assert o.recommendations.get().status == "done"
        assert o.recommendations.get().closed_by_work_order is None
    if intent == "sold_car":
        assert o.vehicle.is_sold
    if intent == "unsubscribe":
        o.client.refresh_from_db()
        assert o.client.unsubscribed_at
    assert outbox[-1].to == o.client.phone  # an auto reply went out


def test_price_question(sent, outbox):
    o = sent(est=Decimal("4500"))
    handle_incoming_text(o.client, "sms", "А сколько это будет стоить?")
    assert "Ориентировочно от 4500 ₽" in outbox[-1].text


def test_price_question_without_estimate_goes_to_manager(sent, outbox):
    o = sent()
    count = len(outbox)
    msg = handle_incoming_text(o.client, "sms", "Сколько стоит?")
    assert msg.intent == "price_question" and msg.needs_attention and not msg.handled_by_ai
    assert len(outbox) == count  # no auto reply


def test_other_goes_to_manager(sent, outbox):
    o = sent()
    count = len(outbox)
    msg = handle_incoming_text(o.client, "sms", "Кто это?")
    assert msg.needs_attention and msg.intent == "other"
    assert len(outbox) == count
    o.refresh_from_db()
    assert o.status == "replied"


def test_low_confidence_and_llm_errors_go_to_manager(sent):
    o = sent()
    set_client(FakeLLMClient(lambda m: '{"intent": "book", "confidence": 0.4}'))
    try:
        assert handle_incoming_text(o.client, "sms", "ну может").needs_attention
        set_client(FakeLLMClient(lambda m: "garbage"))
        assert handle_incoming_text(o.client, "sms", "хм").needs_attention
    finally:
        set_client(None)
    assert not BookingRequest.objects.exists()


def test_ai_cannot_invent_time(sent):
    o = sent()
    set_client(FakeLLMClient(
        lambda m: '{"intent": "book", "confidence": 0.95, "preferred_time_text": "в 10:00"}'
    ))  # fmt: skip
    try:
        handle_incoming_text(o.client, "sms", "Запишите меня")
    finally:
        set_client(None)
    assert BookingRequest.objects.get().preferred_time_text == ""


def test_intent_prompt_is_anonymized(sent):
    o = sent()
    fake = FakeLLMClient(lambda m: '{"intent": "other", "confidence": 0.9}')
    set_client(fake)
    try:
        classify("Тестов на связи, мой номер +7 912 345-67-89", "Иван, …", o.client.station_id,
                 ["Тестов Иван Петрович"])  # fmt: skip
    finally:
        set_client(None)
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "Тестов" not in prompt and "345-67-89" not in prompt


def test_find_client_by_phone_prefers_recent_contact(sent, make_outreach, station_b):
    from tests.factories import ClientFactory

    o = sent()
    ClientFactory(station=station_b, phone=o.client.phone)  # same phone, other station, no contact
    assert find_client_by_phone(o.client.phone) == o.client
    assert find_client_by_phone("+79999999999") is None


def test_inbound_without_outreach_goes_to_manager(station_a):
    from tests.factories import ClientFactory

    client = ClientFactory(station=station_a)
    msg = handle_incoming_text(client, "sms", "Здравствуйте, вопрос")
    assert msg.outreach is None and msg.needs_attention


def test_messages_are_tenant_scoped(sent, manager_a, station_b):
    sent()
    assert Message.objects.for_user(manager_a).exists()
    from tests.factories import UserFactory

    stranger = UserFactory(organization=station_b.organization, stations=[station_b])
    assert not Message.objects.for_user(stranger).exists()
    assert not Outreach.objects.for_user(stranger).exists()
