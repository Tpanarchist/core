"""Versioned, explicit finance-owned codecs for retained execution evidence.

Payloads accepted here have a deliberately small shape. This is not a generic
Core serializer and it never reflects over arbitrary records or uses pickle.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from core.context import Context
from core.effect import Effect
from core.event import Event
from core.identity import Namespace, Ref
from core.provenance import Provenance
from core.time import Duration, WallInstant
from core.trace import Trace
from core.value import Kind
from personal_finance.domain.codec import decode_id, decode_ref, encode_id, encode_ref


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _context_value(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, tuple):
        return {"tuple": [_context_value(item) for item in cast(tuple[object, ...], value)]}
    raise ValueError("Finance evidence context accepts only immutable primitive/tuple values")


def _decode_context_value(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, dict):
        record = cast(dict[str, object], value)
        items = record.get("tuple")
        if set(record) == {"tuple"} and isinstance(items, list):
            return tuple(_decode_context_value(item) for item in cast(list[object], items))
    raise ValueError("Unsupported finance evidence context value")


def context_data(context: Context | None) -> dict[str, object] | None:
    if context is None:
        return None
    if context.environment is not None:
        raise ValueError("Finance evidence codec does not support Context.environment")
    return {
        "as_of": context.as_of.value.isoformat(),
        "namespace": list(context.namespace.segments) if context.namespace else None,
        "source": _context_value(context.source),
        "authority": _context_value(context.authority),
        "version": _context_value(context.version),
        "units": _context_value(context.units),
        "scope": _context_value(context.scope),
        "metadata": {key: _context_value(value) for key, value in context.metadata.items()}
        if context.metadata is not None
        else None,
    }


def encode_provenance(value: Provenance) -> str:
    return canonical(
        {
            "version": 1,
            "id": encode_id(value.id),
            "transform_id": encode_id(value.transform_id),
            "transform_name": value.transform_name,
            "transform_version": value.transform_version,
            "inputs": [encode_ref(ref) for ref in value.inputs],
            "parents": [encode_ref(ref) for ref in value.parents],
            "at": value.at.value.isoformat(),
            "duration_ns": value.duration.nanoseconds,
            "context": context_data(value.context),
        }
    )


def decode_provenance(payload: str) -> Provenance:
    data = cast(dict[str, Any], json.loads(payload))
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported finance provenance version")
    raw = cast(dict[str, Any] | None, data["context"])
    context = None
    if raw is not None:
        context = Context(
            as_of=WallInstant(datetime.fromisoformat(raw["as_of"])),
            namespace=Namespace(tuple(raw["namespace"])) if raw["namespace"] else None,
            source=_decode_context_value(raw["source"]),
            authority=_decode_context_value(raw["authority"]),
            version=_decode_context_value(raw["version"]),
            units=_decode_context_value(raw["units"]),
            scope=_decode_context_value(raw["scope"]),
            metadata={
                key: _decode_context_value(value)
                for key, value in cast(dict[str, object], raw["metadata"]).items()
            }
            if raw["metadata"] is not None
            else None,
        )
    return Provenance(
        id=decode_id(data["id"]),
        transform_id=decode_id(data["transform_id"]),
        transform_name=data["transform_name"],
        transform_version=data["transform_version"],
        inputs=tuple(decode_ref(ref) for ref in data["inputs"]),
        parents=tuple(decode_ref(ref) for ref in data["parents"]),
        at=WallInstant(datetime.fromisoformat(data["at"])),
        duration=Duration(data["duration_ns"]),
        context=context,
    )


def encode_event(value: Event) -> str:
    raw_payload: object = value.payload
    if not isinstance(raw_payload, tuple):
        raise ValueError("Finance event payload must be a tuple of references")
    payload = cast(tuple[object, ...], raw_payload)
    if not all(isinstance(ref, Ref) for ref in payload):
        raise ValueError("Finance event payload must be a tuple of references")
    refs = cast(tuple[Ref, ...], payload)
    return canonical(
        {
            "version": 1,
            "id": encode_id(value.id),
            "kind": value.kind.value,
            "at": value.at.value.isoformat(),
            "references": [encode_ref(ref) for ref in refs],
            "context": context_data(value.context),
        }
    )


def encode_effect(value: Effect) -> str:
    if value.metadata is not None:
        raise ValueError("Finance evidence codec does not support Effect.metadata")
    if not isinstance(value.target, Ref):
        raise ValueError("Finance effect target must be a reference")
    return canonical(
        {
            "version": 1,
            "id": encode_id(value.id),
            "kind": value.kind.value,
            "description": value.description,
            "target": encode_ref(value.target),
            "at": value.at.value.isoformat(),
            "context": context_data(value.context),
        }
    )


def decode_effect(payload: str) -> Effect:
    data = cast(dict[str, Any], json.loads(payload))
    if set(data) != {"version", "id", "kind", "description", "target", "at", "context"}:
        raise ValueError("Unsupported finance effect shape")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported finance effect version")
    raw = cast(dict[str, Any] | None, data["context"])
    context = None
    if raw is not None:
        context = Context(
            as_of=WallInstant(datetime.fromisoformat(raw["as_of"])),
            namespace=Namespace(tuple(raw["namespace"])) if raw["namespace"] else None,
            source=_decode_context_value(raw["source"]),
            authority=_decode_context_value(raw["authority"]),
            version=_decode_context_value(raw["version"]),
            units=_decode_context_value(raw["units"]),
            scope=_decode_context_value(raw["scope"]),
            metadata={
                key: _decode_context_value(value)
                for key, value in cast(dict[str, object], raw["metadata"]).items()
            }
            if raw["metadata"] is not None
            else None,
        )
    effect = Effect(
        id=decode_id(data["id"]),
        kind=Kind(data["kind"]),
        description=data["description"],
        target=decode_ref(data["target"]),
        at=WallInstant(datetime.fromisoformat(data["at"])),
        context=context,
    )
    if encode_effect(effect) != payload:
        raise ValueError("Noncanonical finance effect")
    return effect


def encode_trace(value: Trace) -> str:
    if any(entry.payload is not None for entry in value.entries()):
        raise ValueError("Finance evidence codec does not support TraceEntry.payload")
    return canonical(
        {
            "version": 1,
            "id": encode_id(value.id),
            "entries": [
                {
                    "position": entry.sequence.position,
                    "kind": entry.kind.value,
                    "subject": encode_ref(
                        entry.subject if isinstance(entry.subject, Ref) else Ref(entry.subject)
                    )
                    if entry.subject is not None
                    else None,
                    "observed_at": entry.observed_at.value.isoformat()
                    if entry.observed_at
                    else None,
                    "references": [encode_ref(ref) for ref in entry.references],
                    "context": context_data(entry.context),
                }
                for entry in value.entries()
            ],
        }
    )
