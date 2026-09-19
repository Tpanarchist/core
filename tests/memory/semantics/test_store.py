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
from memory.episode import Episode
from memory.recall import IDENTITY_MATCH, LEXICAL_MATCH
from memory.retention import RetentionMark
from memory.store import (
    IdentityCollision,
    InMemoryStore,
    RetrievalQuery,
    UnsupportedMemoryRecord,
    _canonical_episode_header,  # pyright: ignore[reportPrivateUsage]
    _canonical_record,  # pyright: ignore[reportPrivateUsage]
    lexical_content,
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


class TestContextSnapshotIsolation:
    def test_context_scope_mutation_after_persist_does_not_leak(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        mutable_scope: dict[str, object] = {"k": 1}
        ctx = Context(as_of=AT, scope=mutable_scope)
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x",
            at=AT, source="s", context=ctx,
        )
        store.persist(obs)
        mutable_scope["k"] = 999
        resolved = store.resolve(obs.id)
        assert resolved is not None
        assert dict(resolved.context.scope) == {"k": 1}  # type: ignore[union-attr,arg-type]

    def test_context_scope_snapshot_is_itself_immutable(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        ctx = Context(as_of=AT, scope={"k": 1})
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x",
            at=AT, source="s", context=ctx,
        )
        store.persist(obs)
        resolved = store.resolve(obs.id)
        assert resolved is not None
        with pytest.raises(TypeError):
            resolved.context.scope["k"] = 2  # type: ignore[index,union-attr]

    def test_claim_context_snapshot_isolation(self) -> None:
        store = InMemoryStore()
        mutable_units: dict[str, object] = {"unit": "meters"}
        ctx = Context(as_of=AT, units=mutable_units)
        claim: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Known("v"), context=ctx, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(claim)
        mutable_units["unit"] = "changed"
        resolved = store.resolve(claim.id)
        assert resolved is not None
        assert dict(resolved.context.units) == {"unit": "meters"}  # type: ignore[union-attr,arg-type]

    def test_contradiction_context_snapshot_isolation(self) -> None:
        store = InMemoryStore()
        mutable_authority: dict[str, object] = {"who": "alice"}
        ctx = Context(as_of=AT, authority=mutable_authority)
        contradiction = Contradiction(
            id=Id(Kind("memory.test.contradiction"), "k1"), subject=SUBJECT,
            statements=(Ref(id=Id(CLAIM_KIND, "a")), Ref(id=Id(CLAIM_KIND, "b"))),
            detected_at=AT, context=ctx,
        )
        store.persist(contradiction)
        mutable_authority["who"] = "changed"
        resolved = store.resolve(contradiction.id)
        assert resolved is not None
        assert dict(resolved.context.authority) == {"who": "alice"}  # type: ignore[union-attr,arg-type]

    def test_error_context_snapshot_isolation(self) -> None:
        store = InMemoryStore()
        mutable_env: dict[str, object] = {"host": "a"}
        ctx = Context(as_of=AT, environment=mutable_env)
        error = Error(id=Id(ERROR_KIND, "e1"), kind=ERROR_KIND, message="m", at=AT, context=ctx)
        store.persist(error)
        mutable_env["host"] = "changed"
        resolved = store.resolve(error.id)
        assert resolved is not None
        assert dict(resolved.context.environment) == {"host": "a"}  # type: ignore[union-attr,arg-type]


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
        result = store.claims_for(
            Ref(id=SUBJECT, namespace=Namespace(("finance",))), Kind("memory.test.p")
        )
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
        claim: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=Kind("memory.test.p"),
            value=Known("x"), context=incompatible_context, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        store.persist(claim)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (claim,)

    def test_persistence_order_preserved(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        c2: Claim[object] = Claim(
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
        inference = Inference(
            id=Id(Kind("memory.test.inference"), "i1"), premises=(),
            method=Kind("memory.test.m"), conclusion=claim, at=AT,
        )
        store.persist(inference)
        assert store.claims_for(SUBJECT, Kind("memory.test.p")) == (claim,)


class TestConflictsFor:
    def _contradiction(
        self, statements: tuple[Ref, ...], contra_id: str = "k1", subject: Id | Ref = SUBJECT
    ) -> Contradiction:
        return Contradiction(
            id=Id(CONTRA_KIND, contra_id), subject=subject, statements=statements,
            detected_at=AT, context=CTX,
        )

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
        contradiction = self._contradiction(
            (Ref(id=Id(CLAIM_KIND, "gone1")), Ref(id=Id(CLAIM_KIND, "gone2")))
        )
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
        resolution = Resolution(
            contradiction=Ref(id=contradiction.id), rationale="r", resolved_by=AGENT, at=AT
        )
        store.persist(resolution)
        result = store.conflicts_for(SUBJECT, Kind("memory.test.p"))
        assert result == (contradiction, resolution)

    def test_cl_09_multiple_resolutions_preserve_append_order(self) -> None:
        store = InMemoryStore()
        c1 = make_claim("c1")
        store.persist(c1)
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "other"))))
        store.persist(contradiction)
        r1 = Resolution(
            contradiction=Ref(id=contradiction.id), rationale="first", resolved_by=AGENT, at=AT
        )
        r2 = Resolution(
            contradiction=Ref(id=contradiction.id), rationale="second", resolved_by=AGENT, at=AT
        )
        store.persist(r1)
        store.persist(r2)
        assert store.conflicts_for(SUBJECT, Kind("memory.test.p")) == (contradiction, r1, r2)

    def test_resolution_before_contradiction_raises(self) -> None:
        store = InMemoryStore()
        orphan_resolution = Resolution(
            contradiction=Ref(id=Id(CONTRA_KIND, "never-persisted")), rationale="r",
            resolved_by=AGENT, at=AT,
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
        mark = RetentionMark(
            item=Ref(id=target, namespace=Namespace(("finance",))), accessibility=ARCHIVED, at=AT
        )
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
        # must not raise
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)

    def test_different_header_same_id_collides(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        with pytest.raises(IdentityCollision):
            store.create_episode(
                id=episode_id, subject=Id(SUBJECT_KIND, "different"), context=CTX, opened_at=AT
            )

    def test_episode_id_collides_globally_with_another_entity_type(self) -> None:
        store = InMemoryStore()
        shared_id = Id(Kind("memory.test.episode"), "ep1")
        store.persist(Event(id=shared_id, kind=EVENT_KIND, at=AT, payload="p"))
        with pytest.raises(IdentityCollision):
            store.create_episode(id=shared_id, subject=SUBJECT, context=CTX, opened_at=AT)

    def test_persist_colliding_with_existing_episode_raises_identity_collision(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        with pytest.raises(IdentityCollision):
            store.persist(Event(id=episode_id, kind=EVENT_KIND, at=AT, payload="p"))

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
            store.append_episode(
                Id(Kind("memory.test.episode"), "missing"),
                Ref(id=Id(Kind("memory.test.item"), "x")),
            )

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

    def test_episode_context_mutation_after_create_does_not_leak(self) -> None:
        store = InMemoryStore()
        episode_id = Id(Kind("memory.test.episode"), "ep1")
        mutable_scope: dict[str, object] = {"k": 1}
        ctx = Context(as_of=AT, scope=mutable_scope)
        store.create_episode(id=episode_id, subject=SUBJECT, context=ctx, opened_at=AT)
        mutable_scope["k"] = 999
        resolved = store.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert dict(resolved.context.scope) == {"k": 1}  # type: ignore[arg-type]


class TestLexicalContent:
    def test_observation_bare_string_value_searchable(self) -> None:
        from core.observation import Observation

        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="hello world",
            at=AT, source="s", context=CTX,
        )
        assert "hello world" in lexical_content(obs)

    def test_observation_int_value_not_searchable(self) -> None:
        # source/observer are also checked per MEMORY_ARCHITECTURE.md's frozen
        # table, so they must be non-str here too, or this would not actually
        # be testing that a non-str value is excluded.
        from core.observation import Observation

        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value=42,
            at=AT, source=7, context=CTX,
        )
        assert lexical_content(obs) == ()

    def test_claim_known_str_searchable_unknown_not(self) -> None:
        known = make_claim("c1", value="findme")
        assert "findme" in lexical_content(known)
        unknown: Claim[object] = Claim(
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

        effect = Effect(
            id=Id(Kind("memory.test.effect"), "f1"), kind=Kind("memory.test.k"),
            description="did a thing", target="str target", at=AT,
        )
        content = lexical_content(effect)
        assert "did a thing" in content
        assert "str target" in content

    def test_error_message_and_operation_searchable(self) -> None:
        error = Error(
            id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="failed hard",
            at=AT, operation="do-thing",
        )
        content = lexical_content(error)
        assert "failed hard" in content
        assert "do-thing" in content

    def test_provenance_transform_name_and_version_searchable(self) -> None:
        from core.provenance import Provenance
        from core.time import Duration

        prov = Provenance(
            id=Id(Kind("memory.test.prov"), "p1"), transform_id=Id(Kind("memory.test.tx"), "t1"),
            transform_name="normalize", transform_version="1.0", inputs=(), parents=(), at=AT,
            duration=Duration(0),
        )
        content = lexical_content(prov)
        assert "normalize" in content
        assert "1.0" in content

    def test_inference_and_contradiction_and_episode_contribute_nothing(self) -> None:
        claim = make_claim("c1", value="text")
        inference = Inference(
            id=Id(Kind("memory.test.inference"), "i1"), premises=(),
            method=Kind("memory.test.m"), conclusion=claim, at=AT,
        )
        assert lexical_content(inference) == ()
        contradiction = Contradiction(
            id=Id(CONTRA_KIND, "k1"), subject=SUBJECT,
            statements=(Ref(id=claim.id), Ref(id=Id(CLAIM_KIND, "x"))),
            detected_at=AT, context=CTX,
        )
        assert lexical_content(contradiction) == ()
        episode = Episode(
            id=Id(Kind("memory.test.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT
        )
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
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, identity=obs.id), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].item.id == obs.id
        assert candidates[0].relevance == (IDENTITY_MATCH,)

    def test_text_only_retrieval(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findable text",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findable"), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].relevance == (LEXICAL_MATCH,)

    def test_combined_identity_and_lexical_match_produces_one_candidate_both_kinds(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findable text",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        candidates = store.retrieve(
            RetrievalQuery(context=CTX, identity=obs.id, text="findable"), retrieved_at=AT
        )
        assert len(candidates) == 1
        assert candidates[0].relevance == (IDENTITY_MATCH, LEXICAL_MATCH)

    def test_retrieved_at_and_query_context_copied_exactly(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="x",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        query_ctx = Context(as_of=WallInstant(datetime(2024, 3, 1, tzinfo=UTC)))
        retrieved_at = WallInstant(datetime(2024, 3, 2, tzinfo=UTC))
        candidates = store.retrieve(
            RetrievalQuery(context=query_ctx, identity=obs.id), retrieved_at=retrieved_at
        )
        assert candidates[0].query_context == query_ctx
        assert candidates[0].retrieved_at == retrieved_at

    def test_case_sensitive_substring_not_fts_operators(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="Hello World",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        # case-sensitive
        assert store.retrieve(RetrievalQuery(context=CTX, text="hello"), retrieved_at=AT) == ()
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="Hello"), retrieved_at=AT)) == 1
        # FTS-looking characters are ordinary literal characters, not operators.
        store2 = InMemoryStore()
        literal_obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o2"), subject=SUBJECT, value='a "quoted*" (thing)',
            at=AT, source="s", context=CTX,
        )
        store2.persist(literal_obs)
        assert len(
            store2.retrieve(RetrievalQuery(context=CTX, text='"quoted*"'), retrieved_at=AT)
        ) == 1

    def test_no_result_limit(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        for i in range(50):
            obs: Observation[object] = Observation(
                id=Id(Kind("memory.test.obs"), f"o{i}"), subject=SUBJECT, value="shared text",
                at=AT, source="s", context=CTX,
            )
            store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="shared"), retrieved_at=AT)
        assert len(candidates) == 50


