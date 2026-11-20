"""Evaluate LLM extraction on a golden set (spec 6.3).

    python manage.py eval_extraction --golden tests/golden/extraction.jsonl --model <name>
    python manage.py eval_extraction --golden tests/golden/extraction.jsonl --model mock

Golden format (JSONL): {"input": {make, model, year, mileage, executed_works,
mechanic_comment, recommendations_text}, "expected": [{"work_type_code": ..., ...}]}.
Inputs are anonymized before sending, exactly like in production.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.matching import WorkTypeMatcher
from apps.catalog.models import WorkType
from apps.llm.accounting import record_call
from apps.llm.anonymize import anonymize
from apps.llm.client import FakeLLMClient, get_client
from apps.recommendations.llm_extract import PROMPT_VERSION, ExtractionInput, extract
from apps.recommendations.llm_mock import HeuristicResponder


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def score(examples: list[dict], predictions: list[list[dict]]) -> dict[str, Counts]:
    presence, codes = Counts(), Counts()
    for example, predicted in zip(examples, predictions, strict=True):
        expected = example["expected"]
        has_exp, has_pred = bool(expected), bool(predicted)
        presence.tp += has_exp and has_pred
        presence.fp += has_pred and not has_exp
        presence.fn += has_exp and not has_pred
        exp_codes = {e["work_type_code"] for e in expected if e.get("work_type_code")}
        pred_codes = {p["work_type_code"] for p in predicted if p.get("work_type_code")}
        codes.tp += len(exp_codes & pred_codes)
        codes.fp += len(pred_codes - exp_codes)
        codes.fn += len(exp_codes - pred_codes)
    return {"presence": presence, "work_type_code": codes}


class Command(BaseCommand):
    help = "Run LLM extraction on a golden set and report precision/recall/F1 and cost"

    def add_arguments(self, parser):
        parser.add_argument("--golden", required=True)
        parser.add_argument("--model", default="", help="model id/URI, or 'mock' for offline")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--show-errors", action="store_true")
        parser.add_argument(
            "--min-precision", type=float, default=0.0, help="exit with error if below"
        )

    def handle(self, *args, golden, model, limit, show_errors, min_precision, **options):
        path = Path(golden)
        if not path.exists():
            raise CommandError(f"Golden file not found: {path}")
        examples = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]
        if limit:
            examples = examples[:limit]
        is_mock = model == "mock"
        model = model or settings.LLM_MODEL_EXTRACT
        client = FakeLLMClient(HeuristicResponder()) if is_mock else get_client()
        work_types = list(WorkType.objects.all())

        matcher = WorkTypeMatcher(None)
        inputs, executed = [], []
        for ex in examples:
            data = ExtractionInput.from_dict(ex["input"])
            data.mechanic_comment = anonymize(data.mechanic_comment)
            data.recommendations_text = anonymize(data.recommendations_text)
            data.executed_works = [anonymize(w) for w in data.executed_works]
            inputs.append(data)
            # same as production: works done in this visit are matched by rules and excluded
            executed.append({wt.code for w in data.executed_works if (wt := matcher.match(w))})

        workers = max(1, int(settings.LLM_CONCURRENCY))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(
                    lambda args: extract(args[0], client, model, work_types, args[1]),
                    zip(inputs, executed, strict=True),
                )
            )

        predictions, cost, tokens_in, tokens_out, latency, failed = [], Decimal(0), 0, 0, 0, 0
        for data, result in zip(inputs, results, strict=True):
            predictions.append([r.model_dump() for r in result.recommendations])
            failed += bool(result.error)
            for response in result.responses:
                tokens_in += response.input_tokens
                tokens_out += response.output_tokens
                latency += response.latency_ms
                if not is_mock:
                    cost += response.cost_rub
                    record_call(
                        station_id=None, purpose="extract", prompt_version=PROMPT_VERSION,
                        model=model, response=response, error=result.error,
                        payload={"input": data.to_dict(), "answer": response.text},
                    )  # fmt: skip

        metrics = score(examples, predictions)
        n = len(examples) or 1
        out = self.stdout.write
        out(f"Examples: {len(examples)}  model: {model or '(not set)'}  prompt: {PROMPT_VERSION}")
        out(f"{'metric':<16}{'precision':>10}{'recall':>10}{'f1':>10}")
        for name, c in metrics.items():
            out(f"{name:<16}{c.precision:>10.3f}{c.recall:>10.3f}{c.f1:>10.3f}")
        out(f"Failed answers: {failed}")
        out(f"Tokens per WO: in {tokens_in / n:.0f}, out {tokens_out / n:.0f}; "
            f"avg latency {latency / n:.0f} ms")  # fmt: skip
        out(f"Cost per WO: {cost / n:.4f} RUB (total {cost:.2f} RUB)")

        if show_errors:
            for i, (ex, pred) in enumerate(zip(examples, predictions, strict=True), start=1):
                exp = sorted(e["work_type_code"] or "-" for e in ex["expected"])
                got = sorted(p["work_type_code"] or "-" for p in pred)
                if exp != got:
                    out(f"#{i}: expected {exp}, got {got}")

        precision = metrics["work_type_code"].precision
        if precision < min_precision:
            raise CommandError(f"precision {precision:.3f} < {min_precision}")
