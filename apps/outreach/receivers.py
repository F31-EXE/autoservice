from django.dispatch import receiver

from apps.workorders.signals import work_orders_imported


@receiver(work_orders_imported, dispatch_uid="outreach.after_import")
def after_import(sender, batch, changed_work_order_ids, vehicle_ids, **kwargs):
    """Keep reminder dates fresh and drop reminders whose work was done in the new visits."""
    from .planner import cancel_obsolete, recompute_planned_for

    recompute_planned_for(batch.station, vehicle_ids)
    cancel_obsolete(batch.station)
