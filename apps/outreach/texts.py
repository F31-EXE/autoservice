"""Reminder texts (spec 8.3): Django templates with substitution, no LLM by default."""

from __future__ import annotations

from django.template import Context, Template

MONTHS_PREPOSITIONAL = [
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
]  # fmt: skip

DEFAULT_TEMPLATE = (
    "{{ client_first_name }}, это {{ station.sender_name }}. В {{ visit_month }} мастер отметил: "
    "{{ recommendation_summary }} на вашем {{ vehicle_make_model }}. По пробегу пора проверить. "
    "Записаться: {{ link }}. Отписаться: СТОП"
)

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


def context_for(outreach) -> dict:
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
        "recommendation_summary": recommendation_summary(recs),
        "vehicle_make_model": outreach.vehicle.make_model or "автомобиле",
        "link": station.booking_url or station.phone or "",
        "est_amount": f"{outreach.est_amount:.0f}" if outreach.est_amount else "—",
    }


def _render(template: str, context: dict) -> str:
    return Template(template).render(Context(context, autoescape=False)).strip()


def compose_text(outreach) -> str:
    """Message text: the manager's edited text if any, otherwise the template."""
    if outreach.custom_text:
        return outreach.custom_text
    template = outreach.station.conf.get("message_template") or DEFAULT_TEMPLATE
    return _render(template, context_for(outreach))


def call_script(outreach) -> str:
    return _render(CALL_SCRIPT, context_for(outreach))
