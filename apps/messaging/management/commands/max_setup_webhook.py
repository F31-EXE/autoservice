from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.messaging.backends.max import MaxBackend

UPDATE_TYPES = ["bot_started", "message_created", "message_callback"]


class Command(BaseCommand):
    help = "Subscribe the MAX bot to our webhook: --url https://<host>/messaging/max/webhook/"

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True)

    def handle(self, *args, url, **options):
        if not url.startswith("https://"):
            raise CommandError("MAX requires an https:// webhook URL")
        if not settings.MAX_WEBHOOK_SECRET:
            raise CommandError("Set MAX_WEBHOOK_SECRET first")
        response = MaxBackend().subscribe(url, settings.MAX_WEBHOOK_SECRET, UPDATE_TYPES)
        if response.status_code >= 400:
            raise CommandError(f"MAX API error: HTTP {response.status_code}")
        self.stdout.write(self.style.SUCCESS(f"Subscribed: {response.text[:200]}"))
