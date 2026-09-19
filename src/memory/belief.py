"""BeliefProjection: the mechanically-derived result of asking "what is
currently believed" for a (subject, predicate) slot under a query Context.
Memory selects; Memory does not judge.

See MEMORY_SPECIFICATION.md #5 and MEMORY_ARCHITECTURE.md (belief.py, tier 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.epistemic import Claim, Contradiction, ContradictionLog, Resolution
from core.identity import Id, Ref, identity_of
from core.result import Err, Ok
from core.value import Kind

DETERMINED = Kind("memory.belief.determined")
AMBIGUOUS = Kind("memory.belief.ambiguous")
UNRESOLVED_CONFLICT = Kind("memory.belief.unresolved_conflict")
RESOLVED_OPAQUE_CONFLICT = Kind("memory.belief.resolved_opaque_conflict")
UNKNOWN = Kind("memory.belief.unknown")

_STATUS_KINDS = frozenset(
    {DETERMINED, AMBIGUOUS, UNRESOLVED_CONFLICT, RESOLVED_OPAQUE_CONFLICT, UNKNOWN}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BeliefProjection:
    """An operation's structured result, not a persisted record — same
    category as Core's own AncestorReport.
    """

    subject: Id | Ref
    predicate: Kind
    query_context: Context
    status: Kind
    candidates: tuple[Claim[object], ...]
    conflict_entries: tuple[Contradiction | Resolution, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "conflict_entries", tuple(self.conflict_entries))
        if self.status not in _STATUS_KINDS:
            raise ValueError(f"unknown BeliefProjection status: {self.status!r}")

        has_conflict = len(self.conflict_entries) > 0
        candidate_count = len(self.candidates)

        if self.status == DETERMINED:
            if candidate_count != 1 or has_conflict:
                raise ValueError("DETERMINED requires exactly 1 candidate and no conflict entries")
        elif self.status == AMBIGUOUS:
            if candidate_count < 2 or has_conflict:
                raise ValueError("AMBIGUOUS requires 2+ candidates and no conflict entries")
        elif self.status == UNKNOWN:
            if candidate_count != 0 or has_conflict:
                raise ValueError("UNKNOWN requires 0 candidates and no conflict entries")
        elif self.status in (UNRESOLVED_CONFLICT, RESOLVED_OPAQUE_CONFLICT) and not has_conflict:
            raise ValueError(f"{self.status} requires at least one conflict entry")


def belief_state(
    *,
    subject: Id | Ref,
    predicate: Kind,
    query_context: Context,
    claims: tuple[Claim[object], ...],
    conflict_entries: tuple[Contradiction | Resolution, ...],
) -> BeliefProjection:
    """Mechanical-only projection: never reads Claim.at, never parses
    Resolution.rationale, never performs temporal carry-forward.
    """
    subject_id = identity_of(subject)

    normalized: dict[Id, Claim[object]] = {}
    order: list[Id] = []
    for claim in claims:
        existing = normalized.get(claim.id)
        if existing is None:
            normalized[claim.id] = claim
            order.append(claim.id)
        elif existing != claim:
            raise ValueError(f"conflicting Claim records supplied for the same Id: {claim.id!r}")

    slot_claim_ids: set[Id] = set()
    slot_claims: list[Claim[object]] = []
    for claim_id in order:
        claim = normalized[claim_id]
        if identity_of(claim.subject) == subject_id and claim.predicate == predicate:
            slot_claim_ids.add(claim_id)
            slot_claims.append(claim)

    compatible: list[Claim[object]] = []
    for claim in slot_claims:
        merged = claim.context.merge(query_context)
        if isinstance(merged, Ok):
            compatible.append(claim)
        # isinstance check is a runtime boundary guard: the annotation says
        # Result[Context, ContextConflict], but nothing stops a caller
        # ignoring static typing from handing back something else — the
        # failure needs to be loud, not assumed away.
        elif not isinstance(merged, Err):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"Context.merge() returned neither Ok nor Err: {merged!r}")

    log = ContradictionLog()
    for entry in conflict_entries:
        log.record(entry)

    relevant_entries: list[Contradiction | Resolution] = []
    relevant_contradiction_ids: set[Id] = set()
    for entry in log.entries():
        if isinstance(entry, Contradiction):
            if identity_of(entry.subject) != subject_id:
                continue
            if not any(identity_of(statement) in slot_claim_ids for statement in entry.statements):
                continue
            relevant_contradiction_ids.add(entry.id)
            relevant_entries.append(entry)
        elif entry.contradiction.id in relevant_contradiction_ids:
            relevant_entries.append(entry)

    resolved_ids = {
        entry.contradiction.id for entry in relevant_entries if isinstance(entry, Resolution)
    }
    unresolved_relevant = relevant_contradiction_ids - resolved_ids

    if unresolved_relevant:
        status = UNRESOLVED_CONFLICT
    elif relevant_entries:
        status = RESOLVED_OPAQUE_CONFLICT
    elif len(compatible) >= 2:
        status = AMBIGUOUS
    elif len(compatible) == 1:
        status = DETERMINED
    else:
        status = UNKNOWN

    return BeliefProjection(
        subject=subject,
        predicate=predicate,
        query_context=query_context,
        status=status,
        candidates=tuple(compatible),
        conflict_entries=tuple(relevant_entries),
    )
