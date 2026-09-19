"""Propositions for core.state.

See SPECIFICATION.md #5 and docs/passes/05-change-and-execution.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.event import Event
from core.identity import Entity, Id, Namespace, Ref
from core.state import History, State, Transition
from core.time import WallInstant
from core.value import Kind

T0 = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
T1 = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
T2 = WallInstant(datetime(2024, 1, 3, tzinfo=UTC))

CTX = Context(as_of=T0)
SUBJECT_KIND = Kind("test.subject")
TRANSITION_ID_KIND = Kind("core.transition")
EVENT_ID_KIND = Kind("core.event")


def make_event(suffix: str = "e1") -> Event:
    return Event(id=Id(EVENT_ID_KIND, suffix), kind=Kind("test.occurred"), at=T0)


def make_state(subject: Id | Ref, value: int, at: WallInstant) -> State[int]:
    return State(subject=subject, value=value, at=at, context=CTX)


def make_transition(
    suffix: str,
    subject: Id | Ref,
    before_value: int,
    after_value: int,
    before_at: WallInstant,
    after_at: WallInstant,
) -> Transition[int]:
    return Transition(
        id=Id(TRANSITION_ID_KIND, suffix),
        before=make_state(subject, before_value, before_at),
        event=make_event(suffix),
        operation="increment",
        after=make_state(subject, after_value, after_at),
    )


class TestState:
    def test_carries_subject_value_time_context_unchanged(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        state = make_state(subject, 42, T0)
        assert state.subject == subject
        assert state.value == 42
        assert state.at == T0
        assert state.context is CTX

    def test_is_not_entity_bearing(self) -> None:
        assert not hasattr(make_state(Id(SUBJECT_KIND, "s1"), 1, T0), "id")

    def test_uses_content_equality(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        a = make_state(subject, 1, T0)
        b = make_state(subject, 1, T0)
        c = make_state(subject, 2, T0)
        assert a == b
        assert a != c


class TestTransition:
    def test_satisfies_entity(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        transition = Transition(
            id=Id(TRANSITION_ID_KIND, "t1"),
            before=make_state(subject, 1, T0),
            event=make_event(),
            operation="increment",
            after=make_state(subject, 2, T1),
        )
        assert isinstance(transition, Entity)

    def test_rejects_different_underlying_identities(self) -> None:
        with pytest.raises(ValueError, match="same subject"):
            Transition(
                id=Id(TRANSITION_ID_KIND, "t1"),
                before=make_state(Id(SUBJECT_KIND, "a"), 1, T0),
                event=make_event(),
                operation="increment",
                after=make_state(Id(SUBJECT_KIND, "b"), 2, T1),
            )

    def test_allows_id_and_ref_forms_of_the_same_subject(self) -> None:
        subject_id = Id(SUBJECT_KIND, "s1")
        subject_ref = Ref(subject_id, Namespace(("scoped",)))
        transition = Transition(
            id=Id(TRANSITION_ID_KIND, "t1"),
            before=make_state(subject_id, 1, T0),
            event=make_event(),
            operation="increment",
            after=make_state(subject_ref, 2, T1),
        )
        assert transition.after.subject == subject_ref

    def test_rejects_after_earlier_than_before(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        with pytest.raises(ValueError, match="must not be earlier"):
            Transition(
                id=Id(TRANSITION_ID_KIND, "t1"),
                before=make_state(subject, 1, T1),
                event=make_event(),
                operation="increment",
                after=make_state(subject, 2, T0),
            )

    def test_permits_equal_before_and_after_timestamps(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        transition = Transition(
            id=Id(TRANSITION_ID_KIND, "t1"),
            before=make_state(subject, 1, T0),
            event=make_event(),
            operation="increment",
            after=make_state(subject, 2, T0),
        )
        assert transition.before.at == transition.after.at

    def test_rejects_empty_operation(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        with pytest.raises(ValueError, match="operation must not be empty"):
            Transition(
                id=Id(TRANSITION_ID_KIND, "t1"),
                before=make_state(subject, 1, T0),
                event=make_event(),
                operation="",
                after=make_state(subject, 2, T1),
            )

    def test_subject_property_is_derived_from_before(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        transition = Transition(
            id=Id(TRANSITION_ID_KIND, "t1"),
            before=make_state(subject, 1, T0),
            event=make_event(),
            operation="increment",
            after=make_state(subject, 2, T1),
        )
        assert transition.subject == transition.before.subject


class TestHistory:
    def test_subject_is_stable_and_read_only(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        assert history.subject == subject
        with pytest.raises(AttributeError):
            history.subject = Id(SUBJECT_KIND, "other")  # type: ignore[misc]

    def test_first_transition_must_match_history_subject(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        other_subject = Id(SUBJECT_KIND, "s2")
        history: History[int] = History(subject)
        mismatched = make_transition("t1", other_subject, 1, 2, T0, T1)
        with pytest.raises(ValueError, match="does not match"):
            history.append(mismatched)

    def test_subsequent_transition_must_continue_from_previous_after(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        first = make_transition("t1", subject, 1, 2, T0, T1)
        history.append(first)
        second = make_transition("t2", subject, 2, 3, T1, T2)
        history.append(second)
        assert history.entries() == (first, second)

    def test_rejects_discontinuity(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        history.append(make_transition("t1", subject, 1, 2, T0, T1))
        # does not start from previous.after (value=2) — starts from value=99 instead
        discontinuous = make_transition("t2", subject, 99, 100, T1, T2)
        with pytest.raises(ValueError, match="pick up where"):
            history.append(discontinuous)

    def test_rejects_duplicate_transition_id(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        first = make_transition("t1", subject, 1, 2, T0, T1)
        history.append(first)
        duplicate = Transition(
            id=first.id,
            before=make_state(subject, 2, T1),
            event=make_event("dup"),
            operation="increment",
            after=make_state(subject, 3, T2),
        )
        with pytest.raises(ValueError, match="already recorded"):
            history.append(duplicate)

    def test_append_returns_the_admitted_transition(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        transition = make_transition("t1", subject, 1, 2, T0, T1)
        assert history.append(transition) is transition

    def test_entries_returns_a_tuple_snapshot(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        history.append(make_transition("t1", subject, 1, 2, T0, T1))
        assert isinstance(history.entries(), tuple)

    def test_old_snapshot_unaffected_by_later_append(self) -> None:
        subject = Id(SUBJECT_KIND, "s1")
        history: History[int] = History(subject)
        history.append(make_transition("t1", subject, 1, 2, T0, T1))
        snapshot = history.entries()
        history.append(make_transition("t2", subject, 2, 3, T1, T2))
        assert len(snapshot) == 1


def test_importing_state_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.state", ("uuid.uuid4",))
