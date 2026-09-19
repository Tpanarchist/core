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

import dataclasses

from core.context import Context
from core.effect import Effect
from core.epistemic import Claim, Contradiction, Inference, Resolution
from core.error import Error
from core.event import Event
from core.identity import Id, Ref, identity_of
from core.observation import Observation
from core.provenance import Provenance
from core.time import WallInstant
from core.value import Kind, Known, Unknown
from memory.codec import (
    UnsupportedPersistedValue,
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
from memory.recall import IDENTITY_MATCH, LEXICAL_MATCH, RecallCandidate
from memory.retention import ACTIVE, ARCHIVED, DEPRIORITIZED, RetentionLog, RetentionMark

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


def _canonical_record(
    record: EntityMemoryRecord,
) -> tuple[object, ...]:
    """Canonical PersistedValue-backed representation used for identity-collision
    comparison and semantic-snapshot verification — never a record's own
    __eq__ (Core's Event equality is Id-only, which would otherwise hide a
    real collision). Only the 8 canonicalizable EntityMemoryRecord types reach
    here — Resolution/RetentionMark never do (append-only, no canonical
    comparison), and Episode uses _canonical_episode_header instead.

    Called from InMemoryStore.persist() (via _snapshot_simple_entity's
    fallthrough branch) for identity-collision detection. Also still used
    directly from tests.
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


def _canonical_episode_header(
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


class InMemoryStore:
    """The semantic reference implementation — authoritative for admissibility,
    identity collision, exact resolution, claim/conflict lookup, retention
    history, retrieval eligibility/relevance, and Episode transitions. Mutable,
    single-writer, not thread-safe (no locks — law 19: no unearned
    synchronization). Atomicity here means one public method commits its
    complete semantic mutation or none of it — not multi-thread isolation.
    """

    def __init__(self) -> None:
        self._entities: dict[Id, EntityMemoryRecord] = {}
        self._entity_canonical: dict[Id, tuple[object, ...]] = {}
        self._entity_order: list[Id] = []
        self._conflict_entries: list[Contradiction | Resolution] = []
        self._conflict_ids: set[Id] = set()
        self._retention_marks: list[RetentionMark] = []

    def _check(self, id: Id, canonical: tuple[object, ...], incoming_type: type[object]) -> str:
        """Returns 'insert' or 'idempotent'; raises IdentityCollision — never
        mutates state.
        """
        existing = self._entities.get(id)
        if existing is None:
            return "insert"
        if self._entity_canonical.get(id) == canonical:
            return "idempotent"
        raise IdentityCollision(id, type(existing), incoming_type)

    def _commit(self, id: Id, record: EntityMemoryRecord, canonical: tuple[object, ...]) -> None:
        self._entities[id] = record
        self._entity_canonical[id] = canonical
        self._entity_order.append(id)

    def _snapshot_context(self, context: Context) -> Context:
        """Reconstruct a Context with every object-typed field replaced by its
        as_persisted_value()-validated snapshot. Context.__post_init__ only
        shallow-freezes metadata; scope/environment/source/authority/version/units
        carry no protection at all, so without this a caller's later mutation of
        any of them would leak straight through to what resolve() returns.
        """
        return dataclasses.replace(
            context,
            scope=as_persisted_value(context.scope) if context.scope is not None else None,
            environment=(
                as_persisted_value(context.environment)
                if context.environment is not None
                else None
            ),
            source=as_persisted_value(context.source) if context.source is not None else None,
            authority=(
                as_persisted_value(context.authority) if context.authority is not None else None
            ),
            version=as_persisted_value(context.version) if context.version is not None else None,
            units=as_persisted_value(context.units) if context.units is not None else None,
            metadata=(
                as_persisted_value(context.metadata) if context.metadata is not None else None
            ),
        )

    def _snapshot_simple_entity(self, record: EntityMemoryRecord) -> EntityMemoryRecord:
        """Reconstruct a record with every object-typed payload field (including
        context) replaced by its as_persisted_value()-validated snapshot, via
        dataclasses.replace — never store a reference the caller could later
        mutate through.
        """
        if isinstance(record, Observation):
            return dataclasses.replace(
                record,
                value=as_persisted_value(record.value),
                source=as_persisted_value(record.source),
                observer=(
                    as_persisted_value(record.observer) if record.observer is not None else None
                ),
                context=self._snapshot_context(record.context),
            )
        if isinstance(record, Event):
            return dataclasses.replace(
                record,
                payload=as_persisted_value(record.payload),
                context=(
                    self._snapshot_context(record.context)
                    if record.context is not None
                    else None
                ),
            )
        if isinstance(record, Effect):
            return dataclasses.replace(
                record,
                target=as_persisted_value(record.target),
                metadata=(
                    as_persisted_value(record.metadata) if record.metadata is not None else None
                ),
                context=(
                    self._snapshot_context(record.context)
                    if record.context is not None
                    else None
                ),
            )
        if isinstance(record, Contradiction):
            return dataclasses.replace(record, context=self._snapshot_context(record.context))
        if isinstance(record, Provenance):
            return dataclasses.replace(
                record,
                context=(
                    self._snapshot_context(record.context)
                    if record.context is not None
                    else None
                ),
            )
        return record

    def _snapshot_claim(self, claim: Claim[object]) -> Claim[object]:
        if isinstance(claim.value, Known):
            return dataclasses.replace(
                claim,
                value=Known(as_persisted_value(claim.value.value)),
                context=self._snapshot_context(claim.context),
            )
        return dataclasses.replace(claim, context=self._snapshot_context(claim.context))

    def _snapshot_episode(self, episode: Episode) -> Episode:
        """A fresh, independent Episode with identical observable state —
        resolve() must never return the live, mutable stored object.
        """
        snapshot = Episode(
            id=episode.id, subject=episode.subject,
            context=episode.context, opened_at=episode.opened_at,
        )
        for ref in episode.items():
            snapshot.append(ref)
        if episode.closed_at is not None:
            snapshot.close(episode.closed_at)
        return snapshot

    def persist(self, record: PersistRecord) -> None:
        if isinstance(record, Inference):
            self._persist_inference(record)
            return
        if isinstance(record, Error):
            self._persist_error(record)
            return
        if isinstance(record, Resolution):
            self._persist_resolution(record)
            return
        if isinstance(record, RetentionMark):
            self._retention_marks.append(record)
            return
        if isinstance(record, Claim):
            stored = self._snapshot_claim(record)
            canonical = _canonical_claim(stored)
            action = self._check(stored.id, canonical, Claim)
            if action == "insert":
                self._commit(stored.id, stored, canonical)
            return
        # Anything else falls through to canonicalization directly — no separate
        # isinstance guard here: _canonical_record() already raises
        # UnsupportedMemoryRecord for anything outside the 8 canonicalizable
        # types, so a redundant pre-check here would only duplicate that check
        # (and trip Pyright's reportUnnecessaryIsInstance besides).
        stored = self._snapshot_simple_entity(record)
        canonical = _canonical_record(stored)
        action = self._check(stored.id, canonical, type(record))
        if action == "insert":
            self._commit(stored.id, stored, canonical)
            if isinstance(stored, Contradiction):
                self._conflict_entries.append(stored)
                self._conflict_ids.add(stored.id)

    def _persist_inference(self, inference: Inference[object]) -> None:
        stored_claim = self._snapshot_claim(inference.conclusion)
        claim_canonical = _canonical_claim(stored_claim)
        claim_action = self._check(stored_claim.id, claim_canonical, Claim)

        stored_inference = dataclasses.replace(inference, conclusion=stored_claim)
        inference_canonical = _canonical_inference(stored_inference)
        inference_action = self._check(inference.id, inference_canonical, Inference)

        # Both checks passed without raising — now commit, Claim first (§35).
        if claim_action == "insert":
            self._commit(stored_claim.id, stored_claim, claim_canonical)
        if inference_action == "insert":
            self._commit(inference.id, stored_inference, inference_canonical)

    def _persist_error(self, error: Error) -> None:
        if error.exception is not None:
            raise UnsupportedPersistedValue(
                (), error.exception, "foreign exceptions are not persistable"
            )
        chain: list[Error] = []
        current: Error | None = error
        while current is not None:
            chain.append(current)
            current = current.cause
        chain.reverse()  # root cause first

        prepared: list[tuple[Id, Error, tuple[object, ...], str]] = []
        rebuilt_cause: Error | None = None
        for original in chain:
            snapshotted_metadata = (
                as_persisted_value(original.metadata) if original.metadata is not None else None
            )
            stored = dataclasses.replace(
                original,
                cause=rebuilt_cause,
                metadata=snapshotted_metadata,
                context=(
                    self._snapshot_context(original.context)
                    if original.context is not None
                    else None
                ),
            )
            canonical = _canonical_error(stored)
            action = self._check(stored.id, canonical, Error)
            prepared.append((stored.id, stored, canonical, action))
            rebuilt_cause = stored

        for id_, stored, canonical, action in prepared:
            if action == "insert":
                self._commit(id_, stored, canonical)

    def _persist_resolution(self, resolution: Resolution) -> None:
        if resolution.contradiction.id not in self._conflict_ids:
            raise ValueError(
                f"Resolution references Contradiction {resolution.contradiction.id!r}, "
                "which is not recorded in this store's conflict history"
            )
        self._conflict_entries.append(resolution)

    def resolve(self, item: Id | Ref) -> EntityMemoryRecord | None:
        target = item.id if isinstance(item, Ref) else item
        stored = self._entities.get(target)
        if stored is None:
            return None
        if isinstance(stored, Episode):
            return self._snapshot_episode(stored)
        return stored

    def claims_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Claim[object], ...]:
        subject_id = identity_of(subject)
        result: list[Claim[object]] = []
        for entity_id in self._entity_order:
            entity = self._entities[entity_id]
            if (
                isinstance(entity, Claim)
                and identity_of(entity.subject) == subject_id
                and entity.predicate == predicate
            ):
                result.append(entity)
        return tuple(result)

    def conflicts_for(
        self, subject: Id | Ref, predicate: Kind
    ) -> tuple[Contradiction | Resolution, ...]:
        slot_claim_ids = {claim.id for claim in self.claims_for(subject, predicate)}
        subject_id = identity_of(subject)
        result: list[Contradiction | Resolution] = []
        relevant_ids: set[Id] = set()
        for entry in self._conflict_entries:
            if isinstance(entry, Contradiction):
                if identity_of(entry.subject) != subject_id:
                    continue
                if not any(
                    identity_of(statement) in slot_claim_ids for statement in entry.statements
                ):
                    continue
                relevant_ids.add(entry.id)
                result.append(entry)
            elif entry.contradiction.id in relevant_ids:
                result.append(entry)
        return tuple(result)

    def retention_for(self, item: Id | Ref) -> tuple[RetentionMark, ...]:
        target = identity_of(item)
        return tuple(mark for mark in self._retention_marks if identity_of(mark.item) == target)

    def create_episode(
        self, *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant
    ) -> None:
        encode_context(context)  # validates; raises UnsupportedPersistedValue if malformed
        existing = self._entities.get(id)
        incoming_header = _canonical_episode_header(
            subject=subject, context=context, opened_at=opened_at
        )
        if existing is not None:
            if not isinstance(existing, Episode):
                raise IdentityCollision(id, type(existing), Episode)
            existing_header = _canonical_episode_header(
                subject=existing.subject, context=existing.context, opened_at=existing.opened_at
            )
            if existing_header != incoming_header:
                raise IdentityCollision(id, Episode, Episode)
            return  # idempotent success
        episode = Episode(
            id=id, subject=subject, context=self._snapshot_context(context), opened_at=opened_at
        )
        self._entities[id] = episode
        self._entity_order.append(id)

    def _require_episode(self, episode: Id | Ref) -> Episode:
        target = identity_of(episode)
        stored = self._entities.get(target)
        if stored is None:
            raise KeyError(target)
        if not isinstance(stored, Episode):
            raise TypeError(f"Id {target!r} does not name an Episode")
        return stored

    def append_episode(self, episode: Id | Ref, item: Ref) -> None:
        stored = self._require_episode(episode)
        stored.append(item)

    def close_episode(self, episode: Id | Ref, at: WallInstant) -> None:
        stored = self._require_episode(episode)
        stored.close(at)

    def retrieve(
        self, query: RetrievalQuery, *, retrieved_at: WallInstant
    ) -> tuple[RecallCandidate, ...]:
        identity_target = identity_of(query.identity) if query.identity is not None else None

        raw: list[tuple[Id, list[Kind]]] = []  # (entity_id, relevance kinds in order)
        seen: dict[Id, int] = {}  # entity_id -> index into raw

        if identity_target is not None and identity_target in self._entities:
            raw.append((identity_target, [IDENTITY_MATCH]))
            seen[identity_target] = 0

        if query.text is not None:
            for entity_id in self._entity_order:
                entity = self._entities[entity_id]
                if any(query.text in text for text in lexical_content(entity)):
                    if entity_id in seen:
                        raw[seen[entity_id]][1].append(LEXICAL_MATCH)
                    else:
                        seen[entity_id] = len(raw)
                        raw.append((entity_id, [LEXICAL_MATCH]))

        retention_log = RetentionLog()
        for mark in self._retention_marks:
            retention_log.record(mark)

        active: list[RecallCandidate] = []
        deprioritized: list[RecallCandidate] = []
        archived: list[RecallCandidate] = []
        for entity_id, relevance_kinds in raw:
            accessibility = retention_log.current(entity_id)
            candidate = RecallCandidate(
                item=Ref(id=entity_id),
                query_context=query.context,
                relevance=tuple(relevance_kinds),
                retrieved_at=retrieved_at,
            )
            if accessibility == ACTIVE:
                active.append(candidate)
            elif accessibility == DEPRIORITIZED:
                deprioritized.append(candidate)
            elif accessibility == ARCHIVED:
                archived.append(candidate)
            else:
                raise ValueError(
                    f"default retrieval cannot interpret custom accessibility "
                    f"Kind {accessibility!r} for {entity_id!r}"
                )

        if query.include_archived:
            return tuple(active) + tuple(deprioritized) + tuple(archived)
        return tuple(active) + tuple(deprioritized)


def lexical_content(record: EntityMemoryRecord) -> tuple[str, ...]:
    """Only genuinely textual content already present on a record — never
    str()/repr() of anything else, and no recursive extraction from nested
    structures. Frozen field-by-field per MEMORY_ARCHITECTURE.md.
    """
    if isinstance(record, Observation):
        return tuple(
            v for v in (record.value, record.source, record.observer) if isinstance(v, str)
        )
    if isinstance(record, Claim):
        if isinstance(record.value, Known) and isinstance(record.value.value, str):
            return (record.value.value,)
        return ()
    if isinstance(record, Event):
        return (record.payload,) if isinstance(record.payload, str) else ()
    if isinstance(record, Effect):
        content = [record.description]
        if isinstance(record.target, str):
            content.append(record.target)
        return tuple(content)
    if isinstance(record, Provenance):
        return (record.transform_name, record.transform_version)
    if isinstance(record, Error):
        content = [record.message]
        if record.operation is not None:
            content.append(record.operation)
        return tuple(content)
    # Inference, Contradiction, Episode contribute nothing generically.
    return ()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalQuery:
    context: Context
    identity: Id | Ref | None = None
    text: str | None = None
    include_archived: bool = False

    def __post_init__(self) -> None:
        if self.identity is None and self.text is None:
            raise ValueError("RetrievalQuery requires at least one of identity or text")
        if self.text is not None and not self.text:
            raise ValueError("RetrievalQuery.text must not be empty if supplied")
