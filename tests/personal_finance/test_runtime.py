"""Installed commands and explicit personal/demo storage separation."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

from pytest import MonkeyPatch

from core.identity import Ref
from core.result import Ok
from personal_finance.bootstrap import open_service
from personal_finance.cli import main
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money


def test_demo_only_populates_the_demo_book_and_reopens_cleanly(tmp_path: Path) -> None:
    personal = open_service(tmp_path)
    assert personal.accounts() == ()
    assert personal.entries() == ()
    demo = open_service(tmp_path, demo=True)
    assert len(demo.accounts()) == 5
    assert len(demo.entries()) == 10
    assert all(entry.content.source.startswith("demo:v1:") for entry in demo.entries())
    reopened = open_service(tmp_path, demo=True)
    assert len(reopened.entries()) == 10
    assert open_service(tmp_path).entries() == ()
    assert (tmp_path / "personal.sqlite3").exists()
    assert (tmp_path / "demo.sqlite3").exists()


def test_module_entry_point_initializes_and_cli_prepares_without_posting(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "personal_finance", "--data-dir", str(tmp_path), "init"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "personal.sqlite3").exists()
    assert (
        main(["--data-dir", str(tmp_path), "add-account", "Checking", "asset", "USD", "--liquid"])
        == 0
    )
    assert main(["--data-dir", str(tmp_path), "add-account", "Salary", "income", "USD"]) == 0
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "entry",
                "2026-09-01",
                "Paycheck",
                "Checking",
                "Salary",
                "1200.00",
                "USD",
            ]
        )
        == 0
    )
    reopened = open_service(tmp_path)
    assert reopened.entries() == ()
    assert len(reopened.drafts()) == 1
    draft = reopened.drafts()[0]
    assert isinstance(reopened.post_draft(draft.id, draft.content_hash, "runtime-test"), Ok)
    assert len(open_service(tmp_path).entries()) == 1


def test_cli_requires_interactive_review_for_posting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    service = open_service(tmp_path)

    debit = service.create_account("Checking", AccountType.ASSET, "USD")
    credit = service.create_account("Salary", AccountType.INCOME, "USD")
    assert isinstance(debit, Ok) and isinstance(credit, Ok)
    content = EntryContent(
        date(2026, 9, 1),
        "Paycheck",
        (
            Posting(Ref(debit.value.id), Money(100, "USD")),
            Posting(Ref(credit.value.id), Money(-100, "USD")),
        ),
    )
    draft = service.prepare_entry(content)
    assert isinstance(draft, Ok)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--data-dir", str(tmp_path), "post", draft.value.id.value]) == 1
    assert service.entries() == ()
