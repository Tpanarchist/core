"""Cash planning policy: exact event boundaries, coverage, and reservation accounting."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from typing import cast

import pytest

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind
from personal_finance.application.cash_projection import (
    occurs_on,
    project_cash,
    schedule_occurrences,
)
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.cash import (
    ALLOCATION,
    COVERAGE,
    FLOOR_CHANGE,
    HOLD,
    OBSERVATION,
    RETIREMENT,
    SCHEDULE,
    Cadence,
    CashAllocation,
    CashBalanceObservation,
    CashCoverage,
    CashFloorChange,
    CashHold,
    CashRecords,
    CashRetirement,
    CashSchedule,
    MonthlyPolicy,
    ScheduleOccurrence,
)
from personal_finance.domain.ledger import EntryContent, JournalEntry, Posting
from personal_finance.domain.money import Money

START = date(2026, 9, 24)
AT = WallInstant(datetime(2026, 9, 24, 12, tzinfo=UTC))
CASH = Account(Id(Kind("finance.account"), "cash"), "Checking", AccountType.ASSET, "USD", True)
SAVINGS = Account(Id(Kind("finance.account"), "savings"), "Savings", AccountType.ASSET, "USD", True)
INCOME = Account(Id(Kind("finance.account"), "income"), "Salary", AccountType.INCOME, "USD")
EXPENSE = Account(Id(Kind("finance.account"), "expense"), "Expense", AccountType.EXPENSE, "USD")
FRAME = Context(
    as_of=AT,
    namespace=Namespace(("personal_finance", "test")),
    source="manual_recorded_ledger",
    authority="local_review_policy",
    version="cash-v1",
    units="integer minor units per currency",
)
OBS_FRAME = Context(as_of=AT, source="manual", units="USD")


def _entry(number: int, day: date, *postings: Posting) -> JournalEntry:
    return JournalEntry(
        Id(Kind("finance.journal_entry"), str(number)),
        EntryContent(day, f"Entry {number}", tuple(postings)),
        AT,
        "local-human",
        number,
    )


def _opening(amount: int = 100_000) -> JournalEntry:
    return _entry(
        1,
        START - timedelta(days=1),
        Posting(Ref(CASH.id), Money(amount, "USD")),
        Posting(Ref(INCOME.id), Money(-amount, "USD")),
    )


def _observation(account: Account = CASH, amount: int = 100_000) -> CashBalanceObservation:
    return CashBalanceObservation(
        Id(OBSERVATION, account.id.value),
        Ref(account.id),
        Money(amount, account.currency),
        AT,
        START,
        START + timedelta(days=7),
        "manual statement balance",
        OBS_FRAME,
    )


def _coverage(*accounts: Account, days: int = 90) -> CashCoverage:
    return CashCoverage(
        Id(COVERAGE, "coverage"),
        "USD",
        START,
        START + timedelta(days=days),
        True,
        True,
        tuple(Ref(account.id) for account in accounts),
        AT,
        "manual inventory declaration",
    )


def _floor(amount: int = 10_000, day: date = START) -> CashFloorChange:
    return CashFloorChange(Id(FLOOR_CHANGE, f"floor-{day.isoformat()}"), day, Money(amount, "USD"))


def _bill(amount: int = -30_000, due: date = START + timedelta(days=2)) -> CashSchedule:
    return CashSchedule(
        Id(SCHEDULE, "rent"),
        Ref(CASH.id),
        Money(amount, "USD"),
        due,
        Cadence.ONCE,
        "America/New_York",
        "Rent",
    )


def _plan(
    records: CashRecords,
    *,
    entries: tuple[JournalEntry, ...] | None = None,
    accounts: tuple[Account, ...] = (CASH, INCOME, EXPENSE),
    days: int = 30,
):
    return project_cash(
        START, days, accounts, (_opening(),) if entries is None else entries, records, FRAME
    )


def test_monthly_date_policy_is_anchored_and_timezone_is_iana() -> None:
    jan31 = CashSchedule(
        Id(SCHEDULE, "monthly"),
        Ref(CASH.id),
        Money(-100, "USD"),
        date(2026, 1, 31),
        Cadence.MONTHLY,
        "America/New_York",
        "Month end",
    )
    dates = schedule_occurrences(jan31, date(2026, 1, 30), date(2026, 4, 2))
    assert dates == (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31))
    assert occurs_on(jan31, date(2026, 2, 28))
    skipped = replace(jan31, monthly_policy=MonthlyPolicy.SKIP)
    assert schedule_occurrences(skipped, date(2026, 1, 30), date(2026, 4, 2)) == (
        date(2026, 1, 31),
        date(2026, 3, 31),
    )
    with pytest.raises(ValueError, match="IANA timezone"):
        replace(jan31, timezone="Mars/Olympus")
    with pytest.raises(TypeError):
        occurs_on(jan31, cast(date, datetime(2026, 2, 28, tzinfo=UTC)))


def test_linked_allocation_is_released_atomically_with_bill_not_double_subtracted() -> None:
    bill = _bill()
    allocation = CashAllocation(
        Id(ALLOCATION, "rent-escrow"),
        Money(30_000, "USD"),
        "Rent reserve",
        START,
        ScheduleOccurrence(Ref(bill.id), bill.start_date),
    )
    records = CashRecords(
        schedules=(bill,),
        allocations=(allocation,),
        floor_changes=(_floor(),),
        observations=(_observation(),),
        coverage=(_coverage(CASH),),
    )
    series = _plan(records).currencies[0]
    assert series.missing_inputs == ()
    assert series.starting_balance == Money(100_000, "USD")
    assert series.current_unallocated == Money(70_000, "USD")
    assert [
        (point.kind, point.balance.minor, point.protected.minor, point.available.minor)
        for point in series.points
        if point.available is not None
    ] == [
        ("initial", 100_000, 30_000, 60_000),
        ("scheduled_outflow", 70_000, 0, 60_000),
    ]
    assert series.minimum_balance == Money(70_000, "USD")
    assert series.safe_to_allocate == Money(60_000, "USD")


def test_floor_outflows_and_inflows_use_each_same_day_boundary() -> None:
    tomorrow = START + timedelta(days=1)
    expense = _bill(-30_000, tomorrow)
    income = replace(expense, id=Id(SCHEDULE, "pay"), amount=Money(50_000, "USD"), label="Pay")
    records = CashRecords(
        schedules=(income, expense),
        floor_changes=(_floor(), _floor(50_000, tomorrow)),
        observations=(_observation(),),
        coverage=(_coverage(CASH),),
    )
    series = _plan(records).currencies[0]
    assert [
        (point.kind, point.balance.minor, point.floor.minor)
        for point in series.points
        if point.floor is not None
    ] == [
        ("initial", 100_000, 10_000),
        ("floor_change", 100_000, 50_000),
        ("scheduled_outflow", 70_000, 50_000),
        ("scheduled_inflow", 120_000, 50_000),
    ]
    assert series.minimum_balance == Money(70_000, "USD")
    assert series.safe_to_allocate == Money(20_000, "USD")


def test_future_posted_entries_are_not_in_opening_and_internal_transfer_nets_zero() -> None:
    transfer = _entry(
        2,
        START + timedelta(days=1),
        Posting(Ref(SAVINGS.id), Money(20_000, "USD")),
        Posting(Ref(CASH.id), Money(-20_000, "USD")),
    )
    future_bill = _entry(
        3,
        START + timedelta(days=2),
        Posting(Ref(EXPENSE.id), Money(15_000, "USD")),
        Posting(Ref(CASH.id), Money(-15_000, "USD")),
    )
    records = CashRecords(
        floor_changes=(_floor(),),
        observations=(_observation(), _observation(SAVINGS, 0)),
        coverage=(_coverage(CASH, SAVINGS),),
    )
    series = _plan(
        records,
        entries=(_opening(), transfer, future_bill),
        accounts=(CASH, SAVINGS, INCOME, EXPENSE),
    ).currencies[0]
    assert series.starting_balance == Money(100_000, "USD")
    assert [(point.kind, point.delta.minor, point.balance.minor) for point in series.points] == [
        ("initial", 0, 100_000),
        ("recorded_outflow", -15_000, 85_000),
    ]
    assert series.safe_to_allocate == Money(75_000, "USD")


def test_safe_value_requires_explicit_coverage_observations_and_floor() -> None:
    empty = _plan(CashRecords()).currencies[0]
    assert empty.starting_balance == Money(100_000, "USD")
    assert empty.safe_to_allocate is None
    assert any("coverage" in reason for reason in empty.missing_inputs)
    assert any("observation" in reason for reason in empty.missing_inputs)
    assert any("floor" in reason for reason in empty.missing_inputs)

    incomplete = CashRecords(
        floor_changes=(_floor(),),
        observations=(_observation(amount=99_999),),
        coverage=(replace(_coverage(CASH), schedules_complete=False, through_date=START),),
    )
    result = _plan(incomplete).currencies[0]
    assert result.safe_to_allocate is None
    assert any("schedule inventory is incomplete" in reason for reason in result.missing_inputs)
    assert any("ends before" in reason for reason in result.missing_inputs)
    assert any("does not reconcile" in reason for reason in result.missing_inputs)


def test_stale_observation_and_undeclared_liquid_account_withhold_safe_amount() -> None:
    old = replace(
        _observation(),
        observed_on=START - timedelta(days=8),
        fresh_through=START - timedelta(days=1),
    )
    records = CashRecords(
        floor_changes=(_floor(),),
        observations=(old, _observation(SAVINGS, 0)),
        coverage=(_coverage(CASH),),
    )
    series = _plan(records, accounts=(CASH, SAVINGS, INCOME, EXPENSE)).currencies[0]
    assert series.safe_to_allocate is None
    assert any("stale" in reason for reason in series.missing_inputs)
    assert any("coverage account list differs" in reason for reason in series.missing_inputs)
    with pytest.raises(ValueError, match="seven days"):
        replace(old, fresh_through=START + timedelta(days=8))


def test_holds_and_retirements_are_constraints_at_boundary_not_outflows() -> None:
    hold = CashHold(Id(HOLD, "hold"), Money(20_000, "USD"), "Pending card", START)
    allocation = CashAllocation(Id(ALLOCATION, "fund"), Money(10_000, "USD"), "Buffer", START)
    retire_hold = CashRetirement(
        Id(RETIREMENT, "release-hold"),
        Ref(hold.id),
        START + timedelta(days=1),
        AT,
        "local-human",
        "Cleared",
    )
    records = CashRecords(
        allocations=(allocation,),
        holds=(hold,),
        retirements=(retire_hold,),
        floor_changes=(_floor(),),
        observations=(_observation(),),
        coverage=(_coverage(CASH),),
    )
    series = _plan(records).currencies[0]
    assert [(point.kind, point.delta.minor, point.holds.minor) for point in series.points] == [
        ("initial", 0, 20_000),
        ("retirement", 0, 0),
    ]
    assert series.safe_to_allocate == Money(60_000, "USD")
    assert series.current_unallocated == Money(70_000, "USD")


def test_cash_records_snapshot_input_lists_and_reject_invalid_refs() -> None:
    schedules = [_bill()]
    records = CashRecords(schedules=cast(tuple[CashSchedule, ...], schedules))
    schedules.clear()
    assert len(records.schedules) == 1
    with pytest.raises(FrozenInstanceError):
        setattr(records, "schedules", ())  # noqa: B010 - frozen runtime guard
    segments = ["personal", "cash"]
    scoped = replace(
        records.schedules[0], account=Ref(CASH.id, Namespace(cast(tuple[str, ...], segments)))
    )
    segments.append("changed")
    assert scoped.account.namespace == Namespace(("personal", "cash"))
    with pytest.raises(ValueError):
        replace(records.schedules[0], account=Ref(Id(SCHEDULE, "not-account")))
    with pytest.raises(ValueError):
        CashRecords(schedules=(records.schedules[0], records.schedules[0]))


def test_observation_snapshots_core_context_metadata_before_retention() -> None:
    caller_values = ["statement", "verified"]
    context = Context(
        as_of=AT,
        source="manual",
        units="USD",
        metadata={"tags": caller_values},
    )
    observed = replace(_observation(), context=context)
    caller_values.append("changed")
    assert observed.context.metadata is not None
    assert observed.context.metadata["tags"] == ("statement", "verified")
    assert observed.as_core_observation().subject == Ref(CASH.id)
    with pytest.raises(TypeError, match="immutable"):
        replace(_observation(), context=Context(as_of=AT, source={"mutable": True}))


def test_no_liquid_account_never_becomes_invented_zero_cash() -> None:
    records = CashRecords(floor_changes=(_floor(),), coverage=(_coverage(),))
    result = _plan(records, entries=(), accounts=(INCOME, EXPENSE))
    assert result.currencies[0].starting_balance is None
    assert result.currencies[0].safe_to_allocate is None
    assert "no eligible liquid account" in result.currencies[0].missing_inputs[0]


def test_aggregate_cash_overflow_fails_explicitly() -> None:
    maximum = 2**63 - 1
    first = _entry(
        1,
        START,
        Posting(Ref(CASH.id), Money(maximum, "USD")),
        Posting(Ref(INCOME.id), Money(-maximum, "USD")),
    )
    second = _entry(
        2,
        START,
        Posting(Ref(SAVINGS.id), Money(maximum, "USD")),
        Posting(Ref(INCOME.id), Money(-maximum, "USD")),
    )
    with pytest.raises(ValueError, match="64-bit"):
        _plan(CashRecords(), entries=(first, second), accounts=(CASH, SAVINGS, INCOME))


def test_retiring_schedule_before_due_cancels_future_flow_without_rewriting_it() -> None:
    bill = _bill()
    retirement = CashRetirement(
        Id(RETIREMENT, "cancel-rent"),
        Ref(bill.id),
        bill.start_date,
        AT,
        "local-human",
        "Duplicate plan",
    )
    records = CashRecords(
        schedules=(bill,),
        retirements=(retirement,),
        floor_changes=(_floor(),),
        observations=(_observation(),),
        coverage=(_coverage(CASH),),
    )
    series = _plan(records).currencies[0]
    assert bill.active
    assert not any(point.kind == "scheduled_outflow" for point in series.points)
    assert series.safe_to_allocate == Money(90_000, "USD")


def test_horizon_coverage_is_checked_separately_for_30_60_and_90_days() -> None:
    records = CashRecords(
        floor_changes=(_floor(),),
        observations=(_observation(),),
        coverage=(_coverage(CASH, days=60),),
    )
    assert _plan(records, days=30).currencies[0].safe_to_allocate == Money(90_000, "USD")
    assert _plan(records, days=60).currencies[0].safe_to_allocate == Money(90_000, "USD")
    ninety = _plan(records, days=90).currencies[0]
    assert ninety.safe_to_allocate is None
    assert any("ends before" in reason for reason in ninety.missing_inputs)


def test_negative_cash_has_known_negative_projection_and_zero_safe_amount() -> None:
    debit = _entry(
        1,
        START,
        Posting(Ref(EXPENSE.id), Money(5_000, "USD")),
        Posting(Ref(CASH.id), Money(-5_000, "USD")),
    )
    records = CashRecords(
        floor_changes=(_floor(0),),
        observations=(_observation(amount=-5_000),),
        coverage=(_coverage(CASH),),
    )
    series = _plan(records, entries=(debit,)).currencies[0]
    assert series.minimum_balance == Money(-5_000, "USD")
    assert series.current_unallocated == Money(-5_000, "USD")
    assert series.safe_to_allocate == Money(0, "USD")
