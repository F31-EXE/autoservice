from django.db import models

from apps.common.models import TimeStampedModel


class LLMCall(TimeStampedModel):
    """Audit and cost accounting of every LLM call. No prompt/response text in prod."""

    class Purpose(models.TextChoices):
        EXTRACT = "extract", "Извлечение рекомендаций"
        INTENT = "intent", "Интент ответа"
        COMPOSE = "compose", "Текст сообщения"

    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.SET_NULL, null=True, blank=True
    )
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    model = models.CharField(max_length=255)
    prompt_version = models.CharField(max_length=32)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_rub = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=True)
    error = models.CharField(max_length=255, blank=True)
    # filled only when settings.LLM_LOG_PAYLOADS is on (dev); anonymized text only
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = "вызов LLM"
        verbose_name_plural = "вызовы LLM"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["station", "created_at"])]


class LLMBudgetAlert(TimeStampedModel):
    """One alert per station per month when the LLM budget is exhausted."""

    station = models.ForeignKey("accounts.ServiceStation", on_delete=models.CASCADE)
    month = models.DateField()
    spent_rub = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "превышение бюджета LLM"
        verbose_name_plural = "превышения бюджета LLM"
        constraints = [
            models.UniqueConstraint(fields=["station", "month"], name="llm_budget_alert_once")
        ]
