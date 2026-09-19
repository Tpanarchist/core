"""Trace: structured, ordered evidence of what actually happened during execution.

Trace knows evidence; it does not know what all possible evidence means —
this module never imports Event, Effect, Provenance, Transition, or Error.

See SPECIFICATION.md #16 and ARCHITECTURE.md (trace.py, tier 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.identity import Id, Ref
from core.time import Sequence, WallInstant
from core.value import Kind


@dataclass(frozen=True, slots=True)
class TraceEntry:
    """One piece of structured, ordered evidence — opaque to what its payload means.

    ``sequence`` is authoritative order; ``observed_at`` is non-authoritative
    observational wall time only, never used to determine order. References
    are opaque ``Ref``s — this type never imports the concrete types they
    might identify.
    """

    subject: Id | Ref | None
    kind: Kind
    sequence: Sequence
    observed_at: WallInstant | None
    payload: object
    context: Context | None
    references: tuple[Ref, ...]


class Trace:
    """An append-only, single-writer evidence log, anchored by its own identity.

    ``Trace.append()`` is the only supported creation path for entries that
    end up in ``entries()`` — an API invariant, not a guarantee against a
    determined caller manually constructing a ``TraceEntry`` outside the API.
    Not thread-safe in v0 (no locks — an unearned abstraction with no real
    concurrent consumer yet).
    """

    def __init__(self, id: Id) -> None:
        self._id = id
        self._entries: list[TraceEntry] = []

    @property
    def id(self) -> Id:
        return self._id

    def append(
        self,
        *,
        kind: Kind,
        subject: Id | Ref | None = None,
        payload: object = None,
        context: Context | None = None,
        observed_at: WallInstant | None = None,
        references: tuple[Ref, ...] = (),
    ) -> TraceEntry:
        """Construct and admit one TraceEntry, assigning its authoritative order.

        No caller-supplied Sequence is accepted — positions are contiguous
        and monotonic within this Trace's own space, starting at 0.
        """
        sequence = Sequence(space=self._id, position=len(self._entries))
        entry = TraceEntry(
            subject=subject,
            kind=kind,
            sequence=sequence,
            observed_at=observed_at,
            payload=payload,
            context=context,
            references=tuple(references),
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> tuple[TraceEntry, ...]:
        """An immutable snapshot — never the live internal collection."""
        return tuple(self._entries)

    def since(self, marker: Sequence) -> tuple[TraceEntry, ...]:
        """Entries strictly after ``marker`` (exclusive checkpoint semantics).

        Raises if ``marker`` isn't from this Trace's own space, or doesn't
        identify a currently-existing entry — never silently reinterpreted
        as "from the beginning" or "nothing new."
        """
        if marker.space != self._id:
            raise ValueError(
                f"marker space {marker.space!r} does not match this Trace's id {self._id!r}"
            )
        if not (0 <= marker.position < len(self._entries)):
            raise ValueError(
                f"marker position {marker.position} does not identify an entry in this "
                f"Trace (has {len(self._entries)} entries)"
            )
        return tuple(self._entries[marker.position + 1 :])
