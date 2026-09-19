"""State, Transition, History: what holds, how it changes, and its history.

See SPECIFICATION.md #5 (State) and ARCHITECTURE.md (state.py, tier 5).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.event import Event
from core.identity import Id, Ref, identity_of
from core.time import WallInstant


@dataclass(frozen=True, slots=True, kw_only=True)
class State[T]:
    """What holds for some identified subject, as of some reading of time.

    A snapshot, not a history — not necessarily *now*. Not Entity-bearing:
    identified only by subject + time, which is sufficient for everything
    that uses it. Content equality; Core does not claim transitive
    immutability of arbitrary ``value: T``.
    """

    subject: Id | Ref
    value: T
    at: WallInstant
    context: Context


@dataclass(frozen=True, slots=True, kw_only=True)
class Transition[T]:
    """The record connecting a before-State to an after-State via an Event.

    Mutation hides history; a Transition explains it. No invariant is
    imposed on ``event.at`` relative to the two State timestamps — the
    frozen semantics don't require it.
    """

    id: Id
    before: State[T]
    event: Event
    operation: str
    after: State[T]

    def __post_init__(self) -> None:
        if not self.operation:
            raise ValueError("Transition.operation must not be empty")
        if identity_of(self.before.subject) != identity_of(self.after.subject):
            raise ValueError(
                "Transition.before and Transition.after must concern the same subject"
            )
        if self.after.at < self.before.at:
            raise ValueError("Transition.after.at must not be earlier than before.at")

    @property
    def subject(self) -> Id | Ref:
        """Derived from ``before.subject`` — never independently stored."""
        return self.before.subject


class History[T]:
    """An append-only, subject-scoped, single-writer sequence of Transitions.

    Not thread-safe in v0. A Transition Id may occupy at most one position
    in a given History, and the sequence must be contiguous — each new
    Transition picks up exactly where the last one left off.
    """

    def __init__(self, subject: Id | Ref) -> None:
        self._subject = subject
        self._transitions: list[Transition[T]] = []
        self._transition_ids: set[Id] = set()

    @property
    def subject(self) -> Id | Ref:
        return self._subject

    def append(self, transition: Transition[T]) -> Transition[T]:
        if identity_of(transition.before.subject) != identity_of(self._subject):
            raise ValueError("Transition's subject does not match this History's subject")
        if transition.id in self._transition_ids:
            raise ValueError(
                f"a Transition with id {transition.id!r} is already recorded in this History"
            )
        if self._transitions:
            previous = self._transitions[-1]
            if previous.after != transition.before:
                raise ValueError(
                    "Transition does not pick up where the previous one left off "
                    "(previous.after != transition.before)"
                )
        self._transitions.append(transition)
        self._transition_ids.add(transition.id)
        return transition

    def entries(self) -> tuple[Transition[T], ...]:
        return tuple(self._transitions)
