from django.db import transaction
from django.dispatch import receiver

from apps.workorders.signals import work_orders_imported

from .rules import apply_rules, expire_recommendations


@receiver(work_orders_imported, dispatch_uid="recommendations.apply_rules")
def on_work_orders_imported(sender, batch, changed_work_order_ids, vehicle_ids, **kwargs):
    stats = apply_rules(changed_work_order_ids)
    expire_recommendations([batch.station_id])
    batch.stats = {**batch.stats, "rules": stats}
    batch.save(update_fields=["stats", "updated_at"])
    if changed_work_order_ids:
        from .tasks import extract_pending_task

        station_id = batch.station_id
        transaction.on_commit(lambda: extract_pending_task.delay(station_id))
