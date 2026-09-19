"""MemoryStore: the persistence boundary — MemoryStore protocol, InMemoryStore
(the semantic reference implementation), and the frozen persisted-record union.

Persistence preserves Memory semantics; it does not create them. InMemoryStore
is authoritative for admissibility, identity collision, exact resolution,
claim/conflict lookup, retention history, retrieval eligibility/relevance, and
Episode persistence transitions — a durable backend (Pass 3) may optimize
these operations, never reinterpret them.

See MEMORY_SPECIFICATION.md, MEMORY_ARCHITECTURE.md, and
docs/memory-passes/02-persistence-boundary.md (store.py, tier 1).
"""

from __future__ import annotations

from core.context import Context
from core.effect import Effect
from core.epistemic import Claim, Contradiction, Inference, Resolution
from core.error import Error
from core.event import Event
from core.identity import Id, Ref
from core.observation import Observation
from core.provenance import Provenance
from core.time import WallInstant
from core.value import Known, Unknown
from memory.codec import (
    as_persisted_value,
    encode_context,
    encode_duration,
    encode_id,
    encode_kind,
    encode_persisted_value,
    encode_ref,
    encode_wall_instant,
)
from memory.episode import Episode
from memory.retention import RetentionMark

type EntityMemoryRecord = (
    Observation[object]
    | Claim[object]
    | Inference[object]
    | Contradiction
    | Event
    | Effect
    | Provenance
    | Error
    | Episode
)

type NonEntityMemoryRecord = Resolution | RetentionMark

type MemoryRecord = EntityMemoryRecord | NonEntityMemoryRecord

type PersistRecord = (
    Observation[object]
    | Claim[object]
    | Inference[object]
    | Contradiction
    | Resolution
    | Event
    | Effect
    | Provenance
    | Error
    | RetentionMark
)


class IdentityCollision(ValueError):
    """Raised when persisting a record whose Id already names a different
    canonical record. Never stringifies the colliding records' payloads —
    only the Id and each record's type (matching Core's UnwrapError precedent
    in core/result.py: a plain Exception subclass, not a frozen dataclass).
    """

    def __init__(self, id: Id, existing_type: type[object], incoming_type: type[object]) -> None:
        super().__init__(
            f"identity collision on {id!r}: existing {existing_type.__name__}, "
            f"incoming {incoming_type.__name__}"
        )
        self.id = id
        self.existing_type = existing_type
        self.incoming_type = incoming_type


class UnsupportedMemoryRecord(TypeError):
    """Raised when a runtime caller bypasses typing and supplies something
    outside PersistRecord (or, for Episode, passes it to generic persist()
    instead of create_episode()).
    """

    def __init__(self, record_type: type[object]) -> None:
        message = f"unsupported memory record type: {record_type.__name__}"
        if record_type is Episode:
            message += " — use create_episode() instead of persist()"
        super().__init__(message)
        self.record_type = record_type


def _canonical_id_or_ref(value: Id | Ref) -> tuple[str, bytes]:
    if isinstance(value, Ref):
        return ("ref", encode_ref(value))
    return ("id", encode_id(value))


def _canonical_optional_context(context: Context | None) -> bytes | None:
    return encode_context(context) if context is not None else None


def _canonical_payload(value: object) -> bytes:
    return encode_persisted_value(as_persisted_value(value))


def _canonical_optional_payload(value: object | None) -> bytes | None:
    return _canonical_payload(value) if value is not None else None


def _canonical_refs(refs: tuple[Ref, ...]) -> tuple[bytes, ...]:
    return tuple(encode_ref(r) for r in refs)


def _canonical_claim_value(value: Known[object] | Unknown) -> tuple[object, ...]:
    if isinstance(value, Known):
        return ("known", _canonical_payload(value.value))
    return ("unknown",)


def _canonical_observation(obs: Observation[object]) -> tuple[object, ...]:
    return (
        "observation",
        _canonical_id_or_ref(obs.subject),
        _canonical_payload(obs.value),
        encode_wall_instant(obs.at),
        _canonical_payload(obs.source),
        encode_context(obs.context),
        _canonical_optional_payload(obs.observer),
    )


