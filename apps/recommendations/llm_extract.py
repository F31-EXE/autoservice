"""LLM extraction of recommendations from free text (spec 6.2).

Two layers:
- ``extract(...)`` — pure: anonymize → prompt → LLM → validate → post-process. No DB writes,
  safe to run in threads; used by the eval command too.
- ``extract_pending(...)`` / ``save_extraction(...)`` — orchestration and persistence.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from string import Template

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import WorkType
from apps.llm.accounting import budget_exceeded, record_call
from apps.llm.anonymize import anonymize
from apps.llm.client import LLMClient, LLMError, LLMResponse, get_client
from apps.workorders.models import WorkOrder, WorkOrderLine

from .llm_schema import ExtractedRecommendation, InvalidAnswer, parse_answer
from .models import Recommendation
from .rules import add_months

logger = logging.getLogger(__name__)

PROMPT_VERSION = "extract_v1"
PROMPT = Template((Path(settings.BASE_DIR) / "apps/llm/prompts/extract_v1.txt").read_text("utf-8"))
OPEN_THRESHOLD = 0.7
NOT_QUOTED_CONFIDENCE = 0.3
NO_DEADLINE_DAYS = 90
DECLINED_DUE_DAYS = 14
MAX_TOKENS = 1500


@dataclass
class ExtractionInput:
    """Everything the LLM sees about one work order (already PII-free)."""

    vehicle: str
    mileage: int | None
    executed_works: list[str]
    mechanic_comment: str
    recommendations_text: str

    @property
    def text(self) -> str:
        parts = []
        if self.recommendations_text:
            parts.append(f"Рекомендации: {self.recommendations_text}")
        if self.mechanic_comment:
            parts.append(f"Комментарий мастера: {self.mechanic_comment}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "vehicle": self.vehicle,
            "mileage": self.mileage,
            "executed_works": self.executed_works,
            "mechanic_comment": self.mechanic_comment,
            "recommendations_text": self.recommendations_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExtractionInput:
        vehicle = data.get("vehicle") or " ".join(
            str(data[k]) for k in ("make", "model", "year") if data.get(k)
        )
        return cls(
            vehicle=vehicle,
            mileage=data.get("mileage"),
            executed_works=list(data.get("executed_works", [])),
            mechanic_comment=data.get("mechanic_comment", ""),
            recommendations_text=data.get("recommendations_text", ""),
        )


@dataclass
class ExtractionResult:
    recommendations: list[ExtractedRecommendation] = field(default_factory=list)
    responses: list[LLMResponse] = field(default_factory=list)
    error: str = ""  # PII-free
    attempts: int = 0


def build_input(wo: WorkOrder) -> ExtractionInput:
    client = wo.vehicle.client
    names = [client.full_name] if client.full_name else []
    vehicle = wo.vehicle
    lines = [ln for ln in wo.lines.all() if ln.executed and ln.kind == WorkOrderLine.Kind.LABOR]
    return ExtractionInput(
        vehicle=" ".join(str(x) for x in (vehicle.make, vehicle.model, vehicle.year) if x),
        mileage=wo.mileage,
        executed_works=[anonymize(ln.name, names) for ln in lines][:50],
        mechanic_comment=anonymize(wo.mechanic_comment, names),
        recommendations_text=anonymize(wo.recommendations_text, names),
    )


def catalog_text(work_types) -> str:
    return "\n".join(f"{wt.code} — {wt.name}" for wt in work_types)


def build_messages(data: ExtractionInput, catalog: str) -> list[dict]:
    prompt = PROMPT.substitute(
        catalog=catalog,
        vehicle=data.vehicle or "не указан",
        mileage=f"{data.mileage} км" if data.mileage else "не указан",
        executed="; ".join(data.executed_works) or "нет данных",
        text=data.text,
    )
    return [{"role": "user", "content": prompt}]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def postprocess(
    recs: list[ExtractedRecommendation],
    data: ExtractionInput,
    codes: set[str],
    executed_codes: set[str],
) -> list[ExtractedRecommendation]:
    """Deterministic checks on top of the LLM answer (spec 6.2)."""
    haystack = _norm(data.text)
    result, seen = [], set()
    for rec in recs:
        rec = rec.model_copy()
        if rec.work_type_code not in codes:
            rec.work_type_code = None
        if not rec.work_type_raw:
            rec.work_type_raw = rec.source_text[:200]
        if _norm(rec.source_text) not in haystack:
            rec.confidence = min(rec.confidence, NOT_QUOTED_CONFIDENCE)
        if rec.work_type_code and rec.work_type_code in executed_codes:
            continue  # done in this very visit
        key = rec.work_type_code or _norm(rec.work_type_raw)
        if key in seen:
            continue
        seen.add(key)
        result.append(rec)
    return result


def extract(
    data: ExtractionInput,
    client: LLMClient,
    model: str,
    work_types: list[WorkType],
    executed_codes: set[str] = frozenset(),
) -> ExtractionResult:
    """Pure extraction with one retry on an invalid answer. Never touches the DB."""
    result = ExtractionResult()
    messages = build_messages(data, catalog_text(work_types))
    codes = {wt.code for wt in work_types}
    for _attempt in range(2):
        result.attempts += 1
        try:
            response = client.complete(messages, model=model, max_tokens=MAX_TOKENS)
        except LLMError as exc:
            result.error = f"llm: {exc}"
            return result
        result.responses.append(response)
        try:
            answer = parse_answer(response.text)
        except InvalidAnswer as exc:
            result.error = f"invalid: {exc}"
            continue
        result.error = ""
        result.recommendations = postprocess(answer.recommendations, data, codes, executed_codes)
        return result
    return result


# ------------------------------------------------------------------ persistence


def _source_for(rec: ExtractedRecommendation, wo: WorkOrder, data: ExtractionInput) -> str:
    if data.recommendations_text and _norm(rec.source_text) in _norm(data.recommendations_text):
        return Recommendation.Source.EXPLICIT_FIELD
    return Recommendation.Source.FREE_TEXT


@transaction.atomic
def save_extraction(
    wo: WorkOrder, data: ExtractionInput, result: ExtractionResult, model: str
) -> int:
    """Write recommendations for one work order. Idempotent per work order."""
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if result.error:
        wo.extraction_status = WorkOrder.ExtractionStatus.FAILED
        wo.save(update_fields=["extraction_status", "updated_at"])
        return 0

    llm_sources = (Recommendation.Source.FREE_TEXT, Recommendation.Source.EXPLICIT_FIELD)
    # re-extraction replaces previous unreviewed LLM output of this work order
    Recommendation.objects.filter(
        source_work_order=wo,
        source__in=llm_sources,
        status__in=(Recommendation.Status.OPEN, Recommendation.Status.NEEDS_REVIEW),
    ).delete()

    work_types = {wt.code: wt for wt in WorkType.objects.all()}
    visit = wo.visit_date
    version = f"{PROMPT_VERSION}+{model}"[:128]
    created = 0
    for rec in result.recommendations:
        wt = work_types.get(rec.work_type_code) if rec.work_type_code else None
        vehicle_recs = Recommendation.objects.filter(vehicle_id=wo.vehicle_id)
        same = vehicle_recs.filter(work_type=wt) if wt else vehicle_recs.filter(
            work_type__isnull=True, work_type_raw=rec.work_type_raw[:512]
        )  # fmt: skip
        if same.filter(source_work_order=wo, status=Recommendation.Status.REJECTED).exists():
            continue  # a manager rejected it before
        if wt and same.active().filter(source_work_order=wo).exists():
            continue  # rules already produced it from this visit (rules take precedence)

        due_km = (
            wo.mileage + rec.due_km_delta if wo.mileage and rec.due_km_delta is not None else None
        )
        due_date = add_months(visit, rec.due_months_delta) if rec.due_months_delta else None
        if due_km is None and due_date is None:
            days = (
                DECLINED_DUE_DAYS
                if rec.kind == "declined" or rec.due_km_delta == 0
                else NO_DEADLINE_DAYS
            )
            due_date = visit + timedelta(days=days)
        status = (
            Recommendation.Status.OPEN
            if rec.confidence >= OPEN_THRESHOLD and wt is not None
            else Recommendation.Status.NEEDS_REVIEW
        )
        fields = {
            "source": _source_for(rec, wo, data),
            "source_text": rec.source_text,
            "due_km": due_km,
            "due_date": due_date,
            "urgency": rec.urgency,
            "confidence": rec.confidence,
            "extractor_version": version,
            "work_type_raw": rec.work_type_raw[:512],
        }

        existing = None
        if wt is not None:
            existing = (
                same.active()
                .select_related("source_work_order")
                .order_by("-source_work_order__opened_at", "-id")
                .first()
            )
        if existing is not None:
            if existing.source_work_order.visit_date > visit:
                continue  # newer information exists
            for name, value in fields.items():
                setattr(existing, name, value)
            existing.source_work_order = wo
            if existing.status == Recommendation.Status.NEEDS_REVIEW:
                existing.status = status
            existing.save()
            target = existing
        else:
            target = Recommendation.objects.create(
                station_id=wo.station_id,
                vehicle_id=wo.vehicle_id,
                source_work_order=wo,
                work_type=wt,
                status=status,
                **fields,
            )
            created += 1
        if wt is not None:
            _close_if_done_later(target, wo)

    wo.extraction_status = WorkOrder.ExtractionStatus.DONE
    wo.save(update_fields=["extraction_status", "updated_at"])
    return created


def _close_if_done_later(rec: Recommendation, wo: WorkOrder) -> None:
    later = (
        WorkOrderLine.objects.filter(
            work_order__vehicle_id=wo.vehicle_id,
            work_order__status=WorkOrder.Status.CLOSED,
            work_type_id=rec.work_type_id,
            executed=True,
            kind=WorkOrderLine.Kind.LABOR,
        )
        .exclude(work_order_id=wo.id)
        .select_related("work_order")
    )
    candidates = [ln.work_order for ln in later if ln.work_order.visit_date > wo.visit_date]
    if candidates:
        rec.status = Recommendation.Status.DONE
        rec.closed_by_work_order = min(candidates, key=lambda w: (w.visit_date, w.id))
        rec.save(update_fields=["status", "closed_by_work_order", "updated_at"])


# ------------------------------------------------------------------ orchestration


def _mark_skipped(station_id: int) -> int:
    """No text → no LLM call (spec 6.2). Not-closed and too old orders are skipped too:
    their recommendations would be expired anyway, so we don't pay for them."""
    from apps.accounts.models import ServiceStation

    conf = ServiceStation.objects.get(pk=station_id).conf
    max_age = int(conf["llm_extract_max_age_days"] or conf["recommendation_ttl_days"])
    cutoff = timezone.localdate() - timedelta(days=max_age)
    return (
        WorkOrder.objects.filter(
            station_id=station_id, extraction_status=WorkOrder.ExtractionStatus.PENDING
        )
        .filter(
            Q(mechanic_comment="", recommendations_text="")
            | ~Q(status=WorkOrder.Status.CLOSED)
            | Q(opened_at__lt=cutoff)
        )
        .update(extraction_status=WorkOrder.ExtractionStatus.SKIPPED, updated_at=timezone.now())
    )


