"""Propositions for core.epistemic.

See SPECIFICATION.md (Observation #7 / Constraint #14 derived constructions)
and docs/passes/04-facts-and-relationships.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.epistemic import Claim, Contradiction, ContradictionLog, Inference, Resolution
from core.identity import Entity, Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind, Known

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
CLAIM_ID_KIND = Kind("core.claim")
INFERENCE_ID_KIND = Kind("core.inference")
CONTRADICTION_ID_KIND = Kind("core.contradiction")
SUBJECT_KIND = Kind("test.subject")
AGENT_KIND = Kind("test.agent")
PREDICATE = Kind("test.is_online")
METHOD = Kind("test.deduction")

CTX = Context(as_of=AT)


def make_claim(suffix: str, evidence: tuple[Ref, ...] = ()) -> Claim[bool]:
    return Claim(
        id=Id(CLAIM_ID_KIND, suffix),
        subject=Id(SUBJECT_KIND, "s1"),
        predicate=PREDICATE,
        value=Known(True),
        context=CTX,
        asserted_by=Id(AGENT_KIND, "agent-1"),
        evidence_refs=evidence,
        at=AT,
    )


def make_inference(conclusion: Claim[bool], premises: tuple[Ref, ...] = ()) -> Inference[bool]:
    return Inference(
        id=Id(INFERENCE_ID_KIND, "i1"),
        premises=premises,
        method=METHOD,
        conclusion=conclusion,
        at=AT,
    )


class TestClaim:
    def test_satisfies_entity(self) -> None:
        assert isinstance(make_claim("c1"), Entity)

    def test_predicate_is_kind(self) -> None:
        assert isinstance(make_claim("c1").predicate, Kind)

    def test_evidence_refs_is_a_tuple(self) -> None:
        assert isinstance(make_claim("c1").evidence_refs, tuple)

    def test_zero_evidence_is_valid(self) -> None:
        assert make_claim("c1", evidence=()).evidence_refs == ()


class TestInference:
    def test_satisfies_entity(self) -> None:
        assert isinstance(make_inference(make_claim("c1")), Entity)

    def test_method_is_kind(self) -> None:
        assert isinstance(make_inference(make_claim("c1")).method, Kind)

    def test_premises_is_a_tuple(self) -> None:
        assert isinstance(make_inference(make_claim("c1")).premises, tuple)

    def test_zero_premises_is_valid(self) -> None:
        assert make_inference(make_claim("c1"), premises=()).premises == ()

    def test_preserves_conclusion_without_editing_its_evidence(self) -> None:
        basis_ref = Ref(Id(CLAIM_ID_KIND, "basis"))
        conclusion = make_claim("c1", evidence=(basis_ref,))
        inference = make_inference(conclusion, premises=(Ref(conclusion.id),))
        assert inference.conclusion is conclusion
        assert inference.conclusion.evidence_refs == (basis_ref,)


class TestContradiction:
    def test_satisfies_entity(self) -> None:
        contradiction = Contradiction(
            id=Id(CONTRADICTION_ID_KIND, "k1"),
            subject=Id(SUBJECT_KIND, "s1"),
            statements=(Ref(Id(CLAIM_ID_KIND, "a")), Ref(Id(CLAIM_ID_KIND, "b"))),
            detected_at=AT,
            context=CTX,
        )
        assert isinstance(contradiction, Entity)

    def test_rejects_fewer_than_two_distinct_statement_identities(self) -> None:
        with pytest.raises(ValueError, match="at least two distinct"):
            Contradiction(
                id=Id(CONTRADICTION_ID_KIND, "k1"),
                subject=Id(SUBJECT_KIND, "s1"),
                statements=(Ref(Id(CLAIM_ID_KIND, "a")),),
                detected_at=AT,
                context=CTX,
            )

    def test_differently_namespaced_refs_to_same_claim_do_not_count_as_distinct(self) -> None:
        claim_id = Id(CLAIM_ID_KIND, "a")
        with pytest.raises(ValueError, match="at least two distinct"):
            Contradiction(
                id=Id(CONTRADICTION_ID_KIND, "k1"),
                subject=Id(SUBJECT_KIND, "s1"),
                statements=(
                    Ref(claim_id, Namespace(("x",))),
                    Ref(claim_id, Namespace(("y",))),
                ),
                detected_at=AT,
                context=CTX,
            )


class TestResolution:
    def test_rejects_empty_rationale(self) -> None:
        with pytest.raises(ValueError, match="rationale must not be empty"):
            Resolution(
                contradiction=Ref(Id(CONTRADICTION_ID_KIND, "k1")),
                rationale="",
                resolved_by=Id(AGENT_KIND, "agent-1"),
                at=AT,
            )

    def test_is_not_entity_bearing(self) -> None:
        resolution = Resolution(
            contradiction=Ref(Id(CONTRADICTION_ID_KIND, "k1")),
            rationale="clarified",
            resolved_by=Id(AGENT_KIND, "agent-1"),
            at=AT,
        )
        assert not hasattr(resolution, "id")


def make_contradiction(suffix: str, subject: Id | None = None) -> Contradiction:
    return Contradiction(
        id=Id(CONTRADICTION_ID_KIND, suffix),
        subject=subject or Id(SUBJECT_KIND, "s1"),
        statements=(Ref(Id(CLAIM_ID_KIND, "a")), Ref(Id(CLAIM_ID_KIND, "b"))),
        detected_at=AT,
        context=CTX,
    )


class TestContradictionLog:
    def test_rejects_duplicate_contradiction_id(self) -> None:
        log = ContradictionLog()
        log.record(make_contradiction("k1"))
        with pytest.raises(ValueError, match="already recorded"):
            log.record(make_contradiction("k1"))

    def test_rejects_resolution_before_contradiction(self) -> None:
        log = ContradictionLog()
        resolution = Resolution(
            contradiction=Ref(Id(CONTRADICTION_ID_KIND, "k1")),
            rationale="clarified",
            resolved_by=Id(AGENT_KIND, "agent-1"),
            at=AT,
        )
        with pytest.raises(ValueError, match="not recorded earlier"):
            log.record(resolution)

    def test_multiple_later_resolutions_for_one_contradiction_are_preserved(self) -> None:
        log = ContradictionLog()
        contradiction = make_contradiction("k1")
        log.record(contradiction)
        r1 = Resolution(
            contradiction=Ref(contradiction.id),
            rationale="first pass",
            resolved_by=Id(AGENT_KIND, "agent-1"),
            at=AT,
        )
        r2 = Resolution(
            contradiction=Ref(contradiction.id),
            rationale="revised",
            resolved_by=Id(AGENT_KIND, "agent-2"),
            at=AT,
        )
        log.record(r1)
        log.record(r2)
        assert log.entries() == (contradiction, r1, r2)

    def test_entries_returns_a_tuple_snapshot(self) -> None:
        log = ContradictionLog()
        log.record(make_contradiction("k1"))
        snapshot = log.entries()
        log.record(make_contradiction("k2"))
        assert len(snapshot) == 1

    def test_unresolved_is_derived_from_append_history(self) -> None:
        log = ContradictionLog()
        c1 = make_contradiction("k1")
        c2 = make_contradiction("k2")
        log.record(c1)
        log.record(c2)
        assert log.unresolved() == (c1, c2)

    def test_a_later_resolution_removes_it_from_unresolved_without_mutating_it(self) -> None:
        log = ContradictionLog()
        c1 = make_contradiction("k1")
        log.record(c1)
        assert log.unresolved() == (c1,)

        resolution = Resolution(
            contradiction=Ref(c1.id),
            rationale="clarified",
            resolved_by=Id(AGENT_KIND, "agent-1"),
            at=AT,
        )
        log.record(resolution)
        assert log.unresolved() == ()
        assert log.entries()[0] is c1  # the original Contradiction object, untouched

    def test_for_subject_matches_id_and_ref_forms(self) -> None:
        log = ContradictionLog()
        subject_id = Id(SUBJECT_KIND, "s1")
        c1 = make_contradiction("k1", subject=subject_id)
        log.record(c1)
        by_id = log.for_subject(subject_id)
        by_ref = log.for_subject(Ref(subject_id, Namespace(("other",))))
        assert by_id == by_ref == (c1,)

    def test_for_subject_includes_contradictions_and_their_resolutions_in_log_order(self) -> None:
        log = ContradictionLog()
        subject_a = Id(SUBJECT_KIND, "a")
        subject_b = Id(SUBJECT_KIND, "b")
        c_a = make_contradiction("ka", subject=subject_a)
        c_b = make_contradiction("kb", subject=subject_b)
        log.record(c_a)
        log.record(c_b)
        r_a = Resolution(
            contradiction=Ref(c_a.id),
            rationale="resolved a",
            resolved_by=Id(AGENT_KIND, "agent-1"),
            at=AT,
        )
        log.record(r_a)

        assert log.for_subject(subject_a) == (c_a, r_a)


def test_importing_epistemic_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.epistemic", ("uuid.uuid4",))
