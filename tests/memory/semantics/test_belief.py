"""Propositions for memory.belief.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections E (ordinary),
F (Context/time), G (contradiction).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.epistemic import Claim, Contradiction, Resolution
from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind, Known
from memory.belief import (
    AMBIGUOUS,
    DETERMINED,
    RESOLVED_OPAQUE_CONFLICT,
    UNKNOWN,
    UNRESOLVED_CONFLICT,
    BeliefProjection,
    belief_state,
)

CLAIM_KIND = Kind("memory.test.claim")
CONTRA_KIND = Kind("memory.test.contradiction")
SUBJECT_KIND = Kind("memory.test.subject")
AGENT_KIND = Kind("memory.test.agent")
BALANCE = Kind("memory.test.balance")
BIRTH_DATE = Kind("memory.test.birth_date")
OWNER = Kind("memory.test.owner")
LEGAL_CONTROL = Kind("memory.test.legal_control")

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
SUBJECT = Id(SUBJECT_KIND, "checking")
AGENT = Id(AGENT_KIND, "agent-1")


def make_claim(
    claim_id: str,
    predicate: Kind,
    *,
    subject: Id | Ref = SUBJECT,
    context: Context,
    value: object = True,
) -> Claim[object]:
    return Claim(
        id=Id(CLAIM_KIND, claim_id),
        subject=subject,
        predicate=predicate,
        value=Known(value),
        context=context,
        asserted_by=AGENT,
        evidence_refs=(),
        at=AT,
    )


def ctx(as_of: WallInstant) -> Context:
    return Context(as_of=as_of)


MONDAY = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
FRIDAY = WallInstant(datetime(2024, 1, 5, tzinfo=UTC))
SATURDAY = WallInstant(datetime(2024, 1, 6, tzinfo=UTC))


class TestOrdinaryProjection:
    def test_bp_01_no_matching_claim_is_unknown(self) -> None:
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN
        assert result.candidates == ()

    def test_bp_02_exactly_one_compatible_claim_is_determined(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (claim,)

    def test_bp_03_two_compatible_claims_no_contradiction_is_ambiguous(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        result = belief_state(
            subject=SUBJECT,
            predicate=BIRTH_DATE,
            query_context=ctx(MONDAY),
            claims=(c1, c2),
            conflict_entries=(),
        )
        assert result.status == AMBIGUOUS
        assert set(result.candidates) == {c1, c2}

    def test_bp_04_different_predicate_ignored(self) -> None:
        other = make_claim("c1", Kind("memory.test.other"), context=ctx(MONDAY))
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(other,),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_05_different_subject_ignored(self) -> None:
        other = make_claim(
            "c1", BALANCE, subject=Id(SUBJECT_KIND, "savings"), context=ctx(MONDAY)
        )
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(other,),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_06_bp_07_subject_matches_across_id_and_namespaced_ref(self) -> None:
        claim = make_claim("c1", BALANCE, subject=SUBJECT, context=ctx(MONDAY), value=900)
        query_subject = Ref(id=SUBJECT, namespace=Namespace(("finance",)))
        result = belief_state(
            subject=query_subject,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == DETERMINED

    def test_bp_10_unknown_projection_distinct_from_claim_value_unknown(self) -> None:
        from core.value import UNKNOWN as CORE_UNKNOWN

        claim: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "c1"),
            subject=SUBJECT,
            predicate=BALANCE,
            value=CORE_UNKNOWN,
            context=ctx(MONDAY),
            asserted_by=AGENT,
            evidence_refs=(),
            at=AT,
        )
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates[0].value is CORE_UNKNOWN

    def test_bp_11_no_claim_survives_context_filtering_is_unknown(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(FRIDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_13_duplicate_identical_claim_does_not_fabricate_ambiguity(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim, claim),
            conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (claim,)

    def test_bp_12_identical_value_claims_with_distinct_ids_are_ambiguous(self) -> None:
        # Contrast with bp_13 below: dedup keys strictly on Claim.id, never
        # on semantic content. Two Claims with distinct Ids but otherwise
        # identical subject/predicate/context/value are NOT deduped — both
        # survive as independent candidates, and the projection is AMBIGUOUS.
        c1 = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        c2 = make_claim("c2", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(c1, c2),
            conflict_entries=(),
        )
        assert result.status == AMBIGUOUS
        assert set(result.candidates) == {c1, c2}

    def test_bp_13_different_claims_sharing_an_id_raise(self) -> None:
        shared_id = Id(CLAIM_KIND, "c1")
        first: Claim[object] = Claim(
            id=shared_id, subject=SUBJECT, predicate=BALANCE, value=Known(900),
            context=ctx(MONDAY), asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        second: Claim[object] = Claim(
            id=shared_id, subject=SUBJECT, predicate=BALANCE, value=Known(901),
            context=ctx(MONDAY), asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        with pytest.raises(ValueError):
            belief_state(
                subject=SUBJECT,
                predicate=BALANCE,
                query_context=ctx(MONDAY),
                claims=(first, second),
                conflict_entries=(),
            )


class TestContextAndTime:
    def test_x_01_changing_balance_without_contradiction(self) -> None:
        monday_claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        friday_claim = make_claim("c2", BALANCE, context=ctx(FRIDAY), value=1200)
        claims = (monday_claim, friday_claim)

        monday_result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=claims, conflict_entries=(),
        )
        assert monday_result.status == DETERMINED
        assert monday_result.candidates == (monday_claim,)

        friday_result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(FRIDAY),
            claims=claims, conflict_entries=(),
        )
        assert friday_result.status == DETERMINED
        assert friday_result.candidates == (friday_claim,)

        saturday_result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(SATURDAY),
            claims=claims, conflict_entries=(),
        )
        assert saturday_result.status == UNKNOWN

    def test_ct_01_differing_as_of_is_a_context_conflict(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(FRIDAY),
            claims=(claim,), conflict_entries=(),
        )
        assert claim not in result.candidates

    def test_ct_03_optional_context_field_populated_one_side_fills_in(self) -> None:
        # Claim's context has `source` populated; query leaves it None —
        # Context.merge() fills in from the non-None side, so they're compatible.
        claim = make_claim(
            "c1", BALANCE, context=Context(as_of=MONDAY, source="bank-api"), value=900
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(claim,), conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (claim,)

    def test_ct_04_incompatible_non_none_context_field_excludes_claim(self) -> None:
        claim = make_claim(
            "c1", BALANCE, context=Context(as_of=MONDAY, source="bank-api"), value=900
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE,
            query_context=Context(as_of=MONDAY, source="user-entered"),
            claims=(claim,), conflict_entries=(),
        )
        assert result.status == UNKNOWN
        assert claim not in result.candidates

    def test_ct_05_metadata_only_difference_uses_core_merge_not_custom_logic(self) -> None:
        claim = make_claim(
            "c1", BALANCE, context=Context(as_of=MONDAY, metadata={"note": "a"}), value=900
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE,
            query_context=Context(as_of=MONDAY, metadata={"note": "b"}),
            claims=(claim,), conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_08_ct_08_recency_never_breaks_a_tie(self) -> None:
        # Both claims share the exact same Context (same as_of), so neither
        # is excluded by Context.merge() — recency must not be used to pick
        # a winner; the result stays AMBIGUOUS.
        earlier = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        later = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(earlier, later), conflict_entries=(),
        )
        assert result.status == AMBIGUOUS

    def test_bp_09_only_context_compatible_claim_may_be_determined(self) -> None:
        old_compatible = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        newer_incompatible = make_claim("c2", BALANCE, context=ctx(FRIDAY), value=1200)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(old_compatible, newer_incompatible), conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (old_compatible,)

    def test_belief_state_never_inspects_claim_at(self) -> None:
        # Both claims are asserted (`.at`) in the opposite order from their
        # Context.as_of — if belief_state() ever consulted `.at`, this would
        # produce a different (wrong) result than context-only filtering.
        earlier_context_later_assertion: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=BALANCE,
            value=Known(900), context=ctx(MONDAY), asserted_by=AGENT,
            evidence_refs=(), at=WallInstant(datetime(2024, 6, 1, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(earlier_context_later_assertion,), conflict_entries=(),
        )
        assert result.status == DETERMINED


class TestContradiction:
    def _contradiction(self, statements: tuple[Ref, ...], contra_id: str = "k1") -> Contradiction:
        return Contradiction(
            id=Id(CONTRA_KIND, contra_id),
            subject=SUBJECT,
            statements=statements,
            detected_at=AT,
            context=ctx(MONDAY),
        )

    def test_cf_01_relevant_unresolved_contradiction(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert contradiction in result.conflict_entries
        assert result.candidates == (c1, c2)

    def test_cf_relevance_survives_context_filtering(self) -> None:
        # The single most subtle property in this module: relevant-Contradiction
        # membership is computed from the FULL (subject, predicate) slot, gathered
        # BEFORE context filtering — not from `candidates` (post-filter). A claim
        # that is in the slot but context-incompatible with the query must still
        # establish relevance, even though it does not appear in `candidates`.
        filtered_out = make_claim("c1", BIRTH_DATE, context=ctx(FRIDAY), value="1990-04-12")
        contradiction = self._contradiction(
            (Ref(id=filtered_out.id), Ref(id=Id(CLAIM_KIND, "other")))
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(filtered_out,), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert result.candidates == ()

    def test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        resolution = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale='{"accepted_claim": "c1"}',  # machine-looking text — still opaque
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction, resolution),
        )
        assert result.status == RESOLVED_OPAQUE_CONFLICT
        assert result.conflict_entries == (contradiction, resolution)

    def test_cf_07_irrelevant_contradiction_does_not_affect_slot(self) -> None:
        c1 = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        other_subject_claim = make_claim(
            "c2", BALANCE, subject=Id(SUBJECT_KIND, "savings"), context=ctx(MONDAY), value=50
        )
        contradiction = Contradiction(
            id=Id(CONTRA_KIND, "k1"),
            subject=Id(SUBJECT_KIND, "savings"),
            statements=(Ref(id=c1.id), Ref(id=other_subject_claim.id)),
            detected_at=AT,
            context=ctx(MONDAY),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(c1, other_subject_claim), conflict_entries=(contradiction,),
        )
        assert result.status == DETERMINED

    def test_x_08_cross_predicate_conflict_still_relevant(self) -> None:
        owner_claim = make_claim("c1", OWNER, context=ctx(MONDAY), value="alice")
        legal_control_claim = make_claim("c2", LEGAL_CONTROL, context=ctx(MONDAY), value="bob")
        contradiction = self._contradiction(
            (Ref(id=owner_claim.id), Ref(id=legal_control_claim.id))
        )
        result = belief_state(
            subject=SUBJECT, predicate=OWNER, query_context=ctx(MONDAY),
            claims=(owner_claim, legal_control_claim), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT

    def test_cf_10_single_candidate_with_relevant_conflict_is_not_determined(self) -> None:
        c1 = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        # The other conflicting statement isn't itself in the supplied
        # claims (CL-05/CL-06): relevance still holds via c1.
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "missing"))))
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(c1,), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert result.candidates == (c1,)

    def test_cf_11_unreferenced_contradiction_is_not_relevant(self) -> None:
        # Absence-of-false-positive test, not a positive "conflict with zero
        # candidates" case (that positive case is covered by
        # test_cf_relevance_survives_context_filtering above). Neither
        # statement here resolves to a supplied claim, so this contradiction
        # is *not* relevant by the frozen rule and must not surface —
        # paired with cf_10 above, which is the true-positive case.
        contradiction = self._contradiction(
            (Ref(id=Id(CLAIM_KIND, "gone-1")), Ref(id=Id(CLAIM_KIND, "gone-2")))
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(), conflict_entries=(contradiction,),
        )
        assert result.status == UNKNOWN

    def test_x_09_resolved_and_unresolved_conflicts_coexist(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        c3 = make_claim("c3", BIRTH_DATE, context=ctx(MONDAY), value="1992-04-12")
        resolved = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)), contra_id="k1")
        unresolved = self._contradiction((Ref(id=c1.id), Ref(id=c3.id)), contra_id="k2")
        resolution = Resolution(
            contradiction=Ref(id=resolved.id),
            rationale="addressed",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2, c3), conflict_entries=(resolved, resolution, unresolved),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert resolved in result.conflict_entries
        assert resolution in result.conflict_entries
        assert unresolved in result.conflict_entries

    def test_cf_09_statement_ref_matches_via_identity_not_bare_equality(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        namespaced_statement = Ref(id=c1.id, namespace=Namespace(("some", "ns")))
        contradiction = self._contradiction((namespaced_statement, Ref(id=c2.id)))
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT

    def test_cf_12_multiple_resolutions_for_same_contradiction_all_preserved(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        resolution1 = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale="first pass",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        resolution2 = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale="revisited",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 3, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction, resolution1, resolution2),
        )
        assert result.status == RESOLVED_OPAQUE_CONFLICT
        assert result.conflict_entries == (contradiction, resolution1, resolution2)

    def test_cf_06_multiple_relevant_contradictions_all_resolved(self) -> None:
        # Distinct from cf_12 (one Contradiction, two Resolutions) and x_09
        # (one resolved Contradiction plus one unresolved): here there are
        # TWO separate Contradictions for the same (subject, predicate) slot,
        # each with its own Resolution — none unresolved, so the result is
        # RESOLVED_OPAQUE_CONFLICT, not UNRESOLVED_CONFLICT.
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        c3 = make_claim("c3", BIRTH_DATE, context=ctx(MONDAY), value="1992-04-12")
        contradiction_a = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)), contra_id="k1")
        contradiction_b = self._contradiction((Ref(id=c1.id), Ref(id=c3.id)), contra_id="k2")
        resolution_a = Resolution(
            contradiction=Ref(id=contradiction_a.id),
            rationale="first contradiction addressed",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        resolution_b = Resolution(
            contradiction=Ref(id=contradiction_b.id),
            rationale="second contradiction addressed",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 3, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2, c3),
            conflict_entries=(contradiction_a, resolution_a, contradiction_b, resolution_b),
        )
        assert result.status == RESOLVED_OPAQUE_CONFLICT
        assert contradiction_a in result.conflict_entries
        assert resolution_a in result.conflict_entries
        assert contradiction_b in result.conflict_entries
        assert resolution_b in result.conflict_entries

    def test_cf_14_differing_authority_values_never_compared_to_each_other(self) -> None:
        # Each claim's Context.authority is only ever merged against the query's
        # (which leaves it None), never against the other claim's — so two claims
        # with different "authority-looking" values both remain independently
        # compatible, and neither is preferred. Proves Memory doesn't judge authority.
        query_context = ctx(MONDAY)
        verified: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "cv"), subject=SUBJECT, predicate=BIRTH_DATE,
            value=Known("1990-04-12"), context=Context(as_of=MONDAY, authority="verified"),
            asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        unverified: Claim[object] = Claim(
            id=Id(CLAIM_KIND, "cu"), subject=SUBJECT, predicate=BIRTH_DATE,
            value=Known("1991-04-12"), context=Context(as_of=MONDAY, authority="unverified"),
            asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=query_context,
            claims=(verified, unverified), conflict_entries=(),
        )
        assert result.status == AMBIGUOUS
        assert set(result.candidates) == {verified, unverified}


class TestBeliefProjectionConstructorInvariants:
    def test_determined_requires_exactly_one_candidate_and_no_conflicts(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=DETERMINED, candidates=(claim, claim), conflict_entries=(),
            )

    def test_unknown_requires_zero_candidates(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=UNKNOWN, candidates=(claim,), conflict_entries=(),
            )

    def test_unresolved_conflict_requires_at_least_one_conflict_entry(self) -> None:
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=UNRESOLVED_CONFLICT, candidates=(), conflict_entries=(),
            )

    def test_unknown_status_kind_rejected(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=Kind("memory.belief.not_a_real_status"),
                candidates=(claim,), conflict_entries=(),
            )


class TestQueryContextRetained:
    def test_belief_projection_preserves_query_context(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(claim,), conflict_entries=(),
        )
        assert result.query_context == ctx(MONDAY)


class TestImportSideEffects:
    def test_belief_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.belief",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
