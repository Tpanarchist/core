"""Codec: the durable-value domain crossing the persistence boundary.

See MEMORY_SPECIFICATION.md "Persistence and the codec boundary" and
MEMORY_ARCHITECTURE.md "The codec boundary" (codec.py, tier 0).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

type PersistedValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | tuple[PersistedValue, ...]
    | Mapping[str, PersistedValue]
)


class UnsupportedPersistedValue(ValueError):
    """Raised by ``as_persisted_value()`` when a value cannot cross the
    durable-storage boundary. Carries the exact nested path and the
    offending value/reason — never silently stringified, pickled, or dropped.
    """

    def __init__(self, path: tuple[str | int, ...], value: object, reason: str) -> None:
        super().__init__(f"unsupported persisted value at {path!r}: {reason}")
        self.path = path
        self.value = value
        self.reason = reason


def as_persisted_value(
    value: object,
    *,
    path: tuple[str | int, ...] = (),
    _seen: frozenset[int] = frozenset(),
) -> PersistedValue:
    """Validate ``value`` against the closed PersistedValue domain and return
    a defensive recursive immutable snapshot. Never converts/coerces —
    validates only. Raises UnsupportedPersistedValue, naming the exact path,
    for anything outside the domain.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedPersistedValue(path, value, "float must be finite")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value
    if isinstance(value, tuple):
        tuple_val = cast(tuple[object, ...], value)
        if id(tuple_val) in _seen:
            raise UnsupportedPersistedValue(path, tuple_val, "cyclic container")
        next_seen = _seen | {id(tuple_val)}
        return tuple(
            as_persisted_value(item, path=(*path, index), _seen=next_seen)
            for index, item in enumerate(tuple_val)
        )
    if isinstance(value, list):
        list_val = cast(list[object], value)
        raise UnsupportedPersistedValue(path, list_val, "list is not persistable; use tuple")
    if isinstance(value, Mapping):
        mapping_val = cast(Mapping[object, object], value)
        if id(mapping_val) in _seen:
            raise UnsupportedPersistedValue(path, mapping_val, "cyclic container")
        next_seen = _seen | {id(mapping_val)}
        result: dict[str, PersistedValue] = {}
        for key, item in mapping_val.items():
            if not isinstance(key, str):
                raise UnsupportedPersistedValue(
                    (*path, repr(key)), key, "mapping keys must be strings"
                )
            result[key] = as_persisted_value(item, path=(*path, key), _seen=next_seen)
        return MappingProxyType(result)
    raise UnsupportedPersistedValue(path, value, f"unsupported type {type(value).__name__}")
