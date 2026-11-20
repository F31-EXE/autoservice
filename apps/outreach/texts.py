"""Reminder texts (spec 8.3): Django templates with substitution, no LLM by default."""

from __future__ import annotations

import math

from django.conf import settings
from django.template import Context, Template

MONTHS_PREPOSITIONAL = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]  # fmt: skip

# Messenger (MAX): the full text from spec 8.3; booking happens via the buttons below it.
DEFAULT_TEMPLATE = (
    "{{ client_first_name }}, это {{ station.sender_name }}. В {{ visit_month }} мастер отметил: "
    "{{ recommendation_summary }} на вашем {{ vehicle_make_model }}. По пробегу пора проверить. "
    "{% if link %}Записаться: {{ link }}. {% endif %}Отписаться: СТОП"
)
MAX_TEMPLATE = (
    "{{ client_first_name }}, это {{ station.sender_name }}. В {{ visit_month }} мастер отметил: "
    "{{ recommendation_summary }} на вашем {{ vehicle_make_model }}. По пробегу пора проверить. "
    "Нажмите «Записаться» — администратор свяжется с вами и подберёт время. Отписаться: СТОП"
)
MAX_SECOND_TEMPLATE = (
    "{{ client_first_name }}, {{ station.sender_name }} напоминает: по вашему "
    "{{ vehicle_make_model }} пора — {{ recommendation_summary }}. Нажмите «Записаться», "
    "и мы подберём удобное время. Отписаться: СТОП"
)
# SMS: short, must fit SMS_MAX_SEGMENTS (2 Cyrillic segments = 134 chars). Variants are tried
# from the most informative to the shortest.
SMS_TEMPLATES = [
    "{{ client_first_name }}, {{ station.sender_name }}: по {{ vehicle_make_model }} пора — "
    "{{ recommendation_summary }}. Запись: {{ link }}. СТОП — отписка",
    "{{ client_first_name }}, {{ station.sender_name }}: пора — {{ recommendation_summary }}. "
    "Запись: {{ link }}. СТОП — отписка",
    "{{ client_first_name }}, {{ station.sender_name }}: пора на сервис по рекомендациям "
    "мастера. Запись: {{ link }}. СТОП — отписка",
]
SMS_SECOND_TEMPLATES = [
    "{{ client_first_name }}, {{ station.sender_name }} напоминает: пора — "
    "{{ recommendation_summary }}. Запись: {{ link }}. СТОП — отписка",
    "{{ client_first_name }}, {{ station.sender_name }} напоминает о рекомендациях мастера. "
    "Запись: {{ link }}. СТОП — отписка",
]

CALL_SCRIPT = (
    "Здравствуйте{% if client_first_name %}, {{ client_first_name }}{% endif %}! "
    "Это {{ station.sender_name }}. Звоню напомнить по вашему "
    "{{ vehicle_make_model }}: в {{ visit_month }} мастер рекомендовал — "
    "{{ recommendation_summary }}.\n"
    "Ориентировочная стоимость: {{ est_amount }} ₽ (точнее после осмотра).\n"
    "Предложить удобное время визита. Если клиент отказался или работы уже сделаны — "
    "отметить в карточке."
)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


_URGENCY_ORDER = {"safety": 0, "wear": 1, "comfort": 2}
MAX_LISTED = 2


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def recommendation_summary(recommendations, max_listed: int = MAX_LISTED) -> str:
    """Most important works first; the rest is summarized as "и ещё N работ" (keeps SMS short)."""
    ordered = sorted(
        recommendations, key=lambda r: _URGENCY_ORDER.get(getattr(r, "urgency", "wear"), 1)
    )
    names = []
    for rec in ordered:
        name = _lower_first(rec.title or "")
        if name and name not in names:
            names.append(name)
    listed, rest = names[:max_listed], len(names) - max_listed
    if rest > 0:
        return ", ".join(listed) + f" и ещё {rest} {plural(rest, 'работа', 'работы', 'работ')}"
    if len(listed) <= 1:
        return "".join(listed)
    return ", ".join(listed[:-1]) + " и " + listed[-1]


