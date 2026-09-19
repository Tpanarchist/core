"""Propositions for core.time — see SPECIFICATION.md #4 and docs/passes/02-identity-time.md."""

import importlib
from datetime import UTC, datetime, timedelta, timezone
from unittest import mock

import pytest

import core.time as core_time
from core.identity import Id
from core.time import (
    Duration,
    LamportClock,
    LogicalTime,
    MonotonicDeadline,
    MonotonicInstant,
    Sequence,
    SystemClock,
    SystemMonotonicClock,
    WallDeadline,
    WallInstant,
)
from core.value import Kind

SPACE_KIND = Kind("test.space")
SPACE_A = Id(SPACE_KIND, "a")
SPACE_B = Id(SPACE_KIND, "b")


class FakeClock:
    """A deterministic Clock test double — no real-time dependency."""

    def __init__(self, current: WallInstant) -> None:
        self.current = current

    def now(self) -> WallInstant:
        return self.current


class FakeMonotonicClock:
    """A deterministic MonotonicClock test double."""

    def __init__(self, space: Id, current: MonotonicInstant) -> None:
        self.space = space
        self.current = current

    def now(self) -> MonotonicInstant:
        return self.current


class TestWallInstant:
    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="aware datetime"):
            WallInstant(datetime(2024, 1, 1))

    def test_canonicalizes_non_utc_aware_input_to_utc(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        instant = WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=eastern))
        assert instant.value == datetime(2024, 1, 1, 17, 0, tzinfo=UTC)
        assert instant.value.tzinfo == UTC

    def test_equality_and_order_operate_on_canonical_instant(self) -> None:
        utc = WallInstant(datetime(2024, 1, 1, 17, 0, tzinfo=UTC))
        eastern_equiv = WallInstant(
            datetime(2024, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
        )
        later = WallInstant(datetime(2024, 1, 1, 18, 0, tzinfo=UTC))
        assert utc == eastern_equiv
        assert utc < later


class TestDuration:
    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            Duration(-1)

    def test_ordering(self) -> None:
        assert Duration(1) < Duration(2)


class TestMonotonicInstant:
    def test_preserves_space(self) -> None:
        assert MonotonicInstant(SPACE_A, 100).space == SPACE_A

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            MonotonicInstant(SPACE_A, -1)

    def test_cross_space_equality_is_false_not_an_error(self) -> None:
        assert MonotonicInstant(SPACE_A, 100) != MonotonicInstant(SPACE_B, 100)

    def test_cross_space_ordering_raises(self) -> None:
        with pytest.raises(ValueError, match="different spaces"):
            _ = MonotonicInstant(SPACE_A, 100) < MonotonicInstant(SPACE_B, 200)

    def test_same_space_ordering(self) -> None:
        assert MonotonicInstant(SPACE_A, 100) < MonotonicInstant(SPACE_A, 200)

    def test_same_space_subtraction_returns_duration(self) -> None:
        later = MonotonicInstant(SPACE_A, 300)
        earlier = MonotonicInstant(SPACE_A, 100)
        assert later - earlier == Duration(200)

    def test_reverse_subtraction_rejected(self) -> None:
        later = MonotonicInstant(SPACE_A, 300)
        earlier = MonotonicInstant(SPACE_A, 100)
        with pytest.raises(ValueError, match="negative Duration"):
            _ = earlier - later

    def test_cross_space_subtraction_raises(self) -> None:
        with pytest.raises(ValueError, match="different spaces"):
            _ = MonotonicInstant(SPACE_A, 300) - MonotonicInstant(SPACE_B, 100)


class TestLogicalTime:
    def test_preserves_space(self) -> None:
        assert LogicalTime(SPACE_A, 1).space == SPACE_A

    def test_rejects_negative_counter(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            LogicalTime(SPACE_A, -1)

    def test_precedes_same_space(self) -> None:
        assert LogicalTime(SPACE_A, 1).precedes(LogicalTime(SPACE_A, 2))
        assert not LogicalTime(SPACE_A, 2).precedes(LogicalTime(SPACE_A, 1))

    def test_precedes_cross_space_raises(self) -> None:
        with pytest.raises(ValueError, match="different spaces"):
            LogicalTime(SPACE_A, 1).precedes(LogicalTime(SPACE_B, 2))

    def test_cross_space_equality_is_false_not_an_error(self) -> None:
        assert LogicalTime(SPACE_A, 1) != LogicalTime(SPACE_B, 1)


class TestSequence:
    def test_preserves_space(self) -> None:
        assert Sequence(SPACE_A, 0).space == SPACE_A

    def test_rejects_negative_position(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            Sequence(SPACE_A, -1)

    def test_same_space_ordering(self) -> None:
        assert Sequence(SPACE_A, 0) < Sequence(SPACE_A, 1)

    def test_cross_space_ordering_raises(self) -> None:
        with pytest.raises(ValueError, match="different spaces"):
            _ = Sequence(SPACE_A, 0) < Sequence(SPACE_B, 1)

    def test_cross_space_equality_is_false_not_an_error(self) -> None:
        assert Sequence(SPACE_A, 0) != Sequence(SPACE_B, 0)


class TestSystemClock:
    def test_returns_aware_utc_instant(self) -> None:
        instant = SystemClock().now()
        assert instant.value.tzinfo == UTC


class TestSystemMonotonicClock:
    def test_stamps_constructor_supplied_space(self) -> None:
        clock = SystemMonotonicClock(SPACE_A)
        assert clock.now().space == SPACE_A


class TestLamportClock:
    def test_tick_increments_exactly_once(self) -> None:
        clock = LamportClock(SPACE_A, initial=0)
        assert clock.tick().counter == 1
        assert clock.tick().counter == 2

    def test_observe_advances_to_max_plus_one(self) -> None:
        clock = LamportClock(SPACE_A, initial=5)
        observed = LogicalTime(SPACE_A, 10)
        assert clock.observe(observed).counter == 11

    def test_observe_does_not_regress_below_local(self) -> None:
        clock = LamportClock(SPACE_A, initial=20)
        observed = LogicalTime(SPACE_A, 5)
        assert clock.observe(observed).counter == 21

    def test_observe_rejects_other_space(self) -> None:
        clock = LamportClock(SPACE_A)
        with pytest.raises(ValueError, match="different space"):
            clock.observe(LogicalTime(SPACE_B, 1))

    def test_rejects_negative_initial(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            LamportClock(SPACE_A, initial=-1)


class TestWallDeadline:
    def test_transitions_from_not_reached_to_reached(self) -> None:
        deadline_at = WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))
        clock = FakeClock(WallInstant(datetime(2024, 1, 1, 11, 0, tzinfo=UTC)))
        deadline = WallDeadline(at=deadline_at, clock=clock)

        assert deadline.reached() is False

        clock.current = WallInstant(datetime(2024, 1, 1, 12, 30, tzinfo=UTC))
        assert deadline.reached() is True


class TestMonotonicDeadline:
    def test_requires_matching_space(self) -> None:
        at = MonotonicInstant(SPACE_A, 1000)
        mismatched_clock = FakeMonotonicClock(SPACE_B, MonotonicInstant(SPACE_B, 0))
        with pytest.raises(ValueError, match="share a space"):
            MonotonicDeadline(at=at, clock=mismatched_clock)

    def test_transitions_from_not_reached_to_reached(self) -> None:
        at = MonotonicInstant(SPACE_A, 1000)
        clock = FakeMonotonicClock(SPACE_A, MonotonicInstant(SPACE_A, 500))
        deadline = MonotonicDeadline(at=at, clock=clock)

        assert deadline.reached() is False

        clock.current = MonotonicInstant(SPACE_A, 1500)
        assert deadline.reached() is True


def test_importing_time_module_has_no_side_effects() -> None:
    # datetime.datetime is an immutable C type — .now can't be patched via
    # setattr, and reload() re-runs `from datetime import datetime`, which
    # would rebind any module-local patch anyway. Reading the module confirms
    # no top-level call to datetime.now() exists; the monotonic-clock check
    # below is the mechanically enforceable half of this proposition. Pass 6's
    # architecture-wide import-side-effect test covers wall-clock reads too,
    # via a mechanism that doesn't depend on patching an immutable type.
    with mock.patch(
        "time.monotonic_ns",
        side_effect=AssertionError("import must not read the monotonic clock"),
    ):
        importlib.reload(core_time)
