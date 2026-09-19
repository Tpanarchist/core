"""Episode: an asserted, ordered grouping of references to preserved Core
facts under one subject and context.

See MEMORY_SPECIFICATION.md #1 and MEMORY_ARCHITECTURE.md (episode.py, tier 0).
"""

from __future__ import annotations

from core.context import Context
from core.identity import Id, Ref
from core.time import WallInstant


class Episode:
    """Mutable, single-writer, append-only grouping — same concurrency
    family as Core's Trace/History/ContradictionLog. Entity-bearing via
    ``id``. ``id`` is always caller-supplied; Episode never allocates its
    own identity.
    """

    def __init__(
        self,
        *,
        id: Id,
        subject: Id | Ref,
        context: Context,
        opened_at: WallInstant,
    ) -> None:
        self._id = id
        self._subject = subject
        self._context = context
        self._opened_at = opened_at
        self._closed_at: WallInstant | None = None
        self._items: list[Ref] = []

    @property
    def id(self) -> Id:
        return self._id

    @property
    def subject(self) -> Id | Ref:
        return self._subject

    @property
    def context(self) -> Context:
        return self._context

    @property
    def opened_at(self) -> WallInstant:
        return self._opened_at

    @property
    def closed_at(self) -> WallInstant | None:
        return self._closed_at

    def append(self, ref: Ref) -> Ref:
        if self._closed_at is not None:
            raise ValueError("cannot append to a closed Episode")
        self._items.append(ref)
        return ref

    def items(self) -> tuple[Ref, ...]:
        return tuple(self._items)

    def close(self, at: WallInstant) -> WallInstant:
        if self._closed_at is not None:
            raise ValueError("Episode is already closed")
        if at < self._opened_at:
            raise ValueError("closed_at must not precede opened_at")
        self._closed_at = at
        return at
