from __future__ import annotations

from contextlib import redirect_stdout
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

from core.identity import Id
from core.result import Ok
from core.time import MonotonicInstant, WallInstant
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.application.service import FinanceService
from personal_finance.cli import print_draft_review
from personal_finance.csv_io import export_csv, stage_csv
from personal_finance.domain.accounts import AccountType


class TestIds:
    __test__ = False

    def __init__(self) -> None:
        self.count = 0

    def new(self, kind: Kind) -> Id:
        self.count += 1
        return Id(kind, f"csv-{self.count}")


class FixedClock:
    def now(self) -> WallInstant:
        return WallInstant(datetime(2026, 9, 21, tzinfo=UTC))


class FixedMonotonic:
    space = Id(Kind("test.clock"), "csv")

    def now(self) -> MonotonicInstant:
        return MonotonicInstant(self.space, 0)


def service_at(path: Path) -> FinanceService:
    store = FinanceStore(path)
    store.initialize()
    service = FinanceService(store, FixedClock(), FixedMonotonic(), TestIds())
    assert isinstance(service.create_account("Checking", AccountType.ASSET, "USD", True), Ok)
    assert isinstance(service.create_account("Income", AccountType.INCOME, "USD"), Ok)
    return service


def test_csv_stages_valid_rows_reports_bad_rows_and_deduplicates(tmp_path: Path) -> None:
    service = service_at(tmp_path / "book.sqlite3")
    source = tmp_path / "import.csv"
    source.write_text(
        "date,description,account,counter_account,amount,currency,tags\n"
        "2026-09-01,Pay,Checking,Income,1500.25,USD,salary;monthly\n"
        "2026-09-02,Bad,Checking,Income,1.001,USD,\n"
        "2026-09-03,Unknown,Missing,Income,5,USD,\n",
        encoding="utf-8",
    )
    result = stage_csv(service, source)
    assert len(result.drafts) == 1
    assert [issue.row for issue in result.errors] == [3, 4]
    assert service.entries() == ()
    assert result.drafts[0].content.postings[0].money.minor == 150025
    assert result.drafts[0].content.tags == ("salary", "monthly")
    repeated = stage_csv(service, source)
    assert repeated.duplicates == 1
    assert not repeated.drafts
    draft = result.drafts[0]
    assert isinstance(service.post_draft(draft.id, draft.content_hash, "csv-post"), Ok)
    assert stage_csv(service, source).duplicates == 1


def test_csv_schema_and_size_limits(tmp_path: Path) -> None:
    service = service_at(tmp_path / "book.sqlite3")
    source = tmp_path / "wrong.csv"
    source.write_text("date,amount\n2026-01-01,5\n", encoding="utf-8")
    report = stage_csv(service, source)
    assert not report.drafts
    assert report.errors[0].row == 1
    assert "header" in report.errors[0].message.lower()


def test_export_retains_full_identity_and_escapes_spreadsheet_formulas(tmp_path: Path) -> None:
    import csv

    service = service_at(tmp_path / "book.sqlite3")
    source = tmp_path / "import.csv"
    source.write_text(
        "date,description,account,counter_account,amount,currency,tags\n"
        "2026-09-01,=formula,Checking,Income,15.20,USD,\n",
        encoding="utf-8",
    )
    draft = stage_csv(service, source).drafts[0]
    assert isinstance(service.post_draft(draft.id, draft.content_hash, "export-post"), Ok)
    destination = tmp_path / "export.csv"
    assert export_csv(service, destination) == 1
    with destination.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["entry_kind"] == "finance.journal_entry"
    assert rows[0]["account_kind"] == "finance.account"
    assert rows[0]["minor_units"] == "1520"
    assert rows[0]["description"] == "'=formula"
    assert rows[0]["effective_date"] == "2026-09-01"
    assert "2026-09-21" in rows[0]["recorded_at"]
    # Export is create-only: never silently overwrite a user's existing file.
    import pytest

    with pytest.raises(FileExistsError):
        export_csv(service, destination)


def test_cli_review_escapes_imported_controls_and_shows_reversal_target(tmp_path: Path) -> None:
    service = service_at(tmp_path / "book.sqlite3")
    source = tmp_path / "untrusted.csv"
    source.write_text(
        "date,description,account,counter_account,amount,currency,tags\n"
        "2026-09-01,Pay\x1b[8mhidden,Checking,Income,15.20,USD,tag\u202esecret\n",
        encoding="utf-8",
    )
    draft = stage_csv(service, source).drafts[0]
    output = StringIO()
    with redirect_stdout(output):
        print_draft_review(draft, service.accounts())
    review = output.getvalue()
    assert "\x1b" not in review and "\u202e" not in review
    assert "Pay\\u001B[8mhidden" in review
    assert "tag\\u202Esecret" in review
    assert "15.20 USD" in review and "-15.20 USD" in review
    assert "Reverses: none" in review

    posted = service.post_draft(draft.id, draft.content_hash, "display-review")
    assert isinstance(posted, Ok)
    reversal = service.prepare_reversal(posted.value.id, date(2026, 9, 21))
    assert isinstance(reversal, Ok)
    output = StringIO()
    with redirect_stdout(output):
        print_draft_review(reversal.value, service.accounts())
    assert f"Reverses: finance.journal_entry:{posted.value.id.value}" in output.getvalue()
