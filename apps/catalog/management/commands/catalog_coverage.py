"""Show the most frequent work order line names that do not match the catalog.

Line names are work descriptions, not personal data, so printing them is fine.
"""

from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Count

from apps.catalog.matching import WorkTypeMatcher, normalize_name
from apps.workorders.models import WorkOrderLine


class Command(BaseCommand):
    help = "Report catalog matching coverage for a station"

    def add_arguments(self, parser):
        parser.add_argument("--station", type=int, required=True)
        parser.add_argument("--top", type=int, default=50)
        parser.add_argument("--labor-only", action="store_true")

    def handle(self, *args, station, top, labor_only, **options):
        matcher = WorkTypeMatcher(station)
        qs = WorkOrderLine.objects.filter(work_order__station_id=station)
        if labor_only:
            qs = qs.filter(kind=WorkOrderLine.Kind.LABOR)
        unmatched: Counter[str] = Counter()
        total = matched = 0
        for row in qs.values("name").annotate(n=Count("id")):
            total += row["n"]
            if matcher.match(row["name"]):
                matched += row["n"]
            else:
                unmatched[normalize_name(row["name"])] += row["n"]
        share = matched / total * 100 if total else 0
        self.stdout.write(f"Lines: {total}, matched: {matched} ({share:.1f}%)")
        for name, n in unmatched.most_common(top):
            self.stdout.write(f"{n:6d}  {name}")
