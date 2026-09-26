"""Immutable cash-planning inputs with explicit identity, dates, and evidence.

Amounts in schedules cross the liquid-cash boundary: positive is an external
inflow and negative is an external outflow. Internal liquid transfers belong
in the ledger and must not be represented as two schedules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.observation import Observation
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.money import Money, currency_exponent

SCHEDULE = Kind("finance.cash_schedule")
ALLOCATION = Kind("finance.cash_allocation")
HOLD = Kind("finance.cash_hold")
FLOOR_CHANGE = Kind("finance.cash_floor_change")
OBSERVATION = Kind("finance.cash_observation")
COVERAGE = Kind("finance.cash_coverage")
RETIREMENT = Kind("finance.cash_retirement")
ACCOUNT = Kind("finance.account")


def _id(value: Id, kind: Kind) -> None:
    if type(value) is not Id or value.kind != kind:
        raise ValueError(f"Expected {kind.value} identity")


def _ref(value: Ref, *kinds: Kind) -> Ref:
    if type(value) is not Ref or type(value.id) is not Id or value.id.kind not in kinds:
        raise ValueError("Reference has an unexpected identity kind")
    namespace = value.namespace
    if namespace is not None:
        namespace = Namespace(tuple(namespace.segments))
    return Ref(value.id, namespace)


def _day(value: date) -> None:
    if type(value) is not date:
        raise TypeError("Financial effective dates must be date-only values")


def _label(value: str, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonempty")


def _positive(value: Money, name: str) -> None:
    if type(value) is not Money or value.minor <= 0:
        raise ValueError(f"{name} must be positive Money")


def _immutable_context_value(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, (tuple, list)):
        items = cast(tuple[object, ...] | list[object], value)
        return tuple(_immutable_context_value(item) for item in items)
    raise TypeError("Cash observation context accepts only immutable primitive/tuple values")


def _snapshot_context(context: Context) -> Context:
    if type(context) is not Context or type(context.as_of) is not WallInstant:
        raise TypeError("Cash observation requires a Core Context with a WallInstant")
    if context.environment is not None:
        raise ValueError("Cash observation context does not support mutable environment values")
    namespace = context.namespace
    if namespace is not None:
        namespace = Namespace(tuple(namespace.segments))
    metadata = (
        {key: _immutable_context_value(value) for key, value in context.metadata.items()}
        if context.metadata is not None
        else None
    )
    return Context(
        as_of=context.as_of,
        namespace=namespace,
        source=_immutable_context_value(context.source),
        authority=_immutable_context_value(context.authority),
        version=_immutable_context_value(context.version),
        units=_immutable_context_value(context.units),
        scope=_immutable_context_value(context.scope),
        metadata=metadata,
    )


class Cadence(StrEnum):
    ONCE = "once"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# The user-facing term is recurrence; Cadence remains the stored enum name.
Recurrence = Cadence


class MonthlyPolicy(StrEnum):
    CLAMP = "clamp_to_last_day"
    SKIP = "skip_missing_day"


@dataclass(frozen=True, slots=True)
class ScheduleOccurrence:
    """One date-specific planned obligation, not a financial event."""

    schedule: Ref
    due_on: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "schedule", _ref(self.schedule, SCHEDULE))
        _day(self.due_on)


@dataclass(frozen=True, slots=True)
class CashSchedule:
    id: Id
    account: Ref
    amount: Money
    start_date: date
    cadence: Cadence
    timezone: str
    label: str
    source: str = "manual"
    end_date: date | None = None
    monthly_policy: MonthlyPolicy = MonthlyPolicy.CLAMP
    active: bool = True

    def __post_init__(self) -> None:
        _id(self.id, SCHEDULE)
        object.__setattr__(self, "account", _ref(self.account, ACCOUNT))
        if type(self.amount) is not Money or self.amount.minor == 0:
            raise ValueError("A cash schedule requires nonzero Money")
        _day(self.start_date)
        if type(self.cadence) is not Cadence:
            raise TypeError("Schedule cadence must be a Cadence")
        _label(self.timezone, "Timezone")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {self.timezone}") from exc
        _label(self.label, "Schedule label")
        _label(self.source, "Schedule source")
        if self.end_date is not None:
            _day(self.end_date)
            if self.end_date < self.start_date:
                raise ValueError("Schedule end precedes its start")
        if type(self.monthly_policy) is not MonthlyPolicy:
            raise TypeError("Monthly policy must be a MonthlyPolicy")
        if type(self.active) is not bool:
            raise TypeError("Schedule active flag must be bool")


@dataclass(frozen=True, slots=True)
class CashAllocation:
    id: Id
    amount: Money
    label: str
    active_from: date
    linked_occurrence: ScheduleOccurrence | None = None
    active: bool = True

    def __post_init__(self) -> None:
        _id(self.id, ALLOCATION)
        _positive(self.amount, "Allocation")
        _label(self.label, "Allocation label")
        _day(self.active_from)
        if (
            self.linked_occurrence is not None
            and type(self.linked_occurrence) is not ScheduleOccurrence
        ):
            raise TypeError("Linked occurrence must be a ScheduleOccurrence")
        if type(self.active) is not bool:
            raise TypeError("Allocation active flag must be bool")


@dataclass(frozen=True, slots=True)
class CashHold:
    id: Id
    amount: Money
    label: str
    active_from: date
    release_date: date | None = None
    active: bool = True

    def __post_init__(self) -> None:
        _id(self.id, HOLD)
        _positive(self.amount, "Hold")
        _label(self.label, "Hold label")
        _day(self.active_from)
        if self.release_date is not None:
            _day(self.release_date)
            if self.release_date <= self.active_from:
                raise ValueError("Hold release must follow activation")
        if type(self.active) is not bool:
            raise TypeError("Hold active flag must be bool")


@dataclass(frozen=True, slots=True)
class CashFloorChange:
    id: Id
    effective_date: date
    amount: Money

    def __post_init__(self) -> None:
        _id(self.id, FLOOR_CHANGE)
        _day(self.effective_date)
        if type(self.amount) is not Money or self.amount.minor < 0:
            raise ValueError("Cash floor must be nonnegative Money")


@dataclass(frozen=True, slots=True)
class CashBalanceObservation:
    """A manual balance acquisition that can be converted to Core Observation."""

    id: Id
    account: Ref
    observed: Money
    observed_at: WallInstant
    observed_on: date
    fresh_through: date
    source: str
    context: Context

    def __post_init__(self) -> None:
        _id(self.id, OBSERVATION)
        object.__setattr__(self, "account", _ref(self.account, ACCOUNT))
        if type(self.observed) is not Money or type(self.observed_at) is not WallInstant:
            raise TypeError("Balance observation requires Money and a WallInstant")
        _day(self.observed_on)
        _day(self.fresh_through)
        if self.fresh_through < self.observed_on:
            raise ValueError("Observation freshness cannot precede observation date")
        remaining_days = (date.max - self.observed_on).days
        freshness_limit = self.observed_on + timedelta(days=min(7, remaining_days))
        if self.fresh_through > freshness_limit:
            raise ValueError(
                "Manual balance evidence cannot claim more than seven days of freshness"
            )
        _label(self.source, "Observation source")
        object.__setattr__(self, "context", _snapshot_context(self.context))
        if self.context.units not in (None, self.observed.currency):
            raise ValueError("Observation context units conflict with currency")

    def as_core_observation(self) -> Observation[Money]:
        return Observation(
            id=self.id,
            subject=self.account,
            value=self.observed,
            at=self.observed_at,
            source=self.source,
            context=self.context,
        )


@dataclass(frozen=True, slots=True)
class CashCoverage:
    id: Id
    currency: str
    as_of: date
    through_date: date
    accounts_complete: bool
    schedules_complete: bool
    account_refs: tuple[Ref, ...]
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        _id(self.id, COVERAGE)
        currency_exponent(self.currency)
        _day(self.as_of)
        _day(self.through_date)
        if self.through_date < self.as_of:
            raise ValueError("Coverage horizon precedes its as-of date")
        if type(self.accounts_complete) is not bool or type(self.schedules_complete) is not bool:
            raise TypeError("Coverage completeness flags must be bool")
        refs = tuple(_ref(ref, ACCOUNT) for ref in self.account_refs)
        if len(set(refs)) != len(refs):
            raise ValueError("Coverage account inventory contains duplicate references")
        object.__setattr__(self, "account_refs", refs)
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Coverage requires a recorded WallInstant")
        _label(self.source, "Coverage source")


@dataclass(frozen=True, slots=True)
class CashRetirement:
    id: Id
    target: Ref
    effective_date: date
    recorded_at: WallInstant
    principal: str
    reason: str

    def __post_init__(self) -> None:
        _id(self.id, RETIREMENT)
        object.__setattr__(self, "target", _ref(self.target, SCHEDULE, ALLOCATION, HOLD))
        _day(self.effective_date)
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Retirement requires a recorded WallInstant")
        _label(self.principal, "Retirement principal")
        _label(self.reason, "Retirement reason")


@dataclass(frozen=True, slots=True)
class CashRecords:
    schedules: tuple[CashSchedule, ...] = ()
    allocations: tuple[CashAllocation, ...] = ()
    holds: tuple[CashHold, ...] = ()
    floor_changes: tuple[CashFloorChange, ...] = ()
    observations: tuple[CashBalanceObservation, ...] = ()
    coverage: tuple[CashCoverage, ...] = ()
    retirements: tuple[CashRetirement, ...] = ()

    def __post_init__(self) -> None:
        fields = (
            ("schedules", CashSchedule),
            ("allocations", CashAllocation),
            ("holds", CashHold),
            ("floor_changes", CashFloorChange),
            ("observations", CashBalanceObservation),
            ("coverage", CashCoverage),
            ("retirements", CashRetirement),
        )
        all_ids: list[Id] = []
        for name, expected in fields:
            values = tuple(getattr(self, name))
            if any(type(value) is not expected for value in values):
                raise TypeError(f"CashRecords.{name} has an unexpected record type")
            object.__setattr__(self, name, values)
            all_ids.extend(value.id for value in values)
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("Cash records contain duplicate identities")
