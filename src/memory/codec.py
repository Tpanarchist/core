"""Codec: the durable-value domain crossing the persistence boundary.

See MEMORY_SPECIFICATION.md "Persistence and the codec boundary" and
MEMORY_ARCHITECTURE.md "The codec boundary" (codec.py, tier 0).
"""

from __future__ import annotations

import base64
import json
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
                    path, key, "mapping keys must be strings"
                )
            result[key] = as_persisted_value(item, path=(*path, key), _seen=next_seen)
        return MappingProxyType(result)
    raise UnsupportedPersistedValue(path, value, f"unsupported type {type(value).__name__}")


def _envelope(tag: str, version: int, payload: object) -> bytes:
    return json.dumps(
        [tag, version, payload], ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _unenvelope(expected_tag: str, expected_version: int, data: bytes) -> object:
    try:
        envelope = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed {expected_tag} envelope: {exc}") from exc
    if not isinstance(envelope, list):
        raise ValueError(f"malformed {expected_tag} envelope: {envelope!r}")
    envelope_list = cast(list[object], envelope)
    if len(envelope_list) != 3:
        raise ValueError(f"malformed {expected_tag} envelope: {envelope!r}")
    tag, version, payload = envelope_list[0], envelope_list[1], envelope_list[2]
    if tag != expected_tag or version != expected_version:
        raise ValueError(f"unknown codec version/tag: {tag!r} v{version!r}")
    return payload


def _encode_node(value: PersistedValue) -> object:
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, tuple):
        return ["tuple", [_encode_node(item) for item in value]]
    if isinstance(value, Mapping):  # type: ignore[reportUnnecessaryIsInstance]
        pairs = sorted(value.items(), key=lambda pair: pair[0])
        return ["map", [[key, _encode_node(item)] for key, item in pairs]]
    raise TypeError(f"not a PersistedValue: {type(value).__name__}")


def _decode_node(node: object) -> PersistedValue:
    """Decode one canonical node. ``node`` comes from parsed, but otherwise
    untrusted, external bytes — every branch validates its own payload shape
    and raises ``ValueError`` (never IndexError/TypeError/other) on anything
    malformed, exactly like _unenvelope() already does at the envelope level.
    """
    if not (isinstance(node, list) and node):
        raise ValueError(f"malformed persisted-value node: {node!r}")
    node_list = cast(list[object], node)
    tag = node_list[0]
    rest = node_list[1:]
    if tag == "none":
        if rest:
            raise ValueError(f"malformed 'none' node: {node!r}")
        return None
    if tag == "bool":
        if len(rest) != 1 or not isinstance(rest[0], bool):
            raise ValueError(f"malformed 'bool' node: {node!r}")
        return rest[0]
    if tag == "int":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'int' node: {node!r}")
        try:
            return int(rest[0])
        except ValueError as exc:
            raise ValueError(f"malformed 'int' payload: {node!r}") from exc
    if tag == "float":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'float' node: {node!r}")
        try:
            return float.fromhex(rest[0])
        except ValueError as exc:
            raise ValueError(f"malformed 'float' payload: {node!r}") from exc
    if tag == "str":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'str' node: {node!r}")
        return rest[0]
    if tag == "bytes":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'bytes' node: {node!r}")
        try:
            return base64.b64decode(rest[0], validate=True)
        except ValueError as exc:
            raise ValueError(f"malformed 'bytes' payload: {node!r}") from exc
    if tag == "tuple":
        if len(rest) != 1 or not isinstance(rest[0], list):
            raise ValueError(f"malformed 'tuple' node: {node!r}")
        tuple_items = cast(list[object], rest[0])
        return tuple(_decode_node(item) for item in tuple_items)
    if tag == "map":
        if len(rest) != 1 or not isinstance(rest[0], list):
            raise ValueError(f"malformed 'map' node: {node!r}")
        result: dict[str, PersistedValue] = {}
        map_items = cast(list[object], rest[0])
        for pair in map_items:
            if not isinstance(pair, list):
                raise ValueError(f"malformed 'map' entry: {pair!r}")
            pair_list = cast(list[object], pair)
            if not (len(pair_list) == 2 and isinstance(pair_list[0], str)):
                raise ValueError(f"malformed 'map' entry: {pair!r}")
            key, item = pair_list[0], pair_list[1]
            result[key] = _decode_node(item)
        return MappingProxyType(result)
    raise ValueError(f"unknown persisted-value tag: {tag!r}")


def encode_persisted_value(value: PersistedValue) -> bytes:
    """Deterministic canonical byte encoding. Same input always produces
    byte-identical output — see MEMORY_ARCHITECTURE.md "The codec boundary".
    """
    return _envelope("memory.persisted_value", 1, _encode_node(value))


def decode_persisted_value(data: bytes) -> PersistedValue:
    return _decode_node(_unenvelope("memory.persisted_value", 1, data))
