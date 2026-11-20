"""Rule-based mapping of work order line names to WorkType (spec 6.1, no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import WorkType, WorkTypeAlias

_SPACES = re.compile(r"\s+")


def normalize_name(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = text.replace("\\", "/")
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class CompiledAlias:
    work_type_id: int
    kind: str
    priority: int
    required: tuple[tuple[str, ...], ...] = ()
    forbidden: tuple[str, ...] = ()
    regex: re.Pattern[str] | None = None

    @classmethod
    def build(cls, alias: WorkTypeAlias) -> CompiledAlias | None:
        if alias.kind == WorkTypeAlias.Kind.REGEX:
            try:
                compiled = re.compile(alias.pattern, re.IGNORECASE)
            except re.error:
                return None
            return cls(alias.work_type_id, alias.kind, alias.priority, regex=compiled)
        required, forbidden = [], []
        for group in normalize_name(alias.pattern).split(" "):
            if group.startswith("-") and len(group) > 1:
                forbidden.extend(a.replace("_", " ") for a in group[1:].split("|") if a)
            elif group:
                required.append(tuple(a.replace("_", " ") for a in group.split("|") if a))
        if not required:
            return None
        return cls(
            alias.work_type_id, alias.kind, alias.priority, tuple(required), tuple(forbidden)
        )

    def score(self, name: str) -> int:
        """0 = no match; higher = more specific match."""
        if self.regex is not None:
            m = self.regex.search(name)
            return (m.end() - m.start()) or 1 if m else 0
        if any(word in name for word in self.forbidden):
            return 0
        total = 0
        for alternatives in self.required:
            best = max((len(a) for a in alternatives if a in name), default=0)
            if not best:
                return 0
            total += best
        return total


class WorkTypeMatcher:
    """Loads aliases once; use one instance per station per batch."""

    def __init__(self, station_id: int | None) -> None:
        aliases = WorkTypeAlias.objects.filter(station_id=station_id) if station_id else []
        self.station_aliases = [a for a in map(CompiledAlias.build, aliases) if a]
        self.global_aliases = [
            a
            for a in map(CompiledAlias.build, WorkTypeAlias.objects.filter(station__isnull=True))
            if a
        ]
        self.work_types = {wt.id: wt for wt in WorkType.objects.all()}
        self._cache: dict[str, WorkType | None] = {}

    @staticmethod
    def _best(aliases: list[CompiledAlias], name: str) -> int | None:
        best_key, best_id = None, None
        for alias in aliases:
            score = alias.score(name)
            if score and (best_key is None or (alias.priority, score) > best_key):
                best_key, best_id = (alias.priority, score), alias.work_type_id
        return best_id

    def match(self, line_name: str) -> WorkType | None:
        name = normalize_name(line_name)
        if not name:
            return None
        if name in self._cache:
            return self._cache[name]
        wt_id = self._best(self.station_aliases, name) or self._best(self.global_aliases, name)
        result = self.work_types.get(wt_id) if wt_id else None
        self._cache[name] = result
        return result
