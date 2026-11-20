"""Processing of MAX webhook updates (bot_started, message_created, message_callback)."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.customers.models import Client
from apps.outreach.models import Outreach
from apps.outreach.texts import compose_text

from . import services
from .backends import get_backend
from .models import Channel, Intent, MaxLink, Message

logger = logging.getLogger(__name__)

GREETING = (
    "Здравствуйте! Это {sender}. Теперь напоминания о ТО и рекомендациях мастера будут "
    "приходить сюда."
)
NO_TOKEN = (
    "Здравствуйте! Чтобы получать напоминания от автосервиса, откройте ссылку из SMS, "
    "которое мы вам отправили."
)
UNKNOWN_USER = "Не нашли вас в списке клиентов. Откройте ссылку из SMS от автосервиса."


def process_update(update: dict) -> str:
    kind = update.get("update_type")
    if kind == "bot_started":
        return _bot_started(update)
    if kind == "message_created":
        return _message_created(update)
    if kind == "message_callback":
        return _callback(update)
    return "ignored"


def _user_id(update: dict, *path: str) -> int | None:
    node = update
    for key in path:
        node = (node or {}).get(key)
    try:
        return int(node) if node is not None else None
    except (TypeError, ValueError):
        return None


def _clients_of(user_id: int):
    return Client.objects.filter(max_user_id=user_id).select_related("station")


def _client_for_user(user_id: int) -> Client | None:
    clients = list(_clients_of(user_id))
    if not clients:
        return None
    with_touch = [(services.latest_outreach(c), c) for c in clients]
    with_touch.sort(key=lambda p: (p[0].last_touch_at if p[0] and p[0].last_touch_at else
                                   timezone.now().replace(year=1970), p[1].id))  # fmt: skip
    return with_touch[-1][1]


def _send_plain(user_id: int, text: str) -> None:
    """Reply to a MAX user we can't map to a client (no Message row: no client)."""
    try:
        get_backend("max").send(str(user_id), text)
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.warning("MAX plain reply failed: %s", type(exc).__name__)


def _bot_started(update: dict) -> str:
    user_id = _user_id(update, "user", "user_id")
    if user_id is None:
        return "bad_update"
    token = (update.get("payload") or "").strip()
    link = (
        MaxLink.objects.select_related("client__station").filter(token=token).first()
        if token
        else None
    )
    if link is None:
        _send_plain(user_id, NO_TOKEN)
        return "no_token"
    client = link.client
    with transaction.atomic():
        client.max_user_id = user_id
        client.save(update_fields=["max_user_id", "updated_at"])
        if link.used_at is None:
            link.used_at = timezone.now()
            link.save(update_fields=["used_at", "updated_at"])
    sender = client.station.sender_name or client.station.name
    services.send_reply(client, Channel.MAX, GREETING.format(sender=sender))
    # repeat the current reminder in the messenger, now with buttons (not a new touch)
    outreach = services.latest_outreach(client)
    if outreach is not None and outreach.status == Outreach.Status.SENT:
        message = Message.objects.create(
            client=client, outreach=outreach, channel=Channel.MAX,
            direction=Message.Direction.OUT, status=Message.Status.SENDING, attempts=1,
            text=compose_text(outreach, Channel.MAX, outreach.touch_count or 1),
        )  # fmt: skip
        services.deliver(message, services.outreach_buttons(outreach))
    return "linked"


def _message_created(update: dict) -> str:
    message = update.get("message") or {}
    user_id = _user_id(message, "sender", "user_id")
    if user_id is None or (message.get("sender") or {}).get("is_bot"):
        return "ignored"
    body = message.get("body") or {}
    text = (body.get("text") or "").strip()
    if not text or text.startswith("/start"):
        return "ignored"
    client = _client_for_user(user_id)
    if client is None:
        _send_plain(user_id, UNKNOWN_USER)
        return "unknown_user"
    result = services.handle_incoming_text(client, Channel.MAX, text, str(body.get("mid") or ""))
    return "duplicate" if result is None else "handled"


BUTTON_TEXT = {"book": "Записаться", "done": "Уже сделал", "nr": "Неактуально"}
BUTTON_NOTIFICATION = {"book": "Передали администратору", "done": "Спасибо!", "nr": "Поняли"}


def _callback(update: dict) -> str:
    callback = update.get("callback") or {}
    user_id = _user_id(callback, "user", "user_id")
    callback_id = str(callback.get("callback_id") or "")
    action, _, outreach_id = (callback.get("payload") or "").partition(":")
    if user_id is None or action not in BUTTON_TEXT or not outreach_id.isdigit():
        return "bad_update"
    # the button must belong to this user's own reminder
    outreach = (
        Outreach.objects.select_related("client__station", "vehicle")
        .filter(pk=int(outreach_id), client__max_user_id=user_id)
        .first()
    )
    if outreach is None:
        return "foreign"
    client = outreach.client
    try:
        with transaction.atomic():
            Message.objects.create(
                client=client, outreach=outreach, channel=Channel.MAX,
                direction=Message.Direction.IN, status=Message.Status.RECEIVED,
                text=f"Кнопка: {BUTTON_TEXT[action]}", provider_message_id=callback_id[:128],
                intent={"book": Intent.BOOK, "done": Intent.ALREADY_DONE,
                        "nr": Intent.NOT_RELEVANT}[action],
                intent_confidence=1.0,
            )  # fmt: skip
    except IntegrityError:
        return "duplicate"
    if action == "book":
        services.request_booking(client, Channel.MAX, outreach)
    elif action == "done":
        services.close_as_done(client, Channel.MAX, outreach)
    else:
        services.not_relevant(client, Channel.MAX, outreach)
    _answer_callback(callback_id, BUTTON_NOTIFICATION[action])
    return action


def _answer_callback(callback_id: str, notification: str) -> None:
    if settings.MESSAGE_BACKEND_MAX != "max" or not callback_id:
        return
    from .backends.max import MaxBackend

    try:
        MaxBackend().answer_callback(callback_id, notification)
    except Exception as exc:  # noqa: BLE001 - the button already worked; this is cosmetic
        logger.warning("MAX callback answer failed: %s", type(exc).__name__)
