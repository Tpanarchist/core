"""Propositions for memory.retention.

Matrix reference: MEMORY_ADVERSARIAL_MATRIX.md section D (Retention).
"""

from __future__ import annotations

from datetime import UTC, datetime

from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind
from memory.retention import ACTIVE, ARCHIVED, DEPRIORITIZED, RetentionLog, RetentionMark

ITEM_KIND = Kind("memory.test.item")
T1 = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
T2 = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
T3 = WallInstant(datetime(2024, 1, 3, tzinfo=UTC))


def mark(
    item: Ref, accessibility: Kind, at: WallInstant, rationale: str | None = None
) -> RetentionMark:
    return RetentionMark(item=item, accessibility=accessibility, at=at, rationale=rationale)


class TestDefaultAndBasicTransitions:
    def test_rt_01_no_marks_defaults_to_active(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        assert log.current(item) == ACTIVE

    def test_rt_02_active_to_deprioritized(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, DEPRIORITIZED, T1))
        assert log.current(item) == DEPRIORITIZED

    def test_rt_03_archived_then_active_all_marks_preserved(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ARCHIVED, T1))
        log.record(mark(item, ACTIVE, T2))
        assert log.current(item) == ACTIVE
        assert log.history(item) == (
            mark(item, ARCHIVED, T1),
            mark(item, ACTIVE, T2),
        )

    def test_rt_04_append_order_wins_over_earlier_at_timestamp(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ACTIVE, T3))
        log.record(mark(item, ARCHIVED, T1))  # appended later, earlier `at`
        assert log.current(item) == ARCHIVED

    def test_rt_07_duplicate_marks_both_preserved(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ARCHIVED, T1))
        log.record(mark(item, ARCHIVED, T1))
        assert len(log.history(item)) == 2

    def test_rt_08_rt_09_custom_kind_preserved_never_guessed(self) -> None:
        custom = Kind("memory.retention.legal_hold")
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, custom, T1))
        assert log.current(item) == custom
        assert log.current(item) not in (ACTIVE, DEPRIORITIZED, ARCHIVED)

    def test_rt_11_rationale_may_be_absent(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        recorded = log.record(mark(item, ACTIVE, T1, rationale=None))
        assert recorded.rationale is None


class TestIdentityAcrossNamespaces:
    def test_rt_05_mark_through_namespaced_ref_read_by_bare_id(self) -> None:
        target_id = Id(ITEM_KIND, "x")
        namespaced = Ref(id=target_id, namespace=Namespace(("finance",)))
        log = RetentionLog()
        log.record(mark(namespaced, ARCHIVED, T1))
        assert log.current(target_id) == ARCHIVED

    def test_rt_06_mark_through_one_namespace_read_through_another(self) -> None:
        target_id = Id(ITEM_KIND, "x")
        marked_via = Ref(id=target_id, namespace=Namespace(("finance",)))
        queried_via = Ref(id=target_id, namespace=Namespace(("ledger",)))
        log = RetentionLog()
        log.record(mark(marked_via, ARCHIVED, T1))
        assert log.current(queried_via) == ARCHIVED


class TestHistorySnapshot:
    def test_rt_12_earlier_snapshot_unaffected_by_later_marks(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ACTIVE, T1))
        snapshot = log.history(item)
        log.record(mark(item, ARCHIVED, T2))
        assert snapshot == (mark(item, ACTIVE, T1),)
        assert len(log.history(item)) == 2


class TestImportSideEffects:
    def test_retention_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.retention",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
