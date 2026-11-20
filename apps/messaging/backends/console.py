"""Dev/test backend: keeps messages in memory and logs only PII-free metadata."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from .base import Button, SendResult

logger = logging.getLogger(__name__)


@dataclass
class OutboxItem:
    channel: str
    to: str
    text: str
    buttons: list[Button] = field(default_factory=list)


class ConsoleBackend:
    outbox: list[OutboxItem] = []  # shared, inspected by tests
    fail_next: bool = False

    def __init__(self, channel: str) -> None:
        self.channel = channel

    def send(self, to: str, text: str, buttons: list[Button] | None = None) -> SendResult:
        if ConsoleBackend.fail_next:
            ConsoleBackend.fail_next = False
            return SendResult(ok=False, error="console: simulated failure")
        ConsoleBackend.outbox.append(OutboxItem(self.channel, to, text, list(buttons or [])))
        logger.info("[console:%s] message sent, %d chars, %d buttons", self.channel, len(text),
                    len(buttons or []))  # fmt: skip
        return SendResult(ok=True, provider_message_id=f"console-{uuid.uuid4().hex[:12]}")

    @classmethod
    def reset(cls) -> None:
        cls.outbox = []
        cls.fail_next = False
