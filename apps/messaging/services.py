"""Sending reminders, replying, and acting on client answers (spec 8). Deterministic code;
the LLM only classifies free-text replies (intent.py)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.outreach.models import Outreach
from apps.outreach.planner import release_recommendations
from apps.outreach.texts import compose_text, default_channel
from apps.recommendations.models import Recommendation

from .backends import Button, SendError, get_backend
from .models import BookingRequest, Channel, Intent, Message

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3
OUTREACH_BUTTONS = [("book", "Записаться"), ("done", "Уже сделал"), ("nr", "Неактуально")]

REPLY_BOOK = "Спасибо! Передали администратору — он свяжется с вами, чтобы подтвердить время."
REPLY_DONE = "Спасибо, что сообщили! Отметили, что работы уже выполнены."
REPLY_SOLD = "Спасибо, что сообщили! Больше не будем беспокоить по этому автомобилю."
REPLY_NOT_RELEVANT = "Поняли, спасибо! Не будем напоминать об этих работах."
REPLY_PRICE = "Ориентировочно от {amount} ₽, точная стоимость — после осмотра. {cta}"
REPLY_UNSUBSCRIBED = "Вы отписались от уведомлений {sender}. Больше писать не будем."


# ---------------------------------------------------------------- send window


def _parse(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_send_window(station, now=None) -> bool:
    start, end = (_parse(v) for v in station.conf["send_window"])
    local = (now or timezone.now()).astimezone(station.tz).time()
    return start <= local < end


# ---------------------------------------------------------------- low level send


def _recipient(client, channel: str) -> str | None:
    if channel == Channel.MAX:
        return str(client.max_user_id) if client.max_user_id else None
    return client.phone


def deliver(message: Message, buttons: list[Button] | None = None) -> bool:
    """Send an already saved Message (status sending) through its channel backend."""
    to = _recipient(message.client, message.channel)
    if not to:
        result_ok, provider_id, error = False, "", "no recipient"
    else:
        try:
            result = get_backend(message.channel).send(to, message.text, buttons)
            result_ok, provider_id, error = result.ok, result.provider_message_id, result.error
        except SendError as exc:
            result_ok, provider_id, error = False, "", str(exc)
    message.status = Message.Status.SENT if result_ok else Message.Status.FAILED
    message.provider_message_id = provider_id[:128]
    message.error = error[:255]
    message.save(update_fields=["status", "provider_message_id", "error", "updated_at"])
    if not result_ok:
        logger.warning("Message %s failed: %s", message.pk, message.error)
    return result_ok


def outreach_buttons(outreach: Outreach) -> list[Button]:
    return [Button(text, f"{action}:{outreach.pk}") for action, text in OUTREACH_BUTTONS]


def send_reply(client, channel: str, text: str, outreach=None, user=None) -> Message:
    """Answer within a conversation (not a reminder touch): no send window, no touch number."""
    if channel == Channel.MANUAL_CALL or not _recipient(client, channel):
        channel = default_channel(client) or Channel.SMS
    message = Message.objects.create(
        client=client, outreach=outreach, channel=channel, direction=Message.Direction.OUT,
        text=text, status=Message.Status.SENDING, attempts=1, sent_by=user,
    )  # fmt: skip
    deliver(message)
    return message


# ---------------------------------------------------------------- reminder touches


def send_touch(outreach_id: int, now=None) -> str:
    """Send the next touch of an approved reminder. At-most-once per (outreach, touch)."""
    now = now or timezone.now()
    with transaction.atomic():
        outreach = (
            Outreach.objects.select_for_update(of=("self",))
            .select_related("client", "station", "vehicle")
            .filter(pk=outreach_id)
            .first()
        )
        if outreach is None or outreach.status != Outreach.Status.APPROVED:
            return "not_approved"
        if outreach.is_control_group:
            return "control_group"  # never sent (spec 7.4)
        if outreach.channel == Channel.MANUAL_CALL:
            return "manual_call"
        if outreach.client.unsubscribed_at:
            outreach.status = Outreach.Status.CANCELLED
            outreach.skip_reason = "Клиент отписался"
            outreach.save(update_fields=["status", "skip_reason", "updated_at"])
            release_recommendations(outreach)
            return "unsubscribed"
        if not in_send_window(outreach.station, now):
            return "outside_window"
        channel = outreach.channel or default_channel(outreach.client) or Channel.SMS
        if channel == Channel.MAX and not outreach.client.max_user_id:
            channel = Channel.SMS
        touch = outreach.touch_count + 1
        message = Message.objects.filter(
            outreach=outreach, touch_number=touch, direction=Message.Direction.OUT
        ).first()
        if message is not None and message.status != Message.Status.FAILED:
            return "duplicate"  # sent or possibly sent (crash while sending): never resend
        if message is not None and message.attempts >= MAX_SEND_ATTEMPTS:
            return "gave_up"
        text = compose_text(outreach, channel, touch)
        if message is None:
            try:
                with transaction.atomic():
                    message = Message.objects.create(
                        outreach=outreach, client=outreach.client, channel=channel,
                        direction=Message.Direction.OUT, text=text, touch_number=touch,
                        status=Message.Status.SENDING, sent_by=outreach.approved_by,
                    )  # fmt: skip
            except IntegrityError:
                return "duplicate"
        message.status = Message.Status.SENDING
        message.text = text
        message.channel = channel
        message.attempts += 1
        message.save(update_fields=["status", "text", "channel", "attempts", "updated_at"])

    buttons = outreach_buttons(outreach) if channel == Channel.MAX else None
    ok = deliver(message, buttons)

    with transaction.atomic():
        outreach = Outreach.objects.select_for_update(of=("self",)).get(pk=outreach_id)
        if ok:
            outreach.status = Outreach.Status.SENT
            outreach.touch_count = touch
            outreach.first_touch_at = outreach.first_touch_at or now
            outreach.last_touch_at = now
            outreach.channel = channel
            outreach.save(update_fields=["status", "touch_count", "first_touch_at",
                                         "last_touch_at", "channel", "updated_at"])  # fmt: skip
            return "sent"
        if message.attempts >= MAX_SEND_ATTEMPTS:
            outreach.status = Outreach.Status.CANCELLED
            outreach.skip_reason = f"Не удалось отправить: {message.error}"[:500]
            outreach.save(update_fields=["status", "skip_reason", "updated_at"])
            release_recommendations(outreach)
        return "failed"


def dispatch_due(now=None) -> dict[str, int]:
    """Periodic: send approved reminders whose station is inside its send window now."""
    stats: dict[str, int] = {}
    ids = (
        Outreach.objects.filter(status=Outreach.Status.APPROVED, is_control_group=False)
        .exclude(channel=Channel.MANUAL_CALL)
        .values_list("id", flat=True)
    )
    for outreach_id in list(ids):
        result = send_touch(outreach_id, now)
        stats[result] = stats.get(result, 0) + 1
    return stats


# ---------------------------------------------------------------- client actions


def unsubscribe(client, channel: str, outreach=None) -> Message:
    client.unsubscribed_at = timezone.now()
    client.save(update_fields=["unsubscribed_at", "updated_at"])
    for pending in Outreach.objects.filter(
        client=client,
        status__in=(Outreach.Status.PLANNED, Outreach.Status.AWAITING_APPROVAL,
                    Outreach.Status.APPROVED),
    ):  # fmt: skip
        pending.status = Outreach.Status.CANCELLED
        pending.skip_reason = "Клиент отписался"
        pending.save(update_fields=["status", "skip_reason", "updated_at"])
        release_recommendations(pending)
    sender = client.station.sender_name or client.station.name
    return send_reply(client, channel, REPLY_UNSUBSCRIBED.format(sender=sender), outreach)


def _mark_replied(outreach: Outreach | None) -> None:
    if outreach is not None and outreach.status == Outreach.Status.SENT:
        outreach.status = Outreach.Status.REPLIED
        outreach.save(update_fields=["status", "updated_at"])


def request_booking(client, channel: str, outreach, preferred_time: str = "") -> BookingRequest:
    booking = BookingRequest.objects.filter(
        client=client, outreach=outreach, status=BookingRequest.Status.NEW
    ).first()
    if booking is None:
        booking = BookingRequest.objects.create(
            client=client, outreach=outreach, preferred_time_text=preferred_time[:255]
        )
    elif preferred_time:
        booking.preferred_time_text = preferred_time[:255]
        booking.save(update_fields=["preferred_time_text", "updated_at"])
    _mark_replied(outreach)
    send_reply(client, channel, REPLY_BOOK, outreach)  # the AI never confirms a time itself
    return booking


def close_as_done(client, channel: str, outreach) -> None:
    if outreach is not None:
        outreach.recommendations.filter(status__in=Recommendation.ACTIVE_STATUSES).update(
            status=Recommendation.Status.DONE, updated_at=timezone.now()
        )
    _mark_replied(outreach)
    send_reply(client, channel, REPLY_DONE, outreach)


def not_relevant(client, channel: str, outreach) -> None:
    if outreach is not None:
        outreach.recommendations.filter(status__in=Recommendation.ACTIVE_STATUSES).update(
            status=Recommendation.Status.DECLINED, updated_at=timezone.now()
        )
    _mark_replied(outreach)
    send_reply(client, channel, REPLY_NOT_RELEVANT, outreach)


def sold_car(client, channel: str, outreach) -> None:
    if outreach is not None:
        vehicle = outreach.vehicle
        vehicle.is_sold = True
        vehicle.save(update_fields=["is_sold", "updated_at"])
    _mark_replied(outreach)
    send_reply(client, channel, REPLY_SOLD, outreach)


def answer_price(client, channel: str, outreach) -> bool:
    """Reply with an estimate if we have one; otherwise the manager answers."""
    if outreach is None or not outreach.est_amount:
        return False
    if channel == Channel.MAX:
        cta = "Нажмите «Записаться» — подберём время."
    else:
        from apps.outreach.texts import booking_link

        link = booking_link(outreach)
        cta = f"Запись: {link}" if link else "Ответьте «да», и администратор свяжется с вами."
    _mark_replied(outreach)
    send_reply(client, channel, REPLY_PRICE.format(amount=f"{outreach.est_amount:.0f}", cta=cta),
               outreach)  # fmt: skip
    return True


# ---------------------------------------------------------------- inbound text


def latest_outreach(client) -> Outreach | None:
    return (
        Outreach.objects.filter(
            client=client,
            status__in=(Outreach.Status.SENT, Outreach.Status.REPLIED, Outreach.Status.BOOKED),
        )
        .order_by("-last_touch_at", "-id")
        .first()
    )


def handle_incoming_text(client, channel: str, text: str, provider_id: str = "") -> Message | None:
    """Spec 8.4: STOP rule → LLM intent → action. Returns None for a duplicate delivery."""
    from .intent import CONFIDENCE_THRESHOLD, classify, is_stop

    outreach = latest_outreach(client)
    try:
        with transaction.atomic():
            message = Message.objects.create(
                client=client, outreach=outreach, channel=channel,
                direction=Message.Direction.IN, text=text[:4000],
                status=Message.Status.RECEIVED, provider_message_id=provider_id[:128],
            )  # fmt: skip
    except IntegrityError:
        return None  # webhook redelivery

    if is_stop(text):
        message.intent, message.intent_confidence = Intent.UNSUBSCRIBE, 1.0
        message.save(update_fields=["intent", "intent_confidence", "updated_at"])
        unsubscribe(client, channel, outreach)
        return message

    reminder = (
        outreach.messages.filter(direction="out").order_by("-id").first() if outreach else None
    )
    result = classify(text, reminder.text if reminder else "", client.station_id,
                      [client.full_name] if client.full_name else [])  # fmt: skip
    message.intent = result.intent
    message.intent_confidence = result.confidence
    handled = False
    if result.confidence >= CONFIDENCE_THRESHOLD and not result.error:
        if result.intent == Intent.BOOK:
            request_booking(client, channel, outreach, result.preferred_time_text)
            handled = True
        elif result.intent == Intent.ALREADY_DONE:
            close_as_done(client, channel, outreach)
            handled = True
        elif result.intent == Intent.SOLD_CAR:
            sold_car(client, channel, outreach)
            handled = True
        elif result.intent == Intent.UNSUBSCRIBE:
            unsubscribe(client, channel, outreach)
            handled = True
        elif result.intent == Intent.PRICE_QUESTION:
            handled = answer_price(client, channel, outreach)
    if not handled:
        _mark_replied(outreach)
    message.handled_by_ai = handled
    message.needs_attention = not handled
    message.save(update_fields=["intent", "intent_confidence", "handled_by_ai", "needs_attention",
                                "updated_at"])  # fmt: skip
    return message


def find_client_by_phone(phone: str):
    """A phone can exist at several stations: pick the client we contacted most recently."""
    from apps.customers.models import Client

    clients = list(Client.objects.filter(phone=phone).select_related("station"))
    if not clients:
        return None

    def key(client):
        last = client.outreaches.exclude(last_touch_at__isnull=True).order_by("-last_touch_at")
        first = last.first()
        return (first.last_touch_at if first else datetime.min.replace(tzinfo=UTC), client.id)

    return max(clients, key=key)
