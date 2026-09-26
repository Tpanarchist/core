"""Real SQLite service transactions, exact approvals, races, and retained projections."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest

from core.error import Error
from core.identity import Id, Ref
from core.result import Err, Ok, Result
from core.time import MonotonicInstant, WallInstant
from core.value import UNKNOWN, Kind
from personal_finance.adapters.sqlite import FinanceStore, SQLiteUnitOfWork
from personal_finance.application.ports import LedgerInputs
from personal_finance.application.service import FinanceService
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.codec import content_hash
from personal_finance.domain.kinds import ACTION_DRAFT
from personal_finance.domain.ledger import Draft, EntryContent, JournalEntry, Posting
from personal_finance.domain.money import Money

DAY = date(2026, 9, 21)
AT = WallInstant(datetime(2026, 9, 21, 12, tzinfo=UTC))


class FixedClock:
    def now(self) -> WallInstant:
        return AT


class FixedMonotonic:
    space = Id(Kind("test.clock"), "service")

    def now(self) -> MonotonicInstant:
        return MonotonicInstant(self.space, 7)


class DeterministicIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def new(self, kind: Kind) -> Id:
        self.count += 1
        return Id(kind, f"{self.prefix}-{self.count}")


def _service(store: FinanceStore, prefix: str = "main") -> FinanceService:
    return FinanceService(store, FixedClock(), FixedMonotonic(), DeterministicIds(prefix))


@pytest.fixture
def finance(tmp_path: Path) -> tuple[FinanceService, FinanceStore, Account, Account]:
    store = FinanceStore(tmp_path / "service.sqlite3")
    store.initialize()
    service = _service(store)
    cash = service.create_account("Checking", AccountType.ASSET, "USD", True).unwrap()
    income = service.create_account("Salary", AccountType.INCOME, "USD").unwrap()
    return service, store, cash, income


def _content(cash: Account, income: Account, minor: int = 12345) -> EntryContent:
    return EntryContent(
        DAY,
        "Salary",
        (
            Posting(Ref(cash.id), Money(minor, "USD")),
            Posting(Ref(income.id), Money(-minor, "USD")),
        ),
    )


def _counts(store: FinanceStore) -> tuple[int, ...]:
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        return tuple(
            cast(int, connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("entries", "approvals", "idempotency", "audit")
        )


def test_preparation_has_no_posted_ledger_effect_and_hash_mismatch_cannot_commit(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    service, store, cash, income = finance
    draft = service.prepare_entry(_content(cash, income)).unwrap()
    assert service.entries() == ()
    before = _counts(store)
    rejected = service.post_draft(draft.id, "0" * 64, "review-mismatch")
    assert isinstance(rejected, Err)
    assert rejected.error.operation == "post_draft"
    assert _counts(store) == before
    with store.transaction() as unit:
        effects = cast(SQLiteUnitOfWork, unit).connection.execute("SELECT payload FROM effects")
        assert all('"kind":"finance.ledger.entry_posted"' not in row[0] for row in effects)


def test_commit_recomputes_hash_of_persisted_content(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    service, store, cash, income = finance
    content = _content(cash, income)
    reviewed_hash = content_hash(replace(content, description="Different content"))
    poisoned = Draft(Id(ACTION_DRAFT, "stored-hash-mismatch"), content, AT, reviewed_hash)
    with store.transaction() as unit:
        unit.add_draft(poisoned)
    result = service.post_draft(poisoned.id, reviewed_hash, "poisoned-draft")
    assert isinstance(result, Err)
    assert "hash" in result.error.message.lower()
    assert service.entries() == ()
    assert _counts(store)[:3] == (0, 0, 0)


def test_replay_returns_original_and_changed_request_cannot_reuse_key(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    service, store, cash, income = finance
    first = service.prepare_entry(_content(cash, income)).unwrap()
    posted = service.post_draft(first.id, first.content_hash, "same-key").unwrap()
    before = _counts(store)
    assert service.post_draft(first.id, first.content_hash, "same-key").unwrap() == posted
    assert _counts(store) == before
    second = service.prepare_entry(_content(cash, income, 500)).unwrap()
    changed = service.post_draft(second.id, second.content_hash, "same-key")
    assert isinstance(changed, Err)
    assert "different" in changed.error.message.lower()
    assert len(service.entries()) == 1
    assert _counts(store)[:3] == (1, 1, 1)


@pytest.mark.parametrize("same_key", [True, False])
def test_independent_concurrent_clients_cannot_post_the_same_draft_twice(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
    same_key: bool,
) -> None:
    service, store, cash, income = finance
    draft = service.prepare_entry(_content(cash, income)).unwrap()
    barrier = Barrier(2)

    def commit(index: int) -> Result[JournalEntry, Error]:
        client = _service(FinanceStore(store.path), f"client-{index}")
        barrier.wait(timeout=5)
        key = "concurrent" if same_key else f"concurrent-{index}"
        return client.post_draft(draft.id, draft.content_hash, key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(commit, (1, 2)))
    successes = [result.value for result in results if isinstance(result, Ok)]
    assert len(successes) == (2 if same_key else 1)
    assert len({entry.id for entry in successes}) == 1
    assert len(store.entries()) == 1
    assert _counts(store)[:3] == (1, 1, 1)


def test_reversal_and_recorded_projection_survive_reopen_with_resolvable_provenance(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    service, store, cash, income = finance
    draft = service.prepare_entry(_content(cash, income)).unwrap()
    original = service.post_draft(draft.id, draft.content_hash, "original").unwrap()
    snapshot = service.snapshot().unwrap()
    balances = {balance.account.id: balance for balance in snapshot.balances}
    assert balances[cash.id].money == Money(12345, "USD")
    assert balances[income.id].money == Money(12345, "USD")
    assert balances[cash.id].state.subject == Ref(cash.id)
    assert snapshot.actual_net_worth is UNKNOWN
    assert Ref(original.id) in snapshot.provenance.inputs
    reopened_store = FinanceStore(store.path)
    reopened_store.initialize()
    assert reopened_store.provenance(snapshot.provenance.id) == snapshot.provenance
    reopened = _service(reopened_store, "reopened")
    reversal_draft = reopened.prepare_reversal(original.id, date(2026, 9, 22)).unwrap()
    reversal = reopened.post_draft(
        reversal_draft.id, reversal_draft.content_hash, "reversal"
    ).unwrap()
    assert reversal.content.reversal_of == Ref(original.id)
    after = reopened.snapshot().unwrap()
    assert all(balance.money.minor == 0 for balance in after.balances)
    assert {entry.id for entry in after.entries} == {original.id, reversal.id}
    assert store.entries()[-1] == original


def test_closing_a_period_after_review_rejects_commit_then_allows_open_correction(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    service, store, cash, income = finance
    draft = service.prepare_entry(_content(cash, income)).unwrap()
    original = service.post_draft(draft.id, draft.content_hash, "original").unwrap()
    reversal = service.prepare_reversal(original.id, DAY).unwrap()
    with store.transaction() as unit:
        cast(SQLiteUnitOfWork, unit).connection.execute(
            "INSERT INTO closed_periods VALUES('2026-09-01','2026-09-30','Monthly close')"
        )
    before = _counts(store)
    assert isinstance(service.post_draft(reversal.id, reversal.content_hash, "closed"), Err)
    assert _counts(store) == before
    open_draft = service.prepare_reversal(original.id, date(2026, 10, 1)).unwrap()
    assert isinstance(service.post_draft(open_draft.id, open_draft.content_hash, "open"), Ok)
    assert all(balance.money.minor == 0 for balance in service.snapshot().unwrap().balances)


class MutatingInputsStore(FinanceStore):
    """Real commit between the snapshot read and projection persistence."""

    after_read: Callable[[], None] | None = None

    def inputs(self) -> LedgerInputs:
        captured = super().inputs()
        if self.after_read is not None:
            self.after_read()
        return captured


def test_projection_retries_stale_inputs_and_never_persists_the_obsolete_revision(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    writer, store, cash, income = finance
    reader_store = MutatingInputsStore(store.path)
    reader = _service(reader_store, "reader")

    def post_between_read_and_save() -> None:
        reader_store.after_read = None
        draft = writer.prepare_entry(_content(cash, income)).unwrap()
        writer.post_draft(draft.id, draft.content_hash, "race-writer").unwrap()

    reader_store.after_read = post_between_read_and_save
    snapshot = reader.snapshot().unwrap()
    assert snapshot.revision == store.inputs().revision
    assert len(snapshot.entries) == 1
    assert all(balance.money.minor == 12345 for balance in snapshot.balances)
    with store.transaction() as unit:
        rows = (
            cast(SQLiteUnitOfWork, unit)
            .connection.execute("SELECT revision FROM projection_cache")
            .fetchall()
        )
        assert [row[0] for row in rows] == [snapshot.revision]


def test_repeated_projection_races_return_structured_failure_without_obsolete_cache(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    writer, store, cash, income = finance
    reader_store = MutatingInputsStore(store.path)
    reader = _service(reader_store, "reader")
    index = 0

    def always_mutate() -> None:
        nonlocal index
        index += 1
        draft = writer.prepare_entry(_content(cash, income, 1)).unwrap()
        writer.post_draft(draft.id, draft.content_hash, f"race-{index}").unwrap()

    reader_store.after_read = always_mutate
    result = reader.snapshot()
    assert isinstance(result, Err)
    assert result.error.operation == "snapshot"
    assert "changed" in result.error.message.lower()
    assert len(store.entries()) == 2
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        assert connection.execute("SELECT count(*) FROM projection_cache").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM provenance").fetchone()[0] == 0


def test_migration_failure_rolls_back_partial_ddl_and_retry_preserves_existing_data(
    finance: tuple[FinanceService, FinanceStore, Account, Account],
) -> None:
    _, store, cash, income = finance
    guards = (
        "accounts_identity_insert",
        "drafts_identity_insert",
        "entries_identity_insert",
        "postings_reference_shape_insert",
    )
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        for name in guards:
            connection.execute(f"DROP TRIGGER {name}")
        connection.execute("DELETE FROM schema_migrations WHERE version=2")
        connection.execute(
            "CREATE TRIGGER postings_reference_shape_insert BEFORE INSERT ON postings "
            "BEGIN SELECT 1; END;"
        )
    with pytest.raises(sqlite3.Error, match="already exists"):
        store.initialize()
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
        # Later migrations remain committed; the failed retry of migration 2
        # must not discard their tables or write partial DDL.
        assert versions == [1, 3, 4, 5, 6]
        for name in guards[:-1]:
            assert (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",
                    (name,),
                ).fetchone()
                is None
            )
        connection.execute("DROP TRIGGER postings_reference_shape_insert")
    store.initialize()
    store.initialize()
    assert set(store.accounts()) == {cash, income}
    with store.transaction() as unit:
        versions = cast(SQLiteUnitOfWork, unit).connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        assert [row[0] for row in versions] == [1, 2, 3, 4, 5, 6]
