"""A user can build and inspect a cash plan through installed local commands."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pytest import CaptureFixture

from core.identity import Ref
from core.result import Ok
from personal_finance.bootstrap import open_service
from personal_finance.cli import main
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money


def test_cli_cash_plan_reopens_and_explains_each_horizon(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    service = open_service(tmp_path)
    cash = service.create_account("Checking", AccountType.ASSET, "USD", True)
    equity = service.create_account("Opening equity", AccountType.EQUITY, "USD")
    assert isinstance(cash, Ok) and isinstance(equity, Ok)
    today = date.today()
    opening = service.prepare_entry(
        EntryContent(
            today,
            "Opening cash",
            (
                Posting(Ref(cash.value.id), Money(100_000, "USD")),
                Posting(Ref(equity.value.id), Money(-100_000, "USD")),
            ),
        )
    )
    assert isinstance(opening, Ok)
    assert isinstance(service.post_draft(opening.value.id, opening.value.content_hash, "cli"), Ok)
    prefix = ["--data-dir", str(tmp_path)]
    due_on = today + timedelta(days=5)

    assert main([*prefix, "plan-floor", "USD", "100.00", today.isoformat()]) == 0
    assert (
        main(
            [
                *prefix,
                "plan-observe",
                "Checking",
                "1000.00",
                "USD",
                today.isoformat(),
                (today + timedelta(days=7)).isoformat(),
                "--source",
                "manual review",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                *prefix,
                "plan-schedule",
                "Checking",
                "Rent",
                "-300.00",
                "USD",
                due_on.isoformat(),
                "once",
                "America/New_York",
            ]
        )
        == 0
    )
    schedule = open_service(tmp_path).cash_records().schedules[0]
    assert (
        main(
            [
                *prefix,
                "plan-allocation",
                "Rent reserve",
                "300.00",
                "USD",
                today.isoformat(),
                "--schedule-id",
                schedule.id.value,
                "--due-on",
                due_on.isoformat(),
            ]
        )
        == 0
    )
    assert main([*prefix, "plan-hold", "Card hold", "50.00", "USD", today.isoformat()]) == 0
    assert (
        main(
            [
                *prefix,
                "plan-coverage",
                "USD",
                today.isoformat(),
                (today + timedelta(days=90)).isoformat(),
                "--accounts-complete",
                "--schedules-complete",
                "--source",
                "manual review",
            ]
        )
        == 0
    )
    capsys.readouterr()
    for horizon in (30, 60, 90):
        assert main([*prefix, "cash", "--horizon", str(horizon)]) == 0
        output = capsys.readouterr().out
        assert f"{horizon}-day cash plan" in output
        assert "Safe to allocate: 550.00 USD" in output
        assert "scheduled_outflow" in output
        assert "Core provenance:" in output

    hold = open_service(tmp_path).cash_records().holds[0]
    assert (
        main(
            [
                *prefix,
                "plan-retire",
                "hold",
                hold.id.value,
                (today + timedelta(days=1)).isoformat(),
                "Released",
            ]
        )
        == 0
    )
    assert len(open_service(tmp_path).cash_records().retirements) == 1
