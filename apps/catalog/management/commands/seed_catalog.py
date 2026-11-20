from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.seed_data import load


class Command(BaseCommand):
    help = "Upsert the global work type catalog and its global aliases from seed_data.py"

    @transaction.atomic
    def handle(self, *args, **options):
        count = load()
        self.stdout.write(self.style.SUCCESS(f"Catalog synced: {count} work types"))
