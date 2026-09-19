"""Time: the dimension through which temporal location, ordering, and duration
are represented — a family of distinct readings, not one scalar, and not a
promise of simultaneity.

See SPECIFICATION.md #4 and ARCHITECTURE.md (time.py, tier 2).
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from core.identity import Id


@dataclass(frozen=True, slots=True, order=True)
class WallInstant:
    """A wall-clock reading. Unscoped — wall time is globally meaningful, if imprecise.

    Requires an aware ``datetime``; a naive one is rejected outright rather
    than silently assumed to be some particular zone. An aware non-UTC input
    is canonicalized to UTC at construction, so equality/ordering are always
    comparing the same reference frame.
    """

    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            raise ValueError("WallInstant requires an aware datetime; got a naive one")
        object.__setattr__(self, "value", self.value.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class Duration:
    """A non-negative elapsed magnitude. Unscoped — a plain quantity of nanoseconds."""

    nanoseconds: int

    def __post_init__(self) -> None:
        if self.nanoseconds < 0:
            raise ValueError("Duration.nanoseconds must not be negative")

    def __lt__(self, other: Duration) -> bool:
        return self.nanoseconds < other.nanoseconds

    def __le__(self, other: Duration) -> bool:
        return self.nanoseconds <= other.nanoseconds

    def __gt__(self, other: Duration) -> bool:
        return self.nanoseconds > other.nanoseconds

    def __ge__(self, other: Duration) -> bool:
        return self.nanoseconds >= other.nanoseconds


@dataclass(frozen=True, slots=True)
class MonotonicInstant:
    """An elapsed-time reading, scoped to the ``space`` of the clock that produced it.

    Equality is safe across spaces (readings from different spaces simply
    compare unequal); ordering and subtraction require the same space and
    raise otherwise — two independent processes' "monotonic" clocks are the
    same family but not the same space, and are not comparable.
    """

    space: Id
    nanoseconds: int

    def __post_init__(self) -> None:
        if self.nanoseconds < 0:
            raise ValueError("MonotonicInstant.nanoseconds must not be negative")

    def _check_space(self, other: MonotonicInstant) -> None:
        if self.space != other.space:
            raise ValueError(
                "cannot compare MonotonicInstant readings from different spaces: "
                f"{self.space!r} vs {other.space!r}"
            )

    def __lt__(self, other: MonotonicInstant) -> bool:
        self._check_space(other)
        return self.nanoseconds < other.nanoseconds

    def __le__(self, other: MonotonicInstant) -> bool:
        self._check_space(other)
        return self.nanoseconds <= other.nanoseconds

    def __gt__(self, other: MonotonicInstant) -> bool:
        self._check_space(other)
        return self.nanoseconds > other.nanoseconds

    def __ge__(self, other: MonotonicInstant) -> bool:
        self._check_space(other)
        return self.nanoseconds >= other.nanoseconds

    def __sub__(self, other: MonotonicInstant) -> Duration:
        self._check_space(other)
        delta = self.nanoseconds - other.nanoseconds
        if delta < 0:
            raise ValueError("MonotonicInstant subtraction must not yield a negative Duration")
        return Duration(delta)


@dataclass(frozen=True, slots=True)
class LogicalTime:
    """A logical-clock reading, scoped to the ``space`` of the clock that produced it.

    Deliberately exposes no generic rich ordering — only ``precedes``, which
    is necessary-but-not-sufficient evidence of actual causal precedence.
    Real causal claims belong to Relation/Provenance, not to LogicalTime alone.
    """

    space: Id
    counter: int

    def __post_init__(self) -> None:
        if self.counter < 0:
            raise ValueError("LogicalTime.counter must not be negative")

    def precedes(self, other: LogicalTime) -> bool:
        if self.space != other.space:
            raise ValueError(
                "cannot compare LogicalTime readings from different spaces: "
                f"{self.space!r} vs {other.space!r}"
            )
        return self.counter < other.counter


@dataclass(frozen=True, slots=True)
class Sequence:
    """A local total-order position, scoped to the ``space`` that produced it.

    Deliberately distinct from LogicalTime (causal order across actors) —
    a Sequence is a local order within one space, nothing more.
    """

    space: Id
    position: int

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ValueError("Sequence.position must not be negative")

    def _check_space(self, other: Sequence) -> None:
        if self.space != other.space:
            raise ValueError(
                "cannot compare Sequence readings from different spaces: "
                f"{self.space!r} vs {other.space!r}"
            )

    def __lt__(self, other: Sequence) -> bool:
        self._check_space(other)
        return self.position < other.position

    def __le__(self, other: Sequence) -> bool:
        self._check_space(other)
        return self.position <= other.position

    def __gt__(self, other: Sequence) -> bool:
        self._check_space(other)
        return self.position > other.position

    def __ge__(self, other: Sequence) -> bool:
        self._check_space(other)
        return self.position >= other.position


@runtime_checkable
class Clock(Protocol):
    """Produces WallInstant readings. The seam for controllable wall-time nondeterminism."""

    def now(self) -> WallInstant: ...


@runtime_checkable
class MonotonicClock(Protocol):
    """Produces MonotonicInstant readings scoped to its own space."""

    space: Id

    def now(self) -> MonotonicInstant: ...


@runtime_checkable
class LogicalClock(Protocol):
    """Produces LogicalTime readings scoped to its own space."""

    space: Id

    def tick(self) -> LogicalTime: ...

    def observe(self, other: LogicalTime) -> LogicalTime: ...


class SystemClock:
    """Concrete Clock: reads current UTC wall time only when explicitly invoked."""

    def now(self) -> WallInstant:
        return WallInstant(datetime.now(UTC))


class SystemMonotonicClock:
    """Concrete MonotonicClock.

    Takes an already-established ``space: Id`` — it never allocates one
    itself. Producing readings and allocating identities are separate
    capabilities; whoever wires up this clock allocates its space once,
    using an IdSource, and passes it in.
    """

    def __init__(self, space: Id) -> None:
        self.space = space

    def now(self) -> MonotonicInstant:
        return MonotonicInstant(space=self.space, nanoseconds=_time.monotonic_ns())


class LamportClock:
    """Concrete LogicalClock implementing the standard Lamport merge rule.

    Mutable and single-writer/not thread-safe in v0 (no locks — an unearned
    abstraction with no real concurrent consumer yet).
    """

    def __init__(self, space: Id, initial: int = 0) -> None:
        if initial < 0:
            raise ValueError("LamportClock initial counter must not be negative")
        self.space = space
        self._counter = initial

    def tick(self) -> LogicalTime:
        self._counter += 1
        return LogicalTime(space=self.space, counter=self._counter)

    def observe(self, other: LogicalTime) -> LogicalTime:
        if other.space != self.space:
            raise ValueError(
                "cannot observe a LogicalTime from a different space: "
                f"{self.space!r} vs {other.space!r}"
            )
        self._counter = max(self._counter, other.counter) + 1
        return LogicalTime(space=self.space, counter=self._counter)


@dataclass(frozen=True, slots=True)
class WallDeadline:
    """A deadline expressed as a WallInstant, checked against a Clock."""

    at: WallInstant
    clock: Clock

    def reached(self) -> bool:
        return self.clock.now() >= self.at


@dataclass(frozen=True, slots=True)
class MonotonicDeadline:
    """A deadline expressed as a MonotonicInstant, checked against a MonotonicClock.

    Validates at construction that the instant and clock share a space —
    mixing families/spaces is prevented by construction, not by convention.
    """

    at: MonotonicInstant
    clock: MonotonicClock

    def __post_init__(self) -> None:
        if self.at.space != self.clock.space:
            raise ValueError(
                "MonotonicDeadline requires the instant and clock to share a space: "
                f"{self.at.space!r} vs {self.clock.space!r}"
            )

    def reached(self) -> bool:
        return self.clock.now() >= self.at


type Deadline = WallDeadline | MonotonicDeadline
