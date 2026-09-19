# Memory Pass 2: Persistence Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `src/memory/store.py` — `MemoryStore` protocol, `InMemoryStore` (the semantic reference implementation Pass 3's SQLite backend will be judged against), the frozen persisted-record union, canonical-representation collision detection, embedded-entity atomicity, retrieval + retention composition, and the Episode store surface — with no SQLite, no persistence backend, no network.

**Architecture:** One new production module (`store.py`) built entirely on Pass 1's five tier-0 modules (`episode`, `recall`, `retention`, `codec` — not `belief`, which stays independent) plus Core. Collision detection uses a private canonical `PersistedValue`-backed representation, never a record's own `__eq__` (Core's `Event` has Id-only equality — a real trap this design defeats). Persisted records are reconstructed via `dataclasses.replace()` with `as_persisted_value()`-snapshotted payload fields substituted in, so the store never holds a reference the caller could later mutate through. Two admitted structures embed other admissible records directly (`Inference.conclusion`, `Error.cause`) and are registered atomically with their parent.

**Tech Stack:** Python 3.14, pytest, Ruff, Pyright (strict) — same toolchain as Core and Pass 1, via `uv run`.

**Spec:** `MEMORY_SPECIFICATION.md`, `MEMORY_LAWS.md`, `MEMORY_ARCHITECTURE.md`, `MEMORY_ADVERSARIAL_MATRIX.md` (repo root, all updated for Pass 2), and `docs/memory-passes/02-persistence-boundary.md` (the Pass 2 preregistration this plan implements — read it in full; this plan is its task breakdown, not a replacement). Pass 1's `docs/memory-passes/01-semantic-constructions.md` and the shipped `src/memory/{episode,recall,retention,codec,belief}.py` are load-bearing context.

## Global Constraints

- Core v0 (`src/core/`) is closed — no file under `src/core/` is modified, ever.
- `store.py` imports only: `memory.episode`, `memory.recall`, `memory.retention`, `memory.codec`, and `core.identity`, `core.time`, `core.context`, `core.value`, `core.epistemic`, `core.observation`, `core.event`, `core.effect`, `core.provenance`, `core.error`. It does **not** import `memory.belief`, `memory.sqlite_store`, `sqlite3`, `core.state`, `core.trace`, `core.transform`, `core.relation`.
- No operation reads a wall/monotonic clock, allocates a UUID, or uses `random`. `retrieve()` takes an explicit `retrieved_at: WallInstant`; nothing else needs time at all.
- Every dataclass matches Core's own style: `@dataclass(frozen=True, slots=True, kw_only=True)`. `IdentityCollision`/`UnsupportedMemoryRecord` are plain `Exception` subclasses (not dataclasses) — matching Core's own `UnwrapError` precedent in `core/result.py`, since combining `@dataclass(frozen=True)` with `Exception` conflicts with `BaseException.__init__`'s own attribute assignment.
- The persisted-record union, `MemoryStore` protocol, `RetrievalQuery`, and the Episode store surface are **frozen** (not subject to redesign) — see `MEMORY_ARCHITECTURE.md` and the preregistration. Deviating from a frozen signature requires stopping and asking, not guessing.
- Collision detection **never** uses a record's own `__eq__`/`==` — only the private canonical representation. This is the single most important rule in this pass; `Event`'s Core equality is Id-only and will silently hide real collisions if bypassed.
- A successful `persist()` never leaves the store holding a reference the caller could later mutate through — object-typed payload fields are always the `as_persisted_value()`-validated snapshot, substituted in via `dataclasses.replace()`, never the caller's original object.
- Test commands use `uv run pytest`; lint/type commands use `uv run ruff check` / `uv run pyright`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```

---

### Task 1: Exceptions, record unions, and canonical representation

**Files:**
- Create: `src/memory/store.py`
- Test: `tests/memory/semantics/test_store.py`

**Interfaces:**
- Consumes: `core.identity.{Id, Ref}`, `core.context.Context`, `core.time.{WallInstant, Duration}`, `core.value.{Kind, Known, Unknown}`, `core.observation.Observation`, `core.epistemic.{Claim, Inference, Contradiction, Resolution}`, `core.event.Event`, `core.effect.Effect`, `core.provenance.Provenance`, `core.error.Error`, `memory.episode.Episode`, `memory.retention.RetentionMark`, and from `memory.codec`: `as_persisted_value`, `encode_persisted_value`, `encode_kind`, `encode_id`, `encode_namespace`, `encode_ref`, `encode_wall_instant`, `encode_duration`, `encode_context`.
- Produces (all in `store.py`, consumed by Tasks 2-6): `EntityMemoryRecord`, `NonEntityMemoryRecord`, `MemoryRecord`, `PersistRecord` (type aliases); `IdentityCollision`, `UnsupportedMemoryRecord` (exceptions); `_canonical_record(record: EntityMemoryRecord) -> tuple[object, ...]` (dispatches across the 8 canonicalizable types — Resolution/RetentionMark/Episode never go through it); `_canonical_episode_header(*, subject: Id | Ref, context: Context, opened_at: WallInstant) -> tuple[object, ...]`.

- [x] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_store.py`:

```python
"""Propositions for memory.store.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections K (admissibility),
L (identity collisions).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.epistemic import Claim, Contradiction, Inference, Resolution
from core.error import Error
from core.event import Event
from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind, Known, Unknown

from memory.store import (
    IdentityCollision,
    UnsupportedMemoryRecord,
    _canonical_episode_header,
    _canonical_record,
)

CLAIM_KIND = Kind("memory.test.claim")
CONTRA_KIND = Kind("memory.test.contradiction")
SUBJECT_KIND = Kind("memory.test.subject")
AGENT_KIND = Kind("memory.test.agent")
EVENT_KIND = Kind("memory.test.event")
ERROR_KIND = Kind("memory.test.error")
AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
CTX = Context(as_of=AT)
SUBJECT = Id(SUBJECT_KIND, "s1")
AGENT = Id(AGENT_KIND, "a1")


def make_claim(cid: str, *, subject: Id | Ref = SUBJECT, value: object = "v") -> Claim[object]:
    return Claim(
        id=Id(CLAIM_KIND, cid), subject=subject, predicate=Kind("memory.test.p"),
        value=Known(value), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
    )


class TestCanonicalRecordEventTrap:
    def test_id_04_event_identity_equality_does_not_hide_collision(self) -> None:
        # The critical case from the preregistration: Core's Event.__eq__ is
        # Id-only, so e1 == e2 is True even though payloads differ. Canonical
        # representation must NOT be fooled by that.
        e1 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="A")
        e2 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="B")
        assert e1 == e2, "sanity check: Core Event equality really is Id-only"
        assert _canonical_record(e1) != _canonical_record(e2)

    def test_event_same_payload_is_canonically_identical(self) -> None:
        e1 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="A")
        e2 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="A")
        assert _canonical_record(e1) == _canonical_record(e2)


class TestCanonicalRecordDeterminism:
    def test_same_claim_canonicalizes_identically_every_time(self) -> None:
        claim = make_claim("c1")
        assert _canonical_record(claim) == _canonical_record(claim)

    def test_known_vs_unknown_are_canonically_distinct(self) -> None:
        known = make_claim("c1", value="x")
        unknown = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Unknown(), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        assert _canonical_record(known) != _canonical_record(unknown)

    def test_id_vs_ref_vs_namespaced_ref_subject_are_canonically_distinct(self) -> None:
        bare = make_claim("c1", subject=SUBJECT)
        ref_no_ns = make_claim("c1", subject=Ref(id=SUBJECT))
        ref_with_ns = make_claim("c1", subject=Ref(id=SUBJECT, namespace=Namespace(("finance",))))
        forms = {_canonical_record(bare), _canonical_record(ref_no_ns), _canonical_record(ref_with_ns)}
        assert len(forms) == 3

    def test_metadata_canonical_form_independent_of_dict_iteration_order(self) -> None:
        e_a = Error(id=Id(ERROR_KIND, "m1"), kind=ERROR_KIND, message="x", at=AT, metadata={"a": 1, "b": 2})
        e_b = Error(id=Id(ERROR_KIND, "m1"), kind=ERROR_KIND, message="x", at=AT, metadata={"b": 2, "a": 1})
        assert _canonical_record(e_a) == _canonical_record(e_b)


class TestCanonicalRecordRecursion:
    def test_inference_conclusion_participates_in_canonical_form(self) -> None:
        c1 = make_claim("c1", value="first")
        c2 = make_claim("c1", value="second")  # same Id, different content
        inf1 = Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=c1, at=AT)
        inf2 = Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=c2, at=AT)
        assert _canonical_record(inf1) != _canonical_record(inf2)

    def test_error_cause_chain_participates_in_canonical_form(self) -> None:
        root_a = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="A", at=AT)
        root_b = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="B", at=AT)
        wrap_a = Error(id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="w", at=AT, cause=root_a)
        wrap_b = Error(id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="w", at=AT, cause=root_b)
        assert _canonical_record(wrap_a) != _canonical_record(wrap_b)


class TestCanonicalRecordAllTypes:
    def test_every_admitted_entity_type_canonicalizes_without_error(self) -> None:
        claim = make_claim("c1")
        records = [
            claim,
            Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=claim, at=AT),
            Contradiction(id=Id(CONTRA_KIND, "k1"), subject=SUBJECT, statements=(Ref(id=claim.id), Ref(id=Id(CLAIM_KIND, "other"))), detected_at=AT, context=CTX),
            Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="p"),
            Error(id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="m", at=AT),
        ]
        for record in records:
            assert isinstance(_canonical_record(record), tuple)

    def test_unsupported_type_raises(self) -> None:
        class NotARecord:
            id = Id(Kind("memory.test.fake"), "x")

        with pytest.raises(UnsupportedMemoryRecord):
            _canonical_record(NotARecord())  # type: ignore[arg-type]


class TestEpisodeHeaderCanonical:
    def test_same_header_is_canonically_identical(self) -> None:
        h1 = _canonical_episode_header(subject=SUBJECT, context=CTX, opened_at=AT)
        h2 = _canonical_episode_header(subject=SUBJECT, context=CTX, opened_at=AT)
        assert h1 == h2

    def test_different_subject_is_canonically_distinct(self) -> None:
        h1 = _canonical_episode_header(subject=SUBJECT, context=CTX, opened_at=AT)
        h2 = _canonical_episode_header(subject=Id(SUBJECT_KIND, "other"), context=CTX, opened_at=AT)
        assert h1 != h2


class TestExceptions:
    def test_identity_collision_carries_structured_fields_not_stringified_payload(self) -> None:
        exc = IdentityCollision(Id(EVENT_KIND, "e1"), existing_type=Event, incoming_type=Event)
        assert exc.id == Id(EVENT_KIND, "e1")
        assert exc.existing_type is Event
        assert exc.incoming_type is Event
        assert "Event" in str(exc)

    def test_unsupported_memory_record_names_the_type(self) -> None:
        exc = UnsupportedMemoryRecord(dict)
        assert exc.record_type is dict
        assert "dict" in str(exc)

    def test_unsupported_memory_record_for_episode_directs_to_create_episode(self) -> None:
        from memory.episode import Episode

        exc = UnsupportedMemoryRecord(Episode)
        assert "create_episode" in str(exc)


class TestImportSideEffects:
    def test_store_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.store",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory.store'`

- [x] **Step 3: Write minimal implementation**

Create `src/memory/store.py`:

```python
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


def _canonical_record(record: EntityMemoryRecord) -> tuple[object, ...]:
    """Canonical PersistedValue-backed representation used for identity-collision
    comparison and semantic-snapshot verification — never a record's own
    __eq__ (Core's Event equality is Id-only, which would otherwise hide a
    real collision). Only the 8 canonicalizable EntityMemoryRecord types reach
    here — Resolution/RetentionMark never do (append-only, no canonical
    comparison), and Episode uses _canonical_episode_header instead.
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: PASS (all tests)

- [x] **Step 5: Commit**

```bash
git add src/memory/store.py tests/memory/semantics/test_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 2: exceptions, record unions, canonical representation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `InMemoryStore.persist()` and `resolve()`

**Files:**
- Modify: `src/memory/store.py`
- Modify: `tests/memory/semantics/test_store.py`

**Interfaces:**
- Consumes: Task 1's `_canonical_record`, `_canonical_episode_header`, `IdentityCollision`, `UnsupportedMemoryRecord`, the type aliases; `dataclasses.replace`; `memory.codec.as_persisted_value`.
- Produces: `InMemoryStore` (the class — `__init__`, `persist(record: PersistRecord) -> None`, `resolve(item: Id | Ref) -> EntityMemoryRecord | None`, plus private helpers `_check`, `_commit`, `_snapshot_episode`). Tasks 3-6 add more methods to this same class.

Before writing code, read `src/memory/store.py` as Task 1 left it — you are appending to it, not starting fresh.

- [x] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_store.py` (add these imports to the existing top-of-file import block — `identity_of` from `core.identity`, `InMemoryStore` from `memory.store` — rather than as new mid-file statements):

```python
class TestPersistBasicEntities:
    def test_pa_01_supported_observation_persists(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="reading",
            at=AT, source="sensor-1", context=CTX,
        )
        store.persist(obs)
        assert store.resolve(obs.id) == obs

    def test_pa_04_arbitrary_core_entity_not_in_union_rejected(self) -> None:
        from core.provenance import Traced

        store = InMemoryStore()
        with pytest.raises(UnsupportedMemoryRecord):
            store.persist(Traced(value="x"))  # type: ignore[arg-type]

    def test_pa_05_user_object_with_id_rejected(self) -> None:
        class FakeEntity:
            id = Id(Kind("memory.test.fake"), "f1")

        store = InMemoryStore()
        with pytest.raises(UnsupportedMemoryRecord):
            store.persist(FakeEntity())  # type: ignore[arg-type]

    def test_pa_06_state_rejected(self) -> None:
        from core.state import State

        store = InMemoryStore()
        state = State(subject=SUBJECT, value="x", at=AT, context=CTX)
        with pytest.raises(UnsupportedMemoryRecord):
            store.persist(state)  # type: ignore[arg-type]

    def test_episode_passed_to_generic_persist_rejected_with_helpful_message(self) -> None:
        from memory.episode import Episode

        store = InMemoryStore()
        episode = Episode(id=Id(Kind("memory.test.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT)
        with pytest.raises(UnsupportedMemoryRecord, match="create_episode"):
            store.persist(episode)  # type: ignore[arg-type]

    def test_resolve_missing_id_returns_none(self) -> None:
        store = InMemoryStore()
        assert store.resolve(Id(Kind("memory.test.missing"), "x")) is None


class TestIdentityCollision:
    def test_id_01_first_insertion_succeeds(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        store.persist(claim)
        assert store.resolve(claim.id) == claim

    def test_id_02_idempotent_identical_retry(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        store.persist(claim)
        store.persist(claim)  # must not raise
        assert store.resolve(claim.id) == claim

    def test_id_03_different_representation_same_id_collides(self) -> None:
        store = InMemoryStore()
        store.persist(make_claim("c1", value="first"))
        with pytest.raises(IdentityCollision):
            store.persist(make_claim("c1", value="second"))

    def test_same_id_across_concrete_types_collides(self) -> None:
        store = InMemoryStore()
        shared_kind_value = "shared-id"
        event = Event(id=Id(Kind("memory.test.shared"), shared_kind_value), kind=EVENT_KIND, at=AT, payload="p")
        contradiction = Contradiction(
            id=Id(Kind("memory.test.shared"), shared_kind_value), subject=SUBJECT,
            statements=(Ref(id=Id(CLAIM_KIND, "a")), Ref(id=Id(CLAIM_KIND, "b"))),
            detected_at=AT, context=CTX,
        )
        store.persist(event)
        with pytest.raises(IdentityCollision):
            store.persist(contradiction)

    def test_failed_collision_leaves_original_untouched(self) -> None:
        store = InMemoryStore()
        original = make_claim("c1", value="first")
        store.persist(original)
        try:
            store.persist(make_claim("c1", value="second"))
        except IdentityCollision:
            pass
        assert store.resolve(original.id) == original

    def test_id_04_event_identity_equality_does_not_hide_collision_via_store(self) -> None:
        store = InMemoryStore()
        e1 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="A")
        e2 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="B")
        store.persist(e1)
        with pytest.raises(IdentityCollision):
            store.persist(e2)


class TestPersistDoesNotMutateCallerOrLeakMutation:
    def test_pa_08_persist_does_not_mutate_source_record(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store.persist(obs)
        assert obs.value == "x"  # unchanged

    def test_caller_mutating_a_mutable_payload_after_persist_does_not_change_resolve(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        mutable_source_payload = {"a": 1}
        obs = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value=mutable_source_payload,
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        mutable_source_payload["a"] = 999
        resolved = store.resolve(obs.id)
        assert resolved is not None
        assert dict(resolved.value) == {"a": 1}  # type: ignore[arg-type]

    def test_stored_payload_snapshot_is_itself_immutable(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value={"a": 1}, at=AT, source="s", context=CTX)
        store.persist(obs)
        resolved = store.resolve(obs.id)
        assert resolved is not None
        with pytest.raises(TypeError):
            resolved.value["a"] = 2  # type: ignore[index]


class TestEmbeddedEntities:
    def test_persist_inference_with_new_conclusion_registers_both(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        inference = Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=claim, at=AT)
        store.persist(inference)
        assert store.resolve(claim.id) == claim
        assert store.resolve(inference.id) == inference

    def test_persist_inference_where_embedded_conclusion_conflicts_fails_atomically(self) -> None:
        store = InMemoryStore()
        store.persist(make_claim("c1", value="already stored"))
        conflicting_claim = make_claim("c1", value="different")
        inference = Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=conflicting_claim, at=AT)
        with pytest.raises(IdentityCollision):
            store.persist(inference)
        assert store.resolve(inference.id) is None

    def test_persist_error_with_cause_independently_resolves(self) -> None:
        store = InMemoryStore()
        root = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="root", at=AT)
        wrapping = Error(id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrap", at=AT, cause=root)
        store.persist(wrapping)
        resolved_root = store.resolve(root.id)
        assert resolved_root is not None
        assert resolved_root.message == "root"  # type: ignore[union-attr]

    def test_persist_error_where_nested_cause_collides_fails_atomically(self) -> None:
        store = InMemoryStore()
        store.persist(Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="already stored", at=AT))
        conflicting_root = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="different", at=AT)
        wrapping = Error(id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrap", at=AT, cause=conflicting_root)
        with pytest.raises(IdentityCollision):
            store.persist(wrapping)
        assert store.resolve(wrapping.id) is None

    def test_persist_error_with_foreign_exception_rejected_no_partial_write(self) -> None:
        from memory.codec import UnsupportedPersistedValue

        store = InMemoryStore()
        error = Error(id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="m", at=AT, exception=ValueError("boom"))
        with pytest.raises(UnsupportedPersistedValue):
            store.persist(error)
        assert store.resolve(error.id) is None
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'InMemoryStore'`

- [x] **Step 3: Write minimal implementation**

Append to `src/memory/store.py` (add `import dataclasses` and `from core.observation import Observation` — wait, `Observation` is already imported by Task 1 — just add `import dataclasses` to the top-of-file import block, consolidated with the rest, not mid-file):

```python
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
        if self._entity_canonical[id] == canonical:
            return "idempotent"
        raise IdentityCollision(id, type(existing), incoming_type)

    def _commit(self, id: Id, record: EntityMemoryRecord, canonical: tuple[object, ...]) -> None:
        self._entities[id] = record
        self._entity_canonical[id] = canonical
        self._entity_order.append(id)

    def _snapshot_simple_entity(self, record: EntityMemoryRecord) -> EntityMemoryRecord:
        """Reconstruct a record with every object-typed payload field replaced
        by its as_persisted_value()-validated snapshot, via dataclasses.replace
        — never store a reference the caller could later mutate through.
        """
        if isinstance(record, Observation):
            return dataclasses.replace(
                record,
                value=as_persisted_value(record.value),
                source=as_persisted_value(record.source),
                observer=(
                    as_persisted_value(record.observer) if record.observer is not None else None
                ),
            )
        if isinstance(record, Event):
            return dataclasses.replace(record, payload=as_persisted_value(record.payload))
        if isinstance(record, Effect):
            return dataclasses.replace(
                record,
                target=as_persisted_value(record.target),
                metadata=(
                    as_persisted_value(record.metadata) if record.metadata is not None else None
                ),
            )
        # Contradiction and Provenance have no object-typed payload fields.
        return record

    def _snapshot_claim(self, claim: Claim[object]) -> Claim[object]:
        if isinstance(claim.value, Known):
            return dataclasses.replace(claim, value=Known(as_persisted_value(claim.value.value)))
        return claim  # Unknown carries no payload to snapshot

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
            # NOTE(Task 3): Contradiction persistence must also append to
            # self._conflict_entries/_conflict_ids here.

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
                original, cause=rebuilt_cause, metadata=snapshotted_metadata
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
```

Also add, near the top-level imports, `from memory.codec import UnsupportedPersistedValue` (consolidated into the existing `from memory.codec import (...)` block, not a new statement) — `_persist_error` raises it directly for foreign exceptions.

Note: `Contradiction` persistence (via `persist()`'s final fallthrough branch above) currently only registers the entity itself — it does **not** yet append to `self._conflict_entries`/`self._conflict_ids`. That wiring is Task 3's job (`conflicts_for()` needs it), to keep this task's diff focused on `persist()`/`resolve()`. The `# NOTE(Task 3): ...` comment already left in the code above marks exactly where to add it.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: PASS (all tests)

- [x] **Step 5: Commit**

```bash
git add src/memory/store.py tests/memory/semantics/test_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 2: implement InMemoryStore.persist() and resolve()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `claims_for()` and `conflicts_for()`

**Files:**
- Modify: `src/memory/store.py`
- Modify: `tests/memory/semantics/test_store.py`

**Interfaces:**
- Consumes: Task 2's `InMemoryStore`, `_entities`/`_entity_order`; `core.identity.identity_of`.
- Produces: `InMemoryStore.claims_for(subject, predicate) -> tuple[Claim[object], ...]`, `InMemoryStore.conflicts_for(subject, predicate) -> tuple[Contradiction | Resolution, ...]`. Also wires `Contradiction` persistence into `self._conflict_entries`/`self._conflict_ids` (left as a marked TODO by Task 2).

- [x] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_store.py` (add `from core.identity import identity_of` to the top-of-file import block if not already present):

```python
class TestClaimsFor:
    def test_matches_by_subject_and_predicate(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        store.persist(claim)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (claim,)

    def test_ref_subject_matches_via_identity_of(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        store.persist(claim)
        result = store.claims_for(Ref(id=SUBJECT, namespace=Namespace(("finance",))), Kind("memory.test.p"))
        assert result == (claim,)

    def test_different_predicate_excluded(self) -> None:
        store = InMemoryStore()
        store.persist(make_claim("c1"))
        assert store.claims_for(SUBJECT, Kind("memory.test.other_predicate")) == ()

    def test_claims_for_performs_no_context_filtering(self) -> None:
        # Critical rule: claims_for() never filters by Context — belief_state()
        # needs the full unfiltered slot to correctly determine conflict
        # relevance before its own Context filtering.
        store = InMemoryStore()
        incompatible_context = Context(as_of=WallInstant(datetime(2024, 6, 1, tzinfo=UTC)))
        claim = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Known("x"), context=incompatible_context, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(claim)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (claim,)

    def test_persistence_order_preserved(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        c2 = Claim(
            id=Id(CLAIM_KIND, "c2"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Known("y"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(c2)
        store.persist(c1)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (c2, c1)

    def test_idempotent_retry_does_not_duplicate(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        store.persist(claim)
        store.persist(claim)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (claim,)

    def test_inference_embedded_conclusion_appears_exactly_once(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        inference = Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=claim, at=AT)
        store.persist(inference)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (claim,)


class TestConflictsFor:
    def _contradiction(self, statements: tuple[Ref, ...], contra_id: str = "k1", subject: Id | Ref = SUBJECT) -> Contradiction:
        return Contradiction(id=Id(CONTRA_KIND, contra_id), subject=subject, statements=statements, detected_at=AT, context=CTX)

    def test_cl_01_subject_mismatch_excluded(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        store.persist(c1)
        other_subject_contradiction = self._contradiction(
            (Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "other"))), subject=Id(SUBJECT_KIND, "different"),
        )
        store.persist(other_subject_contradiction)
        assert store.conflicts_for(SUBJECT, Kind("memory.test.p")) == ()

    def test_cl_02_no_slot_statement_excluded(self) -> None:
        store = InMemoryStore()
        contradiction = self._contradiction((Ref(id=Id(CLAIM_KIND, "gone1")), Ref(id=Id(CLAIM_KIND, "gone2"))))
        store.persist(contradiction)
        assert store.conflicts_for(SUBJECT, Kind("memory.test.p")) == ()

    def test_cl_03_one_slot_statement_sufficient(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        store.persist(c1)
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "gone"))))
        store.persist(contradiction)
        assert contradiction in store.conflicts_for(SUBJECT, Kind("memory.test.p"))

    def test_cl_07_ref_statement_namespace_does_not_break_identity_match(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        store.persist(c1)
        namespaced_statement = Ref(id=c1.id, namespace=Namespace(("some", "ns")))
        contradiction = self._contradiction((namespaced_statement, Ref(id=Id(CLAIM_KIND, "other"))))
        store.persist(contradiction)
        assert contradiction in store.conflicts_for(SUBJECT, Kind("memory.test.p"))

    def test_cl_08_resolution_follows_relevant_contradiction(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        c2 = make_claim("c2")
        store.persist(c1)
        store.persist(c2)
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        store.persist(contradiction)
        resolution = Resolution(contradiction=Ref(id=contradiction.id), rationale="r", resolved_by=AGENT, at=AT)
        store.persist(resolution)
        result = store.conflicts_for(SUBJECT, Kind("memory.test.p"))
        assert result == (contradiction, resolution)

    def test_cl_09_multiple_resolutions_preserve_append_order(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        store.persist(c1)
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "other"))))
        store.persist(contradiction)
        r1 = Resolution(contradiction=Ref(id=contradiction.id), rationale="first", resolved_by=AGENT, at=AT)
        r2 = Resolution(contradiction=Ref(id=contradiction.id), rationale="second", resolved_by=AGENT, at=AT)
        store.persist(r1)
        store.persist(r2)
        assert store.conflicts_for(SUBJECT, Kind("memory.test.p")) == (contradiction, r1, r2)

    def test_resolution_before_contradiction_raises(self) -> None:
        store = InMemoryStore()
        orphan_resolution = Resolution(
            contradiction=Ref(id=Id(CONTRA_KIND, "never-persisted")), rationale="r", resolved_by=AGENT, at=AT,
        )
        with pytest.raises(ValueError, match="not recorded"):
            store.persist(orphan_resolution)

    def test_idempotent_contradiction_retry_does_not_duplicate_conflict_history(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        store.persist(c1)
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "other"))))
        store.persist(contradiction)
        store.persist(contradiction)
        result = store.conflicts_for(SUBJECT, Kind("memory.test.p"))
        assert result.count(contradiction) == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: FAIL/ERROR — `AttributeError: 'InMemoryStore' object has no attribute 'claims_for'`

- [x] **Step 3: Write minimal implementation**

In `src/memory/store.py`, find the final fallthrough branch inside `persist()` (left by Task 2 with a `# NOTE(Task 3): ...` comment) and wire `Contradiction` persistence into the conflict history — change that branch to:

```python
        stored = self._snapshot_simple_entity(record)
        canonical = _canonical_record(stored)
        action = self._check(stored.id, canonical, type(record))
        if action == "insert":
            self._commit(stored.id, stored, canonical)
            if isinstance(stored, Contradiction):
                self._conflict_entries.append(stored)
                self._conflict_ids.add(stored.id)
```

(Removing the `# NOTE(Task 3): ...` comment now that it's addressed.)

(Removing the `# NOTE(Task 3): ...` comment now that it's addressed.)

Then add these two methods to `InMemoryStore` (append after `resolve()`):

```python
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
```

Add `from core.identity import identity_of` to the top-of-file import block (consolidated with the existing `from core.identity import Id, Ref` line — change it to `from core.identity import Id, Ref, identity_of`). Also add `Kind` to the top-of-file `from core.value import Known, Unknown` line — change it to `from core.value import Kind, Known, Unknown` — since `claims_for`/`conflicts_for`'s `predicate: Kind` parameter is the first bare use of the `Kind` type in this file (earlier code only ever called `encode_kind()` on an already-`Kind`-typed field, never needing the type itself imported).

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: PASS (all tests)

- [x] **Step 5: Commit**

```bash
git add src/memory/store.py tests/memory/semantics/test_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 2: implement claims_for() and conflicts_for()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `retention_for()` and the Episode store surface

**Files:**
- Modify: `src/memory/store.py`
- Modify: `tests/memory/semantics/test_store.py`

**Interfaces:**
- Consumes: Task 2's `InMemoryStore`, `_snapshot_episode`, `_canonical_episode_header`; `memory.episode.Episode`; `core.identity.identity_of`.
- Produces: `InMemoryStore.retention_for(item) -> tuple[RetentionMark, ...]`, `InMemoryStore.create_episode(*, id, subject, context, opened_at) -> None`, `InMemoryStore.append_episode(episode, item) -> None`, `InMemoryStore.close_episode(episode, at) -> None`.

- [x] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_store.py`:

```python
class TestRetentionFor:
    def test_matches_via_identity_of(self) -> None:
        from memory.retention import ACTIVE

        store = InMemoryStore()
        target = Ref(id=Id(Kind("memory.test.item"), "x"))
        mark = RetentionMark(item=target, accessibility=ACTIVE, at=AT)
        store.persist(mark)
        assert store.retention_for(Id(Kind("memory.test.item"), "x")) == (mark,)

    def test_no_marks_returns_empty_tuple(self) -> None:
        store = InMemoryStore()
        assert store.retention_for(Id(Kind("memory.test.item"), "nomarks")) == ()

    def test_duplicate_marks_all_preserved(self) -> None:
        from memory.retention import ARCHIVED

        store = InMemoryStore()
        target = Id(Kind("memory.test.item"), "x")
        mark1 = RetentionMark(item=Ref(id=target), accessibility=ARCHIVED, at=AT)
        mark2 = RetentionMark(item=Ref(id=target), accessibility=ARCHIVED, at=AT)
        store.persist(mark1)
        store.persist(mark2)
        assert store.retention_for(target) == (mark1, mark2)

    def test_namespace_does_not_bypass_mark(self) -> None:
        from memory.retention import ARCHIVED

        store = InMemoryStore()
        target = Id(Kind("memory.test.item"), "x")
        mark = RetentionMark(item=Ref(id=target, namespace=Namespace(("finance",))), accessibility=ARCHIVED, at=AT)
        store.persist(mark)
        assert store.retention_for(Ref(id=target, namespace=Namespace(("ledger",)))) == (mark,)


class TestEpisodeStoreSurface:
    def test_create_empty_open_episode(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        resolved = store.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == ()
        assert resolved.closed_at is None

    def test_same_header_create_is_idempotent(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)  # must not raise

    def test_different_header_same_id_collides(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        with pytest.raises(IdentityCollision):
            store.create_episode(id=episode_id, subject=Id(SUBJECT_KIND, "different"), context=CTX, opened_at=AT)

    def test_episode_id_collides_globally_with_another_entity_type(self) -> None:
        store = InMemoryStore()
        shared_id = Id(Kind("memory.test.episode"), "ep1")
        store.persist(Event(id=shared_id, kind=EVENT_KIND, at=AT, payload="p"))
        with pytest.raises(IdentityCollision):
            store.create_episode(id=shared_id, subject=SUBJECT, context=CTX, opened_at=AT)

    def test_append_exact_order_and_duplicates_preserved(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        ref_a = Ref(id=Id(Kind("memory.test.item"), "a"))
        ref_b = Ref(id=Id(Kind("memory.test.item"), "b"))
        store.append_episode(episode_id, ref_a)
        store.append_episode(episode_id, ref_b)
        store.append_episode(episode_id, ref_a)  # duplicate
        resolved = store.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == (ref_a, ref_b, ref_a)

    def test_append_to_missing_episode_raises_keyerror(self) -> None:
        store = InMemoryStore()
        with pytest.raises(KeyError):
            store.append_episode(Id(Kind("memory.test.episode"), "missing"), Ref(id=Id(Kind("memory.test.item"), "x")))

    def test_append_to_non_episode_id_raises_typeerror(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        store.persist(claim)
        with pytest.raises(TypeError):
            store.append_episode(claim.id, Ref(id=Id(Kind("memory.test.item"), "x")))

    def test_append_after_close_raises(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.close_episode(episode_id, AT)
        with pytest.raises(ValueError):
            store.append_episode(episode_id, Ref(id=Id(Kind("memory.test.item"), "x")))

    def test_close_once_then_twice_raises(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.close_episode(episode_id, AT)
        with pytest.raises(ValueError):
            store.close_episode(episode_id, AT)

    def test_close_before_open_raises(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        opened_at = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=opened_at)
        with pytest.raises(ValueError):
            store.close_episode(episode_id, AT)  # AT is 2024-01-01, before opened_at

    def test_resolved_episode_is_a_snapshot_not_live_store_state(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        first_snapshot = store.resolve(episode_id)
        assert isinstance(first_snapshot, Episode)
        store.append_episode(episode_id, Ref(id=Id(Kind("memory.test.item"), "x")))
        assert first_snapshot.items() == ()  # earlier snapshot unaffected

    def test_caller_mutation_of_resolved_episode_does_not_mutate_store(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        resolved = store.resolve(episode_id)
        assert isinstance(resolved, Episode)
        resolved.append(Ref(id=Id(Kind("memory.test.item"), "x")))  # mutate the returned snapshot
        assert store.resolve(episode_id).items() == ()  # type: ignore[union-attr]
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: FAIL/ERROR — `AttributeError: 'InMemoryStore' object has no attribute 'retention_for'`

- [x] **Step 3: Write minimal implementation**

Append these methods to `InMemoryStore` in `src/memory/store.py`:

```python
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
        episode = Episode(id=id, subject=subject, context=context, opened_at=opened_at)
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
```

Note: `create_episode`/`append_episode`/`close_episode` don't add entries to `self._entity_canonical` — that dict is only used by `_check()` for the generic-`persist()` collision path; Episode's own idempotency check uses `_canonical_episode_header` directly against the live stored `Episode`'s own fields, not a cached canonical form. This is intentional (Episode's mutable state means a cached canonical snapshot would go stale on every append/close) — do not "fix" this by trying to keep `_entity_canonical` in sync for Episodes.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: PASS (all tests)

- [x] **Step 5: Commit**

```bash
git add src/memory/store.py tests/memory/semantics/test_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 2: implement retention_for() and the Episode store surface

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `lexical_content()`, `RetrievalQuery`, and `retrieve()`

**Files:**
- Modify: `src/memory/store.py`
- Modify: `tests/memory/semantics/test_store.py`

**Interfaces:**
- Consumes: Task 2-4's `InMemoryStore` (`_entities`, `_entity_order`, `retention_for`); `memory.recall.{RecallCandidate, IDENTITY_MATCH, LEXICAL_MATCH}`; `memory.retention.{RetentionLog, RetentionMark, ACTIVE, DEPRIORITIZED, ARCHIVED}`.
- Produces: `lexical_content(record: EntityMemoryRecord) -> tuple[str, ...]`, `RetrievalQuery` (dataclass), `InMemoryStore.retrieve(query, *, retrieved_at) -> tuple[RecallCandidate, ...]`.

- [x] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_store.py`:

```python
class TestLexicalContent:
    def test_observation_bare_string_value_searchable(self) -> None:
        from core.observation import Observation

        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="hello world", at=AT, source="s", context=CTX)
        assert "hello world" in lexical_content(obs)

    def test_observation_int_value_not_searchable(self) -> None:
        from core.observation import Observation

        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value=42, at=AT, source="s", context=CTX)
        assert lexical_content(obs) == ()

    def test_claim_known_str_searchable_unknown_not(self) -> None:
        known = make_claim("c1", value="findme")
        assert "findme" in lexical_content(known)
        unknown = Claim(
            id=Id(CLAIM_KIND, "c2"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Unknown(), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        assert lexical_content(unknown) == ()

    def test_event_bare_string_payload_searchable_bytes_not(self) -> None:
        e1 = Event(id=Id(EVENT_KIND, "e1"), kind=EVENT_KIND, at=AT, payload="text")
        assert "text" in lexical_content(e1)
        e2 = Event(id=Id(EVENT_KIND, "e2"), kind=EVENT_KIND, at=AT, payload=b"bytes")
        assert lexical_content(e2) == ()

    def test_effect_description_always_target_when_string(self) -> None:
        from core.effect import Effect

        effect = Effect(id=Id(Kind("memory.test.effect"), "f1"), kind=Kind("memory.test.k"), description="did a thing", target="str target", at=AT)
        content = lexical_content(effect)
        assert "did a thing" in content
        assert "str target" in content

    def test_error_message_and_operation_searchable(self) -> None:
        error = Error(id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="failed hard", at=AT, operation="do-thing")
        content = lexical_content(error)
        assert "failed hard" in content
        assert "do-thing" in content

    def test_provenance_transform_name_and_version_searchable(self) -> None:
        from core.provenance import Provenance
        from core.time import Duration

        prov = Provenance(
            id=Id(Kind("memory.test.prov"), "p1"), transform_id=Id(Kind("memory.test.tx"), "t1"),
            transform_name="normalize", transform_version="1.0", inputs=(), parents=(), at=AT, duration=Duration(0),
        )
        content = lexical_content(prov)
        assert "normalize" in content
        assert "1.0" in content

    def test_inference_and_contradiction_and_episode_contribute_nothing(self) -> None:
        claim = make_claim("c1", value="text")
        inference = Inference(id=Id(Kind("memory.test.inference"), "i1"), premises=(), method=Kind("memory.test.m"), conclusion=claim, at=AT)
        assert lexical_content(inference) == ()
        contradiction = Contradiction(id=Id(CONTRA_KIND, "k1"), subject=SUBJECT, statements=(Ref(id=claim.id), Ref(id=Id(CLAIM_KIND, "x"))), detected_at=AT, context=CTX)
        assert lexical_content(contradiction) == ()
        episode = Episode(id=Id(Kind("memory.test.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT)
        assert lexical_content(episode) == ()


class TestRetrievalQuery:
    def test_neither_identity_nor_text_rejects(self) -> None:
        with pytest.raises(ValueError):
            RetrievalQuery(context=CTX)

    def test_empty_text_rejects(self) -> None:
        with pytest.raises(ValueError):
            RetrievalQuery(context=CTX, text="")

    def test_identity_only_is_valid(self) -> None:
        RetrievalQuery(context=CTX, identity=SUBJECT)  # must not raise

    def test_text_only_is_valid(self) -> None:
        RetrievalQuery(context=CTX, text="hello")  # must not raise


class TestRetrieve:
    def test_identity_only_retrieval(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, identity=obs.id), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].item.id == obs.id
        assert candidates[0].relevance == (IDENTITY_MATCH,)

    def test_text_only_retrieval(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findable text", at=AT, source="s", context=CTX)
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findable"), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].relevance == (LEXICAL_MATCH,)

    def test_combined_identity_and_lexical_match_produces_one_candidate_both_kinds(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findable text", at=AT, source="s", context=CTX)
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, identity=obs.id, text="findable"), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].relevance == (IDENTITY_MATCH, LEXICAL_MATCH)

    def test_retrieved_at_and_query_context_copied_exactly(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store.persist(obs)
        query_ctx = Context(as_of=WallInstant(datetime(2024, 3, 1, tzinfo=UTC)))
        retrieved_at = WallInstant(datetime(2024, 3, 2, tzinfo=UTC))
        candidates = store.retrieve(RetrievalQuery(context=query_ctx, identity=obs.id), retrieved_at=retrieved_at)
        assert candidates[0].query_context == query_ctx
        assert candidates[0].retrieved_at == retrieved_at

    def test_case_sensitive_substring_not_fts_operators(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="Hello World", at=AT, source="s", context=CTX)
        store.persist(obs)
        assert store.retrieve(RetrievalQuery(context=CTX, text="hello"), retrieved_at=AT) == ()  # case-sensitive
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="Hello"), retrieved_at=AT)) == 1
        # FTS-looking characters are ordinary literal characters, not operators.
        store2 = InMemoryStore()
        literal_obs = Observation(id=Id(Kind("memory.test.obs"), "o2"), subject=SUBJECT, value='a "quoted*" (thing)', at=AT, source="s", context=CTX)
        store2.persist(literal_obs)
        assert len(store2.retrieve(RetrievalQuery(context=CTX, text='"quoted*"'), retrieved_at=AT)) == 1

    def test_no_result_limit(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        for i in range(50):
            store.persist(Observation(id=Id(Kind("memory.test.obs"), f"o{i}"), subject=SUBJECT, value="shared text", at=AT, source="s", context=CTX))
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="shared"), retrieved_at=AT)) == 50


class TestRetrievalRetention:
    def test_rr_01_active_eligible_by_default(self) -> None:
        from core.observation import Observation
        from memory.retention import ACTIVE

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ACTIVE, at=AT))
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)) == 1

    def test_rr_02_rr_03_archived_excluded_by_default_included_when_requested(self) -> None:
        from core.observation import Observation
        from memory.retention import ARCHIVED

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        assert store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT) == ()
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="findme", include_archived=True), retrieved_at=AT)) == 1

    def test_rr_07_no_mark_treated_as_active(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)) == 1

    def test_deprioritized_ordered_after_active(self) -> None:
        from core.observation import Observation
        from memory.retention import ACTIVE, DEPRIORITIZED

        store = InMemoryStore()
        deprioritized_obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme first", at=AT, source="s", context=CTX)
        active_obs = Observation(id=Id(Kind("memory.test.obs"), "o2"), subject=SUBJECT, value="findme second", at=AT, source="s", context=CTX)
        store.persist(deprioritized_obs)
        store.persist(active_obs)
        store.persist(RetentionMark(item=Ref(id=deprioritized_obs.id), accessibility=DEPRIORITIZED, at=AT))
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert [c.item.id for c in candidates] == [active_obs.id, deprioritized_obs.id]

    def test_custom_accessibility_kind_raises_on_default_retrieval(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs = Observation(id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=Kind("memory.retention.legal_hold"), at=AT))
        with pytest.raises(ValueError):
            store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'lexical_content'`

- [x] **Step 3: Write minimal implementation**

Add these imports to the top-of-file import block in `src/memory/store.py` (consolidated, not mid-file): `from dataclasses import dataclass` (if not already present via the `import dataclasses` module-level import — use `dataclasses.dataclass` as the decorator instead to avoid a duplicate import style, i.e. `@dataclasses.dataclass(...)`), and `from memory.recall import IDENTITY_MATCH, LEXICAL_MATCH, RecallCandidate`, `from memory.retention import ACTIVE, ARCHIVED, DEPRIORITIZED, RetentionLog`.

Append to `src/memory/store.py`:

```python
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
```

Then add `retrieve()` to `InMemoryStore`:

```python
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
```

Note the raised `ValueError` for a custom accessibility `Kind` happens even when `include_archived=True` and even for entities that wouldn't otherwise be excluded — this matches the frozen rule that default retrieval never guesses at an unrecognized `Kind`, full stop, rather than only when that specific candidate would otherwise be filtered.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: PASS (all tests)

- [x] **Step 5: Commit**

```bash
git add src/memory/store.py tests/memory/semantics/test_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 2: implement lexical_content(), RetrievalQuery, and retrieve()

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `MemoryStore` protocol and closing gate