class TestRetrievalRetention:
    def test_rr_01_active_eligible_by_default(self) -> None:
        from core.observation import Observation
        from memory.retention import ACTIVE

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ACTIVE, at=AT))
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)) == 1

    def test_rr_02_rr_03_archived_excluded_by_default_included_when_requested(self) -> None:
        from core.observation import Observation
        from memory.retention import ARCHIVED

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        assert store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT) == ()
        assert len(
            store.retrieve(
                RetrievalQuery(context=CTX, text="findme", include_archived=True), retrieved_at=AT
            )
        ) == 1

    def test_rr_07_no_mark_treated_as_active(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        assert len(store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)) == 1

    def test_deprioritized_ordered_after_active(self) -> None:
        from core.observation import Observation
        from memory.retention import DEPRIORITIZED

        store = InMemoryStore()
        deprioritized_obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme first",
            at=AT, source="s", context=CTX,
        )
        active_obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o2"), subject=SUBJECT, value="findme second",
            at=AT, source="s", context=CTX,
        )
        store.persist(deprioritized_obs)
        store.persist(active_obs)
        store.persist(
            RetentionMark(item=Ref(id=deprioritized_obs.id), accessibility=DEPRIORITIZED, at=AT)
        )
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert [c.item.id for c in candidates] == [active_obs.id, deprioritized_obs.id]

    def test_custom_accessibility_kind_raises_on_default_retrieval(self) -> None:
        from core.observation import Observation

        store = InMemoryStore()
        obs: Observation[object] = Observation(
            id=Id(Kind("memory.test.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.persist(
            RetentionMark(
                item=Ref(id=obs.id), accessibility=Kind("memory.retention.legal_hold"), at=AT
            )
        )
        with pytest.raises(ValueError):
            store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
