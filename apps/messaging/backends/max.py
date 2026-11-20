"""MAX messenger Bot API backend.

Wire format follows the official client (@maxhub/max-bot-api): base URL
https://platform-api2.max.ru, token in the ``Authorization`` header (no prefix),
``POST /messages?user_id=…`` with ``{"text", "attachments"}``; inline keyboard is an
attachment ``{"type": "inline_keyboard", "payload": {"buttons": [[{type, text, payload}]]}}``;
response ``{"message": {"body": {"mid": …}}}``.
"""

from __future__ import annotations

import httpx
from django.conf import settings

from .base import Button, SendError, SendResult

TIMEOUT = 15.0


class MaxApi:
    def __init__(self, http_client: httpx.Client | None = None) -> None:
        if not settings.MAX_BOT_TOKEN:
            raise SendError("MAX_BOT_TOKEN is not configured")
        self._http = http_client or httpx.Client(timeout=TIMEOUT)
        self._base = settings.MAX_API_BASE_URL.rstrip("/")
        self._headers = {"Authorization": settings.MAX_BOT_TOKEN}

    def call(self, method: str, path: str, *, params=None, json=None) -> httpx.Response:
        return self._http.request(
            method, f"{self._base}/{path}", params=params, json=json, headers=self._headers
        )


def inline_keyboard(buttons: list[Button]) -> dict:
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [{"type": "callback", "text": b.text, "payload": b.payload}] for b in buttons
            ]
        },
    }


class MaxBackend:
    channel = "max"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self.api = MaxApi(http_client)

    def send(self, to: str, text: str, buttons: list[Button] | None = None) -> SendResult:
        body: dict = {"text": text}
        if buttons:
            body["attachments"] = [inline_keyboard(buttons)]
        try:
            response = self.api.call("POST", "messages", params={"user_id": to}, json=body)
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=f"max: {type(exc).__name__}")
        if response.status_code >= 400:
            code = _error_code(response)
            return SendResult(ok=False, error=f"max: HTTP {response.status_code} {code}".strip())
        try:
            mid = response.json()["message"]["body"]["mid"]
        except (ValueError, KeyError, TypeError):
            mid = ""
        return SendResult(ok=True, provider_message_id=str(mid))

    def answer_callback(self, callback_id: str, notification: str) -> bool:
        try:
            response = self.api.call(
                "POST", "answers", params={"callback_id": callback_id},
                json={"notification": notification},
            )  # fmt: skip
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    def subscribe(self, url: str, secret: str, update_types: list[str]) -> httpx.Response:
        return self.api.call(
            "POST", "subscriptions",
            json={"url": url, "update_types": update_types, "secret": secret},
        )  # fmt: skip


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json().get("code", ""))[:64]
    except ValueError:
        return ""