**Files:**
- Modify: `src/memory/store.py`
- Modify: `tests/memory/semantics/test_store.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: `MemoryStore` (the `@runtime_checkable` `Protocol`). No new runtime behavior beyond the protocol declaration — this task is about closure and verification.

- [x] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_store.py`:

```python
class TestMemoryStoreProtocol:
    def test_in_memory_store_satisfies_protocol(self) -> None:
        assert isinstance(InMemoryStore(), MemoryStore)

    def test_incomplete_fake_does_not_satisfy_protocol(self) -> None:
        class IncompleteFake:
            def persist(self, record: object) -> None: ...
            def resolve(self, item: object) -> object: ...
            # missing retrieve/claims_for/conflicts_for/retention_for/episode methods

        assert not isinstance(IncompleteFake(), MemoryStore)


class TestNoGenericEnumerationOrDelete:
    def test_store_has_no_delete_or_enumeration_surface(self) -> None:
        forbidden = ("delete", "remove", "purge", "overwrite", "upsert", "all_records", "list_everything", "scan")
        store = InMemoryStore()
        for name in forbidden:
            assert not hasattr(store, name), f"InMemoryStore must not expose {name}()"
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'MemoryStore'`

- [x] **Step 3: Write minimal implementation**

Add `from typing import Protocol, runtime_checkable` to the top-of-file import block in `src/memory/store.py`.

