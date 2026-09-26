"""Migration 4 and append-only model evidence survive reopen and direct SQL attacks."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from core.result import Ok
from personal_finance.adapters.sqlite import FinanceStore, SQLiteUnitOfWork
from personal_finance.application.ports import FinanceFailure
from personal_finance.bootstrap import open_service
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.codec import encode_id
from personal_finance.domain.money import Money


def test_model_history_is_append_only_and_direct_inserts_are_rejected(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    debt = service.create_account("Card", AccountType.LIABILITY, "USD")
    assert isinstance(debt, Ok)
    terms = service.set_debt_terms(debt.value.id, 1_800, Money(5_000, "USD"), 15, "statement")
    assert isinstance(terms, Ok)
    store = cast(FinanceStore, service.store)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "UPDATE debt_terms SET content_hash=? WHERE id=?",
                ("0" * 64, encode_id(terms.value.id)),
            )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "DELETE FROM debt_terms WHERE id=?", (encode_id(terms.value.id),)
            )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "INSERT INTO debt_terms(id,account_id,payload,content_hash) VALUES(?,?,?,?)",
                ("finance.debt_terms:forged", encode_id(debt.value.id), "{}", "0" * 64),
            )
    assert open_service(tmp_path).model_records().debt_terms == (terms.value,)


def test_migration_four_upgrades_existing_v3_database(tmp_path: Path) -> None:
    store = FinanceStore(tmp_path / "old.sqlite3")
    store.initialize()
    connection = sqlite3.connect(store.path)
    try:
        for table in (
            "model_projection_cache",
            "node_funding",
            "income_nodes",
            "asset_flow_coverage",
            "asset_flows",
            "asset_valuations",
            "asset_positions",
            "debt_payment_splits",
            "debt_terms",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version=4")
        connection.commit()
    finally:
        connection.close()
    store.initialize()
    store.initialize()
    with store.transaction() as unit:
        versions = tuple(
            row[0]
            for row in cast(SQLiteUnitOfWork, unit).connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        )
    assert versions == (1, 2, 3, 4, 5, 6)
    assert store.model_records().debt_terms == ()
