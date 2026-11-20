from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import TimeStampedModel


class Urgency(models.TextChoices):
    SAFETY = "safety", "Безопасность"
    WEAR = "wear", "Износ"
    COMFORT = "comfort", "Комфорт"


class WorkType(TimeStampedModel):
    """Global catalog of work types (seeded by migration, see seed_data.py)."""

    code = models.SlugField("код", max_length=64, unique=True)
    name = models.CharField("название", max_length=255)
    category = models.CharField("категория", max_length=64)
    default_interval_km = models.PositiveIntegerField("интервал, км", null=True, blank=True)
    default_interval_months = models.PositiveSmallIntegerField(
        "интервал, мес.", null=True, blank=True
    )
    urgency_default = models.CharField(
        "срочность", max_length=8, choices=Urgency.choices, default=Urgency.WEAR
    )
    is_regular = models.BooleanField("регламентная работа ТО", default=False)

    class Meta:
        verbose_name = "вид работ"
        verbose_name_plural = "справочник работ"
        ordering = ["category", "name"]

    def __str__(self) -> str:
        return self.name


class WorkTypeAlias(TimeStampedModel):
    """How work order line names map to a WorkType.

    ``keywords`` (default): space-separated groups; inside a group alternatives are split by
    ``|``. Every group must occur as a substring of the normalized line name; a group starting
    with ``-`` must NOT occur; ``_`` stands for a space inside one alternative.
    Example: ``замен|с/у колодк перед|пер. -диагност``.
    ``regex``: Python regex searched case-insensitively in the normalized name.
    Station aliases (``station`` set) are checked before global ones.
    """

    class Kind(models.TextChoices):
        KEYWORDS = "keywords", "Ключевые слова"
        REGEX = "regex", "Регулярное выражение"

    work_type = models.ForeignKey(WorkType, on_delete=models.CASCADE, related_name="aliases")
    pattern = models.CharField("шаблон", max_length=500)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.KEYWORDS)
    priority = models.SmallIntegerField("приоритет", default=0)
    station = models.ForeignKey(
        "accounts.ServiceStation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="work_type_aliases",
        help_text="Пусто — глобальный синоним",
    )

    class Meta:
        verbose_name = "синоним работы"
        verbose_name_plural = "синонимы работ"

    def __str__(self) -> str:
        return self.pattern

    def clean(self) -> None:
        if self.kind == self.Kind.REGEX:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValidationError(
                    {"pattern": f"Некорректное регулярное выражение: {exc}"}
                ) from exc
        elif not any(not g.startswith("-") for g in self.pattern.split()):
            raise ValidationError({"pattern": "Нужно хотя бы одно обязательное слово"})
