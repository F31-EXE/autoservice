"""Channel backends. ``get_backend(channel)`` picks the implementation from settings."""

from __future__ import annotations

from django.conf import settings

from .base import Button, MessageBackend, SendError, SendResult
from .console import ConsoleBackend


def get_backend(channel: str) -> MessageBackend:
    if channel == "max":
        if settings.MESSAGE_BACKEND_MAX == "max":
            from .max import MaxBackend

            return MaxBackend()
        return ConsoleBackend("max")
    if channel == "sms":
        name = settings.MESSAGE_BACKEND_SMS
        if name == "console":
            return ConsoleBackend("sms")
        raise SendError(f"unknown SMS backend: {name}")  # provider is chosen later (spec 14.1)
    raise SendError(f"channel {channel} has no backend")


__all__ = ["Button", "ConsoleBackend", "MessageBackend", "SendError", "SendResult", "get_backend"]
