"""Explicit version-one JSON codecs for review, persistence, and content hashing.

Unknown fields/versions and duplicate object keys are rejected. Identity kind,
token value, and reference namespace always travel together. Only named finance
fields are encoded; arbitrary objects are never pickled, reflected, or stringified.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import cast

from core.identity import Id, Namespace, Ref
from core.value import Kind
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"Non-finite JSON number is not permitted: {value}")


def _load(raw: str) -> object:
    if type(raw) is not str:
        raise ValueError("Codec requires a JSON string")
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _object(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    result = cast(dict[str, object], value)
    if set(result) != fields:
        raise ValueError("Unexpected or missing JSON fields")
    return result


def _version(value: object) -> None:
    if type(value) is not int or value != 1:
        raise ValueError("Unsupported finance codec version")


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected an array")
    return cast(list[object], value)


def _id_data(identifier: Id) -> dict[str, object]:
    if type(identifier) is not Id or type(identifier.kind) is not Kind:
        raise ValueError("Expected a Core Id with Kind")
    if type(identifier.value) is not str or not identifier.value:
        raise ValueError("Identity token must be a nonempty string")
    return {"version": 1, "kind": identifier.kind.value, "value": identifier.value}


def _id_value(value: object) -> Id:
    record = _object(value, {"version", "kind", "value"})
    _version(record["version"])
    kind = _string(record["kind"])
    if kind.strip() != kind:
        raise ValueError("Identity kind must use canonical spelling")
    return Id(Kind(kind), _string(record["value"]))


def _ref_data(reference: Ref) -> dict[str, object]:
    namespace: list[str] | None = None
    if reference.namespace is not None:
        namespace = list(reference.namespace.segments)
        if not namespace or any(type(segment) is not str or not segment for segment in namespace):
            raise ValueError("Reference namespace needs nonempty string segments")
    return {"version": 1, "id": _id_data(reference.id), "namespace": namespace}


def _ref_value(value: object) -> Ref:
    record = _object(value, {"version", "id", "namespace"})
    _version(record["version"])
    raw_namespace = record["namespace"]
    namespace = (
        None
        if raw_namespace is None
        else Namespace(tuple(_string(segment) for segment in _array(raw_namespace)))
    )
    return Ref(_id_value(record["id"]), namespace)


def encode_id(identifier: Id) -> str:
    return _json(_id_data(identifier))


def decode_id(raw: str) -> Id:
    return _id_value(_load(raw))


def encode_ref(reference: Ref) -> str:
    return _json(_ref_data(reference))


def decode_ref(raw: str) -> Ref:
    return _ref_value(_load(raw))


def encode_content(content: EntryContent) -> str:
    return _json(
        {
            "version": 1,
            "effective_date": content.effective_date.isoformat(),
            "description": content.description,
            "postings": [
                {
                    "account": _ref_data(posting.account),
                    "money": {"currency": posting.money.currency, "minor": posting.money.minor},
                }
                for posting in content.postings
            ],
            "tags": list(content.tags),
            "source": content.source,
            "reversal_of": None if content.reversal_of is None else _ref_data(content.reversal_of),
        }
    )


def _posting(value: object) -> Posting:
    record = _object(value, {"account", "money"})
    money = _object(record["money"], {"currency", "minor"})
    minor = money["minor"]
    if type(minor) is not int:
        raise ValueError("Money minor units require an integer, never a bool or float")
    return Posting(_ref_value(record["account"]), Money(minor, _string(money["currency"])))


def decode_content(raw: str) -> EntryContent:
    record = _object(
        _load(raw),
        {
            "version",
            "effective_date",
            "description",
            "postings",
            "tags",
            "source",
            "reversal_of",
        },
    )
    _version(record["version"])
    raw_date = _string(record["effective_date"])
    effective_date = date.fromisoformat(raw_date)
    if effective_date.isoformat() != raw_date:
        raise ValueError("Effective date must use YYYY-MM-DD")
    return EntryContent(
        effective_date=effective_date,
        description=_string(record["description"]),
        postings=tuple(_posting(value) for value in _array(record["postings"])),
        tags=tuple(_string(value) for value in _array(record["tags"])),
        source=_string(record["source"]),
        reversal_of=None if record["reversal_of"] is None else _ref_value(record["reversal_of"]),
    )


def content_hash(content: EntryContent) -> str:
    """Hash the complete, versioned and canonical reviewed entry content."""
    return hashlib.sha256(encode_content(content).encode("utf-8")).hexdigest()
