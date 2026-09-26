"""Cash migration, immutable record roundtrips, and database boundary attacks."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.provenance import Provenance
from core.time import Duration, WallInstant
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore, SQLiteUnitOfWork
from personal_finance.application.cash_codec import cash_record_hash, encode_cash_record
from personal_finance.application.ports import FinanceFailure, StaleProjection
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
from personal_finance.domain.codec import decode_ref, encode_id, encode_ref
from personal_finance.domain.kinds import ACCOUNT
from personal_finance.domain.money import Money

DAY = date(2026, 9, 24)
AT = WallInstant(datetime(2026, 9, 24, 12, tzinfo=UTC))
CASH = Account(Id(ACCOUNT, "checking"), "Checking", AccountType.ASSET, "USD", True)
INCOME = Account(Id(ACCOUNT, "salary"), "Salary", AccountType.INCOME, "USD")
SCOPE = Namespace(("profiles", "household"))


def _records() -> CashRecords:
    schedule = CashSchedule(
        Id(SCHEDULE, "rent"),
        Ref(CASH.id, SCOPE),
        Money(-120_000, "USD"),
        date(2026, 10, 1),
        Cadence.MONTHLY,
        "America/New_York",
        "Rent",
        "manual",
        date(2027, 9, 1),
        MonthlyPolicy.CLAMP,
    )
    allocation = CashAllocation(
        Id(ALLOCATION, "rent-envelope"),
        Money(120_000, "USD"),
        "Rent envelope",
        DAY,
        ScheduleOccurrence(Ref(schedule.id, SCOPE), date(2026, 10, 1)),
    )
    hold = CashHold(Id(HOLD, "bank-hold"), Money(4_000, "USD"), "Bank hold", DAY)
    floor = CashFloorChange(Id(FLOOR_CHANGE, "minimum"), DAY, Money(10_000, "USD"))
    observation = CashBalanceObservation(
        Id(OBSERVATION, "checked-balance"),
        Ref(CASH.id, SCOPE),
        Money(250_000, "USD"),
        AT,
        DAY,
        date(2026, 9, 27),
        "manual statement",
        Context(
            as_of=AT,
            namespace=SCOPE,
            source="manual statement",
            authority="observed",
            version="1",
            units="USD",
            scope=("account", "checking"),
            metadata={"confidence": "reviewed"},
        ),
    )
    coverage = CashCoverage(
        Id(COVERAGE, "inventory"),
        "USD",
        DAY,
        date(2026, 12, 23),
        True,
        True,
        (Ref(CASH.id, SCOPE),),
        AT,
        "local declaration",
    )
    retirement = CashRetirement(
        Id(RETIREMENT, "bank-release"),
        Ref(hold.id, SCOPE),
        date(2026, 9, 28),
        AT,
        "local-human",
        "Hold settled",
    )
    return CashRecords(
        (schedule,),
        (allocation,),
        (hold,),
        (floor,),
        (observation,),
        (coverage,),
        (retirement,),
    )


def _store(tmp_path: Path) -> FinanceStore:
    store = FinanceStore(tmp_path / "cash.sqlite3")
    store.initialize()
    with store.transaction() as unit:
        unit.add_account(CASH)
        unit.add_account(INCOME)
    return store


def _save_all(store: FinanceStore, records: CashRecords) -> None:
    with store.transaction() as unit:
        unit.add_cash_schedule(records.schedules[0])
        unit.add_cash_allocation(records.allocations[0])
        unit.add_cash_hold(records.holds[0])
        unit.add_cash_floor_change(records.floor_changes[0])
        unit.add_cash_observation(records.observations[0])
        unit.add_cash_coverage(records.coverage[0])
        unit.add_cash_retirement(records.retirements[0])


def test_cash_records_roundtrip_full_core_references_and_reopen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = _records()
    before = store.inputs().revision
    _save_all(store, records)
    inputs = store.inputs()
    assert inputs.accounts == (CASH, INCOME) or set(inputs.accounts) == {CASH, INCOME}
    assert inputs.cash == records
    assert inputs.revision > before
    assert store.cash_records() == records
    payload = json.loads(encode_cash_record(records.schedules[0]))
    assert decode_ref(payload["account"]) == Ref(CASH.id, SCOPE)
    with store.transaction() as unit:
        row = (
            cast(SQLiteUnitOfWork, unit)
            .connection.execute(
                "SELECT content_hash FROM cash_schedules WHERE id=?",
                (encode_id(records.schedules[0].id),),
            )
            .fetchone()
        )
        assert row[0] == cash_record_hash(records.schedules[0])
    reopened = FinanceStore(store.path)
    reopened.initialize()
    reopened.initialize()
    assert reopened.inputs().cash == records


def test_upgrade_from_v2_is_transactional_and_rerunnable(tmp_path: Path) -> None:
    store = FinanceStore(tmp_path / "old.sqlite3")
    store.initialize()
    # Remove only the v3 objects to reproduce an existing v2 file. The v1/v2
    # ledger schema and migration records remain exactly as initialized.
    connection = sqlite3.connect(store.path)
    try:
        for table in (
            "cash_coverage_accounts",
            "cash_projection_cache",
            "cash_retirements",
            "cash_coverage",
            "cash_observations",
            "cash_floor_changes",
            "cash_holds",
            "cash_allocations",
            "cash_schedules",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version=3")
        connection.commit()
    finally:
        connection.close()
    store.initialize()
    store.initialize()
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        versions = tuple(
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        )
        assert versions == (1, 2, 3, 4, 5, 6)
    assert store.inputs().cash == CashRecords()


def test_cash_history_is_append_only_and_raw_inserts_require_unit_of_work(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = _records()
    _save_all(store, records)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "UPDATE cash_holds SET label='tampered' WHERE id=?",
                (encode_id(records.holds[0].id),),
            )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "DELETE FROM cash_retirements WHERE id=?",
                (encode_id(records.retirements[0].id),),
            )
    with pytest.raises(FinanceFailure, match="trusted finance unit of work"):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "INSERT INTO cash_floor_changes(id,effective_date,amount_minor,currency,"
                "content_hash) VALUES(?,?,?,?,?)",
                (encode_id(Id(FLOOR_CHANGE, "raw")), DAY.isoformat(), 1, "USD", "0" * 64),
            )
    assert store.cash_records() == records


def test_cash_sql_constraints_reject_wrong_account_and_reference(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = _records()
    wrong_currency = CashSchedule(
        Id(SCHEDULE, "wrong-currency"),
        Ref(CASH.id),
        Money(100, "EUR"),
        DAY,
        Cadence.ONCE,
        "UTC",
        "Wrong currency",
    )
    with pytest.raises(FinanceFailure, match="liquid account"):
        with store.transaction() as unit:
            unit.add_cash_schedule(wrong_currency)
    nonliquid = CashSchedule(
        Id(SCHEDULE, "not-liquid"),
        Ref(INCOME.id),
        Money(100, "USD"),
        DAY,
        Cadence.ONCE,
        "UTC",
        "Not liquid",
    )
    with pytest.raises(FinanceFailure, match="liquid account"):
        with store.transaction() as unit:
            unit.add_cash_schedule(nonliquid)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            concrete = cast(SQLiteUnitOfWork, unit)
            concrete.connection.create_function("finance_cash_authorized", 0, lambda: 1)
            concrete.connection.execute(
                "INSERT INTO cash_schedules(id,account_id,account_ref,amount_minor,currency,"
                "start_date,cadence,timezone,label,source,monthly_policy,active,content_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    encode_id(Id(SCHEDULE, "bad-ref")),
                    encode_id(CASH.id),
                    encode_ref(Ref(INCOME.id)),
                    100,
                    "USD",
                    DAY.isoformat(),
                    "once",
                    "UTC",
                    "Bad reference",
                    "manual",
                    "clamp_to_last_day",
                    1,
                    "0" * 64,
                ),
            )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            concrete = cast(SQLiteUnitOfWork, unit)
            concrete.connection.create_function("finance_cash_authorized", 0, lambda: 1)
            concrete.connection.execute(
                "INSERT INTO cash_floor_changes(id,effective_date,amount_minor,currency,"
                "content_hash) VALUES(?,?,?,?,?)",
                (encode_id(Id(FLOOR_CHANGE, "bad-day")), "not-a-date", 1, "USD", "0" * 64),
            )
    with store.transaction() as unit:
        unit.add_cash_hold(records.holds[0])
        unit.add_cash_retirement(records.retirements[0])
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            unit.add_cash_retirement(
                CashRetirement(
                    Id(RETIREMENT, "duplicate"),
                    Ref(records.holds[0].id),
                    date(2026, 9, 29),
                    AT,
                    "local-human",
                    "Second release",
                )
            )


def test_cash_mutation_rolls_back_and_projection_rejects_stale_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = _records()
    revision = store.inputs().revision
    with pytest.raises(FinanceFailure, match="forced"):
        with store.transaction() as unit:
            unit.add_cash_hold(records.holds[0])
            raise FinanceFailure("forced late failure")
    assert store.cash_records() == CashRecords()
    with store.transaction() as unit:
        unit.add_cash_hold(records.holds[0])
    provenance = Provenance(
        id=Id(Kind("core.provenance"), "cash-1"),
        transform_id=Id(Kind("core.transform"), "cash-projection"),
        transform_name="Cash projection",
        transform_version="1",
        inputs=(Ref(records.holds[0].id, SCOPE),),
        parents=(),
        at=AT,
        duration=Duration(0),
    )
    with pytest.raises(StaleProjection):
        with store.transaction() as unit:
            unit.save_cash_projection(revision, 30, "{}", provenance)
    current = store.inputs().revision
    with store.transaction() as unit:
        unit.save_cash_projection(current, 30, '{"known":false}', provenance)
    with store.transaction() as unit:
        row = (
            cast(SQLiteUnitOfWork, unit)
            .connection.execute(
                "SELECT payload FROM cash_projection_cache WHERE revision=? AND horizon=30",
                (current,),
            )
            .fetchone()
        )
        assert row[0] == '{"known":false}'


def test_ledger_and_cash_inputs_share_one_read_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    hold = _records().holds[0]
    before = store.inputs().revision
    original_accounts = SQLiteUnitOfWork.accounts
    inserted = False

    def interleaved_accounts(unit: SQLiteUnitOfWork) -> tuple[Account, ...]:
        nonlocal inserted
        result = original_accounts(unit)
        if not inserted:
            inserted = True
            with store.transaction() as writer:
                writer.add_cash_hold(hold)
        return result

    monkeypatch.setattr(SQLiteUnitOfWork, "accounts", interleaved_accounts)
    snapshot = store.inputs()
    assert snapshot.revision == before
    assert snapshot.cash.holds == ()
    refreshed = store.inputs()
    assert refreshed.revision > before
    assert refreshed.cash.holds == (hold,)
