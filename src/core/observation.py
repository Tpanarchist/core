"""Observation: the recorded acquisition of information about some identified subject.

See SPECIFICATION.md #7 and ARCHITECTURE.md (observation.py, tier 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.identity import Id, Ref
from core.time import WallInstant


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation[T]:
    """The recorded acquisition of information about some identified subject.

    Subject, source, time, and context must remain attached — an
    Observation stripped of how it was made degrades into an unmoored
    Value, exactly the collapse this ontology exists to prevent. May
    concern an Event simply by holding that Event's Id/Ref in ``subject``;
    this module never imports Event.
    """

    id: Id
    subject: Id | Ref
    value: T
    at: WallInstant
    source: object
    context: Context
    observer: object | None = None
