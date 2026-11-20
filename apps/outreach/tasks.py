from celery import shared_task

from .planner import run_daily_queues


@shared_task
def build_daily_queues_task() -> dict:
    return run_daily_queues()