def context_for(outreach, max_listed: int = MAX_LISTED, channel: str = "") -> dict:
    recs = list(outreach.recommendations.select_related("work_type", "source_work_order"))
    latest_visit = max((r.source_work_order.visit_date for r in recs), default=None)
    station = outreach.station
    return {
        "client_first_name": outreach.client.first_name or "Здравствуйте",
        # plain dict: templates may be edited by station owners, don't expose model objects
        "station": {
            "sender_name": station.sender_name or station.name,
            "name": station.name,
            "phone": station.phone,
        },
        "visit_month": MONTHS_PREPOSITIONAL[latest_visit.month - 1]
        if latest_visit
        else "прошлый раз",
        "recommendation_summary": recommendation_summary(recs, max_listed),
        "vehicle_make_model": outreach.vehicle.make_model or "автомобиле",
        "link": booking_link(outreach) if channel != "max" else "",
        "est_amount": f"{outreach.est_amount:.0f}" if outreach.est_amount else "—",
    }


def booking_link(outreach) -> str:
    """Invitation to the MAX bot if configured, otherwise the station's booking page / phone."""
    if settings.MAX_BOT_USERNAME:
        from apps.messaging.models import MaxLink

        link = MaxLink.objects.filter(
            client=outreach.client, used_at__isnull=True
        ).first() or MaxLink.objects.create(client=outreach.client)
        return settings.MAX_LINK_TEMPLATE.format(bot=settings.MAX_BOT_USERNAME, payload=link.token)
    station = outreach.station
    return station.booking_url or station.phone or ""


def _render(template: str, context: dict) -> str:
    return Template(template).render(Context(context, autoescape=False)).strip()


# ---------------------------------------------------------------- SMS length

GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENDED = set("^{}\\[~]|€")


def sms_segments(text: str) -> tuple[int, str]:
    """Number of SMS segments and the encoding ("gsm7" or "ucs2"), counted honestly."""
    if all(ch in GSM7_BASIC or ch in GSM7_EXTENDED for ch in text):
        units = sum(2 if ch in GSM7_EXTENDED else 1 for ch in text)
        return (1 if units <= 160 else math.ceil(units / 153)), "gsm7"
    units = len(text.encode("utf-16-le")) // 2  # emoji take two UTF-16 units
    return (1 if units <= 70 else math.ceil(units / 67)), "ucs2"


def _fit_sms(outreach, templates: list[str]) -> str:
    limit = int(settings.SMS_MAX_SEGMENTS)
    custom = outreach.station.conf.get("sms_template")
    if custom:
        templates = [custom, *templates]
    # most informative first; the last template (no list of works) is the final fallback
    attempts = [(t, n) for t in templates[:-1] for n in (MAX_LISTED, 1)] + [(templates[-1], 1)]
    contexts = {n: context_for(outreach, n, channel="sms") for n in (MAX_LISTED, 1)}
    shortest = ""
    for template, max_listed in attempts:
        text = _render(template, contexts[max_listed])
        if sms_segments(text)[0] <= limit:
            return text
        if not shortest or len(text) < len(shortest):
            shortest = text
    return shortest  # does not fit: the Today screen shows a warning


def compose_text(outreach, channel: str | None = None, touch: int | None = None) -> str:
    """Message text for a channel: the manager's edited text if any, otherwise a template."""
    if outreach.custom_text:
        return outreach.custom_text
    channel = channel or outreach.channel or default_channel(outreach.client) or "sms"
    touch = touch or (outreach.touch_count + 1)
    if channel == "sms":
        return _fit_sms(outreach, SMS_TEMPLATES if touch == 1 else SMS_SECOND_TEMPLATES)
    if channel == "max":
        template = outreach.station.conf.get("message_template") or (
            MAX_TEMPLATE if touch == 1 else MAX_SECOND_TEMPLATE
        )
        return _render(template, context_for(outreach, channel="max"))
    return _render(DEFAULT_TEMPLATE, context_for(outreach))


def default_channel(client) -> str | None:
    if client.max_user_id:
        return "max"
    if client.phone:
        return "sms"
    return None


def call_script(outreach) -> str:
    return _render(CALL_SCRIPT, context_for(outreach))