Append to `src/memory/store.py` (after `InMemoryStore`'s class body, or anywhere at module level — placing the protocol after the concrete implementer, matching Core's own `EffectSink`/`MemoryEffectSink` ordering in `core/effect.py`):

```python
@runtime_checkable
class MemoryStore(Protocol):
    """The persistence-boundary capability. InMemoryStore is the reference
    implementer; a future SqliteMemoryStore (Pass 3) must satisfy the same
    protocol and reproduce the same observable behavior.
    """

    def persist(self, record: PersistRecord) -> None: ...
    def resolve(self, item: Id | Ref) -> EntityMemoryRecord | None: ...
    def retrieve(
        self, query: RetrievalQuery, *, retrieved_at: WallInstant
    ) -> tuple[RecallCandidate, ...]: ...
    def claims_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Claim[object], ...]: ...
    def conflicts_for(
        self, subject: Id | Ref, predicate: Kind
    ) -> tuple[Contradiction | Resolution, ...]: ...
    def retention_for(self, item: Id | Ref) -> tuple[RetentionMark, ...]: ...
    def create_episode(
        self, *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant
    ) -> None: ...
    def append_episode(self, episode: Id | Ref, item: Ref) -> None: ...
    def close_episode(self, episode: Id | Ref, at: WallInstant) -> None: ...
```

