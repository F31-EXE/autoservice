from datetime import date

from django.core.management.base import BaseCommand

from apps.accounts.models import ServiceStation
from apps.outreach.planner import build_queue


class Command(BaseCommand):
    help = "Build today's reminder queue for a station (the daily job does this at 07:00)"

    def add_arguments(self, parser):
        parser.add_argument("--station", type=int, required=True)
        parser.add_argument("--date", help="YYYY-MM-DD, default: today in the station timezone")

    def handle(self, *args, station, date=None, **options):
        obj = ServiceStation.objects.get(pk=station)
        today = _parse(date) if date else None
        self.stdout.write(str(build_queue(obj, today)))


def _parse(value: str) -> date:
    return date.fromisoformat(value)
