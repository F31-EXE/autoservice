from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.demo import DEMO_ORG, load, remove_demo


class Command(BaseCommand):
    help = (
        "Fill the cabinet with demo data (made-up clients) through the real pipeline. "
        "Re-running recreates the demo organization."
    )

    def add_arguments(self, parser):
        parser.add_argument("--clients", type=int, default=80)
        parser.add_argument("--password", default="demo12345")
        parser.add_argument("--seed", type=int, default=7)
        parser.add_argument("--remove", action="store_true", help="only delete the demo data")
        parser.add_argument("--force", action="store_true", help="allow when DEBUG is off")

    def handle(self, *args, clients, password, seed, remove, force, **options):
        if not settings.DEBUG and not force:
            raise CommandError("DEBUG is off: demo data in production? Use --force if you mean it.")
        if remove:
            removed = remove_demo()
            self.stdout.write("Demo data removed." if removed else "No demo data found.")
            return
        if not 10 <= clients <= 2000:
            raise CommandError("--clients must be between 10 and 2000")
        stats = load(clients=clients, password=password, seed=seed)
        self.stdout.write(self.style.SUCCESS(f"Demo organization «{DEMO_ORG}» is ready:"))
        for key, value in stats.items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write(
            f"Log in at /login/ as owner «demo» or manager «manager», password «{password}»."
        )
        self.stdout.write("The demo campaign was 25 days ago: on «Результаты» pick «30 дней».")
