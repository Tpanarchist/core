"""Independent attacks on immutable accounting history and retained evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from core.context import Context
from core.effect import Effect
from core.identity import Id, Namespace, Ref
from core.provenance import Provenance
from core.time import Duration, WallInstant
from core.trace import Trace
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore, SQLiteUnitOfWork
from personal_finance.application.evidence_codec import decode_provenance, encode_provenance
from personal_finance.application.ports import FinanceFailure
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.codec import encode_id, encode_ref
from personal_finance.domain.kinds import ACCOUNT, JOURNAL_ENTRY
from personal_finance.domain.ledger import EntryContent, JournalEntry, Posting
from personal_finance.domain.money import MAX_MINOR, Money

AT = WallInstant(datetime(2026, 9, 21, 12, tzinfo=UTC))
DAY = date(2026, 9, 21)
CASH = Account(Id(ACCOUNT, "cash"), "Checking", AccountType.ASSET, "USD", True)
INCOME = Account(Id(ACCOUNT, "income"), "Income", AccountType.INCOME, "USD")
EUR_CASH = Account(Id(ACCOUNT, "eur-cash"), "EUR cash", AccountType.ASSET, "EUR", True)
EUR_INCOME = Account(Id(ACCOUNT, "eur-income"), "EUR income", AccountType.INCOME, "EUR")
ORIGINAL_ID = Id(JOURNAL_ENTRY, "original")
STAGED_ID = Id(JOURNAL_ENTRY, "staged")


@pytest.fixture
def store(tmp_path: Path) -> FinanceStore:
    value = FinanceStore(tmp_path / "adversarial.sqlite3")
    value.initialize()
    with value.transaction() as unit:
        for account in (CASH, INCOME, EUR_CASH, EUR_INCOME):
            unit.add_account(account)
    return value


def _posted(store: FinanceStore) -> JournalEntry:
    content = EntryContent(
        DAY,
        "Original income",
        (
            Posting(Ref(CASH.id), Money(100, "USD")),
            Posting(Ref(INCOME.id), Money(-100, "USD")),
        ),
    )
    with store.transaction() as unit:
        return unit.post_entry(JournalEntry(ORIGINAL_ID, content, AT, "local-human", 1))


def _stage(
    connection: sqlite3.Connection,
    *,
    effective_date: str = "2026-09-21",
    reversal_id: Id | None = None,
    reversal_ref: Ref | None = None,
    principal: str = "local-human",
    status: str = "staging",
) -> None:
    connection.execute(
        "INSERT INTO entries(id,effective_date,description,tags,source,reversal_id,"
        "reversal_ref,recorded_at,principal,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            encode_id(STAGED_ID),
            effective_date,
            "Staged",
            "[]",
            "manual",
            encode_id(reversal_id) if reversal_id else None,
            encode_ref(reversal_ref) if reversal_ref else None,
            AT.value.isoformat(),
            principal,
            status,
        ),
    )


def _postings(connection: sqlite3.Connection, values: tuple[tuple[Account, int], ...]) -> None:
    connection.executemany(
        "INSERT INTO postings VALUES(?,?,?,?,?,?)",
        (
            (
                encode_id(STAGED_ID),
                position,
                encode_id(account.id),
                encode_ref(Ref(account.id)),
                amount,
                account.currency,
            )
            for position, (account, amount) in enumerate(values)
        ),
    )


def _finalize(connection: sqlite3.Connection) -> None:
    connection.execute("UPDATE entries SET status='posted' WHERE id=?", (encode_id(STAGED_ID),))


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE entries SET description='changed' WHERE id=?",
        "UPDATE entries SET status='staging' WHERE id=?",
        "UPDATE entries SET effective_date='2026-09-22' WHERE id=?",
        "UPDATE entries SET principal='other' WHERE id=?",
        "DELETE FROM entries WHERE id=?",
        "UPDATE postings SET minor=minor+1 WHERE entry_id=?",
        "UPDATE postings SET position=position+5 WHERE entry_id=?",
        "DELETE FROM postings WHERE entry_id=?",
    ],
)
def test_direct_sql_cannot_edit_or_delete_posted_history(store: FinanceStore, sql: str) -> None:
    original = _posted(store)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(sql, (encode_id(original.id),))
    assert store.entries() == (original,)


def test_direct_sql_cannot_insert_a_finalized_entry(store: FinanceStore) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            _stage(cast(SQLiteUnitOfWork, unit).connection, status="posted")
    assert store.entries() == ()


def test_balanced_raw_finalization_cannot_bypass_authorized_unit_of_work(
    store: FinanceStore,
) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection)
            _postings(connection, ((CASH, 100), (INCOME, -100)))
            _finalize(connection)
    assert store.entries() == ()


def test_direct_sql_cannot_append_or_reparent_postings_to_posted_entry(store: FinanceStore) -> None:
    original = _posted(store)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            connection.execute(
                "INSERT INTO postings VALUES(?,?,?,?,?,?)",
                (
                    encode_id(original.id),
                    2,
                    encode_id(CASH.id),
                    encode_ref(Ref(CASH.id)),
                    1,
                    "USD",
                ),
            )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection)
            _postings(connection, ((CASH, 1), (INCOME, -1)))
            connection.execute(
                "UPDATE postings SET entry_id=? WHERE entry_id=?",
                (
                    encode_id(original.id),
                    encode_id(STAGED_ID),
                ),
            )
    assert store.entries() == (original,)


@pytest.mark.parametrize(
    "values",
    [
        ((CASH, 1),),
        ((CASH, 100), (INCOME, -99)),
        ((CASH, 0), (INCOME, 0)),
        ((CASH, 100), (INCOME, -100), (EUR_CASH, 100), (EUR_INCOME, -100)),
    ],
)
def test_direct_sql_finalization_enforces_all_accounting_rules(
    store: FinanceStore,
    values: tuple[tuple[Account, int], ...],
) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection)
            _postings(connection, values)
            _finalize(connection)
    assert store.entries() == ()


def test_exact_valid_split_does_not_overflow_intermediate_sql_sum(store: FinanceStore) -> None:
    content = EntryContent(
        DAY,
        "Large split",
        (
            Posting(Ref(CASH.id), Money(MAX_MINOR, "USD")),
            Posting(Ref(INCOME.id), Money(1, "USD")),
            Posting(Ref(CASH.id), Money(-MAX_MINOR, "USD")),
            Posting(Ref(INCOME.id), Money(-1, "USD")),
        ),
    )
    with store.transaction() as unit:
        entry = unit.post_entry(JournalEntry(ORIGINAL_ID, content, AT, "local-human", 1))
    assert store.entries() == (entry,)


@pytest.mark.parametrize("amount", [MAX_MINOR, MAX_MINOR - 1])
def test_large_unbalanced_entry_cannot_pass_by_float_rounding(
    store: FinanceStore,
    amount: int,
) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection)
            _postings(connection, ((CASH, amount), (INCOME, -(amount - 1))))
            _finalize(connection)


@pytest.mark.parametrize("invalid_date", ["2026-99-99", "abcdefghij"])
def test_direct_sql_rejects_invalid_effective_date(store: FinanceStore, invalid_date: str) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            _stage(cast(SQLiteUnitOfWork, unit).connection, effective_date=invalid_date)


def test_direct_sql_rejects_blank_authorizing_principal(store: FinanceStore) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection, principal=" ")
            _postings(connection, ((CASH, 1), (INCOME, -1)))
            _finalize(connection)


def test_close_after_staging_still_prevents_finalization(store: FinanceStore) -> None:
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        _stage(connection)
        _postings(connection, ((CASH, 1), (INCOME, -1)))
    with store.transaction() as unit:
        cast(SQLiteUnitOfWork, unit).connection.execute(
            "INSERT INTO closed_periods VALUES('2026-09-01','2026-09-30','Monthly close')"
        )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            _finalize(cast(SQLiteUnitOfWork, unit).connection)
    assert store.entries() == ()


def test_direct_sql_reversal_cannot_insert_into_closed_period(store: FinanceStore) -> None:
    original = _posted(store)
    with store.transaction() as unit:
        cast(SQLiteUnitOfWork, unit).connection.execute(
            "INSERT INTO closed_periods VALUES('2026-09-01','2026-09-30','Monthly close')"
        )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            _stage(
                cast(SQLiteUnitOfWork, unit).connection,
                reversal_id=original.id,
                reversal_ref=Ref(original.id),
            )
    assert store.entries() == (original,)


def test_direct_sql_reversal_reference_must_match_target_identity(store: FinanceStore) -> None:
    original = _posted(store)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(
                connection,
                reversal_id=original.id,
                reversal_ref=Ref(Id(JOURNAL_ENTRY, "other-entry")),
            )
            _postings(connection, ((CASH, -100), (INCOME, 100)))
            _finalize(connection)
    assert store.entries() == (original,)


def test_direct_sql_reversal_must_exactly_negate_original_postings(store: FinanceStore) -> None:
    original = _posted(store)
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection, reversal_id=original.id, reversal_ref=Ref(original.id))
            _postings(connection, ((CASH, -1), (INCOME, 1)))
            _finalize(connection)
    assert store.entries() == (original,)


def test_unit_of_work_rejects_wrong_reversal_amount_and_duplicate_reversal(
    store: FinanceStore,
) -> None:
    original = _posted(store)
    incorrect = EntryContent(
        DAY,
        "Incorrect reversal",
        (
            Posting(Ref(CASH.id), Money(-1, "USD")),
            Posting(Ref(INCOME.id), Money(1, "USD")),
        ),
        reversal_of=Ref(original.id),
    )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            unit.post_entry(JournalEntry(STAGED_ID, incorrect, AT, "local-human", 1))
    correct = EntryContent(
        DAY,
        "Exact reversal",
        (
            Posting(Ref(CASH.id), Money(-100, "USD")),
            Posting(Ref(INCOME.id), Money(100, "USD")),
        ),
        reversal_of=Ref(original.id),
    )
    with store.transaction() as unit:
        reversal = unit.post_entry(JournalEntry(STAGED_ID, correct, AT, "local-human", 1))
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            unit.post_entry(
                JournalEntry(Id(JOURNAL_ENTRY, "duplicate"), correct, AT, "local-human", 1)
            )
    assert store.entries() == (reversal, original)


def test_stored_posting_namespace_and_reversal_namespace_are_retained(store: FinanceStore) -> None:
    scope = Namespace(("profiles", "household"))
    content = EntryContent(
        DAY,
        "Scoped income",
        (
            Posting(Ref(CASH.id, scope), Money(10, "USD")),
            Posting(Ref(INCOME.id, scope), Money(-10, "USD")),
        ),
    )
    with store.transaction() as unit:
        original = unit.post_entry(JournalEntry(ORIGINAL_ID, content, AT, "local-human", 1))
    reversal_content = EntryContent(
        DAY,
        "Scoped reversal",
        tuple(Posting(posting.account, -posting.money) for posting in content.postings),
        reversal_of=Ref(original.id, scope),
    )
    with store.transaction() as unit:
        reversal = unit.post_entry(JournalEntry(STAGED_ID, reversal_content, AT, "local-human", 1))
    reloaded = FinanceStore(store.path)
    reloaded.initialize()
    assert reloaded.entries() == (reversal, original)


def test_failure_rolls_back_posted_entry_effect_and_audit_together(store: FinanceStore) -> None:
    content = EntryContent(
        DAY,
        "Atomic entry",
        (
            Posting(Ref(CASH.id), Money(10, "USD")),
            Posting(Ref(INCOME.id), Money(-10, "USD")),
        ),
    )
    effect = Effect(
        id=Id(Kind("finance.effect"), "atomic-effect"),
        kind=Kind("finance.ledger.entry_posted"),
        description="Posted entry",
        target=Ref(ORIGINAL_ID),
        at=AT,
    )
    trace = Trace(Id(Kind("finance.trace"), "atomic-trace"))
    trace.append(
        kind=Kind("finance.posted"),
        subject=Ref(ORIGINAL_ID),
        references=(Ref(effect.id),),
        observed_at=AT,
    )
    with pytest.raises(FinanceFailure, match="Forced late failure"):
        with store.transaction() as unit:
            unit.post_entry(JournalEntry(ORIGINAL_ID, content, AT, "local-human", 1))
            unit.record(effect)
            unit.record_trace(trace, "local-human", "input-hash")
            raise FinanceFailure("Forced late failure")
    assert store.entries() == ()
    with store.transaction() as unit:
        connection = cast(SQLiteUnitOfWork, unit).connection
        assert connection.execute("SELECT count(*) FROM effects").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM postings").fetchone()[0] == 0


@pytest.mark.parametrize(
    "operation", ["UPDATE closed_periods SET reason='edited'", "DELETE FROM closed_periods"]
)
def test_closed_period_records_cannot_be_rewritten(store: FinanceStore, operation: str) -> None:
    with store.transaction() as unit:
        cast(SQLiteUnitOfWork, unit).connection.execute(
            "INSERT INTO closed_periods VALUES('2026-09-01','2026-09-30','Monthly close')"
        )
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(operation)


@pytest.mark.parametrize(
    "identifier",
    [
        "{}",
        '{"kind":"finance.account","value":"raw","version":true}',
        '{"kind":"finance.account","value":27,"version":1}',
        '{"value":"raw","kind":"finance.account","version":1}',
        '{"kind":"finance.account","value":"raw","version":1,"extra":0}',
    ],
)
def test_direct_sql_rejects_noncanonical_or_malformed_account_identity(
    store: FinanceStore,
    identifier: str,
) -> None:
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            cast(SQLiteUnitOfWork, unit).connection.execute(
                "INSERT INTO accounts VALUES(?,?,?,?,?)",
                (identifier, "Malformed account", "asset", "USD", 1),
            )
    assert len(store.accounts()) == 4


@pytest.mark.parametrize("namespace", ["[]", '[""]', "[27]"])
def test_direct_sql_rejects_malformed_posting_reference_namespace(
    store: FinanceStore,
    namespace: str,
) -> None:
    malformed = encode_ref(Ref(CASH.id)).replace('"namespace":null', f'"namespace":{namespace}')
    with pytest.raises(FinanceFailure):
        with store.transaction() as unit:
            connection = cast(SQLiteUnitOfWork, unit).connection
            _stage(connection)
            connection.execute(
                "INSERT INTO postings VALUES(?,?,?,?,?,?)",
                (
                    encode_id(STAGED_ID),
                    0,
                    encode_id(CASH.id),
                    malformed,
                    1,
                    "USD",
                ),
            )


def _provenance() -> Provenance:
    return Provenance(
        id=Id(Kind("core.provenance"), "projection-1"),
        transform_id=Id(Kind("finance.transform"), "recorded-balances"),
        transform_name="Recorded balances",
        transform_version="1",
        inputs=(Ref(ORIGINAL_ID, Namespace(("profiles", "personal"))),),
        parents=(Ref(Id(Kind("core.provenance"), "missing-parent")),),
        at=AT,
        duration=Duration(17),
        context=Context(
            as_of=AT,
            namespace=Namespace(("profiles", "personal")),
            units=("USD", "EUR"),
            source="ledger",
            authority="posted",
            version="1",
            metadata={"policy": "recorded-only"},
        ),
    )


def test_provenance_codec_preserves_context_and_full_source_refs() -> None:
    original = _provenance()
    restored = decode_provenance(encode_provenance(original))
    assert restored == original
    assert restored.ancestors(lambda ref: None).unresolved == original.parents


def test_provenance_codec_rejects_boolean_version() -> None:
    payload = encode_provenance(_provenance())
    altered = payload.replace('"version":1', '"version":true')
    assert altered != payload
    with pytest.raises(ValueError):
        decode_provenance(altered)


def test_narrow_provenance_codec_rejects_unsupported_environment_instead_of_dropping_it() -> None:
    original = _provenance()
    assert original.context is not None
    unsupported = replace(original, context=original.context.with_(environment="local"))
    with pytest.raises(ValueError):
        encode_provenance(unsupported)
