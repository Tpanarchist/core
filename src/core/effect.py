"""Effect: a change made to something outside the computation's own return value.

See SPECIFICATION.md #11 and ARCHITECTURE.md (effect.py, tier 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.context import Context
from core.identity import Id
from core.time import WallInstant
from core.value import Kind


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectSpec:
    """Declarative: what an operation may do. Not a record that anything happened."""

    kind: Kind
    target_shape: object
    description: str

    def __post_init__(self) -> None:
        if not self.description:
            raise ValueError("EffectSpec.description must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class Effect:
    """A record that a specific effect actually happened.

    Declared-possible (EffectSpec) and actually-happened (Effect) remain
    permanently separate types (law 5).
    """

    id: Id
    kind: Kind
    description: str
    target: object
    at: WallInstant
    context: Context | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.description:
            raise ValueError("Effect.description must not be empty")
        if self.metadata is not None:
            for key in self.metadata:
                # Runtime boundary guard — see the identical note in context.py.
                if not isinstance(key, str) or not key:  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise ValueError("Effect.metadata keys must be non-empty strings")
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@runtime_checkable
class EffectSink(Protocol):
    """Consumes Effect records. No query API — that's a concrete sink's own business."""

    def record(self, effect: Effect) -> None: ...


class MemoryEffectSink:
    """Concrete v0 EffectSink: an in-memory, insertion-ordered, single-writer log.

    Never allocates identities, infers EffectSpecs, or validates whether an
    Effect was declared by some Transformation.
    """

    def __init__(self) -> None:
        self._effects: list[Effect] = []

    def record(self, effect: Effect) -> None:
        self._effects.append(effect)

    def effects(self) -> tuple[Effect, ...]:
        return tuple(self._effects)
