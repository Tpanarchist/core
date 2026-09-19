"""Propositions for core.observation.

See SPECIFICATION.md #7 and docs/passes/04-facts-and-relationships.md.
"""

from datetime import UTC, datetime

from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.event import Event
from core.identity import Entity, Id
from core.observation import Observation
from core.time import WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
OBS_ID_KIND = Kind("core.observation")
EVENT_ID_KIND = Kind("core.event")
SENSOR_KIND = Kind("test.sensor")


class TestObservation:
    def test_satisfies_entity(self) -> None:
        ctx = Context(as_of=AT)
        obs = Observation(
            id=Id(OBS_ID_KIND, "o1"),
            subject=Id(SENSOR_KIND, "s1"),
            value=80,
            at=AT,
            source="sensor-7",
            context=ctx,
        )
        assert isinstance(obs, Entity)

    def test_requires_subject_source_time_and_context(self) -> None:
        import inspect

        params = inspect.signature(Observation).parameters
        for required in ("subject", "source", "at", "context"):
            assert required in params
            assert params[required].default is inspect.Parameter.empty

    def test_preserves_value_source_observer_without_interpretation(self) -> None:
        ctx = Context(as_of=AT)
        obs = Observation(
            id=Id(OBS_ID_KIND, "o1"),
            subject=Id(SENSOR_KIND, "s1"),
            value=80,
            at=AT,
            source="sensor-7",
            context=ctx,
            observer="agent-1",
        )
        assert obs.value == 80
        assert obs.source == "sensor-7"
        assert obs.observer == "agent-1"

    def test_can_refer_to_an_event_identity_without_importing_event(self) -> None:
        event = Event(id=Id(EVENT_ID_KIND, "e1"), kind=Kind("test.occurred"), at=AT)
        ctx = Context(as_of=AT)
        obs = Observation(
            id=Id(OBS_ID_KIND, "o1"),
            subject=event.id,  # a bare Id — Observation never needs the Event type
            value="occurred",
            at=AT,
            source="watcher",
            context=ctx,
        )
        assert obs.subject == event.id

        import core.observation as core_observation

        assert not hasattr(core_observation, "Event")


def test_importing_observation_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.observation", ("uuid.uuid4",))
