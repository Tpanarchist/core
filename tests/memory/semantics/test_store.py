"""Propositions for memory.store.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections K (admissibility),
L (identity collisions).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.epistemic import Claim, Contradiction, Inference
from core.error import Error
from core.event import Event
from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind, Known, Unknown
from memory.store import (
    IdentityCollision,
    InMemoryStore,
    UnsupportedMemoryRecord,
    _canonical_episode_header,  # pyright: ignore[reportPrivateUsage]
    _canonical_record,  # pyright: ignore[reportPrivateUsage]
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
        unknown: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Unknown(), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        assert _canonical_record(known) != _canonical_record(unknown)

    def test_id_vs_ref_vs_namespaced_ref_subject_are_canonically_distinct(self) -> None:
        bare = make_claim("c1", subject=SUBJECT)
        ref_no_ns = make_claim("c1", subject=Ref(id=SUBJECT))
        ref_with_ns = make_claim(
            "c1", subject=Ref(id=SUBJECT, namespace=Namespace(("finance",)))
        )
        forms = {
            _canonical_record(bare),
            _canonical_record(ref_no_ns),
            _canonical_record(ref_with_ns),
        }
        assert len(forms) == 3

    def test_metadata_canonical_form_independent_of_dict_iteration_order(self) -> None:
        e_a = Error(
            id=Id(ERROR_KIND, "m1"), kind=ERROR_KIND, message="x", at=AT,
            metadata={"a": 1, "b": 2},
        )
        e_b = Error(
            id=Id(ERROR_KIND, "m1"), kind=ERROR_KIND, message="x", at=AT,
            metadata={"b": 2, "a": 1},
        )
        assert _canonical_record(e_a) == _canonical_record(e_b)


class TestCanonicalRecordRecursion:
    def test_inference_conclusion_participates_in_canonical_form(self) -> None:
        c1 = make_claim("c1", value="first")
        c2 = make_claim("c1", value="second")  # same Id, different content
        inf1 = Inference(
            id=Id(Kind("memory.test.inference"), "i1"), premises=(),
            method=Kind("memory.test.m"), conclusion=c1, at=AT,
        )
        inf2 = Inference(
            id=Id(Kind("memory.test.inference"), "i1"), premises=(),
            method=Kind("memory.test.m"), conclusion=c2, at=AT,
        )
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
            Inference(
                id=Id(Kind("memory.test.inference"), "i1"), premises=(),
                method=Kind("memory.test.m"), conclusion=claim, at=AT,
            ),
            Contradiction(
                id=Id(CONTRA_KIND, "k1"), subject=SUBJECT,
                statements=(Ref(id=claim.id), Ref(id=Id(CLAIM_KIND, "other"))),
                detected_at=AT, context=CTX,
            ),
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


class TestPersistBasicEntities:
    def test_pa_01_supported_observation_persists(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
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
        episode = Episode(
            id=Id(Kind("memory.test.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT
        )
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
        event = Event(
            id=Id(Kind("memory.test.shared"), shared_kind_value),
            kind=EVENT_KIND, at=AT, payload="p",
        )
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
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        assert obs.value == "x"  # unchanged

    def test_caller_mutating_a_mutable_payload_after_persist_does_not_change_resolve(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        mutable_source_payload = {"a": 1}
        obs: Observation[object] = Observation(
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
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value={"a": 1},
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        resolved = store.resolve(obs.id)
        assert resolved is not None
        with pytest.raises(TypeError):
            resolved.value["a"] = 2  # type: ignore[index]


class TestEmbeddedEntities:
    def test_persist_inference_with_new_conclusion_registers_both(self) -> None:
        store = InMemoryStore()
        claim = make_claim("c1")
        inference = Inference(
            id=Id(Kind("memory.test.inference"), "i1"), premises=(),
            method=Kind("memory.test.m"), conclusion=claim, at=AT,
        )
        store.persist(inference)
        assert store.resolve(claim.id) == claim
        assert store.resolve(inference.id) == inference

    def test_persist_inference_where_embedded_conclusion_conflicts_fails_atomically(self) -> None:
        store = InMemoryStore()
        store.persist(make_claim("c1", value="already stored"))
        conflicting_claim = make_claim("c1", value="different")
        inference = Inference(
            id=Id(Kind("memory.test.inference"), "i1"), premises=(),
            method=Kind("memory.test.m"), conclusion=conflicting_claim, at=AT,
        )
        with pytest.raises(IdentityCollision):
            store.persist(inference)
        assert store.resolve(inference.id) is None

    def test_persist_error_with_cause_independently_resolves(self) -> None:
        store = InMemoryStore()
        root = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="root", at=AT)
        wrapping = Error(
            id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrap", at=AT, cause=root
        )
        store.persist(wrapping)
        resolved_root = store.resolve(root.id)
        assert resolved_root is not None
        assert resolved_root.message == "root"  # type: ignore[union-attr]

    def test_persist_error_where_nested_cause_collides_fails_atomically(self) -> None:
        store = InMemoryStore()
        store.persist(
            Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="already stored", at=AT)
        )
        conflicting_root = Error(
            id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="different", at=AT
        )
        wrapping = Error(
            id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrap", at=AT,
            cause=conflicting_root,
        )
        with pytest.raises(IdentityCollision):
            store.persist(wrapping)
        assert store.resolve(wrapping.id) is None

    def test_persist_error_with_foreign_exception_rejected_no_partial_write(self) -> None:
        from memory.codec import UnsupportedPersistedValue

        store = InMemoryStore()
        error = Error(
            id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="m", at=AT,
            exception=ValueError("boom"),
        )
        with pytest.raises(UnsupportedPersistedValue):
            store.persist(error)
        assert store.resolve(error.id) is None
