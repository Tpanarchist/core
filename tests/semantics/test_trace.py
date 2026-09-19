"""Propositions for core.trace.

See SPECIFICATION.md #16 and docs/passes/03-context-error-trace.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.identity import Entity, Id, Ref
from core.time import Sequence, WallInstant
from core.trace import Trace
from core.value import Kind

TRACE_KIND = Kind("test.trace")
SUBJECT_KIND = Kind("test.subject")
EVENT_KIND = Kind("test.event")


def make_trace(suffix: str = "t1") -> Trace:
    return Trace(Id(TRACE_KIND, suffix))


class TestTraceIdentity:
    def test_satisfies_entity(self) -> None:
        assert isinstance(make_trace(), Entity)

    def test_id_is_stable_and_read_only(self) -> None:
        trace = make_trace()
        original_id = trace.id
        with pytest.raises(AttributeError):
            trace.id = Id(TRACE_KIND, "other")  # type: ignore[misc]
        assert trace.id == original_id


class TestAppend:
    def test_first_entry_gets_sequence_position_zero(self) -> None:
        trace = make_trace()
        entry = trace.append(kind=EVENT_KIND)
        assert entry.sequence == Sequence(trace.id, 0)

    def test_later_entries_get_contiguous_positions_in_same_space(self) -> None:
        trace = make_trace()
        entries = [trace.append(kind=EVENT_KIND) for _ in range(3)]
        assert [e.sequence.position for e in entries] == [0, 1, 2]
        assert all(e.sequence.space == trace.id for e in entries)

    def test_append_order_is_authoritative_even_if_observed_at_runs_backward(self) -> None:
        trace = make_trace()
        later_wall = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        earlier_wall = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
        first = trace.append(kind=EVENT_KIND, observed_at=later_wall)
        second = trace.append(kind=EVENT_KIND, observed_at=earlier_wall)
        assert first.sequence.position == 0
        assert second.sequence.position == 1

    def test_preserves_fields_without_interpretation(self) -> None:
        trace = make_trace()
        subject = Id(SUBJECT_KIND, "s1")
        ref = Ref(Id(SUBJECT_KIND, "s2"))
        payload = {"anything": object()}
        context = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))
        entry = trace.append(
            kind=EVENT_KIND,
            subject=subject,
            payload=payload,
            context=context,
            references=(ref,),
        )
        assert entry.subject == subject
        assert entry.kind == EVENT_KIND
        assert entry.payload is payload
        assert entry.context is context
        assert entry.references == (ref,)

    def test_no_caller_selected_sequence_is_accepted(self) -> None:
        trace = make_trace()
        with pytest.raises(TypeError):
            trace.append(kind=EVENT_KIND, sequence=Sequence(trace.id, 99))  # type: ignore[call-arg]


class TestEntries:
    def test_returns_a_tuple_snapshot(self) -> None:
        trace = make_trace()
        trace.append(kind=EVENT_KIND)
        assert isinstance(trace.entries(), tuple)

    def test_old_snapshot_is_unaffected_by_later_appends(self) -> None:
        trace = make_trace()
        trace.append(kind=EVENT_KIND)
        snapshot = trace.entries()
        trace.append(kind=EVENT_KIND)
        assert len(snapshot) == 1
        assert len(trace.entries()) == 2


class TestSince:
    def test_is_exclusive_of_the_marker_itself(self) -> None:
        trace = make_trace()
        first = trace.append(kind=EVENT_KIND)
        second = trace.append(kind=EVENT_KIND)
        assert trace.since(first.sequence) == (second,)

    def test_current_last_marker_returns_empty_tuple(self) -> None:
        trace = make_trace()
        trace.append(kind=EVENT_KIND)
        last = trace.append(kind=EVENT_KIND)
        assert trace.since(last.sequence) == ()

    def test_rejects_marker_from_another_trace(self) -> None:
        trace_a = make_trace("t1")
        trace_b = make_trace("t2")
        entry = trace_a.append(kind=EVENT_KIND)
        trace_b.append(kind=EVENT_KIND)
        with pytest.raises(ValueError, match="does not match"):
            trace_b.since(entry.sequence)

    def test_rejects_same_space_marker_beyond_current_entries(self) -> None:
        trace = make_trace()
        trace.append(kind=EVENT_KIND)
        bogus_marker = Sequence(trace.id, 5)
        with pytest.raises(ValueError, match="does not identify an entry"):
            trace.since(bogus_marker)


def test_importing_trace_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.trace", ("uuid.uuid4",))
