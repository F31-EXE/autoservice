"""Intent of an inbound reply (spec 8.4): STOP rules first, then a light LLM."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Literal

from django.conf import settings
from pydantic import BaseModel, Field, ValidationError

from apps.llm.accounting import budget_exceeded, record_call
from apps.llm.anonymize import anonymize
from apps.llm.client import LLMError, get_client

PROMPT_VERSION = "intent_v1"
PROMPT = Template((Path(settings.BASE_DIR) / "apps/llm/prompts/intent_v1.txt").read_text("utf-8"))
INTENT_MARKER = "Задача: классификация ответа клиента"
CONFIDENCE_THRESHOLD = 0.7

_STOP = re.compile(r"^\W*(стоп|stop|отписаться|отписка|отпишите|unsubscribe)\W*$", re.I)


def is_stop(text: str) -> bool:
    return bool(_STOP.match(text or ""))


class IntentAnswer(BaseModel):
    intent: Literal["book", "price_question", "already_done", "sold_car", "unsubscribe", "other"]
    confidence: float = Field(ge=0, le=1)
    preferred_time_text: str = Field(default="", max_length=255)


@dataclass
class IntentResult:
    intent: str = "other"
    confidence: float = 0.0
    preferred_time_text: str = ""
    error: str = ""


def parse_intent(text: str) -> IntentAnswer:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON")
    return IntentAnswer.model_validate(json.loads(text[start : end + 1]))


def classify(text: str, reminder: str, station_id: int | None, known_names=()) -> IntentResult:
    if budget_exceeded(station_id):
        return IntentResult(error="budget")
    try:
        client = get_client()
    except LLMError as exc:
        return IntentResult(error=f"llm: {exc}")
    prompt = PROMPT.substitute(
        reminder=anonymize(reminder, known_names)[:600], text=anonymize(text, known_names)[:1000]
    )
    model = settings.LLM_MODEL_INTENT
    try:
        response = client.complete(
            [{"role": "user", "content": prompt}], model=model, max_tokens=100
        )
    except LLMError as exc:
        record_call(station_id=station_id, purpose="intent", prompt_version=PROMPT_VERSION,
                    model=model, error=str(exc))  # fmt: skip
        return IntentResult(error=f"llm: {exc}")
    try:
        answer = parse_intent(response.text)
        error = ""
    except (ValueError, ValidationError):
        answer, error = None, "invalid answer"
    record_call(station_id=station_id, purpose="intent", prompt_version=PROMPT_VERSION,
                model=model, response=response, error=error,
                payload={"prompt": prompt, "answer": response.text})  # fmt: skip
    if answer is None:
        return IntentResult(error=error)
    # the model must not invent a time: keep it only if it is quoted from the reply
    preferred = answer.preferred_time_text.strip()
    if preferred and preferred.lower() not in text.lower():
        preferred = ""
    return IntentResult(answer.intent, answer.confidence, preferred)


# ------------------------------------------------------------ offline heuristic (dev, tests)

_RULES = [
    ("unsubscribe", re.compile(r"не пиш|хватит|отпиш", re.I)),
    ("sold_car", re.compile(r"продал|продали|нет машин|машины нет", re.I)),
    (
        "already_done",
        re.compile(
            r"уже (сделал|поменял|заменил|меняли|сделали)|сделал уже|"
            r"поменял уже",
            re.I,
        ),
    ),  # fmt: skip
    ("price_question", re.compile(r"сколько|цен[аы]|стоимост|стоит", re.I)),
    (
        "book",
        re.compile(
            r"запиш|записат|приед|подъед|давайте|можно|в (понедельник|вторник|среду|"
            r"четверг|пятницу|субботу|воскресенье)|завтра|послезавтра|утром|вечером|"
            r"перезвон|да\b",
            re.I,
        ),
    ),  # fmt: skip
]
_TIME = re.compile(
    r"((в|во|на|к)\s+)?(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|"
    r"завтра|послезавтра|выходные|утром|вечером|днём|днем|\d{1,2}[:.]\d{2}|\d{1,2}\s*час\w*)"
    r"(\s+(утром|вечером|днём|днем|в\s+\d{1,2}[:.]?\d{0,2}))?",
    re.I,
)


def heuristic_responder(messages: list[dict]) -> str:
    content = messages[-1]["content"]
    found = re.search(r"Клиент ответил: «(.*?)»\n", content, re.S)
    text = found.group(1) if found else ""
    for intent, pattern in _RULES:
        if pattern.search(text):
            time = _TIME.search(text) if intent == "book" else None
            return json.dumps({"intent": intent, "confidence": 0.85,
                               "preferred_time_text": time.group(0) if time else ""},
                              ensure_ascii=False)  # fmt: skip
    return json.dumps({"intent": "other", "confidence": 0.5, "preferred_time_text": ""})
