"""Value: information, considered apart from any act of producing, observing,
or asserting it.

Plain information has no dedicated runtime class here (SPECIFICATION.md #2) —
a bare ``T`` occupies the Value role wherever it's a payload field elsewhere
in Core. Two refinements of "information" do earn runtime representation,
and both live here: the epistemic-status axis (``Known``/``Unknown``/``Maybe``,
law 6) and the open categorical label (``Kind``), shared by every primitive
that needs an extensible "what kind of thing is this" tag.

See SPECIFICATION.md #2 and ARCHITECTURE.md (value.py, tier 0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


@dataclass(frozen=True, slots=True)
class Kind:
    """An open, namespaced categorical label.

    Canonical spelling only: lowercase, dot-separated segments, each
    starting with a letter (``core.contract``, ``io.not_found``). An
    invalid spelling is rejected outright at construction, never silently
    normalized — this is the one type every categorical label in Core
    compares against, so there is exactly one way to spell any given kind.
    """

    value: str

    def __post_init__(self) -> None:
        if not _KIND_PATTERN.match(self.value):
            raise ValueError(
                f"invalid Kind spelling: {self.value!r} "
                "(expected lowercase dot-separated segments, e.g. 'core.contract')"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Known[T]:
    """Information whose value is known."""

    value: T


class Unknown:
    """Information whose value is legitimately not known.

    Distinct from ``False`` and from ``None`` (law 6): ``False`` means we
    know something isn't so; ``Unknown`` means we don't know. A true
    singleton — every ``Unknown()`` call returns the same instance.
    """

    _instance: Unknown | None = None

    def __new__(cls) -> Unknown:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Unknown()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Unknown)

    def __hash__(self) -> int:
        return hash(Unknown)


UNKNOWN = Unknown()

type Maybe[T] = Known[T] | Unknown
