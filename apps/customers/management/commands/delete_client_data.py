from django.core.management.base import BaseCommand, CommandError

from apps.customers.deletion import delete_client_data, plan_deletion
from apps.customers.models import Client


class Command(BaseCommand):
    help = (
        "Delete all data of a client on their request (personal data law). "
        "Without --yes only shows what would be deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument("--client-id", type=int, required=True)
        parser.add_argument("--yes", action="store_true", help="really delete (irreversible)")

    def handle(self, *args, client_id, yes, **options):
        client = Client.objects.filter(pk=client_id).first()
        if client is None:
            raise CommandError(f"Client {client_id} not found")
        plan = plan_deletion(client)
        self.stdout.write(f"Client #{client_id} (station #{plan.station_id}) — will be deleted:")
        for name, count in plan.counts.items():
            self.stdout.write(f"  {name}: {count}")
        if not yes:
            self.stdout.write(self.style.WARNING("Dry run. Re-run with --yes to delete."))
            return
        delete_client_data(client)
        self.stdout.write(self.style.SUCCESS(f"Client #{client_id} data deleted."))
