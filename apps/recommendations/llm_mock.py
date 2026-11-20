"""Offline heuristic "LLM" for dev and the eval mock. Not a production extractor.

It splits the text into phrases and maps each phrase with the rule-based catalog matcher.
All DB access happens in ``__init__`` so the responder is safe to call from threads.
"""

from __future__ import annotations

import json
import re

from apps.catalog.matching import WorkTypeMatcher

_TEXT = re.compile(r"<<<\n(.*)\n>>>", re.S)
_SPLIT = re.compile(r";|\n|\.\s+(?=[А-ЯЁA-Z])")
_PREFIX = re.compile(r"^(рекомендации|комментарий мастера)\s*:\s*", re.I)
_KM = re.compile(r"через\s+(\d+(?:[.,]\d+)?)\s*(?:-\s*\d+(?:[.,]\d+)?\s*)?(т\.?\s*км|тыс)", re.I)
_KM_FULL = re.compile(r"через\s+(\d{3,6})\s*км", re.I)
_MONTHS = re.compile(r"через\s+(\d+)\s*(мес|год|лет)", re.I)
_DECLINED = re.compile(r"отказ|отложил|не стал|перенес|без замены", re.I)


class HeuristicResponder:
    def __init__(self, station_id: int | None = None) -> None:
        self.matcher = WorkTypeMatcher(station_id)
        # warm up lazy attributes; match() then works purely in memory
        self.matcher.match("")

    def __call__(self, messages: list[dict]) -> str:
        found = _TEXT.search(messages[-1]["content"])
        text = found.group(1) if found else ""
        recs, seen = [], set()
        for raw in _SPLIT.split(text):
            segment = _PREFIX.sub("", raw.strip()).strip(" .,")
            if len(segment) < 4:
                continue
            wt = self.matcher.match(segment) or self.matcher.match("замена " + segment)
            if wt is None or wt.code in seen:
                continue
            seen.add(wt.code)
            km = months = None
            if m := _KM.search(segment):
                km = int(float(m.group(1).replace(",", ".")) * 1000)
            elif m := _KM_FULL.search(segment):
                km = int(m.group(1))
            if m := _MONTHS.search(segment):
                months = int(m.group(1)) * (1 if m.group(2).lower().startswith("мес") else 12)
            recs.append(
                {
                    "work_type_code": wt.code,
                    "work_type_raw": wt.name,
                    "source_text": segment,
                    "kind": "declined" if _DECLINED.search(segment) else "future",
                    "due_km_delta": km,
                    "due_months_delta": months,
                    "urgency": wt.urgency_default,
                    "confidence": 0.8,
                }
            )
        return json.dumps({"recommendations": recs}, ensure_ascii=False)
