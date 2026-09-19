"""Identity: the fact that some particular thing is that thing and not another,
independent of its current properties.

See SPECIFICATION.md #1 and ARCHITECTURE.md (identity.py, tier 1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.value import Kind


@dataclass(frozen=True, slots=True)
class Id:
    """A canonical ``(kind, value)`` identity token.

    Equality means the same identity token — not real-world co-reference
    between two *different* tokens, which is a Relation/reconciliation
    problem, not an equality claim Core makes for you.
    """

    kind: Kind
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Id.value must not be empty")


@runtime_checkable
class Entity(Protocol):
    """Structural "has identity" — implemented by anything carrying an ``id: Id``.

    A capability expressed without inheritance: nothing needs to subclass
    ``Entity`` to satisfy it, only to have the attribute. ``id`` is a
    read-only property, not a plain protocol variable — protocol variables
    are readable *and writable* by default, and Core's identity-bearing
    records are structurally immutable, so this should not tell type
    checkers that a consumer may assign a new identity through the
    protocol. A settable attribute on a concrete class still structurally
    satisfies this (it's strictly more permissive than what's required).
    """

    @property
    def id(self) -> Id: ...


@dataclass(frozen=True, slots=True)
class Namespace:
    """A tuple of non-empty string segments giving an identifier its scope."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("Namespace must have at least one segment")
        for segment in self.segments:
            if not segment:
                raise ValueError("Namespace segments must not be empty")


@dataclass(frozen=True, slots=True)
class Ref:
    """Points at an Entity's identity. Distinct from the identity itself.

    Equality includes the namespace: two references to the same ``Id`` in
    different namespaces are different ``Ref``s, even though they identify
    the same underlying entity — see ``identity_of`` for entity-identity
    comparison that looks through this.
    """

    id: Id
    namespace: Namespace | None = None


def identity_of(value: Id | Ref) -> Id:
    """The canonical mechanism for identity-equivalence across ``Id | Ref`` fields.

    ``Id(kind, value)`` and ``Ref(id=Id(kind, value))`` are different Python
    objects referring to the same identity; ordinary ``==`` would wrongly
    treat them as different subjects. A ``Ref``'s own namespace stays
    relevant to the reference itself — it must never make the referred
    entity compare as a different entity.
    """
    return value.id if isinstance(value, Ref) else value


@dataclass(frozen=True, slots=True)
class Key:
    """Identifies something within a structure — scoped, not absolute."""

    namespace: Namespace
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Key.name must not be empty")


@runtime_checkable
class IdSource(Protocol):
    """Explicit identity allocation — nondeterminism made a visible capability."""

    def new(self, kind: Kind) -> Id: ...


class UuidIdSource:
    """Concrete, stateless ``IdSource``: each call draws one fresh ``uuid4()``.

    Allocation happens only on this explicit method call — never at import,
    at class construction, or anywhere else invisible.
    """

    def new(self, kind: Kind) -> Id:
        return Id(kind=kind, value=str(uuid.uuid4()))
