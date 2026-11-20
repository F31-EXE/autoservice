"""Celery tasks for importing work orders. All tasks are idempotent."""

from __future__ import annotations

import logging
import time

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.customers.services import recompute_vehicle_stats

from .importing.parser import parse_table
from .importing.readers import ImportFileError, read_table
from .importing.writer import write_work_orders
from .models import ImportBatch
from .signals import work_orders_imported

logger = logging.getLogger(__name__)


@shared_task(acks_late=True)
def parse_import_batch(batch_id: int) -> None:
    with transaction.atomic():
        batch = (
            ImportBatch.objects.select_for_update(of=("self",))
            .select_related("station", "mapping_profile")
            .filter(pk=batch_id)
            .first()
        )
        if batch is None or batch.mapping_profile is None:
            return
        # PARSING is allowed too: a worker crash rolls the write back, so a re-run is safe.
        if batch.status not in (ImportBatch.Status.PENDING, ImportBatch.Status.PARSING):
            return
        batch.status = ImportBatch.Status.PARSING
        batch.save(update_fields=["status", "updated_at"])

    started = time.monotonic()
    profile = batch.mapping_profile
    try:
        with batch.file.open("rb") as fh:
            table = read_table(fh, batch.original_filename or batch.file.name, profile.sheet_name)
        missing = [h for h in profile.column_map.values() if h and h not in table.headers]
        if missing:
            raise ImportFileError(
                "В файле нет колонок из профиля сопоставления: " + ", ".join(missing)
            )
        result = parse_table(table, profile.column_map, profile.layout, profile.date_format)
        stats = write_work_orders(batch, result.work_orders)
        recompute_vehicle_stats(stats.vehicle_ids)
    except ImportFileError as exc:
        _fail(batch, str(exc))
        return
    except Exception as exc:
        # Exception messages may carry PII (e.g. DB constraint details) — log the type only.
        logger.error("Import batch %s failed: %s", batch_id, type(exc).__name__)
        _fail(batch, "Внутренняя ошибка при обработке файла. Обратитесь в поддержку.")
        raise

    batch.status = ImportBatch.Status.PARSED
    batch.finished_at = timezone.now()
    batch.stats = {
        "rows": result.rows_total,
        "rows_skipped": result.rows_skipped,
        "work_orders": len(result.work_orders),
        "work_orders_created": stats.work_orders_created,
        "work_orders_updated": stats.work_orders_updated,
        "work_orders_unchanged": stats.work_orders_unchanged,
        "clients_created": stats.clients_created,
        "clients_updated": stats.clients_updated,
        "vehicles_created": stats.vehicles_created,
        "vehicles_affected": len(stats.vehicle_ids),
        "errors": len(result.errors),
        "seconds": round(time.monotonic() - started, 1),
    }
    batch.error_log = "\n".join(result.errors)
    batch.save(update_fields=["status", "finished_at", "stats", "error_log", "updated_at"])
    logger.info(
        "Import batch %s parsed: %s work orders, %s errors",
        batch_id,
        len(result.work_orders),
        len(result.errors),
    )
    work_orders_imported.send(
        sender=ImportBatch,
        batch=batch,
        changed_work_order_ids=stats.changed_work_order_ids,
        vehicle_ids=sorted(stats.vehicle_ids),
    )


def _fail(batch: ImportBatch, message: str) -> None:
    batch.status = ImportBatch.Status.FAILED
    batch.finished_at = timezone.now()
    batch.error_log = message
    batch.save(update_fields=["status", "finished_at", "error_log", "updated_at"])
