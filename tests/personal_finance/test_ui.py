"""Headless keyboard acceptance against the persisted local ledger."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from unittest.mock import patch

from textual.pilot import Pilot
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Select,
    Static,
    TabbedContent,
    TextArea,
)

from core.identity import Id, Ref
from core.result import Ok
from core.time import MonotonicInstant, WallInstant
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.app import FinanceApp
from personal_finance.application.service import FinanceService
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money
from personal_finance.ui.forms import AccountForm, EntryForm, InfoScreen, ReviewScreen, TextPrompt


class FixedClock:
    def now(self) -> WallInstant:
        return WallInstant(datetime(2026, 9, 21, 12, tzinfo=UTC))


class FixedMonotonic:
    space = Id(Kind("finance.clock"), "ui-test-clock")

    def now(self) -> MonotonicInstant:
        return MonotonicInstant(self.space, 0)


class TestIds:
    __test__ = False

    def __init__(self) -> None:
        self.counter = 0

    def new(self, kind: Kind) -> Id:
        self.counter += 1
        return Id(kind, f"ui-{self.counter}")


def make_service(tmp_path: Path) -> FinanceService:
    store = FinanceStore(tmp_path / "ui.sqlite3")
    store.initialize()
    return FinanceService(store, FixedClock(), FixedMonotonic(), TestIds())


async def settled(app: FinanceApp, pilot: Pilot[None]) -> None:
    for _ in range(3):
        # Textual's upstream annotation leaves its optional Worker generic unspecified.
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()


def test_empty_navigation_modes_resize_and_errors(tmp_path: Path) -> None:
    app = FinanceApp(make_service(tmp_path))

    async def scenario() -> None:
        async with app.run_test(size=(130, 42)) as pilot:
            await settled(app, pilot)
            assert app.snapshot is not None
            assert not app.snapshot.entries
            assert "Unavailable" in str(app.query_one("#overview-summary", Static).content)
            for index, name in enumerate(
                ("overview", "ledger", "cash", "debts", "assets", "nodes", "forecast", "reconcile"),
                1,
            ):
                await pilot.press(str(index))
                assert app.query_one("#finance-tabs", TabbedContent).active == name
            await pilot.press("1", "g")
            assert not app.query_one("#overview-text").display
            assert app.query_one("#overview-graph").display
            await pilot.press("t")
            assert app.query_one("#overview-text").display
            assert not app.query_one("#overview-graph").display
            await pilot.press("b", "n")
            assert "two accounts" in str(app.query_one("#status", Static).content)
            await pilot.resize_terminal(80, 26)
            await pilot.pause()
            base = cast(Screen[None], app.default_screen)  # pyright: ignore[reportUnknownMemberType]
            assert base.has_class("compact")
            assert app.query_one("#action-bar").region.height == 3
            await pilot.press("2")
            register = cast(DataTable[object], app.query_one("#entry-table", DataTable))
            register_pane = app.query_one("#ledger-text")
            assert register.region.y <= register_pane.region.y + 2
            await pilot.press("1")
            await pilot.press("x")
            assert isinstance(app.screen, InfoScreen)
            assert "RETAINED SOURCE REFERENCES" in str(
                app.screen.query_one("#info-content", Static).content
            )
            await pilot.press("escape")
            await pilot.press("ctrl+p")
            assert app.screen.__class__.__name__ == "CommandPalette"
            await pilot.press("escape")

    asyncio.run(scenario())


def test_account_entry_review_post_detail_and_reversal(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    app = FinanceApp(service)

    async def scenario() -> None:
        async with app.run_test(size=(130, 52)) as pilot:
            await settled(app, pilot)
            for name, kind in (("Checking", "asset"), ("Opening equity", "equity")):
                await pilot.press("a")
                assert isinstance(app.screen, AccountForm)
                await pilot.press(*name)
                cast(Select[str], app.screen.query_one("#account-type", Select)).value = kind
                await pilot.pause()
                if kind == "equity":
                    assert not app.screen.query_one("#account-liquid", Checkbox).value
                app.screen.query_one("#create-account", Button).focus()
                await pilot.press("enter")
                await settled(app, pilot)
            assert app.snapshot is not None and len(app.snapshot.accounts) == 2
            await pilot.press("n")
            assert isinstance(app.screen, EntryForm)
            await pilot.press(*"Opening balance")
            app.screen.query_one("#entry-splits", TextArea).load_text(
                "Checking | 100.00\nOpening equity | -99.00"
            )
            app.screen.query_one("#prepare-entry", Button).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, EntryForm)
            assert "balance" in str(app.screen.query_one("#form-error", Static).content)
            app.screen.query_one("#entry-splits", TextArea).load_text(
                "Checking | 100.00\nOpening equity | -100.00"
            )
            await pilot.press("enter")
            await settled(app, pilot)
            assert isinstance(app.screen, ReviewScreen)
            assert not service.entries()
            assert len(service.drafts()) == 1
            # Review defaults to retaining the draft; posting is a separate explicit action.
            assert app.focused is app.screen.query_one("#keep-draft", Button)
            app.screen.query_one("#confirm-post", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert len(service.entries()) == 1
            assert app.snapshot is not None and len(app.snapshot.entries) == 1
            assert "RECORDED LIQUID CASH HISTORY" in str(
                app.query_one("#overview-history", Static).content
            )
            await pilot.press("2", "enter")
            assert isinstance(app.screen, InfoScreen)
            assert "Opening balance" in str(app.screen.query_one("#info-content", Static).content)
            await pilot.press("escape", "r")
            assert isinstance(app.screen, TextPrompt)
            await pilot.press("enter")
            await settled(app, pilot)
            assert isinstance(app.screen, ReviewScreen)
            app.screen.query_one("#confirm-post", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert len(service.entries()) == 2
            assert app.snapshot is not None
            assert all(balance.money.minor == 0 for balance in app.snapshot.balances)

    asyncio.run(scenario())


def test_draft_retention_search_and_filter(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    assert isinstance(service.create_account("Cash", AccountType.ASSET, "USD", True), Ok)
    assert isinstance(service.create_account("Income", AccountType.INCOME, "USD"), Ok)
    app = FinanceApp(service)

    async def scenario() -> None:
        async with app.run_test(size=(130, 52)) as pilot:
            await settled(app, pilot)
            await pilot.press("n")
            assert isinstance(app.screen, EntryForm)
            app.screen.query_one("#entry-description", Input).value = "Paycheck"
            accounts = {account.name: account for account in service.accounts()}
            cast(Select[str], app.screen.query_one("#entry-debit", Select)).value = accounts[
                "Cash"
            ].id.value
            cast(Select[str], app.screen.query_one("#entry-credit", Select)).value = accounts[
                "Income"
            ].id.value
            app.screen.query_one("#entry-amount", Input).value = "12.34"
            app.screen.query_one("#prepare-entry", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert isinstance(app.screen, ReviewScreen)
            await pilot.press("escape")
            await settled(app, pilot)
            assert not service.entries()
            app.search_text = "unmatched"
            app.action_refresh_data()
            await settled(app, pilot)
            assert app.query_one("#entry-table", DataTable).row_count == 0
            app.search_text = ""
            app.action_refresh_data()
            await settled(app, pilot)
            assert app.query_one("#entry-table", DataTable).row_count == 1
            await pilot.press("2", "enter")
            assert isinstance(app.screen, ReviewScreen)
            app.screen.query_one("#confirm-post", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            await pilot.press("slash")
            await pilot.press(*"unmatched", "enter")
            await settled(app, pilot)
            assert app.query_one("#entry-table", DataTable).row_count == 0
            await pilot.press("f")
            await pilot.press(*"salary=Paycheck", "enter")
            await settled(app, pilot)
            await pilot.press("f")
            await pilot.press(*"salary", "enter")
            await settled(app, pilot)
            assert app.search_text == "Paycheck"
            assert app.query_one("#entry-table", DataTable).row_count == 1

    asyncio.run(scenario())


def test_register_search_uses_balance_snapshot_revision(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    cash = service.create_account("Cash", AccountType.ASSET, "USD", True)
    income = service.create_account("Income", AccountType.INCOME, "USD")
    assert isinstance(cash, Ok) and isinstance(income, Ok)
    prepared = service.prepare_entry(
        EntryContent(
            date(2026, 9, 20),
            "Paycheck",
            (
                Posting(Ref(cash.value.id), Money(1200, "USD")),
                Posting(Ref(income.value.id), Money(-1200, "USD")),
            ),
        )
    )
    assert isinstance(prepared, Ok)
    assert isinstance(
        service.post_draft(prepared.value.id, prepared.value.content_hash, "ui-post"), Ok
    )
    app = FinanceApp(service)
    app.search_text = "paycheck"

    async def scenario() -> None:
        # A second entries() read would be from another SQLite snapshot and
        # could include a commit absent from the displayed balance revision.
        with patch.object(FinanceService, "entries", side_effect=AssertionError("second read")):
            async with app.run_test(size=(130, 42)) as pilot:
                await settled(app, pilot)
                assert app.snapshot is not None
                assert app.query_one("#entry-table", DataTable).row_count == 1
                app.search_text = "missing"
                app.action_refresh_data()
                await settled(app, pilot)
                assert app.query_one("#entry-table", DataTable).row_count == 0

    asyncio.run(scenario())
