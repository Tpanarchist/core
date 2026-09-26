"""The new model tabs and local input forms work from the keyboard."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import cast

from textual.pilot import Pilot
from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent

from core.identity import Ref
from core.result import Ok
from personal_finance.app import FinanceApp
from personal_finance.bootstrap import open_service
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.codec import encode_id
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money
from personal_finance.ui.forms import InfoScreen
from personal_finance.ui.model_forms import ModelActionMenu, ModelInputForm


async def settled(app: FinanceApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
    await pilot.pause()


def test_model_tabs_create_terms_position_and_node(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    cash = service.create_account("Checking", AccountType.ASSET, "USD", True)
    brokerage = service.create_account("Brokerage", AccountType.ASSET, "USD")
    liability = service.create_account("Card", AccountType.LIABILITY, "USD")
    equity = service.create_account("Equity", AccountType.EQUITY, "USD")
    revenue = service.create_account("Project revenue", AccountType.INCOME, "USD")
    expense = service.create_account("Project expense", AccountType.EXPENSE, "USD")
    assert all(
        isinstance(item, Ok) for item in (cash, brokerage, liability, equity, revenue, expense)
    )
    assert isinstance(cash, Ok) and isinstance(liability, Ok) and isinstance(equity, Ok)
    prepared = service.prepare_entry(
        EntryContent(
            date.today(),
            "Opening card balance",
            (
                Posting(Ref(equity.value.id), Money(50_000, "USD")),
                Posting(Ref(liability.value.id), Money(-50_000, "USD")),
            ),
        )
    )
    assert isinstance(prepared, Ok)
    assert isinstance(
        service.post_draft(prepared.value.id, prepared.value.content_hash, "model-ui"), Ok
    )
    app = FinanceApp(service)

    async def scenario() -> None:
        async with app.run_test(size=(140, 50)) as pilot:
            await settled(app, pilot)
            await pilot.press("4", "m")
            assert app.query_one("#finance-tabs", TabbedContent).active == "debts"
            assert isinstance(app.screen, ModelActionMenu)
            await pilot.press("enter")
            assert isinstance(app.screen, ModelInputForm)
            form = app.screen
            cast(Select[str], form.query_one("#model-account", Select)).value = encode_id(
                liability.value.id
            )
            form.query_one("#model-apr", Input).value = "18.25"
            form.query_one("#model-minimum", Input).value = "50.00"
            form.query_one("#model-due", Input).value = "15"
            form.query_one("#model-save", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert len(service.model_records().debt_terms) == 1
            assert app.query_one("#debts-table", DataTable).row_count == 1
            assert "18.25%" in str(app.query_one("#debts-text-content", Static).content) or (
                "APR" in str(app.query_one("#debts-text-content", Static).content)
            )
            await pilot.press("x")
            assert isinstance(app.screen, InfoScreen)
            assert "finance.debt_terms" in str(
                app.screen.query_one("#info-content", Static).content
            )
            await pilot.press("escape")

            await pilot.press("m")
            await pilot.click("#model-action-debt_compare")
            assert isinstance(app.screen, ModelInputForm)
            app.screen.query_one("#model-save", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert "PAYOFF COMPARISON" in str(app.query_one("#debts-chart", Static).content)

            await pilot.press("5", "m")
            assert isinstance(app.screen, ModelActionMenu)
            await pilot.press("enter")
            assert isinstance(app.screen, ModelInputForm)
            form = app.screen
            assert isinstance(brokerage, Ok)
            cast(Select[str], form.query_one("#model-account", Select)).value = encode_id(
                brokerage.value.id
            )
            form.query_one("#model-name", Input).value = "Index fund"
            form.query_one("#model-save", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert len(service.model_records().positions) == 1
            assert "Unknown" in str(app.query_one("#assets-text-content", Static).content) or (
                "No valuation" in str(app.query_one("#assets-text-content", Static).content)
            )

            await pilot.press("6", "m")
            assert isinstance(app.screen, ModelActionMenu)
            await pilot.press("enter")
            assert isinstance(app.screen, ModelInputForm)
            form = app.screen
            assert isinstance(revenue, Ok) and isinstance(expense, Ok)
            form.query_one("#model-name", Input).value = "Side project"
            cast(Select[str], form.query_one("#model-revenue", Select)).value = encode_id(
                revenue.value.id
            )
            cast(Select[str], form.query_one("#model-expense", Select)).value = encode_id(
                expense.value.id
            )
            cast(Select[str], form.query_one("#model-cash", Select)).value = encode_id(
                cash.value.id
            )
            form.query_one("#model-save", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert len(service.model_records().nodes) == 1
            assert app.query_one("#nodes-table", DataTable).row_count == 1
            await pilot.press("g", "x")
            assert isinstance(app.screen, InfoScreen)
            await pilot.press("escape")
            assert app.query_one("#nodes-graph").display

    asyncio.run(scenario())
