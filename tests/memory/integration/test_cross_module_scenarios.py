"""Cross-module integration scenarios: MEMORY_ADVERSARIAL_MATRIX.md
section X (X-01..10), the minimum cross-module adversarial scenarios --
each crosses several semantic boundaries (belief/recall/retention/store),
so these live in their own integration-shaped test module rather than
being wedged into any one module's own semantics file.

Only X-03 and X-10 are new here -- the other eight of the ten X cases
already have real test coverage elsewhere (belief/store/sqlite_store's own
semantics test files); see docs/MEMORY_V0_AUDIT.md for the full citation
list, one row per case.

See docs/memory-passes/04-architectural-closure.md, "4. Manual v0 audit",
the "Cross-module integration scenarios" subsection.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.context import Context
from core.epistemic import Claim
from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind, Known
from memory.belief import belief_state
from memory.recall import admit
from memory.retention import ACTIVE
from memory.store import InMemoryStore, RetentionMark, RetrievalQuery

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
CTX = Context(as_of=AT)
SUBJECT = Id(Kind("t.subject"), "s1")
AGENT = Id(Kind("t.agent"), "a1")
PREDICATE = Kind("t.predicate")


class TestX03RetrievalIsNotBelief:
    def test_false_lexically_perfect_match_ranks_first_but_belief_state_unaffected(
        self,
    ) -> None:
        store = InMemoryStore()
        false_claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "false1"), subject=SUBJECT, predicate=PREDICATE,
            value=Known("findme wrong"), context=CTX, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        true_claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "true1"), subject=SUBJECT, predicate=PREDICATE,
            value=Known("correct"), context=CTX, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        store.persist(false_claim)
        store.persist(true_claim)

        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert len(candidates) >= 1
        assert candidates[0].item == Ref(id=false_claim.id)

        # attention ≠ truth: WorkingSet(capacity=1) may therefore contain
        # only the false claim.
        working_set, _excluded = admit(candidates, capacity=1)
        assert working_set.admitted == (candidates[0],)

        # This must NOT alter persisted belief/conflict state -- belief_state
        # is computed from claims_for(), never from what retrieval/attention
        # happened to surface.
        claims_for_subject = store.claims_for(SUBJECT, PREDICATE)
        projection = belief_state(
            subject=SUBJECT, predicate=PREDICATE, query_context=CTX,
            claims=claims_for_subject, conflict_entries=(),
        )
        assert len(projection.candidates) == 2


class TestX10ForgettingVsFailureToRecall:
    def test_active_retention_survives_a_failed_lexical_query(self) -> None:
        store = InMemoryStore()
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=PREDICATE,
            value=Known("stored value"), context=CTX, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        store.persist(claim)
        store.persist(RetentionMark(item=Ref(id=claim.id), accessibility=ACTIVE, at=AT))

        no_match = store.retrieve(
            RetrievalQuery(context=CTX, text="nonexistent-query-text"), retrieved_at=AT
        )
        assert no_match == ()

        # A failed retrieval says only "this query didn't surface a match"
        # -- it must never be conflated with forgotten/archived/nonexistent.
        retention = store.retention_for(claim.id)
        assert len(retention) == 1
        assert retention[0].accessibility == ACTIVE
