"""Event: something that occurred — a discrete, dated occurrence.

See SPECIFICATION.md #6 and ARCHITECTURE.md (event.py, tier 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.identity import Id
from core.time import WallInstant
from core.value import Kind


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Event:
    """Something that occurred, independent of who noticed it or what it changed.

    Equality and hashing are identity equality — "compare by Id," per the
    frozen specification's own operation for this concept. Two Events
    sharing an Id but disagreeing in other fields are still the same
    identified Event represented inconsistently; Python equality does not
    silently invent a second notion of Event identity to paper over that.
    """

    id: Id
    kind: Kind
    at: WallInstant
    payload: object = None
    context: Context | None = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def summary(self) -> tuple[Id, Kind, WallInstant]:
        """A small, lossless-for-identity projection — no formatting policy."""
        return self.id, self.kind, self.at
