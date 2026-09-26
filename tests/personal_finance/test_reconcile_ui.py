"""Statement review and evidence recall are reachable in the terminal workspace."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from textual.pilot import Pilot
from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TabbedContent

from core.result import Ok
from personal_finance.app import FinanceApp
from personal_finance.bootstrap import open_service
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.codec import encode_id
from personal_finance.ui.forms import InfoScreen, TextPrompt
from personal_finance.ui.reconcile_forms import ReconcileActionMenu, ReconcileInputForm


async def settled(app: FinanceApp, pilot: Pilot[None]) -> None:
    await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
    await pilot.pause()


def test_statement_form_and_recall(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    checking = service.create_account("Checking", AccountType.ASSET, "USD", True)
    assert isinstance(checking, Ok)
    app = FinanceApp(service)
    last_month_end = date.today().replace(day=1) - timedelta(days=1)

    async def scenario() -> None:
        async with app.run_test(size=(140, 52)) as pilot:
            await settled(app, pilot)
            await pilot.press("8", "u")
            assert app.query_one("#finance-tabs", TabbedContent).active == "reconcile"
            assert isinstance(app.screen, ReconcileActionMenu)
            await pilot.press("enter")
            assert isinstance(app.screen, ReconcileInputForm)
            form = app.screen
            cast(Select[str], form.query_one("#reconcile-account", Select)).value = encode_id(
                checking.value.id
            )
            form.query_one("#reconcile-month", Input).value = f"{last_month_end:%Y-%m}"
            form.query_one("#reconcile-opening", Input).value = "0.00"
            form.query_one("#reconcile-closing", Input).value = "0.00"
            form.query_one("#reconcile-complete", Checkbox).value = True
            form.query_one("#reconcile-submit", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, TextPrompt)
            app.screen.query_one("#prompt-input", Input).value = "RECORD"
            app.screen.query_one("#prompt-submit", Button).press()
            await settled(app, pilot)
            assert len(service.reconcile_records().statements) == 1
            assert app.query_one("#reconcile-table", DataTable).row_count == 1
            await pilot.press("x")
            assert isinstance(app.screen, InfoScreen), str(app.query_one("#status", Static).content)
            assert "statement" in str(app.screen.query_one("#info-content", Static).content)
            await pilot.press("escape")
            await pilot.press("u")
            assert isinstance(app.screen, ReconcileActionMenu)
            app.screen.query_one("#reconcile-action-close", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, ReconcileInputForm)
            cast(Select[str], app.screen.query_one("#reconcile-choice", Select)).value = (
                f"{last_month_end:%Y-%m}"
            )
            app.screen.query_one("#reconcile-rationale", Input).value = "Reviewed zero activity"
            app.screen.query_one("#reconcile-submit", Button).press()
            await pilot.pause()
            assert isinstance(app.screen, TextPrompt)
            assert "Checking" in app.screen.hint
            app.screen.query_one("#prompt-input", Input).value = "CLOSE"
            app.screen.query_one("#prompt-submit", Button).press()
            await settled(app, pilot)
            assert len(service.reconcile_records().closes) == 1
            await pilot.press("z")
            assert isinstance(app.screen, TextPrompt)
            app.screen.query_one("#prompt-input", Input).value = "Checking"
            app.screen.query_one("#prompt-submit", Button).press()
            await settled(app, pilot)
            for _ in range(10):
                if type(app.screen) is InfoScreen:
                    break
                await pilot.pause(0.05)
            assert isinstance(app.screen, InfoScreen), str(app.query_one("#status", Static).content)
            assert "Checking" in str(app.screen.query_one("#info-content", Static).content)

    asyncio.run(scenario())
