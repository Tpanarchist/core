"""Error: structured information about why an attempted operation did not succeed.

See SPECIFICATION.md #9 and ARCHITECTURE.md (error.py, tier 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from core.context import Context
from core.identity import Id
from core.time import WallInstant
from core.value import Kind


@dataclass(frozen=True, slots=True, kw_only=True)
class Error:
    """Structured information about why an attempted operation did not succeed.

    Causation is two distinct fields, not one heterogeneous chain: ``cause``
    wraps a prior structured Core Error; ``exception`` carries a foreign
    Python exception that triggered this one, if any. ``.chain()`` follows
    ``cause`` only — one precise meaning, never guessing which kind a link is.
    """

    id: Id
    kind: Kind
    message: str
    at: WallInstant

    cause: Error | None = None
    exception: BaseException | None = None
    context: Context | None = None
    operation: str | None = None
    recoverable: bool = False
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("Error.message must not be empty")
        if self.operation is not None and not self.operation:
            raise ValueError("Error.operation must not be empty if supplied")
        if self.metadata is not None:
            for key in self.metadata:
                # Runtime boundary guard — see the identical note in context.py.
                if not isinstance(key, str) or not key:  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise ValueError("Error.metadata keys must be non-empty strings")
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def chain(self) -> tuple[Error, ...]:
        """The structured Core cause chain, self-first, stopping at None.

        ``exception`` (a foreign Python exception, if any) is never part of
        this chain — it answers a different question than ``cause`` does.
        """
        result: list[Error] = []
        current: Error | None = self
        while current is not None:
            result.append(current)
            current = current.cause
        return tuple(result)

    def is_kind(self, kind: Kind) -> bool:
        """Exact Kind equality — no hierarchy, wildcard, or prefix matching."""
        return self.kind == kind
