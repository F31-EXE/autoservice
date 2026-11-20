from django.dispatch import receiver

from apps.workorders.signals import work_orders_imported


@receiver(work_orders_imported, dispatch_uid="reports.attribution")
def after_import(sender, batch, changed_work_order_ids, vehicle_ids, **kwargs):
    from .attribution import attribute_visits

    stats = attribute_visits(station_ids=[batch.station_id], vehicle_ids=vehicle_ids)
    batch.stats = {**batch.stats, "attribution": stats}
    batch.save(update_fields=["stats", "updated_at"])