def _canonical_claim(claim: Claim[object]) -> tuple[object, ...]:
    return (
        "claim",
        _canonical_id_or_ref(claim.subject),
        encode_kind(claim.predicate),
        _canonical_claim_value(claim.value),
        encode_context(claim.context),
        _canonical_id_or_ref(claim.asserted_by),
        _canonical_refs(claim.evidence_refs),
        encode_wall_instant(claim.at),
    )


def _canonical_inference(inference: Inference[object]) -> tuple[object, ...]:
    return (
        "inference",
        _canonical_refs(inference.premises),
        encode_kind(inference.method),
        _canonical_claim(inference.conclusion),
        encode_wall_instant(inference.at),
    )


def _canonical_contradiction(contradiction: Contradiction) -> tuple[object, ...]:
    return (
        "contradiction",
        _canonical_id_or_ref(contradiction.subject),
        _canonical_refs(contradiction.statements),
        encode_wall_instant(contradiction.detected_at),
        encode_context(contradiction.context),
    )


def _canonical_event(event: Event) -> tuple[object, ...]:
    return (
        "event",
        encode_kind(event.kind),
        encode_wall_instant(event.at),
        _canonical_payload(event.payload),
        _canonical_optional_context(event.context),
    )


def _canonical_effect(effect: Effect) -> tuple[object, ...]:
    return (
        "effect",
        encode_kind(effect.kind),
        effect.description,
        _canonical_payload(effect.target),
        encode_wall_instant(effect.at),
        _canonical_optional_context(effect.context),
        _canonical_optional_payload(effect.metadata),
    )


def _canonical_provenance(provenance: Provenance) -> tuple[object, ...]:
    return (
        "provenance",
        encode_id(provenance.transform_id),
        provenance.transform_name,
        provenance.transform_version,
        _canonical_refs(provenance.inputs),
        _canonical_refs(provenance.parents),
        encode_wall_instant(provenance.at),
        encode_duration(provenance.duration),
        _canonical_optional_context(provenance.context),
    )


def _canonical_error(error: Error) -> tuple[object, ...]:
    return (
        "error",
        encode_kind(error.kind),
        error.message,
        encode_wall_instant(error.at),
        _canonical_error(error.cause) if error.cause is not None else None,
        _canonical_optional_context(error.context),
        error.operation,
        error.recoverable,
        _canonical_optional_payload(error.metadata),
    )


def _canonical_record(  # pyright: ignore[reportUnusedFunction]
    record: EntityMemoryRecord,
) -> tuple[object, ...]:
    """Canonical PersistedValue-backed representation used for identity-collision
    comparison and semantic-snapshot verification — never a record's own
    __eq__ (Core's Event equality is Id-only, which would otherwise hide a
    real collision). Only the 8 canonicalizable EntityMemoryRecord types reach
    here — Resolution/RetentionMark never do (append-only, no canonical
    comparison), and Episode uses _canonical_episode_header instead.

    Only used from tests in this task; Tasks 2-6 call it from persist() /
    create_episode() for identity-collision detection, which will make this
    ignore comment (and the one on _canonical_episode_header below)
    unnecessary — left for a later task to remove.
    """
    if isinstance(record, Observation):
        return _canonical_observation(record)
    if isinstance(record, Claim):
        return _canonical_claim(record)
    if isinstance(record, Inference):
        return _canonical_inference(record)
    if isinstance(record, Contradiction):
        return _canonical_contradiction(record)
    if isinstance(record, Event):
        return _canonical_event(record)
    if isinstance(record, Effect):
        return _canonical_effect(record)
    if isinstance(record, Provenance):
        return _canonical_provenance(record)
    if isinstance(record, Error):
        return _canonical_error(record)
    raise UnsupportedMemoryRecord(type(record))


def _canonical_episode_header(  # pyright: ignore[reportUnusedFunction]
    *, subject: Id | Ref, context: Context, opened_at: WallInstant
) -> tuple[object, ...]:
    """Canonical form of only an Episode's immutable header (subject, context,
    opened_at) — deliberately excludes items/closed_at, since create_episode()'s
    idempotency check concerns identity of the header only (an Episode that has
    since acquired items or been closed is still the "same" Episode for this
    purpose).
    """
    return (
        "episode_header",
        _canonical_id_or_ref(subject),
        encode_context(context),
        encode_wall_instant(opened_at),
    )
