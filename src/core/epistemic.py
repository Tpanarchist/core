"""Epistemic derived constructions: Claim, Inference, Contradiction, Resolution.

Claim/Inference cross into evidence only by opaque Ref — this module never
imports Observation or Event.

See SPECIFICATION.md (Observation #7, Constraint #14) and ARCHITECTURE.md
(epistemic.py, tier 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.identity import Id, Ref, identity_of
from core.time import WallInstant
from core.value import Kind, Maybe


@dataclass(frozen=True, slots=True, kw_only=True)
class Claim[T]:
    """An assertion about a subject — may rest on evidence, another Claim, an
    Inference, or nothing beyond assertion. Evidence is opaque by Ref.
    """

    id: Id
    subject: Id | Ref
    predicate: Kind
    value: Maybe[T]
    context: Context
    asserted_by: Id | Ref
    evidence_refs: tuple[Ref, ...]
    at: WallInstant

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


@dataclass(frozen=True, slots=True, kw_only=True)
class Inference[T]:
    """Premises + a stated method + a conclusion (itself a Claim)."""

    id: Id
    premises: tuple[Ref, ...]
    method: Kind
    conclusion: Claim[T]
    at: WallInstant

    def __post_init__(self) -> None:
        object.__setattr__(self, "premises", tuple(self.premises))


@dataclass(frozen=True, slots=True, kw_only=True)
class Contradiction:
    """Two or more conflicting Claims/statements — semantic, not structural.

    Never inferred automatically: construction means some higher process
    has already made the judgment that these statements conflict.
    """

    id: Id
    subject: Id | Ref
    statements: tuple[Ref, ...]
    detected_at: WallInstant
    context: Context

    def __post_init__(self) -> None:
        statements = tuple(self.statements)
        object.__setattr__(self, "statements", statements)
        distinct = {identity_of(ref) for ref in statements}
        if len(distinct) < 2:
            raise ValueError(
                "Contradiction.statements must identify at least two distinct entities"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class Resolution:
    """A later, separate fact addressing a specific Contradiction. Never mutates it.

    Not Entity-bearing — nothing in v0 targets a Resolution by Ref.
    """

    contradiction: Ref
    rationale: str
    resolved_by: Id | Ref
    at: WallInstant

    def __post_init__(self) -> None:
        if not self.rationale:
            raise ValueError("Resolution.rationale must not be empty")


class ContradictionLog:
    """An append-only log of Contradiction | Resolution facts.

    A Contradiction is recorded at most once per Id; a Resolution may only
    reference a Contradiction already recorded earlier in this same log —
    making "later" exactly append order, never inferred from WallInstant.
    "Unresolved" is a projection over that history, never a stored flag.
    Single-writer, not thread-safe in v0.
    """

    def __init__(self) -> None:
        self._entries: list[Contradiction | Resolution] = []
        self._contradiction_ids: set[Id] = set()

    def record(self, entry: Contradiction | Resolution) -> Contradiction | Resolution:
        if isinstance(entry, Contradiction):
            if entry.id in self._contradiction_ids:
                raise ValueError(
                    f"a Contradiction with id {entry.id!r} is already recorded in this log"
                )
            self._contradiction_ids.add(entry.id)
        else:
            if entry.contradiction.id not in self._contradiction_ids:
                raise ValueError(
                    f"Resolution references Contradiction {entry.contradiction.id!r}, "
                    "which is not recorded earlier in this log"
                )
        self._entries.append(entry)
        return entry

    def entries(self) -> tuple[Contradiction | Resolution, ...]:
        return tuple(self._entries)

    def unresolved(self) -> tuple[Contradiction, ...]:
        resolved_ids = {
            entry.contradiction.id for entry in self._entries if isinstance(entry, Resolution)
        }
        return tuple(
            entry
            for entry in self._entries
            if isinstance(entry, Contradiction) and entry.id not in resolved_ids
        )

    def for_subject(self, subject: Id | Ref) -> tuple[Contradiction | Resolution, ...]:
        target = identity_of(subject)
        matching_ids: set[Id] = set()
        result: list[Contradiction | Resolution] = []
        for entry in self._entries:
            if isinstance(entry, Contradiction):
                if identity_of(entry.subject) == target:
                    matching_ids.add(entry.id)
                    result.append(entry)
            elif entry.contradiction.id in matching_ids:
                result.append(entry)
        return tuple(result)
