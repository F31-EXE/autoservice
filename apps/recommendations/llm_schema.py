"""Pydantic schema of the LLM extraction answer (spec 6.2) and tolerant JSON parsing."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ExtractedRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    work_type_code: str | None = None
    work_type_raw: str = Field(default="", max_length=512)
    source_text: str = Field(min_length=1, max_length=2000)
    kind: Literal["future", "declined"]
    due_km_delta: int | None = Field(default=None, ge=0, le=500_000)
    due_months_delta: int | None = Field(default=None, ge=0, le=120)
    urgency: Literal["safety", "wear", "comfort"] = "wear"
    confidence: float = Field(ge=0, le=1)

    @field_validator("work_type_code", mode="before")
    @classmethod
    def _null_code(cls, value):
        if value in ("", "null", "None"):
            return None
        return value


class ExtractionAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    recommendations: list[ExtractedRecommendation]


class InvalidAnswer(Exception):
    """LLM answer is not valid JSON of the expected schema (message is PII-free)."""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_answer(text: str) -> ExtractionAnswer:
    cleaned = _FENCE.sub("", (text or "").strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise InvalidAnswer("no JSON object in answer")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvalidAnswer(f"JSON decode error at pos {exc.pos}") from exc
    try:
        return ExtractionAnswer.model_validate(data)
    except ValidationError as exc:
        raise InvalidAnswer(f"schema error: {exc.error_count()} problems") from exc
