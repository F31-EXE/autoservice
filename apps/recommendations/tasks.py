from celery import shared_task

from .rules import expire_recommendations


@shared_task
def expire_recommendations_task() -> int:
    return expire_recommendations()
