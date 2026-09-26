"""Monthly statements compare exact ledger boundaries before an auditable close."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pytest import MonkeyPatch

from core.identity import Ref
from core.result import Err, Ok
from personal_finance.application.service import FinanceService
from personal_finance.bootstrap import open_service
from personal_finance.cli import main
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.ledger import EntryContent, JournalEntry, Posting
from personal_finance.domain.money import Money


def _last_month() -> tuple[date, date]:
    first = date.today().replace(day=1)
    end = first - timedelta(days=1)
    return end.replace(day=1), end


def _account(service: FinanceService, name: str, kind: AccountType) -> Account:
    result = service.create_account(name, kind, "USD", kind is AccountType.ASSET)
    assert isinstance(result, Ok)
    return result.value


def _post(
    service: FinanceService,
    on: date,
    label: str,
    debit: Account,
    credit: Account,
    minor: int,
) -> JournalEntry:
    draft = service.prepare_entry(
        EntryContent(
            on,
            label,
            (
                Posting(Ref(debit.id), Money(minor, "USD")),
                Posting(Ref(credit.id), Money(-minor, "USD")),
            ),
        )
    )
    assert isinstance(draft, Ok)
    committed = service.post_draft(draft.value.id, draft.value.content_hash, label)
    assert isinstance(committed, Ok)
    return committed.value


def test_latest_statement_version_is_reviewed_and_closed(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    start, end = _last_month()
    checking = _account(service, "Checking", AccountType.ASSET)
    first = service.record_statement(
        checking.id, start, end, Money(0, "USD"), Money(0, "USD"), (), False, "draft statement"
    )
    assert isinstance(first, Ok)
    second = service.record_statement(
        checking.id, start, end, Money(0, "USD"), Money(0, "USD"), (), True, "final statement"
    )
    assert isinstance(second, Ok)
    assert len(service.reconcile_records().statements) == 2
    snapshot = service.snapshot()
    assert isinstance(snapshot, Ok) and snapshot.value.reconcile_view is not None
    views = snapshot.value.reconcile_view.portfolio.statements
    assert len(views) == 1
    assert views[0].statement.id == second.value.id
    assert views[0].closable
    assert isinstance(service.close_month(start.year, start.month, "Final statement reviewed"), Ok)
    assert isinstance(
        service.record_statement(
            checking.id,
            start,
            end,
            Money(0, "USD"),
            Money(0, "USD"),
            (),
            True,
            "after close",
        ),
        Err,
    )


def test_cli_statement_review_and_close(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    service = open_service(tmp_path)
    _account(service, "Checking", AccountType.ASSET)
    start, _ = _last_month()
    csv_path = tmp_path / "statement.csv"
    csv_path.write_text("date,amount,description,external_id\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def record_choice(_prompt: str) -> str:
        return "RECORD"

    monkeypatch.setattr("builtins.input", record_choice)
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "statement-add",
                "Checking",
                f"{start:%Y-%m}",
                "0.00",
                "0.00",
                str(csv_path),
                "--complete",
            ]
        )
        == 0
    )
    assert main(["--data-dir", str(tmp_path), "reconcile"]) == 0
    def close_choice(_prompt: str) -> str:
        return "CLOSE"

    monkeypatch.setattr("builtins.input", close_choice)
    assert main(["--data-dir", str(tmp_path), "reconcile-close", f"{start:%Y-%m}", "Reviewed"]) == 0
    assert len(open_service(tmp_path).reconcile_records().closes) == 1


def test_statement_match_close_and_later_reversal(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    start, end = _last_month()
    checking = _account(service, "Checking", AccountType.ASSET)
    equity = _account(service, "Opening equity", AccountType.EQUITY)
    entry = _post(service, start + timedelta(days=5), "Deposit", checking, equity, 100_000)
    statement = service.record_statement(
        checking.id,
        start,
        end,
        Money(0, "USD"),
        Money(100_000, "USD"),
        ((entry.content.effective_date, Money(100_000, "USD"), "Deposit", "bank-1"),),
        True,
        "bank statement",
    )
    assert isinstance(statement, Ok)
    snapshot = service.snapshot()
    assert isinstance(snapshot, Ok) and snapshot.value.reconcile_view is not None
    view = snapshot.value.reconcile_view.portfolio.statements[0]
    assert view.closing_difference == Money(0, "USD")
    assert len(view.suggestions) == 1
    assert view.suggestions[0].entry_id == entry.id
    assert not view.closable
    line = service.reconcile_records().lines[0]
    assert isinstance(service.match_statement_line(line.id, entry.id), Ok)
    ready = service.snapshot()
    assert isinstance(ready, Ok) and ready.value.reconcile_view is not None
    assert ready.value.reconcile_view.portfolio.statements[0].closable
    closed = service.close_month(start.year, start.month, "Reviewed bank statement")
    assert isinstance(closed, Ok)
    assert len(open_service(tmp_path).reconcile_records().closes) == 1
    assert isinstance(service.prepare_reversal(entry.id, entry.content.effective_date), Err)
    correction = service.prepare_reversal(entry.id, date.today())
    assert isinstance(correction, Ok)
    posted = service.post_draft(
        correction.value.id, correction.value.content_hash, "later-correction"
    )
    assert isinstance(posted, Ok)
    current = service.snapshot()
    assert isinstance(current, Ok) and current.value.reconcile_view is not None
    assert current.value.reconcile_view.portfolio.later_corrections == (posted.value,)


def test_difference_requires_resolution_and_zero_before_close(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    start, end = _last_month()
    checking = _account(service, "Checking", AccountType.ASSET)
    equity = _account(service, "Opening equity", AccountType.EQUITY)
    expense = _account(service, "Bank fees", AccountType.EXPENSE)
    deposit = _post(service, start + timedelta(days=2), "Deposit", checking, equity, 100_000)
    statement = service.record_statement(
        checking.id,
        start,
        end,
        Money(0, "USD"),
        Money(90_000, "USD"),
        ((deposit.content.effective_date, Money(90_000, "USD"), "Deposit net", None),),
        True,
        "bank statement",
    )
    assert isinstance(statement, Ok)
    issue = service.reconcile_records().issues[0]
    assert issue.ledger_closing == Money(100_000, "USD")
    assert isinstance(service.close_month(start.year, start.month, "Premature"), Err)
    fee = _post(service, start + timedelta(days=3), "Statement fee", expense, checking, 10_000)
    assert isinstance(
        service.resolve_reconcile_issue(issue.id, "Statement was net of the fee", fee.id), Ok
    )
    assert isinstance(service.close_month(start.year, start.month, "Still unmatched"), Err)
    line = service.reconcile_records().lines[0]
    assert isinstance(service.explain_reconcile_item(statement.value.id, line.id, "Net line"), Ok)
    assert isinstance(
        service.explain_reconcile_item(statement.value.id, deposit.id, "Gross deposit"), Ok
    )
    assert isinstance(service.explain_reconcile_item(statement.value.id, fee.id, "Netted fee"), Ok)
    assert isinstance(service.close_month(start.year, start.month, "Reviewed net statement"), Ok)
