from celery import shared_task

from . import services
from .max_updates import process_update


@shared_task(acks_late=True)
def send_touch_task(outreach_id: int) -> str:
    return services.send_touch(outreach_id)


@shared_task
def dispatch_due_task() -> dict:
    return services.dispatch_due()


@shared_task(acks_late=True)
def process_max_update_task(update: dict) -> str:
    return process_update(update)


@shared_task(acks_late=True)
def process_incoming_sms_task(phone: str, text: str, provider_id: str = "") -> str:
    from apps.workorders.importing.normalize import normalize_phone

    normalized = normalize_phone(phone)
    client = services.find_client_by_phone(normalized) if normalized else None
    if client is None:
        return "unknown_client"
    result = services.handle_incoming_text(client, "sms", text, provider_id)
    return "duplicate" if result is None else "handled"
