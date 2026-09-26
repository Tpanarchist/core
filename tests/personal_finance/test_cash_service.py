"""Cash planning stays reviewable, persisted, and tied to one ledger revision."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from core.identity import Id, Ref
from core.result import Err, Ok
from core.time import MonotonicInstant, WallInstant
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.application.service import FinanceService
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.cash import Cadence, ScheduleOccurrence
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money

TODAY = date(2026, 9, 21)


class FixedClock:
    def __init__(self) -> None:
        self.tick = 0

    def now(self) -> WallInstant:
        value = WallInstant(datetime(2026, 9, 21, 12, tzinfo=UTC) + timedelta(seconds=self.tick))
        self.tick += 1
        return value


class FixedMonotonic:
    space = Id(Kind("test.clock"), "cash-service")

    def now(self) -> MonotonicInstant:
        return MonotonicInstant(self.space, 0)


class FixedIds:
    def __init__(self, prefix: str) -> None:
        self.counter = 0
        self.prefix = prefix

    def new(self, kind: Kind) -> Id:
        self.counter += 1
        return Id(kind, f"{self.prefix}-{self.counter}")


def service_at(path: Path, prefix: str = "cash-service") -> FinanceService:
    store = FinanceStore(path)
    store.initialize()
    return FinanceService(store, FixedClock(), FixedMonotonic(), FixedIds(prefix))


def opening_cash(service: FinanceService) -> Id:
    cash = service.create_account("Checking", AccountType.ASSET, "USD", True)
    equity = service.create_account("Opening equity", AccountType.EQUITY, "USD")
    assert isinstance(cash, Ok) and isinstance(equity, Ok)
    content = EntryContent(
        TODAY - timedelta(days=1),
        "Observed opening balance",
        (
            Posting(Ref(cash.value.id), Money(100_000, "USD")),
            Posting(Ref(equity.value.id), Money(-100_000, "USD")),
        ),
    )
    prepared = service.prepare_entry(content)
    assert isinstance(prepared, Ok)
    assert isinstance(
        service.post_draft(prepared.value.id, prepared.value.content_hash, "open"), Ok
    )
    return cash.value.id


def test_cash_plan_requires_coverage_observation_floor_and_reconciled_balance(
    tmp_path: Path,
) -> None:
    service = service_at(tmp_path / "cash.sqlite3")
    cash_id = opening_cash(service)
    before = service.snapshot()
    assert isinstance(before, Ok)
    assert len(before.value.cash_plans) == 3
    assert before.value.cash_plans[0].projection.currencies[0].safe_to_allocate is None
    assert any(
        "coverage" in reason for reason in before.value.cash_plans[0].projection.missing_inputs
    )

    assert isinstance(service.add_cash_floor("USD", TODAY, Money(0, "USD")), Ok)
    assert isinstance(
        service.declare_cash_coverage(
            "USD", TODAY, TODAY + timedelta(days=90), True, True, "manual"
        ),
        Ok,
    )
    assert isinstance(
        service.record_cash_balance(
            cash_id, Money(99_999, "USD"), TODAY, TODAY + timedelta(days=7), "manual"
        ),
        Ok,
    )
    mismatch = service.snapshot()
    assert isinstance(mismatch, Ok)
    assert mismatch.value.cash_plans[0].projection.currencies[0].safe_to_allocate is None
    assert any(
        "does not reconcile" in reason
        for reason in mismatch.value.cash_plans[0].projection.missing_inputs
    )

    assert isinstance(
        service.record_cash_balance(
            cash_id, Money(100_000, "USD"), TODAY, TODAY + timedelta(days=7), "manual"
        ),
        Ok,
    )
    ready = service.snapshot()
    assert isinstance(ready, Ok)
    assert [view.projection.horizon_days for view in ready.value.cash_plans] == [30, 60, 90]
    for view in ready.value.cash_plans:
        currency = view.projection.currencies[0]
        assert currency.safe_to_allocate == Money(100_000, "USD")
        assert view.provenance.parents == (Ref(ready.value.provenance.id),)
        assert any(ref.id == cash_id for ref in view.provenance.inputs)


def test_linked_bill_reservation_hold_floor_and_retirement_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "cash.sqlite3"
    service = service_at(path)
    cash_id = opening_cash(service)
    floor = service.add_cash_floor("USD", TODAY, Money(10_000, "USD"))
    assert isinstance(floor, Ok)
    assert isinstance(
        service.add_cash_floor("USD", TODAY + timedelta(days=3), Money(20_000, "USD")), Ok
    )
    assert isinstance(
        service.record_cash_balance(
            cash_id, Money(100_000, "USD"), TODAY, TODAY + timedelta(days=7), "manual"
        ),
        Ok,
    )
    rent_day = TODAY + timedelta(days=5)
    schedule = service.add_cash_schedule(
        cash_id, "Rent", Money(-30_000, "USD"), rent_day, Cadence.ONCE, "America/New_York"
    )
    assert isinstance(schedule, Ok)
    assert isinstance(
        service.declare_cash_coverage(
            "USD", TODAY, TODAY + timedelta(days=90), True, True, "manual"
        ),
        Ok,
    )
    allocation = service.add_cash_allocation(
        "Rent reserve",
        Money(30_000, "USD"),
        TODAY,
        ScheduleOccurrence(Ref(schedule.value.id), rent_day),
    )
    assert isinstance(allocation, Ok)
    hold = service.add_cash_hold("Card authorization", Money(5_000, "USD"), TODAY)
    assert isinstance(hold, Ok)
    projected = service.snapshot()
    assert isinstance(projected, Ok)
    first = projected.value.cash_plans[0].projection.currencies[0]
    assert first.safe_to_allocate == Money(45_000, "USD")
    rent_points = [point for point in first.points if point.kind == "scheduled_outflow"]
    assert len(rent_points) == 1
    assert rent_points[0].balance == Money(70_000, "USD")
    assert rent_points[0].protected == Money(0, "USD")
    assert rent_points[0].holds == Money(5_000, "USD")

    reopened = service_at(path, "cash-reopened")
    repeat = reopened.snapshot()
    assert isinstance(repeat, Ok)
    assert repeat.value.cash_plans[0].projection.currencies[0].safe_to_allocate == Money(
        45_000, "USD"
    )
    retired = reopened.retire_cash_item(hold.value.id, TODAY + timedelta(days=1), "Released")
    assert isinstance(retired, Ok)
    after = reopened.snapshot()
    assert isinstance(after, Ok)
    assert after.value.cash_plans[0].projection.currencies[0].safe_to_allocate == Money(
        50_000, "USD"
    )
    # The initial hold still appears in history; release raises the tighter
    # later floor/bill boundary without rewriting earlier points.
    assert any(
        point.kind == "retirement"
        for point in after.value.cash_plans[0].projection.currencies[0].points
    )


def test_invalid_linked_allocation_and_currency_are_rejected(tmp_path: Path) -> None:
    service = service_at(tmp_path / "cash.sqlite3")
    cash_id = opening_cash(service)
    wrong_currency = service.add_cash_schedule(
        cash_id, "Wrong", Money(-100, "EUR"), TODAY, Cadence.ONCE, "UTC"
    )
    assert isinstance(wrong_currency, Err)
    inflow = service.add_cash_schedule(
        cash_id, "Income", Money(10_000, "USD"), TODAY + timedelta(days=5), Cadence.ONCE, "UTC"
    )
    assert isinstance(inflow, Ok)
    invalid = service.add_cash_allocation(
        "No bill",
        Money(1_000, "USD"),
        TODAY,
        ScheduleOccurrence(Ref(inflow.value.id), TODAY + timedelta(days=5)),
    )
    assert isinstance(invalid, Err)
    assert not service.cash_records().allocations


def test_retired_bill_allocation_can_be_replaced_before_due_date(tmp_path: Path) -> None:
    service = service_at(tmp_path / "cash.sqlite3")
    cash_id = opening_cash(service)
    due_on = TODAY + timedelta(days=10)
    schedule = service.add_cash_schedule(
        cash_id, "Rent", Money(-30_000, "USD"), due_on, Cadence.ONCE, "UTC"
    )
    assert isinstance(schedule, Ok)
    occurrence = ScheduleOccurrence(Ref(schedule.value.id), due_on)
    old = service.add_cash_allocation("Old reserve", Money(30_000, "USD"), TODAY, occurrence)
    assert isinstance(old, Ok)
    assert isinstance(
        service.retire_cash_item(old.value.id, TODAY + timedelta(days=1), "Replaced reserve"),
        Ok,
    )
    replacement = service.add_cash_allocation(
        "New reserve", Money(30_000, "USD"), TODAY + timedelta(days=1), occurrence
    )
    assert isinstance(replacement, Ok)
    assert len(service.cash_records().allocations) == 2


def test_schedule_changes_require_a_new_coverage_review(tmp_path: Path) -> None:
    service = service_at(tmp_path / "cash.sqlite3")
    cash_id = opening_cash(service)
    assert isinstance(service.add_cash_floor("USD", TODAY, Money(0, "USD")), Ok)
    assert isinstance(
        service.record_cash_balance(
            cash_id, Money(100_000, "USD"), TODAY, TODAY + timedelta(days=7), "manual"
        ),
        Ok,
    )
    through = TODAY + timedelta(days=90)
    assert isinstance(
        service.declare_cash_coverage("USD", TODAY, through, True, True, "reviewed"), Ok
    )
    schedule = service.add_cash_schedule(
        cash_id, "Bill", Money(-10_000, "USD"), TODAY + timedelta(days=5), Cadence.ONCE, "UTC"
    )
    assert isinstance(schedule, Ok)
    assert not service.cash_records().coverage[-1].schedules_complete
    stale = service.snapshot()
    assert isinstance(stale, Ok)
    assert stale.value.cash_plans[0].projection.currencies[0].safe_to_allocate is None
    assert any(
        "schedule inventory" in item for item in stale.value.cash_plans[0].projection.missing_inputs
    )

    assert isinstance(
        service.declare_cash_coverage("USD", TODAY, through, True, True, "reviewed again"), Ok
    )
    fresh = service.snapshot()
    assert isinstance(fresh, Ok)
    assert fresh.value.cash_plans[0].projection.currencies[0].safe_to_allocate == Money(
        90_000, "USD"
    )
    assert isinstance(service.retire_cash_item(schedule.value.id, TODAY, "Cancelled"), Ok)
    stale_after_retirement = service.snapshot()
    assert isinstance(stale_after_retirement, Ok)
    assert (
        stale_after_retirement.value.cash_plans[0].projection.currencies[0].safe_to_allocate is None
    )
