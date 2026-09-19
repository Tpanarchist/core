"""Propositions for memory.recall.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections B (RecallCandidate)
and C (WorkingSet).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind
from memory.recall import (
    CONTEXTUAL_MATCH,
    IDENTITY_MATCH,
    LEXICAL_MATCH,
    RecallCandidate,
    WorkingSet,
    admit,
)

ITEM_KIND = Kind("memory.test.item")
CTX = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))
AT = WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))


def candidate(value: str, relevance: tuple[Kind, ...] = (IDENTITY_MATCH,)) -> RecallCandidate:
    return RecallCandidate(
        item=Ref(id=Id(ITEM_KIND, value)),
        query_context=CTX,
        relevance=relevance,
        retrieved_at=AT,
    )


class TestRecallCandidate:
    def test_rc_01_identity_match_kind_distinct_from_lexical(self) -> None:
        c = candidate("a", relevance=(IDENTITY_MATCH,))
        assert IDENTITY_MATCH in c.relevance
        assert LEXICAL_MATCH not in c.relevance

    def test_rc_03_multiple_evidence_kinds_may_coexist(self) -> None:
        c = candidate("a", relevance=(IDENTITY_MATCH, LEXICAL_MATCH))
        assert c.relevance == (IDENTITY_MATCH, LEXICAL_MATCH)

    def test_rc_09_empty_relevance_rejected(self) -> None:
        with pytest.raises(ValueError):
            candidate("a", relevance=())

    def test_rc_10_duplicate_relevance_kinds_rejected(self) -> None:
        with pytest.raises(ValueError):
            candidate("a", relevance=(IDENTITY_MATCH, IDENTITY_MATCH))

    def test_relevance_order_preserved(self) -> None:
        c = candidate("a", relevance=(LEXICAL_MATCH, IDENTITY_MATCH, CONTEXTUAL_MATCH))
        assert c.relevance == (LEXICAL_MATCH, IDENTITY_MATCH, CONTEXTUAL_MATCH)

    def test_relevance_is_defensively_tuple_normalized(self) -> None:
        c = RecallCandidate(
            item=Ref(id=Id(ITEM_KIND, "a")),
            query_context=CTX,
            relevance=[IDENTITY_MATCH],  # type: ignore[arg-type]
            retrieved_at=AT,
        )
        assert c.relevance == (IDENTITY_MATCH,)


class TestWorkingSetAdmission:
    def test_ws_01_capacity_smaller_than_candidates_splits_exactly(self) -> None:
        candidates = (candidate("a"), candidate("b"), candidate("c"))
        working_set, excluded = admit(candidates, capacity=2)
        assert working_set.admitted == candidates[:2]
        assert excluded == candidates[2:]

    def test_ws_02_capacity_equals_candidate_count(self) -> None:
        candidates = (candidate("a"), candidate("b"))
        working_set, excluded = admit(candidates, capacity=2)
        assert working_set.admitted == candidates
        assert excluded == ()

    def test_ws_03_capacity_exceeds_candidate_count(self) -> None:
        candidates = (candidate("a"),)
        working_set, excluded = admit(candidates, capacity=5)
        assert working_set.admitted == candidates
        assert excluded == ()

    def test_ws_04_capacity_zero_is_a_valid_empty_working_set(self) -> None:
        candidates = (candidate("a"),)
        working_set, excluded = admit(candidates, capacity=0)
        assert working_set.admitted == ()
        assert excluded == candidates

    def test_ws_05_negative_capacity_rejected(self) -> None:
        with pytest.raises(ValueError):
            admit((candidate("a"),), capacity=-1)

    def test_ws_06_admit_preserves_caller_order_regardless_of_relevance(self) -> None:
        poorly_ordered = (
            candidate("lexical-first", relevance=(LEXICAL_MATCH,)),
            candidate("identity-second", relevance=(IDENTITY_MATCH,)),
        )
        working_set, _ = admit(poorly_ordered, capacity=2)
        assert working_set.admitted == poorly_ordered

    def test_ws_07_duplicate_items_in_input_are_not_deduplicated(self) -> None:
        same = candidate("a")
        working_set, _ = admit((same, same), capacity=2)
        assert working_set.admitted == (same, same)

    def test_ws_09_mutating_input_after_admission_does_not_affect_working_set(self) -> None:
        candidates = [candidate("a"), candidate("b")]
        working_set, _ = admit(tuple(candidates), capacity=2)
        candidates.clear()
        assert working_set.admitted == (candidate("a"), candidate("b"))


class TestWorkingSetConstruction:
    def test_ws_capacity_must_not_be_negative(self) -> None:
        with pytest.raises(ValueError):
            WorkingSet(capacity=-1, admitted=())

    def test_ws_admitted_must_not_exceed_capacity(self) -> None:
        with pytest.raises(ValueError):
            WorkingSet(capacity=1, admitted=(candidate("a"), candidate("b")))


class TestImportSideEffects:
    def test_recall_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.recall",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