def extract_pending(station_id: int, limit: int | None = None) -> dict[str, int]:
    """Process pending work orders of a station. Stops when the monthly budget is exhausted."""
    stats = {"skipped": _mark_skipped(station_id), "done": 0, "failed": 0, "created": 0}
    model = settings.LLM_MODEL_EXTRACT
    limit = limit or settings.LLM_EXTRACT_BATCH_SIZE
    concurrency = max(1, int(settings.LLM_CONCURRENCY))
    work_types = list(WorkType.objects.all())
    code_by_id = {wt.id: wt.code for wt in work_types}
    try:
        client = get_client()
    except LLMError as exc:
        # not configured (no API key): leave orders pending, extraction resumes once configured
        logger.warning("LLM extraction is not configured: %s", exc)
        stats["not_configured"] = 1
        return stats

    while True:
        if budget_exceeded(station_id):
            stats["budget_exceeded"] = 1
            break
        batch = list(
            WorkOrder.objects.filter(
                station_id=station_id, extraction_status=WorkOrder.ExtractionStatus.PENDING
            )
            .select_related("vehicle__client")
            .prefetch_related("lines")
            .order_by("-opened_at", "id")[:concurrency]
        )
        if not batch:
            break
        inputs = [build_input(wo) for wo in batch]
        executed = [
            {
                code_by_id[ln.work_type_id]
                for ln in wo.lines.all()
                if ln.executed and ln.work_type_id
            }
            for wo in batch
        ]
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(
                pool.map(
                    lambda args: extract(args[0], client, model, work_types, args[1]),
                    zip(inputs, executed, strict=True),
                )
            )
        for wo, data, result in zip(batch, inputs, results, strict=True):
            _record(wo.station_id, model, data, result)
            stats["created"] += save_extraction(wo, data, result, model)
            stats["failed" if result.error else "done"] += 1
            if result.error:
                logger.warning("Extraction failed for work order %s: %s", wo.pk, result.error)
        limit -= len(batch)
        if limit <= 0:
            stats["more"] = 1
            break
    return stats


def _record(station_id, model, data: ExtractionInput, result: ExtractionResult) -> None:
    for i, response in enumerate(result.responses):
        last = i == len(result.responses) - 1
        error = result.error if last and result.error.startswith("invalid") else ""
        if not last:
            error = "invalid answer, retried"
        record_call(
            station_id=station_id,
            purpose="extract",
            prompt_version=PROMPT_VERSION,
            model=model,
            response=response,
            error=error,
            payload={"input": data.to_dict(), "answer": response.text},
        )
    if result.error.startswith("llm:"):
        record_call(
            station_id=station_id,
            purpose="extract",
            prompt_version=PROMPT_VERSION,
            model=model,
            error=result.error,
        )
