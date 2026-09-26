"""Deterministic, per-currency cash event stream and safe-allocation policy.

This function has no clock or storage access. The service supplies an explicit
start date, an atomic set of ledger/planning records, and a Core context; it
may wrap this significant derivation in a named Core Transform for provenance.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from core.context import Context
from core.identity import Id
from personal_finance.domain.accounts import Account
from personal_finance.domain.cash import (
    ALLOCATION,
    HOLD,
    SCHEDULE,
    Cadence,
    CashCoverage,
    CashRecords,
    CashSchedule,
    MonthlyPolicy,
)
from personal_finance.domain.ledger import JournalEntry, validate_accounts
from personal_finance.domain.money import Money

CASH_PROJECTION_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class CashPoint:
    """Balance after a single event boundary, including constraints then in force."""

    on: date
    kind: str
    label: str
    delta: Money
    balance: Money
    floor: Money | None
    protected: Money
    holds: Money
    available: Money | None


@dataclass(frozen=True, slots=True)
class CashCurrencyProjection:
    currency: str
    points: tuple[CashPoint, ...]
    starting_balance: Money | None
    minimum_balance: Money | None
    minimum_on: date | None
    current_unallocated: Money | None
    safe_to_allocate: Money | None
    missing_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CashProjection:
    start_on: date
    horizon_days: int
    through_on: date
    currencies: tuple[CashCurrencyProjection, ...]
    context: Context
    missing_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Event:
    on: date
    priority: int
    tie: str
    kind: str
    label: str
    delta: int = 0
    target: Id | None = None


def occurs_on(schedule: CashSchedule, day: date) -> bool:
    """Whether an IANA-framed, date-only schedule has this occurrence."""
    if type(day) is not date:
        raise TypeError("Schedule occurrence requires a date-only value")
    if day < schedule.start_date or (schedule.end_date is not None and day > schedule.end_date):
        return False
    if schedule.cadence is Cadence.ONCE:
        return day == schedule.start_date
    if schedule.cadence is Cadence.WEEKLY:
        return (day - schedule.start_date).days % 7 == 0
    months = (day.year - schedule.start_date.year) * 12 + day.month - schedule.start_date.month
    if months < 0:
        return False
    last = monthrange(day.year, day.month)[1]
    anchor = schedule.start_date.day
    if anchor > last and schedule.monthly_policy is MonthlyPolicy.SKIP:
        return False
    return day.day == min(anchor, last)


def schedule_occurrences(schedule: CashSchedule, after: date, through: date) -> tuple[date, ...]:
    """Dates after the opening point; month recurrence stays anchored to its first day."""
    if type(after) is not date or type(through) is not date:
        raise TypeError("Schedule projection requires date-only boundaries")
    if through <= after or not schedule.active:
        return ()
    first = max(schedule.start_date, after + timedelta(days=1))
    last = min(through, schedule.end_date) if schedule.end_date is not None else through
    if first > last:
        return ()
    if schedule.cadence is Cadence.ONCE:
        return (schedule.start_date,) if first <= schedule.start_date <= last else ()
    if schedule.cadence is Cadence.WEEKLY:
        offset = (first - schedule.start_date).days
        remainder = offset % 7
        candidate = first if remainder == 0 else first + timedelta(days=7 - remainder)
        result: list[date] = []
        while candidate <= last:
            result.append(candidate)
            candidate += timedelta(days=7)
        return tuple(result)
    result = []
    start_month = first.year * 12 + first.month - 1
    end_month = last.year * 12 + last.month - 1
    for month_number in range(start_month, end_month + 1):
        year, zero_month = divmod(month_number, 12)
        month = zero_month + 1
        month_last = monthrange(year, month)[1]
        anchor = schedule.start_date.day
        if anchor > month_last and schedule.monthly_policy is MonthlyPolicy.SKIP:
            continue
        candidate = date(year, month, min(anchor, month_last))
        if first <= candidate <= last:
            result.append(candidate)
    return tuple(result)


def _retirement_dates(records: CashRecords) -> dict[Id, date]:
    known = {
        item.id
        for collection in (records.schedules, records.allocations, records.holds)
        for item in collection
    }
    dates: dict[Id, date] = {}
    for retirement in records.retirements:
        target = retirement.target.id
        if target not in known or target.kind not in (SCHEDULE, ALLOCATION, HOLD):
            raise ValueError("Cash retirement targets an unknown planning record")
        previous = dates.get(target)
        if previous is None or retirement.effective_date < previous:
            dates[target] = retirement.effective_date
    return dates


def _latest_coverage(records: CashRecords, currency: str, start_on: date) -> CashCoverage | None:
    candidates = (
        (position, item)
        for position, item in enumerate(records.coverage)
        if item.currency == currency and item.as_of <= start_on
    )
    latest = max(
        candidates,
        key=lambda pair: (pair[1].as_of, pair[1].recorded_at.value, pair[0]),
        default=None,
    )
    return None if latest is None else latest[1]


def _recorded_account_balances(
    accounts: tuple[Account, ...], entries: tuple[JournalEntry, ...], start_on: date
) -> dict[Id, int]:
    balances = {account.id: 0 for account in accounts}
    for entry in entries:
        if entry.content.effective_date > start_on:
            continue
        for posting in entry.content.postings:
            if posting.account.id in balances:
                balances[posting.account.id] += posting.money.minor
    return balances


def _missing_evidence(
    currency: str,
    accounts: tuple[Account, ...],
    balances: dict[Id, int],
    records: CashRecords,
    start_on: date,
    through_on: date,
) -> list[str]:
    reasons: list[str] = []
    coverage = _latest_coverage(records, currency, start_on)
    if coverage is None:
        reasons.append(f"{currency}: account and schedule coverage not declared")
    else:
        if not coverage.accounts_complete:
            reasons.append(f"{currency}: liquid-account inventory is incomplete")
        if not coverage.schedules_complete:
            reasons.append(f"{currency}: schedule inventory is incomplete")
        if coverage.through_date < through_on:
            reasons.append(f"{currency}: schedule coverage ends before the projection horizon")
        covered = {ref.id for ref in coverage.account_refs}
        eligible = {account.id for account in accounts}
        if covered != eligible:
            reasons.append(
                f"{currency}: coverage account list differs from eligible liquid accounts"
            )
    for account in accounts:
        candidates = [
            (position, item)
            for position, item in enumerate(records.observations)
            if item.account.id == account.id and item.observed_on <= start_on
        ]
        if not candidates:
            reasons.append(f"{currency}: missing balance observation for {account.name}")
            continue
        _, latest = max(
            candidates,
            key=lambda pair: (pair[1].observed_on, pair[1].observed_at.value, pair[0]),
        )
        if latest.observed.currency != currency:
            reasons.append(f"{currency}: balance observation currency differs for {account.name}")
        elif latest.fresh_through < start_on:
            reasons.append(f"{currency}: balance observation is stale for {account.name}")
        elif latest.observed.minor != balances[account.id]:
            reasons.append(f"{currency}: balance observation does not reconcile for {account.name}")
        peers = [
            item
            for _, item in candidates
            if (item.observed_on, item.observed_at.value)
            == (latest.observed_on, latest.observed_at.value)
        ]
        if len({item.observed for item in peers}) > 1:
            reasons.append(f"{currency}: conflicting current observations for {account.name}")
    return reasons


def _point(
    currency: str,
    on: date,
    kind: str,
    label: str,
    delta: int,
    balance: int,
    floor: int | None,
    protected: int,
    holds: int,
) -> CashPoint:
    available = None if floor is None else Money(balance - floor - protected - holds, currency)
    return CashPoint(
        on,
        kind,
        label,
        Money(delta, currency),
        Money(balance, currency),
        None if floor is None else Money(floor, currency),
        Money(protected, currency),
        Money(holds, currency),
        available,
    )


def _project_currency(
    currency: str,
    start_on: date,
    through_on: date,
    accounts: tuple[Account, ...],
    entries: tuple[JournalEntry, ...],
    records: CashRecords,
    retirement_dates: dict[Id, date],
) -> CashCurrencyProjection:
    eligible = tuple(
        account for account in accounts if account.liquid and account.currency == currency
    )
    if not eligible:
        return CashCurrencyProjection(
            currency,
            (),
            None,
            None,
            None,
            None,
            None,
            (f"{currency}: no eligible liquid account is recorded",),
        )
    eligible_ids = {account.id for account in eligible}
    all_accounts = {account.id: account for account in accounts}
    for schedule in records.schedules:
        if schedule.amount.currency != currency:
            continue
        target = all_accounts.get(schedule.account.id)
        if target is None or target.id not in eligible_ids or target.currency != currency:
            raise ValueError("Cash schedule must target an eligible liquid account in its currency")

    balances = _recorded_account_balances(eligible, entries, start_on)
    opening = sum(balances.values())
    reasons = _missing_evidence(currency, eligible, balances, records, start_on, through_on)
    floor_changes = sorted(
        (item for item in records.floor_changes if item.amount.currency == currency),
        key=lambda item: (item.effective_date, item.id.value),
    )
    floor_dates = [item.effective_date for item in floor_changes]
    if len(set(floor_dates)) != len(floor_dates):
        raise ValueError("Multiple cash floors for one currency and effective date")
    opening_floor = next(
        (item.amount.minor for item in reversed(floor_changes) if item.effective_date <= start_on),
        None,
    )
    if opening_floor is None:
        reasons.append(f"{currency}: no cash floor is declared at the starting date")

    schedules = tuple(item for item in records.schedules if item.amount.currency == currency)
    allocations = {
        item.id: item for item in records.allocations if item.amount.currency == currency
    }
    holds = {item.id: item for item in records.holds if item.amount.currency == currency}
    schedule_by_id = {item.id: item for item in schedules}
    for allocation in allocations.values():
        link = allocation.linked_occurrence
        if link is None or not allocation.active:
            continue
        schedule = schedule_by_id.get(link.schedule.id)
        if (
            schedule is None
            or not schedule.active
            or schedule.amount.minor >= 0
            or allocation.active_from > link.due_on
            or not occurs_on(schedule, link.due_on)
            or (
                retirement_dates.get(schedule.id) is not None
                and retirement_dates[schedule.id] <= link.due_on
            )
        ):
            reasons.append(
                f"{currency}: allocation {allocation.label} has no matching active bill occurrence"
            )
        elif link.due_on <= start_on and retirement_dates.get(allocation.id, date.max) > start_on:
            reasons.append(
                f"{currency}: allocation {allocation.label} links to an already due bill"
            )
        elif allocation.amount.minor > -schedule.amount.minor:
            reasons.append(f"{currency}: allocation {allocation.label} exceeds its linked bill")

    active_allocations = {
        item.id
        for item in allocations.values()
        if item.active
        and item.active_from <= start_on
        and retirement_dates.get(item.id, date.max) > start_on
    }
    active_holds = {
        item.id
        for item in holds.values()
        if item.active
        and item.active_from <= start_on
        and (item.release_date is None or item.release_date > start_on)
        and retirement_dates.get(item.id, date.max) > start_on
    }
    protected = sum(allocations[item_id].amount.minor for item_id in active_allocations)
    held = sum(holds[item_id].amount.minor for item_id in active_holds)
    points = [
        _point(
            currency,
            start_on,
            "initial",
            "Recorded opening cash",
            0,
            opening,
            opening_floor,
            protected,
            held,
        )
    ]
    events: list[_Event] = []
    for floor in floor_changes:
        if start_on < floor.effective_date <= through_on:
            events.append(
                _Event(
                    floor.effective_date,
                    0,
                    floor.id.value,
                    "floor_change",
                    "Cash floor change",
                    target=floor.id,
                )
            )
    for allocation in allocations.values():
        if allocation.active and start_on < allocation.active_from <= through_on:
            events.append(
                _Event(
                    allocation.active_from,
                    1,
                    allocation.id.value,
                    "allocation_start",
                    allocation.label,
                    target=allocation.id,
                )
            )
    for hold in holds.values():
        if hold.active and start_on < hold.active_from <= through_on:
            events.append(
                _Event(hold.active_from, 1, hold.id.value, "hold_start", hold.label, target=hold.id)
            )
        if hold.release_date is not None and start_on < hold.release_date <= through_on:
            events.append(
                _Event(
                    hold.release_date, 3, hold.id.value, "hold_release", hold.label, target=hold.id
                )
            )
    for retirement in records.retirements:
        if start_on < retirement.effective_date <= through_on and retirement.target.id in (
            set(allocations) | set(holds) | set(schedule_by_id)
        ):
            events.append(
                _Event(
                    retirement.effective_date,
                    3,
                    retirement.id.value,
                    "retirement",
                    retirement.reason,
                    target=retirement.target.id,
                )
            )

    scheduled_keys: set[tuple[date, Id, int]] = set()
    for schedule in schedules:
        if not schedule.active:
            continue
        if occurs_on(schedule, start_on) and retirement_dates.get(schedule.id, date.max) > start_on:
            reasons.append(f"{currency}: schedule {schedule.label} is due today; resolve it first")
        for due_on in schedule_occurrences(schedule, start_on, through_on):
            if retirement_dates.get(schedule.id, date.max) <= due_on:
                continue
            kind = "scheduled_outflow" if schedule.amount.minor < 0 else "scheduled_inflow"
            priority = 2 if schedule.amount.minor < 0 else 4
            events.append(
                _Event(
                    due_on,
                    priority,
                    schedule.id.value,
                    kind,
                    schedule.label,
                    schedule.amount.minor,
                    schedule.id,
                )
            )
            scheduled_keys.add((due_on, schedule.account.id, schedule.amount.minor))
    for entry in entries:
        day = entry.content.effective_date
        if not start_on < day <= through_on:
            continue
        liquid_postings = [
            post for post in entry.content.postings if post.account.id in eligible_ids
        ]
        delta = sum(post.money.minor for post in liquid_postings)
        if delta == 0:
            continue  # An internal liquid-to-liquid transfer does not change operating cash.
        kind = "recorded_outflow" if delta < 0 else "recorded_inflow"
        events.append(
            _Event(
                day,
                2 if delta < 0 else 4,
                f"{entry.sequence:020d}",
                kind,
                entry.content.description,
                delta,
                entry.id,
            )
        )
        if any(
            (day, post.account.id, post.money.minor) in scheduled_keys for post in liquid_postings
        ):
            reasons.append(
                f"{currency}: a future posted entry may duplicate a schedule on {day.isoformat()}"
            )

    floor_by_id = {item.id: item for item in floor_changes}
    current_floor = opening_floor
    balance = opening
    for event in sorted(events, key=lambda item: (item.on, item.priority, item.tie, item.kind)):
        if event.kind == "floor_change" and event.target is not None:
            current_floor = floor_by_id[event.target].amount.minor
        elif event.kind == "allocation_start" and event.target is not None:
            if retirement_dates.get(event.target, date.max) > event.on:
                active_allocations.add(event.target)
        elif event.kind == "hold_start" and event.target is not None:
            hold = holds[event.target]
            if retirement_dates.get(event.target, date.max) > event.on and (
                hold.release_date is None or hold.release_date > event.on
            ):
                active_holds.add(event.target)
        elif event.kind == "hold_release" and event.target is not None:
            active_holds.discard(event.target)
        elif event.kind == "retirement" and event.target is not None:
            if event.target.kind == ALLOCATION:
                active_allocations.discard(event.target)
            elif event.target.kind == HOLD:
                active_holds.discard(event.target)
        balance += event.delta
        if event.kind == "scheduled_outflow" and event.target is not None:
            # The bill consumes cash and its earmark at the same boundary.
            for item_id in tuple(active_allocations):
                link = allocations[item_id].linked_occurrence
                if (
                    link is not None
                    and link.schedule.id == event.target
                    and link.due_on == event.on
                ):
                    active_allocations.remove(item_id)
        protected = sum(allocations[item_id].amount.minor for item_id in active_allocations)
        held = sum(holds[item_id].amount.minor for item_id in active_holds)
        points.append(
            _point(
                currency,
                event.on,
                event.kind,
                event.label,
                event.delta,
                balance,
                current_floor,
                protected,
                held,
            )
        )

    lowest = min(points, key=lambda item: item.balance.minor)
    missing_inputs = tuple(dict.fromkeys(reasons))
    unallocated = (
        None
        if missing_inputs
        else Money(opening - points[0].protected.minor - points[0].holds.minor, currency)
    )
    safe = None
    if not missing_inputs:
        free_low = min(point.available.minor for point in points if point.available is not None)
        assert unallocated is not None
        safe = Money(max(0, min(unallocated.minor, free_low)), currency)
    return CashCurrencyProjection(
        currency,
        tuple(points),
        Money(opening, currency),
        lowest.balance,
        lowest.on,
        unallocated,
        safe,
        missing_inputs,
    )


def project_cash(
    start_on: date,
    horizon_days: int,
    accounts: tuple[Account, ...],
    entries: tuple[JournalEntry, ...],
    records: CashRecords,
    context: Context,
) -> CashProjection:
    """Project 30/60/90 days, withholding safe cash until all evidence is sound.

    Holdings and earmarks are constraints, never cash movements. A matching
    linked bill releases its earmark atomically with the scheduled outflow.
    The event stream starts at the end of ``start_on``; an unresolved schedule
    due on that date makes safe allocation unavailable.
    """
    if (
        type(start_on) is not date
        or type(horizon_days) is not int
        or horizon_days not in (30, 60, 90)
    ):
        raise ValueError("Cash projection requires a date and a 30/60/90-day horizon")
    if type(context) is not Context or type(records) is not CashRecords:
        raise TypeError("Cash projection requires Core Context and CashRecords")
    try:
        through_on = start_on + timedelta(days=horizon_days)
    except OverflowError as exc:
        raise ValueError("Cash projection horizon exceeds the supported date range") from exc
    account_values = tuple(accounts)
    entry_values = tuple(entries)
    if any(type(item) is not Account for item in account_values):
        raise TypeError("Cash projection accounts must be Account records")
    if any(type(item) is not JournalEntry for item in entry_values):
        raise TypeError("Cash projection entries must be JournalEntry records")
    if len({account.id for account in account_values}) != len(account_values):
        raise ValueError("Cash projection account identities must be unique")
    if len({entry.id for entry in entry_values}) != len(entry_values):
        raise ValueError("Cash projection entry identities must be unique")
    for entry in entry_values:
        validate_accounts(entry.content, account_values)
    currencies = sorted(
        {account.currency for account in account_values if account.liquid}
        | {item.amount.currency for item in records.schedules}
        | {item.amount.currency for item in records.allocations}
        | {item.amount.currency for item in records.holds}
        | {item.amount.currency for item in records.floor_changes}
        | {item.currency for item in records.coverage}
    )
    retirement_dates = _retirement_dates(records)
    series = tuple(
        _project_currency(
            currency, start_on, through_on, account_values, entry_values, records, retirement_dates
        )
        for currency in currencies
    )
    if not series:
        missing = ("No eligible liquid account or cash-planning currency is recorded",)
    else:
        missing = tuple(reason for item in series for reason in item.missing_inputs)
    return CashProjection(start_on, horizon_days, through_on, series, context, missing)
