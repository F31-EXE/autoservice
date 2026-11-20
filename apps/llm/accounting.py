"""LLMCall recording and the monthly budget (spec 11)."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .client import LLMResponse
from .models import LLMBudgetAlert, LLMCall

logger = logging.getLogger(__name__)


def record_call(
    *,
    station_id: int | None,
    purpose: str,
    prompt_version: str,
    model: str,
    response: LLMResponse | None = None,
    error: str = "",
    payload: dict | None = None,
) -> LLMCall:
    return LLMCall.objects.create(
        station_id=station_id,
        purpose=purpose,
        model=(response.model if response else model)[:255],
        prompt_version=prompt_version,
        input_tokens=response.input_tokens if response else 0,
        output_tokens=response.output_tokens if response else 0,
        cost_rub=response.cost_rub if response else Decimal(0),
        latency_ms=response.latency_ms if response else 0,
        success=not error,
        error=error[:255],
        payload=payload if settings.LLM_LOG_PAYLOADS else None,
    )


def month_start(today=None):
    today = today or timezone.localdate()
    return today.replace(day=1)


def month_spend(station_id: int) -> Decimal:
    total = LLMCall.objects.filter(
        station_id=station_id, created_at__date__gte=month_start()
    ).aggregate(s=Sum("cost_rub"))["s"]
    return total or Decimal(0)


def budget_exceeded(station_id: int | None) -> bool:
    """True when the station has spent its monthly budget; alerts superadmins once a month."""
    if station_id is None:
        return False
    budget = Decimal(str(settings.LLM_MONTHLY_BUDGET_RUB))
    spent = month_spend(station_id)
    if spent < budget:
        return False
    _alert(station_id, spent)
    return True


def _alert(station_id: int, spent: Decimal) -> None:
    from apps.accounts.models import Role, ServiceStation, User

    try:
        with transaction.atomic():
            LLMBudgetAlert.objects.create(
                station_id=station_id, month=month_start(), spent_rub=spent
            )
    except IntegrityError:
        return  # already alerted this month
    station = ServiceStation.objects.get(pk=station_id)
    logger.warning("LLM budget exhausted for station %s: %s RUB", station_id, spent)
    recipients = list(
        User.objects.filter(role=Role.SUPERADMIN, is_active=True)
        .exclude(email="")
        .values_list("email", flat=True)
    )
    if recipients:
        send_mail(
            subject=f"Vozvrat: исчерпан бюджет LLM — {station.name}",
            message=(
                f"Точка «{station.name}» (id {station_id}) потратила {spent} ₽ на LLM в этом "
                f"месяце при лимите {settings.LLM_MONTHLY_BUDGET_RUB} ₽. Извлечение рекомендаций "
                "приостановлено до следующего месяца или увеличения лимита."
            ),
            from_email=None,
            recipient_list=recipients,
            fail_silently=True,
        )
