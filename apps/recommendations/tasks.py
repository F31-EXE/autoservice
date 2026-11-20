from celery import shared_task

from .rules import expire_recommendations


@shared_task
def expire_recommendations_task() -> int:
    return expire_recommendations()


@shared_task(acks_late=True)
def extract_pending_task(station_id: int) -> dict:
    """Extract recommendations from free text for pending work orders, batch by batch."""
    from .llm_extract import extract_pending

    stats = extract_pending(station_id)
    if stats.get("more") and not stats.get("budget_exceeded"):
        extract_pending_task.delay(station_id)
    return stats
