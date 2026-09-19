"""Propositions for core.event.

See SPECIFICATION.md #6 and docs/passes/04-facts-and-relationships.md.
"""

from datetime import UTC, datetime

from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.event import Event
from core.identity import Entity, Id
from core.time import WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
EVENT_ID_KIND = Kind("core.event")
KIND = Kind("test.occurred")


class TestEvent:
    def test_satisfies_entity(self) -> None:
        event = Event(id=Id(EVENT_ID_KIND, "e1"), kind=KIND, at=AT)
        assert isinstance(event, Entity)

    def test_equality_is_by_id_not_other_fields(self) -> None:
        id_ = Id(EVENT_ID_KIND, "e1")
        a = Event(id=id_, kind=KIND, at=AT, payload={"x": 1})
        b = Event(id=id_, kind=Kind("test.other"), at=AT, payload={"x": 2})
        assert a == b

    def test_different_ids_are_not_equal_even_with_identical_other_fields(self) -> None:
        a = Event(id=Id(EVENT_ID_KIND, "e1"), kind=KIND, at=AT)
        b = Event(id=Id(EVENT_ID_KIND, "e2"), kind=KIND, at=AT)
        assert a != b

    def test_hash_is_by_id(self) -> None:
        id_ = Id(EVENT_ID_KIND, "e1")
        a = Event(id=id_, kind=KIND, at=AT, payload=1)
        b = Event(id=id_, kind=KIND, at=AT, payload=2)
        assert hash(a) == hash(b)
        assert {a, b} == {a}

    def test_summary_returns_id_kind_at(self) -> None:
        id_ = Id(EVENT_ID_KIND, "e1")
        ctx = Context(as_of=AT)
        event = Event(id=id_, kind=KIND, at=AT, payload="anything", context=ctx)
        assert event.summary() == (id_, KIND, AT)


def test_importing_event_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.event", ("uuid.uuid4",))
