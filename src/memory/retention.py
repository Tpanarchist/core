"""RetentionMark / RetentionLog: append-only accessibility history — the
concrete realization of "forgetting" as a projection over history, never
deletion. Direct sibling of Core's ContradictionLog.

See MEMORY_SPECIFICATION.md #4 and MEMORY_ARCHITECTURE.md (retention.py, tier 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.identity import Id, Ref, identity_of
from core.time import WallInstant
from core.value import Kind

ACTIVE = Kind("memory.retention.active")
DEPRIORITIZED = Kind("memory.retention.deprioritized")
ARCHIVED = Kind("memory.retention.archived")


@dataclass(frozen=True, slots=True, kw_only=True)
class RetentionMark:
    item: Ref
    accessibility: Kind
    at: WallInstant
    rationale: str | None = None


class RetentionLog:
    """Append-only, single-writer log — same concurrency family as Core's
    ContradictionLog. "Current" is always a projection over append order,
    never a stored flag and never sorted by ``at``.
    """

    def __init__(self) -> None:
        self._marks: list[RetentionMark] = []

    def record(self, mark: RetentionMark) -> RetentionMark:
        self._marks.append(mark)
        return mark

    def current(self, item: Id | Ref) -> Kind:
        target = identity_of(item)
        for mark in reversed(self._marks):
            if identity_of(mark.item) == target:
                return mark.accessibility
        return ACTIVE

    def history(self, item: Id | Ref) -> tuple[RetentionMark, ...]:
        target = identity_of(item)
        return tuple(mark for mark in self._marks if identity_of(mark.item) == target)
