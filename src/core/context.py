"""Context: the frame relative to which a piece of information's meaning is fixed.

See SPECIFICATION.md #3 and ARCHITECTURE.md (context.py, tier 3).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.identity import Namespace
from core.result import Err, Ok, Result
from core.time import WallInstant


def _validate_and_freeze_metadata(
    metadata: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if metadata is None:
        return None
    for key in metadata:
        # isinstance check is a runtime boundary guard: the annotation says str,
        # but nothing stops a caller ignoring static typing from passing a dict
        # keyed on something else — the failure needs to be loud, not assumed away.
        if not isinstance(key, str) or not key:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("metadata keys must be non-empty strings")
    return MappingProxyType(dict(metadata))


@dataclass(frozen=True, slots=True, kw_only=True)
class Context:
    """The frame relative to which a piece of information's meaning is fixed.

    Never silently and partially overwritten — every field-level change is
    one of ``with_``/``merge``/``override``, never an implicit dict-merge.
    Has no knowledge of ``Contradiction`` (structural vs. semantic conflict).
    """

    as_of: WallInstant
    namespace: Namespace | None = None
    scope: object | None = None
    environment: object | None = None
    source: object | None = None
    authority: object | None = None
    version: object | None = None
    units: object | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _validate_and_freeze_metadata(self.metadata))

    def with_(self, **changes: object) -> Context:
        """Derive a new Context with only the explicitly named fields changed."""
        field_names = {f.name for f in dataclasses.fields(self)}
        unknown = sorted(set(changes) - field_names)
        if unknown:
            raise ValueError(f"unknown Context field(s): {unknown}")
        return dataclasses.replace(self, **changes)

    def merge(self, other: Context) -> Result[Context, ContextConflict]:
        """Compatible union: equal fields pass through, one-sided fields fill in,
        incompatible fields are reported (all of them) rather than silently won.
        """
        conflicts: list[tuple[str, object, object]] = []
        merged: dict[str, Any] = {}
        for f in dataclasses.fields(self):
            left = getattr(self, f.name)
            right = getattr(other, f.name)
            if left == right:
                merged[f.name] = left
            elif left is None:
                merged[f.name] = right
            elif right is None:
                merged[f.name] = left
            else:
                conflicts.append((f.name, left, right))
        if conflicts:
            return Err(ContextConflict(tuple(conflicts)))
        return Ok(Context(**merged))

    def override(self, other: Context, *fields: str) -> tuple[Context, ContextOverride]:
        """Deliberate, audited replacement of named fields from ``other``."""
        field_names = {f.name for f in dataclasses.fields(self)}
        unknown = [name for name in fields if name not in field_names]
        if unknown:
            raise ValueError(f"unknown Context field(s): {unknown}")

        seen: set[str] = set()
        duplicates: list[str] = []
        for name in fields:
            if name in seen:
                duplicates.append(name)
            seen.add(name)
        if duplicates:
            raise ValueError(f"duplicate Context field(s) in override: {duplicates}")

        changes: list[tuple[str, object, object]] = []
        replacements: dict[str, Any] = {}
        for name in fields:
            old = getattr(self, name)
            new = getattr(other, name)
            replacements[name] = new
            if old != new:
                changes.append((name, old, new))

        new_context = dataclasses.replace(self, **replacements)
        return new_context, ContextOverride(tuple(changes))


@dataclass(frozen=True, slots=True)
class ContextConflict:
    """Structural: incompatible values for one or more Context fields during merge().

    Not the semantic Contradiction — a failed merge is a structural fact
    about two Contexts, not (by itself) a judgment about conflicting claims.
    """

    conflicts: tuple[tuple[str, object, object], ...]

    def __post_init__(self) -> None:
        if not self.conflicts:
            raise ValueError("ContextConflict.conflicts must not be empty")


@dataclass(frozen=True, slots=True)
class ContextOverride:
    """The audit trail of what override() actually replaced."""

    changes: tuple[tuple[str, object, object], ...]