Add `from core.value import Kind` if not already present in the top-of-file block (it should already be there from Task 1's `_canonical_*` helpers).

- [x] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_store.py -v`
Expected: PASS (all tests)

- [x] **Step 5: Commit**

```bash
git add src/memory/store.py tests/memory/semantics/test_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 2: freeze the MemoryStore protocol

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Pass 2 closing gate

**Files:** none created; this task runs verification across everything Tasks 1-6 produced, per `docs/memory-passes/02-persistence-boundary.md` §63 ("Quality gates").

**Interfaces:**
- Consumes: the full `src/memory/store.py` and its test file from Tasks 1-6.
- Produces: nothing new — this is Pass 2's checkpoint.

- [x] **Step 1: Run the full test suite (Core + all Memory)**

Run: `uv run pytest -v`
Expected: PASS — every existing Core test, every Pass-1 Memory test, and every Task 1-6 Pass-2 test green, none skipped/xfail.

- [x] **Step 2: Run Ruff**

Run: `uv run ruff check src/memory tests/memory`
Expected: no findings. Fix any and re-run before proceeding.

- [x] **Step 3: Run Pyright**

Run: `uv run pyright src/memory tests/memory`
Expected: 0 errors in strict mode. Fix any and re-run before proceeding.

- [x] **Step 4: Manually confirm no forbidden import exists**

Run:
```bash
grep -n "^import sqlite3\|^from sqlite3\|memory\.belief\|memory\.sqlite_store\|core\.state\|core\.trace\|core\.transform\|core\.relation" src/memory/store.py
```
Expected: no output.

- [x] **Step 5: Manually cross-check adversarial matrix coverage**

Cross-check `MEMORY_ADVERSARIAL_MATRIX.md` sections K, L, and the Pass-2-relevant portions of N, O, Q (the parts that don't require SQLite — see the preregistration §46) against `tests/memory/semantics/test_store.py`. Every PA/ID/CL/RR case referenced in the preregistration's §47-54 must be traceable to at least one test (by name or by docstring/comment reference) or a documented, deliberate non-applicability. Record any gap found and close it with an additional test before continuing — do not defer a genuine gap to Pass 3 unless it is explicitly SQLite-specific (matrix items CL-11, M's transaction/reopen/corruption cases, and Q's actual SQLite-vs-reference equivalence check are legitimately Pass 3's job).

- [x] **Step 6: Commit the closing state (only if Steps 1-5 required fixes)**

If every prior task's commit already left the tree green, this step is a no-op. If Steps 1-5 required corrections, stage exactly those corrections:

```bash
git add -A
git commit -m "$(cat <<'EOF'
Memory Pass 2: closing gate corrections

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

Pass 2 is closed once this task's steps pass clean. Pass 3 (SQLite backend) starts a new preregistration.
