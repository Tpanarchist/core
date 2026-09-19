"""RecallCandidate / WorkingSet: retrieval evidence and bounded attention.

See MEMORY_SPECIFICATION.md #2-3 and MEMORY_ARCHITECTURE.md (recall.py, tier 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.identity import Ref
from core.time import WallInstant
from core.value import Kind

IDENTITY_MATCH = Kind("memory.relevance.identity")
LEXICAL_MATCH = Kind("memory.relevance.lexical")
CONTEXTUAL_MATCH = Kind("memory.relevance.contextual")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecallCandidate:
    """A proposed memory item surfaced by retrieval, with structured evidence
    for why — never a truth claim. Not Entity-bearing: nothing targets a
    RecallCandidate by Ref.
    """

    item: Ref
    query_context: Context
    relevance: tuple[Kind, ...]
    retrieved_at: WallInstant

    def __post_init__(self) -> None:
        relevance = tuple(self.relevance)
        object.__setattr__(self, "relevance", relevance)
        if not relevance:
            raise ValueError("RecallCandidate.relevance must not be empty")
        if len(set(relevance)) != len(relevance):
            raise ValueError("RecallCandidate.relevance must not contain duplicate Kinds")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingSet:
    """The bounded subset of RecallCandidates currently admitted to active
    reasoning — the concrete realization of "attention."
    """

    capacity: int
    admitted: tuple[RecallCandidate, ...]

    def __post_init__(self) -> None:
        admitted = tuple(self.admitted)
        object.__setattr__(self, "admitted", admitted)
        if self.capacity < 0:
            raise ValueError("WorkingSet.capacity must not be negative")
        if len(admitted) > self.capacity:
            raise ValueError("WorkingSet.admitted must not exceed capacity")


def admit(
    candidates: tuple[RecallCandidate, ...], capacity: int
) -> tuple[WorkingSet, tuple[RecallCandidate, ...]]:
    """Admit the first ``capacity`` candidates in caller order. No
    reranking, scoring, deduplication, or retention lookup — admit() bounds
    attention, it does not decide relevance.
    """
    if capacity < 0:
        raise ValueError("capacity must not be negative")
    candidates = tuple(candidates)
    admitted = candidates[:capacity]
    excluded = candidates[capacity:]
    return WorkingSet(capacity=capacity, admitted=admitted), excluded
