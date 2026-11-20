from django.dispatch import Signal

#: Sent after an import batch is written.
#: kwargs: batch, changed_work_order_ids (created or updated), vehicle_ids (affected)
#: Later stages hook in here: rule-based extraction (3), LLM queue (4), attribution (7).
work_orders_imported = Signal()
