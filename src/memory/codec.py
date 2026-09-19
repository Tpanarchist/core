"""Codec: the durable-value domain crossing the persistence boundary.

See MEMORY_SPECIFICATION.md "Persistence and the codec boundary" and
MEMORY_ARCHITECTURE.md "The codec boundary" (codec.py, tier 0).
"""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import cast

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.time import Duration, WallInstant
from core.value import Kind

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


_CONTEXT_OBJECT_FIELDS = ("scope", "environment", "source", "authority", "version", "units")


def _expect_list(payload: object, tag: str, *, length: int | None = None) -> list[object]:
    """Validate that a decoded envelope/node payload is a list (optionally of
    an exact length) before it is indexed or unpacked. ``payload`` comes from
    parsed, but otherwise untrusted, external bytes — raises ``ValueError``
    (never IndexError/TypeError) on anything malformed.
    """
    if not isinstance(payload, list):
        raise ValueError(f"malformed {tag} payload: {payload!r}")
    payload_list = cast(list[object], payload)
    if length is not None and len(payload_list) != length:
        raise ValueError(f"malformed {tag} payload: {payload!r}")
    return payload_list


def _expect_str(value: object, tag: str) -> str:
    """Validate that a decoded payload element is a string before it is
    passed to a constructor/parser that assumes ``str``.
    """
    if not isinstance(value, str):
        raise ValueError(f"malformed {tag} payload: {value!r}")
    return value


def _expect_segments(payload: object, tag: str) -> tuple[str, ...]:
    segments = _expect_list(payload, tag)
    return tuple(_expect_str(segment, f"{tag} segment") for segment in segments)


def _expect_dict(payload: object, tag: str) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"malformed {tag} payload: {payload!r}")
    return cast(dict[str, object], payload)


def _expect_key(payload: dict[str, object], key: str, tag: str) -> object:
    if key not in payload:
        raise ValueError(f"malformed {tag} payload: missing {key!r} field")
    return payload[key]


def _parse_iso_datetime(iso: str, tag: str) -> datetime:
    try:
        return datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"malformed {tag} payload: {iso!r}") from exc


def encode_kind(kind: Kind) -> bytes:
    return _envelope("memory.kind", 1, kind.value)


def decode_kind(data: bytes) -> Kind:
    payload = _unenvelope("memory.kind", 1, data)
    value = _expect_str(payload, "memory.kind")
    return Kind(value)


def encode_id(value: Id) -> bytes:
    return _envelope("memory.id", 1, [value.kind.value, value.value])


def decode_id(data: bytes) -> Id:
    payload = _unenvelope("memory.id", 1, data)
    pair = _expect_list(payload, "memory.id", length=2)
    kind_value = _expect_str(pair[0], "memory.id.kind")
    id_value = _expect_str(pair[1], "memory.id.value")
    return Id(Kind(kind_value), id_value)


def encode_namespace(namespace: Namespace) -> bytes:
    return _envelope("memory.namespace", 1, list(namespace.segments))


def decode_namespace(data: bytes) -> Namespace:
    payload = _unenvelope("memory.namespace", 1, data)
    return Namespace(_expect_segments(payload, "memory.namespace"))


def encode_ref(ref: Ref) -> bytes:
    namespace_segments = list(ref.namespace.segments) if ref.namespace is not None else None
    payload = [[ref.id.kind.value, ref.id.value], namespace_segments]
    return _envelope("memory.ref", 1, payload)


def decode_ref(data: bytes) -> Ref:
    payload = _unenvelope("memory.ref", 1, data)
    outer = _expect_list(payload, "memory.ref", length=2)
    id_pair = _expect_list(outer[0], "memory.ref.id", length=2)
    kind_value = _expect_str(id_pair[0], "memory.ref.id.kind")
    id_value = _expect_str(id_pair[1], "memory.ref.id.value")
    namespace_segments = outer[1]
    namespace = (
        None
        if namespace_segments is None
        else Namespace(_expect_segments(namespace_segments, "memory.ref.namespace"))
    )
    return Ref(id=Id(Kind(kind_value), id_value), namespace=namespace)


def encode_wall_instant(instant: WallInstant) -> bytes:
    return _envelope("memory.wall_instant", 1, instant.value.isoformat())


def decode_wall_instant(data: bytes) -> WallInstant:
    payload = _unenvelope("memory.wall_instant", 1, data)
    iso = _expect_str(payload, "memory.wall_instant")
    return WallInstant(_parse_iso_datetime(iso, "memory.wall_instant"))


def encode_duration(duration: Duration) -> bytes:
    return _envelope("memory.duration", 1, str(duration.nanoseconds))


def decode_duration(data: bytes) -> Duration:
    payload = _unenvelope("memory.duration", 1, data)
    raw = _expect_str(payload, "memory.duration")
    try:
        nanoseconds = int(raw)
    except ValueError as exc:
        raise ValueError(f"malformed memory.duration payload: {raw!r}") from exc
    return Duration(nanoseconds)


def encode_context(context: Context) -> bytes:
    payload: dict[str, object] = {
        "as_of": context.as_of.value.isoformat(),
        "namespace": list(context.namespace.segments) if context.namespace is not None else None,
    }
    for field in _CONTEXT_OBJECT_FIELDS:
        raw = getattr(context, field)
        payload[field] = (
            None if raw is None else _encode_node(as_persisted_value(raw, path=(field,)))
        )
    payload["metadata"] = (
        None
        if context.metadata is None
        else _encode_node(as_persisted_value(context.metadata, path=("metadata",)))
    )
    return _envelope("memory.context", 1, payload)


def decode_context(data: bytes) -> Context:
    payload = _unenvelope("memory.context", 1, data)
    payload_dict = _expect_dict(payload, "memory.context")

    as_of_raw = _expect_key(payload_dict, "as_of", "memory.context")
    as_of_iso = _expect_str(as_of_raw, "memory.context.as_of")
    as_of = WallInstant(_parse_iso_datetime(as_of_iso, "memory.context.as_of"))

    namespace_raw = _expect_key(payload_dict, "namespace", "memory.context")
    namespace = (
        None
        if namespace_raw is None
        else Namespace(_expect_segments(namespace_raw, "memory.context.namespace"))
    )

    fields: dict[str, object] = {}
    for field in _CONTEXT_OBJECT_FIELDS:
        node = _expect_key(payload_dict, field, "memory.context")
        fields[field] = None if node is None else _decode_node(node)

    metadata_node = _expect_key(payload_dict, "metadata", "memory.context")
    metadata: Mapping[str, object] | None = None
    if metadata_node is not None:
        decoded_metadata = _decode_node(metadata_node)
        if not isinstance(decoded_metadata, Mapping):
            raise ValueError(f"malformed memory.context.metadata payload: {metadata_node!r}")
        metadata = decoded_metadata

    return Context(as_of=as_of, namespace=namespace, metadata=metadata, **fields)
