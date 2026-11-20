"""LLM clients. Real calls go to an OpenAI-compatible API (Yandex AI Studio).

Tests and the eval mock use ``FakeLLMClient``; the backend is chosen by ``settings.LLM_BACKEND``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from django.conf import settings


class LLMError(Exception):
    """Call failed. The message is PII-free (exception type / HTTP status only)."""


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def cost_rub(self) -> Decimal:
        return cost_rub(self.input_tokens, self.output_tokens)


def cost_rub(input_tokens: int, output_tokens: int) -> Decimal:
    price_in = Decimal(str(settings.LLM_PRICE_PER_1K_INPUT_RUB))
    price_out = Decimal(str(settings.LLM_PRICE_PER_1K_OUTPUT_RUB))
    value = (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / 1000
    return value.quantize(Decimal("0.0001"))


def model_uri(model: str) -> str:
    """Yandex AI Studio expects ``gpt://<folder>/<model>``; accept short names too."""
    if not model or "://" in model or not settings.LLM_FOLDER_ID:
        return model
    if "/" not in model:
        model = f"{model}/latest"
    return f"gpt://{settings.LLM_FOLDER_ID}/{model}"


class LLMClient(Protocol):
    def complete(self, messages: list[dict], model: str, max_tokens: int) -> LLMResponse: ...


class OpenAICompatibleClient:
    def __init__(self, http_client=None) -> None:
        if not settings.LLM_API_KEY:
            raise LLMError("LLM_API_KEY is not configured")
        from openai import OpenAI

        self._client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            project=settings.LLM_FOLDER_ID or None,  # sent as the OpenAI-Project header
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=2,
            http_client=http_client,
        )

    def complete(self, messages: list[dict], model: str, max_tokens: int) -> LLMResponse:
        uri = model_uri(model)
        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=uri, messages=messages, temperature=0, max_tokens=max_tokens
            )
        except Exception as exc:  # noqa: BLE001 - normalize every SDK/network error
            status = getattr(exc, "status_code", None)
            raise LLMError(f"{type(exc).__name__}{f' {status}' if status else ''}") from exc
        latency = int((time.monotonic() - started) * 1000)
        usage = response.usage
        return LLMResponse(
            text=response.choices[0].message.content or "",
            model=uri,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency,
        )


class FakeLLMClient:
    """Deterministic offline client. ``responder(messages) -> str`` produces the answer."""

    def __init__(self, responder: Callable[[list[dict]], str] | None = None) -> None:
        self.responder = responder or (lambda messages: '{"recommendations": []}')
        self.calls: list[dict] = []

    def complete(self, messages: list[dict], model: str, max_tokens: int) -> LLMResponse:
        self.calls.append({"messages": messages, "model": model})
        text = self.responder(messages)
        if isinstance(text, Exception):
            raise text
        prompt_chars = sum(len(m["content"]) for m in messages)
        return LLMResponse(
            text=text,
            model=model or "fake",
            input_tokens=prompt_chars // 4,
            output_tokens=len(text) // 4,
            latency_ms=1,
        )


_override: LLMClient | None = None


def set_client(client: LLMClient | None) -> None:
    """Install a client for the current process (tests / eval)."""
    global _override
    _override = client


def get_client() -> LLMClient:
    if _override is not None:
        return _override
    if settings.LLM_BACKEND == "fake":
        from apps.messaging.intent import INTENT_MARKER, heuristic_responder
        from apps.recommendations.llm_mock import HeuristicResponder

        extraction = HeuristicResponder()

        def route(messages: list[dict]) -> str:
            if INTENT_MARKER in messages[-1]["content"]:
                return heuristic_responder(messages)
            return extraction(messages)

        return FakeLLMClient(route)
    return OpenAICompatibleClient()
