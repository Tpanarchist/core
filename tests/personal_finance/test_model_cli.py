"""The local commands create and inspect the new reviewed model records."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pytest import CaptureFixture

from core.result import Ok
from personal_finance.bootstrap import open_service
from personal_finance.cli import main
from personal_finance.domain.accounts import AccountType


def test_model_commands_reopen_and_report_provenance(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    service = open_service(tmp_path)
    assert isinstance(service.create_account("Card", AccountType.LIABILITY, "USD"), Ok)
    assert isinstance(service.create_account("Brokerage", AccountType.ASSET, "USD"), Ok)
    assert isinstance(service.create_account("Project revenue", AccountType.INCOME, "USD"), Ok)
    assert isinstance(service.create_account("Project costs", AccountType.EXPENSE, "USD"), Ok)
    prefix = ["--data-dir", str(tmp_path)]
    today = date.today()

    assert main([*prefix, "debt-terms", "Card", "18.25", "50.00", "15"]) == 0
    assert main([*prefix, "asset-position", "Brokerage", "Index fund", "investment"]) == 0
    position = open_service(tmp_path).model_records().positions[0]
    assert (
        main(
            [
                *prefix,
                "asset-value",
                position.id.value,
                "2000.00",
                "USD",
                today.isoformat(),
                (today + timedelta(days=7)).isoformat(),
                "--basis",
                "1700.00",
            ]
        )
        == 0
    )
    assert main([*prefix, "node-create", "Side project", "Project revenue", "Project costs"]) == 0
    capsys.readouterr()

    for command, expected in (
        (["debts"], "Card"),
        (["assets"], "Index fund"),
        (["nodes"], "Side project"),
    ):
        assert main([*prefix, *command]) == 0
        output = capsys.readouterr().out
        assert expected in output
        assert "Core provenance:" in output

    assert (
        main(
            [
                *prefix,
                "asset-value",
                position.id.value,
                "2000.00",
                "USD",
                today.isoformat(),
                today.isoformat(),
                "--quantity",
                "not-a-number",
            ]
        )
        == 1
    )
