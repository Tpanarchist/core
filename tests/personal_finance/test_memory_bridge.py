"""Finance evidence survives Memory outages and is recalled after retry."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.effect import Effect
from core.identity import Id, Ref
from core.result import Ok
from core.time import SystemClock
from core.value import Kind
from memory.sqlite_store import SqliteMemoryStore
from personal_finance.adapters.memory import FinanceMemory
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.bootstrap import open_service
from personal_finance.cli import main
from personal_finance.domain.accounts import AccountType


def test_outbox_retry_reopen_and_bounded_recall(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    created = service.create_account("Checking", AccountType.ASSET, "USD", True)
    assert isinstance(created, Ok)
    finance = service.store
    assert isinstance(finance, FinanceStore)
    assert finance.memory_delivery_status() == (1, 0, 0)
    unavailable_path = tmp_path / "memory.sqlite3"
    unavailable_path.mkdir()
    bridge = FinanceMemory(finance, unavailable_path, "personal")
    assert bridge.deliver() == (0, 0, 1)
    assert len(service.accounts()) == 1
    unavailable = bridge.recall("Checking", SystemClock().now())
    assert unavailable.unavailable is not None
    unavailable_path.rmdir()
    assert bridge.deliver() == (0, 1, 0)
    result = bridge.recall("Checking", SystemClock().now(), limit=1)
    assert result.unavailable is None
    assert len(result.items) == 1
    assert result.items[0].subject.id == created.value.id
    assert result.items[0].source == "finance.account.created"
    assert bridge.deliver() == (0, 1, 0)
    reopened = SqliteMemoryStore(unavailable_path)
    try:
        assert reopened.resolve(result.items[0].id) is not None
    finally:
        reopened.close()
    assert main(["--data-dir", str(tmp_path), "recall", "Checking"]) == 0


def test_migration_backfills_existing_finance_effects(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    created = service.create_account("Savings", AccountType.ASSET, "USD", True)
    assert isinstance(created, Ok)
    finance = service.store
    assert isinstance(finance, FinanceStore)
    connection = sqlite3.connect(finance.path)
    try:
        connection.execute("DROP TABLE memory_outbox")
        connection.execute("DELETE FROM schema_migrations WHERE version=6")
        connection.commit()
    finally:
        connection.close()
    finance.initialize()
    finance.initialize()
    reopened = open_service(tmp_path)
    assert len(reopened.accounts()) == 1
    assert finance.memory_delivery_status() == (1, 0, 0)


def test_outbox_rolls_back_with_finance_change(tmp_path: Path) -> None:
    finance = FinanceStore(tmp_path / "finance.sqlite3")
    finance.initialize()
    effect = Effect(
        id=Id(Kind("core.effect"), "rollback-check"),
        kind=Kind("finance.test"),
        description="Never committed",
        target=Ref(Id(Kind("finance.account"), "uncommitted")),
        at=SystemClock().now(),
    )
    with pytest.raises(RuntimeError, match="rollback"):
        with finance.transaction() as unit:
            unit.record(effect)
            raise RuntimeError("rollback")
    assert finance.memory_delivery_status() == (0, 0, 0)
    assert finance.pending_memory_effects() == ()
