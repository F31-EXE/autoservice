from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SendError(Exception):
    """Backend not configured or misconfigured (message is PII-free)."""


@dataclass(frozen=True)
class Button:
    text: str
    payload: str


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str = ""
    error: str = ""  # PII-free: error code / HTTP status only


class MessageBackend(Protocol):
    channel: str

    def send(self, to: str, text: str, buttons: list[Button] | None = None) -> SendResult: ...
