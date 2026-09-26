"""Keyboard workflows for cash planning against the real local service/store."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TabbedContent

from core.identity import Id, Ref
from core.result import Ok
from core.time import MonotonicInstant, WallInstant
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.app import FinanceApp
from personal_finance.application.service import FinanceService
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.cash import Cadence
from personal_finance.domain.codec import encode_id
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money
from personal_finance.ui.cash_forms import CashAction, CashActionMenu, CashInputForm
from personal_finance.ui.forms import InfoScreen

TODAY = date(2026, 9, 21)


class FixedClock:
    def now(self) -> WallInstant:
        return WallInstant(datetime(2026, 9, 21, 12, tzinfo=UTC))


class FixedMonotonic:
    space = Id(Kind("finance.clock"), "cash-ui")

    def now(self) -> MonotonicInstant:
        return MonotonicInstant(self.space, 0)


class SequentialIds:
    def __init__(self) -> None:
        self.count = 0

    def new(self, kind: Kind) -> Id:
        self.count += 1
        return Id(kind, f"cash-ui-{self.count}")


def service_at(tmp_path: Path, *, opening: bool = False) -> FinanceService:
    store = FinanceStore(tmp_path / "cash-ui.sqlite3")
    store.initialize()
    service = FinanceService(store, FixedClock(), FixedMonotonic(), SequentialIds())
    if opening:
        cash = service.create_account("Checking", AccountType.ASSET, "USD", True)
        equity = service.create_account("Opening equity", AccountType.EQUITY, "USD")
        assert isinstance(cash, Ok) and isinstance(equity, Ok)
        draft = service.prepare_entry(
            EntryContent(
                TODAY,
                "Opening cash",
                (
                    Posting(Ref(cash.value.id), Money(100000, "USD")),
                    Posting(Ref(equity.value.id), Money(-100000, "USD")),
                ),
            )
        )
        assert isinstance(draft, Ok)
        assert isinstance(service.post_draft(draft.value.id, draft.value.content_hash, "open"), Ok)
    return service


async def settled(app: FinanceApp, pilot: Pilot[None]) -> None:
    for _ in range(3):
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()


async def choose(app: FinanceApp, pilot: Pilot[None], action: CashAction) -> CashInputForm:
    await pilot.press("c")
    assert isinstance(app.screen, CashActionMenu)
    app.screen.query_one(f"#cash-action-{action}", Button).focus()
    await pilot.press("enter")
    assert isinstance(app.screen, CashInputForm)
    return app.screen


async def save(app: FinanceApp, pilot: Pilot[None]) -> None:
    app.screen.query_one("#cash-save", Button).focus()
    await pilot.press("enter")
    await settled(app, pilot)
    assert not isinstance(app.screen, CashInputForm)
    assert not app.query_one("#status", Static).has_class("error"), str(
        app.query_one("#status", Static).content
    )


def test_empty_cash_horizon_keyboard_modes_and_compact_layout(tmp_path: Path) -> None:
    app = FinanceApp(service_at(tmp_path))

    async def scenario() -> None:
        async with app.run_test(size=(130, 42)) as pilot:
            await settled(app, pilot)
            await pilot.press("3")
            assert app.query_one("#finance-tabs", TabbedContent).active == "cash"
            assert "unavailable" in str(app.query_one("#cash-summary", Static).content).lower()
            assert app.query_one("#cash-table", DataTable).row_count == 0
            await pilot.press("h")
            assert app.cash_horizon == 60
            await pilot.press("h")
            assert app.cash_horizon == 90
            await pilot.press("g")
            assert app.query_one("#cash-graph").display
            assert not app.query_one("#cash-text").display
            chart = str(app.query_one("#cash-projection-chart", Static).content)
            assert "Unavailable" in chart
            assert "0.00" not in chart
            await pilot.press("t", "x")
            assert isinstance(app.screen, InfoScreen)
            assert "MISSING INPUTS" in str(app.screen.query_one("#info-content", Static).content)
            await pilot.press("escape")
            await pilot.resize_terminal(80, 26)
            await pilot.pause()
            assert app.query_one("#cash-controls").region.height == 3
            assert app.query_one("#cash-text-content").display
            await pilot.press("c", "enter")
            await pilot.pause()
            assert "liquid" in str(app.query_one("#status", Static).content)

    asyncio.run(scenario())


def test_cash_forms_record_all_inputs_and_retire_without_posting(tmp_path: Path) -> None:
    service = service_at(tmp_path, opening=True)
    cash = next(account for account in service.accounts() if account.liquid)
    app = FinanceApp(service)

    async def scenario() -> None:
        async with app.run_test(size=(140, 55)) as pilot:
            await settled(app, pilot)
            form = await choose(app, pilot, "schedule")
            form.query_one("#cash-label", Input).value = "Rent"
            cast(Select[str], form.query_one("#cash-account", Select)).value = encode_id(cash.id)
            form.query_one("#cash-amount", Input).value = "-250.00"
            form.query_one("#cash-date", Input).value = "2026-09-22"
            await save(app, pilot)
            assert app.snapshot is not None
            schedule = app.snapshot.cash_records.schedules[0]
            assert schedule.cadence is Cadence.ONCE

            form = await choose(app, pilot, "allocation")
            form.query_one("#cash-label", Input).value = "Rent envelope"
            form.query_one("#cash-amount", Input).value = "250.00"
            cast(Select[str], form.query_one("#cash-linked-schedule", Select)).value = encode_id(
                schedule.id
            )
            form.query_one("#cash-linked-date", Input).value = "2026-09-22"
            await save(app, pilot)

            form = await choose(app, pilot, "hold")
            form.query_one("#cash-label", Input).value = "Card authorization"
            form.query_one("#cash-amount", Input).value = "30.00"
            form.query_one("#cash-until", Input).value = "2026-09-24"
            await save(app, pilot)

            form = await choose(app, pilot, "floor")
            form.query_one("#cash-amount", Input).value = "100.00"
            await save(app, pilot)

            form = await choose(app, pilot, "observation")
            cast(Select[str], form.query_one("#cash-account", Select)).value = encode_id(cash.id)
            form.query_one("#cash-amount", Input).value = "1000.00"
            form.query_one("#cash-until", Input).value = "2026-09-28"
            form.query_one("#cash-source", Input).value = "Bank statement reviewed locally"
            await save(app, pilot)

            form = await choose(app, pilot, "coverage")
            assert not form.query_one("#cash-accounts-complete", Checkbox).value
            assert not form.query_one("#cash-schedules-complete", Checkbox).value
            form.query_one("#cash-until", Input).value = "2026-12-20"
            form.query_one("#cash-source", Input).value = "Reviewed account and bill inventory"
            form.query_one("#cash-accounts-complete", Checkbox).value = True
            form.query_one("#cash-schedules-complete", Checkbox).value = True
            await save(app, pilot)
            assert app.snapshot is not None
            assert len(app.snapshot.entries) == 1
            assert app.snapshot.cash_plans[0].projection.currencies[0].safe_to_allocate is not None
            assert "Safe to allocate Unavailable" not in str(
                app.query_one("#cash-summary", Static).content
            )
            assert app.query_one("#cash-table", DataTable).row_count >= 3
            await pilot.press("x")
            assert isinstance(app.screen, InfoScreen)
            explanation = str(app.screen.query_one("#info-content", Static).content)
            assert "finance.cash_schedule" in explanation
            assert "Rent envelope" in explanation
            assert "RETAINED SOURCE REFERENCES" in explanation
            await pilot.press("escape")
            hold = app.snapshot.cash_records.holds[0]
            form = await choose(app, pilot, "retire")
            cast(Select[str], form.query_one("#cash-target", Select)).value = encode_id(hold.id)
            form.query_one("#cash-source", Input).value = "Authorization cleared"
            await save(app, pilot)
            assert app.snapshot is not None
            assert app.snapshot.cash_records.retirements[0].target.id == hold.id
            assert len(app.snapshot.cash_records.holds) == 1
            assert len(service.entries()) == 1
            await pilot.press("3", "enter")
            assert isinstance(app.screen, InfoScreen)
            assert "Protected allocations" in str(
                app.screen.query_one("#info-content", Static).content
            )

    asyncio.run(scenario())


def test_cash_form_invalid_decimal_is_kept_for_correction(tmp_path: Path) -> None:
    app = FinanceApp(service_at(tmp_path, opening=True))

    async def scenario() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await settled(app, pilot)
            form = await choose(app, pilot, "floor")
            form.query_one("#cash-amount", Input).value = "1.001"
            form.query_one("#cash-save", Button).focus()
            await pilot.press("enter")
            assert isinstance(app.screen, CashInputForm)
            assert "precision" in str(form.query_one("#cash-form-error", Static).content)
            assert app.snapshot is not None
            assert not app.snapshot.cash_records.floor_changes
            await pilot.press("escape")

    asyncio.run(scenario())


def test_cash_service_failure_stays_visible_and_does_not_post(tmp_path: Path) -> None:
    app = FinanceApp(service_at(tmp_path, opening=True))

    async def scenario() -> None:
        async with app.run_test(size=(120, 48)) as pilot:
            await settled(app, pilot)
            form = await choose(app, pilot, "hold")
            form.query_one("#cash-label", Input).value = "Invalid hold"
            form.query_one("#cash-amount", Input).value = "-10"
            form.query_one("#cash-save", Button).focus()
            await pilot.press("enter")
            await settled(app, pilot)
            assert app.query_one("#status", Static).has_class("error")
            assert "positive" in str(app.query_one("#status", Static).content)
            assert app.snapshot is not None
            assert not app.snapshot.cash_records.holds
            assert len(app.snapshot.entries) == 1

    asyncio.run(scenario())
