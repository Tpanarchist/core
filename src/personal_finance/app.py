"""Private local finance terminal, driven by the shared application services."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import cast

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Static, TabbedContent, TabPane
from textual.worker import (
    Worker,
    get_current_worker,  # pyright: ignore[reportUnknownVariableType]
)

from core.error import Error
from core.identity import Id
from core.result import Err, Result
from core.time import SystemClock
from personal_finance.adapters.memory import FinanceMemory, RecallResult
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.application.cash_projection import CashPoint, CashProjection
from personal_finance.application.history import CurrencyHistory, recorded_liquid_history
from personal_finance.application.service import (
    CashPlanView,
    DebtComparisonView,
    FinanceService,
    LedgerSnapshot,
)
from personal_finance.csv_io import ImportReport, export_csv, stage_csv
from personal_finance.display import safe_display
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import AssetCategory, AssetFlowKind
from personal_finance.domain.ledger import Draft, EntryContent, JournalEntry
from personal_finance.domain.money import MAX_MINOR, Money, currency_exponent
from personal_finance.domain.nodes import FundingKind
from personal_finance.ui.cash_forms import (
    AllocationRequest,
    CashAction,
    CashActionMenu,
    CashInputForm,
    CashRequest,
    CoverageRequest,
    FloorRequest,
    HoldRequest,
    ObservationRequest,
    ScheduleRequest,
)
from personal_finance.ui.forms import (
    AccountForm,
    EntryForm,
    InfoScreen,
    ReviewScreen,
    TextPrompt,
    content_text,
)
from personal_finance.ui.model_forms import (
    ModelAction,
    ModelActionMenu,
    ModelInputForm,
    ModelRequest,
)
from personal_finance.ui.reconcile_forms import (
    ReconcileAction,
    ReconcileActionMenu,
    ReconcileInputForm,
    ReconcileRequest,
)

_TABS = ("overview", "ledger", "cash", "debts", "assets", "nodes", "forecast", "reconcile")
_DEFERRED = {
    "forecast": (
        "SCENARIO COMPARISON",
        "Cash horizons are in tab 3. Named scenarios arrive later.",
    ),
}


def _bars(values: tuple[tuple[str, Money], ...]) -> Text:
    result = Text()
    if not values:
        result.append("No recorded values to graph.\n", style="#c1c2fa")
        result.append("Create accounts and post a reviewed entry to begin.")
        return result
    by_currency: dict[str, list[tuple[str, Money]]] = {}
    for label, money in values:
        by_currency.setdefault(money.currency, []).append((label, money))
    for currency, rows in sorted(by_currency.items()):
        result.append(f"{currency} · each currency uses its own scale\n", style="bold #22c6bc")
        maximum = max(abs(money.minor) for _, money in rows) or 1
        for label, money in rows:
            length = abs(money.minor) * 22 // maximum
            result.append(f"{safe_display(label)[:25]}\n", style="#c1c2fa")
            glyph = "◀" if money.minor < 0 else "▶"
            result.append(
                f"{glyph} {'━' * length}  {money.format()}\n",
                style="#ff334e" if money.minor < 0 else "#57b277",
            )
        result.append("\n")
    return result


def _debt_scenario_chart(comparison: DebtComparisonView) -> Text:
    chart = Text("\nPAYOFF COMPARISON · CONDITIONAL MONTHLY MODEL\n", style="bold #22c6bc")
    known = [item.payoff_months for item in comparison.scenarios if item.payoff_months]
    maximum = max(known, default=1)
    for scenario in comparison.scenarios:
        label = scenario.strategy.value.replace("_", " ").title()
        if scenario.payoff_months is None:
            chart.append(f"{label}: unavailable · {scenario.missing_reason}\n", style="#ea6be8")
            continue
        length = max(1, scenario.payoff_months * 22 // maximum)
        chart.append(f"{label}\n", style="#c1c2fa")
        interest = scenario.total_interest.format() if scenario.total_interest else "unknown"
        chart.append(
            f"▶ {'━' * length}  {scenario.payoff_months} months\n",
            style="#57b277",
        )
        chart.append(f"  interest {interest}\n", style="#c1c2fa")
    chart.append(
        "Months to payoff; shorter bars finish sooner. No payments are posted.\n", style="#c1c2fa"
    )
    return chart


def _format_total(minor: int, currency: str) -> str:
    """Display a derived sum exactly even if it exceeds one Money value's bound."""
    exponent = currency_exponent(currency)
    sign = "-" if minor < 0 else ""
    whole, fraction = divmod(abs(minor), 10**exponent)
    amount = str(whole) if exponent == 0 else f"{whole}.{fraction:0{exponent}d}"
    return f"{sign}{amount} {currency}"


def _low_total(item: CurrencyHistory) -> str:
    return _format_total(min(item.points, key=lambda point: point.minor).minor, item.currency)


def _history_chart(series: tuple[CurrencyHistory, ...]) -> Text:
    chart = Text()
    chart.append("RECORDED LIQUID CASH HISTORY\n", style="bold #22c6bc")
    chart.append("Posted ledger · end-of-day effective dates · no forecast\n\n", style="#c1c2fa")
    if not series or all(not item.points for item in series):
        chart.append("No posted liquid-account activity to chart.\n", style="#c1c2fa")
        return chart
    bars = "▁▂▃▄▅▆▇█"
    for item in series:
        if not item.points:
            chart.append(f"{item.currency}: no recorded history\n", style="#c1c2fa")
            continue
        points = item.points[-24:]
        minimum = min(point.minor for point in points)
        maximum = max(point.minor for point in points)
        span = maximum - minimum
        glyphs = "".join(
            bars[3 if span == 0 else (point.minor - minimum) * 7 // span] for point in points
        )
        low = min(points, key=lambda point: (point.minor, point.effective_date))
        chart.append(
            f"{item.currency} · {points[0].effective_date} → {points[-1].effective_date}"
            f" · last {len(points)} of {len(item.points)} boundaries\n",
            style="#c1c2fa",
        )
        chart.append(glyphs + "\n", style="#57b277")
        chart.append(
            f"End {_format_total(points[-1].minor, item.currency)}"
            f" · Low {_format_total(low.minor, item.currency)} on {low.effective_date}\n\n",
            style="#f8ffbb",
        )
    return chart


def _matches_search(content: EntryContent, query: str, names: dict[Id, str]) -> bool:
    return (
        not query
        or query
        in " ".join(
            (
                content.description,
                content.source,
                *content.tags,
                content.effective_date.isoformat(),
                *(names.get(post.account.id, "") for post in content.postings),
            )
        ).casefold()
    )


def _matching_entries(snapshot: LedgerSnapshot, search: str) -> tuple[JournalEntry, ...]:
    """Filter the same immutable input revision used for displayed balances."""
    query = search.casefold().strip()
    names = {account.id: account.name for account in snapshot.accounts}
    return tuple(
        entry for entry in snapshot.entries if _matches_search(entry.content, query, names)
    )


def _cash_projection_chart(projection: CashProjection) -> Text:
    """Calendar-spaced step lines with explicit initial, projected, and unknown labels."""
    chart = Text("CASH HORIZON · CONDITIONAL PROJECTION\n", style="bold #22c6bc")
    chart.append(
        f"{projection.start_on} → {projection.through_on} · {projection.horizon_days} days\n",
        style="#c1c2fa",
    )
    chart.append("● recorded opening  ─ projected  · floor\n", style="#c1c2fa")
    chart.append("Enter on an event for exact values; x explains assumptions.\n\n", style="#c1c2fa")
    if not projection.currencies:
        chart.append(
            "Unavailable: no liquid-account currency has been recorded.\n", style="#ea6be8"
        )
        return chart
    width, height = 34, 7
    for currency in projection.currencies:
        chart.append(f"{currency.currency} · own scale\n", style="bold #f8ffbb")
        if not currency.points:
            chart.append("No projection boundaries available.\n", style="#ea6be8")
            continue
        if currency.missing_inputs:
            chart.append("PARTIAL · safe allocation unavailable\n", style="#ea6be8")
        values: list[int] = []
        floors: list[int | None] = []
        cursor = 0
        for column in range(width):
            day = projection.start_on.toordinal() + column * projection.horizon_days // (width - 1)
            while (
                cursor + 1 < len(currency.points)
                and currency.points[cursor + 1].on.toordinal() <= day
            ):
                cursor += 1
            point = currency.points[cursor]
            values.append(point.balance.minor)
            floors.append(point.floor.minor if point.floor is not None else None)
        bounds = (*values, *(floor for floor in floors if floor is not None))
        low, high = min(bounds), max(bounds)
        span = max(high - low, 1)
        canvas = [[" " for _ in range(width)] for _ in range(height)]
        rows = [height - 1 - (value - low) * (height - 1) // span for value in values]
        for column, floor in enumerate(floors):
            if floor is not None:
                row = height - 1 - (floor - low) * (height - 1) // span
                canvas[row][column] = "·"
        for column, row in enumerate(rows):
            if column:
                for between in range(min(rows[column - 1], row), max(rows[column - 1], row) + 1):
                    canvas[between][column] = "│"
            canvas[row][column] = "●" if column == 0 else "─"
        chart.append(f"{_format_total(high, currency.currency)}\n", style="#c1c2fa")
        for row in canvas:
            chart.append("│", style="#6a8ba7")
            for character in row:
                chart.append(
                    character,
                    style=(
                        "#57b277"
                        if character == "●"
                        else "#ea6be8"
                        if character == "·"
                        else "#22c6bc"
                    ),
                )
            chart.append("\n")
        chart.append("└" + "─" * width + "\n", style="#6a8ba7")
        chart.append(f"{_format_total(low, currency.currency)}\n", style="#c1c2fa")
        chart.append(
            f"Start {_maybe_money(currency.starting_balance)}\n"
            f"Low {_maybe_money(currency.minimum_balance)} on {currency.minimum_on or 'unknown'}\n"
            f"End {currency.points[-1].balance.format()}\n",
            style="#f8ffbb",
        )
        if all(floor is None for floor in floors):
            chart.append("Cash floor unknown; no floor line is plotted.\n", style="#ea6be8")
        chart.append("\n")
    return chart


def _maybe_money(value: Money | None) -> str:
    return value.format() if value is not None else "Unavailable"


class FinanceApp(App[None]):
    """All service calls run in owned workers behind one serialized access lock."""

    CSS_PATH = "finance.tcss"
    TITLE = "Personal Finance"
    BINDINGS = [
        Binding("1", "tab('overview')", "Overview", show=False),
        Binding("2", "tab('ledger')", "Ledger", show=False),
        Binding("3", "tab('cash')", "Cash", show=False),
        Binding("4", "tab('debts')", "Debts", show=False),
        Binding("5", "tab('assets')", "Assets", show=False),
        Binding("6", "tab('nodes')", "Nodes", show=False),
        Binding("7", "tab('forecast')", "Forecast", show=False),
        Binding("8", "tab('reconcile')", "Reconcile", show=False),
        Binding("n", "new_entry", "New entry"),
        Binding("a", "new_account", "Account"),
        Binding("slash", "search", "Search"),
        Binding("enter", "detail", "Detail", show=False),
        Binding("r", "reverse", "Reverse", show=False),
        Binding("d", "duplicate", "Duplicate", show=False),
        Binding("f", "filters", "Filters", show=False),
        Binding("i", "import_csv", "Import", show=False),
        Binding("e", "export_csv", "Export", show=False),
        Binding("x", "explain", "Explain", show=False),
        Binding("z", "recall", "Recall", show=False),
        Binding("c", "cash_manage", "Cash plan", show=False),
        Binding("m", "model_manage", "Manage", show=False),
        Binding("u", "reconcile_manage", "Statement review", show=False),
        Binding("h", "cash_horizon", "Horizon", show=False),
        Binding("t", "mode('text')", "Text", show=False),
        Binding("g", "mode('graph')", "Graph", show=False),
        Binding("b", "mode('split')", "Split", show=False),
        Binding("left_square_bracket", "date_range", "Date range", show=False),
        Binding("ctrl+r", "refresh_data", "Refresh", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, service: FinanceService, *, demo: bool = False) -> None:
        super().__init__()
        self.service = service
        self.demo = demo
        self.snapshot: LedgerSnapshot | None = None
        self.search_text = ""
        self.date_start: date | None = None
        self.date_end: date | None = None
        self.view_mode = "split"
        self._operation_lock = RLock()
        self._refresh_generation = 0
        self._rows: dict[str, JournalEntry | Draft] = {}
        self._filtered_entries: tuple[JournalEntry, ...] = ()
        self._saved_filters: tuple[tuple[str, str], ...] = ()
        self._busy = False
        self.cash_horizon = 30
        self._debt_comparison: DebtComparisonView | None = None
        self._debt_comparison_revision: int | None = None
        self._cash_event_rows: dict[str, tuple[str, CashPoint]] = {}

    def compose(self) -> ComposeResult:
        profile = "SYNTHETIC DEMO · ISOLATED DATABASE" if self.demo else "PRIVATE LOCAL PROFILE"
        yield Static(
            Text.assemble(("  ◇ PERSONAL FINANCE", "bold #f8ffbb"), (f"    {profile}", "#22c6bc")),
            id="masthead",
        )
        yield Static(
            "ACTUAL RECORDS  ·  loading persisted data  ·  integrations OFF",
            id="context-bar",
            markup=False,
        )
        with TabbedContent(initial="overview", id="finance-tabs"):
            for number, name in enumerate(_TABS, 1):
                with TabPane(f"{number} {name.title()}", id=name):
                    if name in _DEFERRED:
                        heading, description = _DEFERRED[name]
                        with VerticalScroll(classes="deferred-panel"):
                            yield Static(heading, classes="section-title")
                            yield Static("UNAVAILABLE · future delivery slice", classes="warning")
                            yield Static(description, markup=False)
                            yield Static(
                                "No estimates, observations, or external accounts have been "
                                "assumed. The recorded ledger remains available in tabs 1–3.",
                                classes="muted",
                                markup=False,
                            )
                            yield Static(
                                "Financial execution and connectors are disabled.",
                                classes="muted",
                                markup=False,
                            )
                    else:
                        yield Static("", id=f"{name}-summary", classes="summary", markup=False)
                        if name == "cash":
                            with Horizontal(id="cash-controls"):
                                for horizon in (30, 60, 90):
                                    yield Button(
                                        f"{horizon} days",
                                        id=f"cash-horizon-{horizon}",
                                        classes="cash-horizon",
                                    )
                                yield Button("＋ Cash plan [c]", id="cash-manage")
                                yield Static(
                                    "[h] horizon  [x] explain", id="cash-key-hints", markup=False
                                )
                        if name in ("debts", "assets", "nodes"):
                            with Horizontal(classes="model-controls"):
                                yield Button(f"＋ Manage {name} [m]", id=f"{name}-manage")
                                yield Static("[x] explain · [t/g/b] view", markup=False)
                        if name == "reconcile":
                            with Horizontal(classes="model-controls"):
                                yield Button("＋ Review statement [u]", id="reconcile-manage")
                                yield Static("[z] recall evidence · [t/g/b] view", markup=False)
                        with Horizontal(classes="view-body", id=f"{name}-body"):
                            with VerticalScroll(classes="text-view", id=f"{name}-text"):
                                yield Static("", id=f"{name}-text-content", markup=False)
                                if name == "ledger":
                                    yield DataTable(
                                        id="entry-table", cursor_type="row", zebra_stripes=True
                                    )
                                else:
                                    yield DataTable(
                                        id=f"{name}-table", cursor_type="row", zebra_stripes=True
                                    )
                            with VerticalScroll(classes="graph-view", id=f"{name}-graph"):
                                if name == "cash":
                                    yield Static("", id="cash-projection-chart")
                                if name in ("overview", "cash"):
                                    yield Static("", id=f"{name}-history")
                                yield Static(
                                    {
                                        "debts": "RECORDED DEBT BALANCES",
                                        "assets": "MANUAL ASSET VALUATIONS · KNOWN VALUES ONLY",
                                        "nodes": "RECORDED NODE OPERATIONS · 30 DAYS",
                                        "reconcile": "REVIEWED STATEMENT AND LEDGER ITEMS",
                                    }.get(name, "ACTUAL · RECORDED ACCOUNT BALANCES"),
                                    classes="section-title",
                                )
                                yield Static("", id=f"{name}-chart")
                                yield Static(
                                    {
                                        "cash": "Recorded history is separate from forecast.",
                                        "debts": (
                                            "Bars show recorded balances; payoff models appear "
                                            "after comparison."
                                        ),
                                        "assets": (
                                            "Only fresh manual values are graphed; "
                                            "unknown is not zero."
                                        ),
                                        "nodes": (
                                            "Bars show recorded 30-day operating net, "
                                            "excluding funding."
                                        ),
                                        "reconcile": (
                                            "Bars count accepted matches and explained exceptions; "
                                            "zero items are shown as empty, not complete."
                                        ),
                                    }.get(
                                        name, "Actual records only. Dates, end and low are labeled."
                                    ),
                                    classes="muted",
                                    markup=False,
                                )
        with Horizontal(id="action-bar"):
            yield Button("＋ Entry", id="new-entry", variant="primary")
            yield Button("＋ Account", id="new-account")
            yield Button("Explain", id="explain")
            yield Static(
                "[t] TEXT  [g] GRAPH  [b] SPLIT   / Search   Ctrl+P Commands",
                id="mode-hints",
                markup=False,
            )
        yield Static("Ready", id="status", markup=False)
        yield Footer()

    def _table(self, selector: str) -> DataTable[str | Text]:
        return cast(DataTable[str | Text], self.query_one(selector, DataTable))

    def on_mount(self) -> None:
        self._table("#overview-table").add_columns("Account", "Type", "Recorded")
        self._table("#cash-table").add_columns(
            "Date", "Boundary / currency", "Change", "Projected", "Available"
        )
        self._table("#entry-table").add_columns(
            "Effective",
            "Recorded UTC",
            "Description",
            "Accounts",
            "Tags",
            "Source",
            "State",
            "Debit volume",
        )
        self._table("#debts-table").add_columns("Debt", "Balance", "APR", "Minimum", "Next due")
        self._table("#assets-table").add_columns(
            "Position", "Category", "Value", "Unrealized", "Period gain", "Freshness"
        )
        self._table("#nodes-table").add_columns(
            "Node", "Revenue", "Expenses", "Net", "Capital", "Runway"
        )
        self._table("#reconcile-table").add_columns(
            "Account", "Month", "Opening Δ", "Closing Δ", "Lines Δ", "Decisions", "State"
        )
        self.action_refresh_data()

    def on_resize(self, event: Resize) -> None:
        # Textual leaves the default Screen's result type unparameterized.
        base = cast(Screen[None], self.default_screen)  # pyright: ignore[reportUnknownMemberType]
        compact = event.size.width < 105
        base.set_class(compact, "compact")
        # Stacking two panes leaves the register below the fold on short
        # terminals. Open compact workspaces in text mode; graph remains [g].
        if compact and self.view_mode == "split":
            self.call_after_refresh(self.action_mode, "text")

    def get_system_commands(self, screen: Screen[object]) -> Iterable[SystemCommand]:
        yield SystemCommand("Quit", "Close Personal Finance", self.action_quit)
        commands: tuple[tuple[str, str, Callable[[], None]], ...] = (
            (
                "New journal entry",
                "Prepare, review, and post a balanced entry",
                self.action_new_entry,
            ),
            ("New account", "Add a financial account", self.action_new_account),
            ("Search ledger", "Search descriptions, account names, and tags", self.action_search),
            (
                "Import CSV",
                "Stage reviewable drafts from an explicit CSV schema",
                self.action_import_csv,
            ),
            ("Export ledger", "Write a lossless posting export", self.action_export_csv),
            (
                "Explain recorded balances",
                "Inspect source records and Core provenance",
                self.action_explain,
            ),
            (
                "Recall accepted evidence",
                "Search retained local Memory evidence",
                self.action_recall,
            ),
            (
                "Review selected draft",
                "Inspect or review the selected register row",
                self.action_detail,
            ),
            ("Reverse selected entry", "Prepare a linked reversing entry", self.action_reverse),
            (
                "Duplicate selected entry",
                "Prepare a new draft from the selected entry",
                self.action_duplicate,
            ),
            ("Saved search filters", "Save or apply a named register search", self.action_filters),
            ("Set date range", "Filter effective dates in the register", self.action_date_range),
            (
                "Manage cash plan",
                "Schedules, allocations, holds, floors, evidence and coverage",
                self.action_cash_manage,
            ),
            (
                "Manage debt, asset, or node",
                "Open a local input form for the current model tab",
                self.action_model_manage,
            ),
            (
                "Review monthly statements",
                "Record, match, explain, resolve, and close",
                self.action_reconcile_manage,
            ),
            ("Change cash horizon", "Cycle 30, 60 and 90 days", self.action_cash_horizon),
        )
        for title, help_text, callback in commands:
            yield SystemCommand(title, help_text, callback)

    def _status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.update(safe_display(message))
        status.set_class(error, "error")

    def action_recall(self) -> None:
        self.push_screen(
            TextPrompt("EVIDENCE RECALL", "Literal text in retained finance change evidence"),
            self._recall_text,
        )

    def _recall_text(self, query: str | None) -> None:
        if query is None or not query.strip():
            return
        self._status("Searching retained evidence…")

        def load() -> RecallResult:
            store = cast(FinanceStore, self.service.store)
            path = store.path.parent / (
                "demo-memory.sqlite3" if self.demo else "personal-memory.sqlite3"
            )
            bridge = FinanceMemory(store, path, "demo" if self.demo else "personal")
            bridge.deliver()
            return bridge.recall(query, SystemClock().now())

        self._perform(load, self._recall_ready)

    def _recall_ready(self, result: RecallResult) -> None:
        if result.unavailable is not None:
            self._status("Memory recall unavailable; finance evidence remains local.", error=True)
            return
        lines = [
            f"{len(result.items)} results · {result.excluded} beyond display limit · "
            f"{result.pending} pending / {result.failed} failed deliveries",
        ]
        for item in result.items:
            lines.append(
                f"\n{item.at.value.isoformat()} · {safe_display(item.description)}\n"
                f"Source: {safe_display(item.source)}\n"
                f"Subject: {item.subject.id.kind.value}/{item.subject.id.value}\n"
                f"Evidence: {item.id.kind.value}/{item.id.value}\n"
                f"Relevance: {', '.join(kind.value for kind in item.relevance)}"
            )
        if not result.items:
            lines.append("No indexed evidence matched this literal text.")
        self.push_screen(InfoScreen("RECALLED EVIDENCE", "\n".join(lines)))
        self._status("Evidence recall complete.")

    @work(thread=True, group="finance-operations", exit_on_error=False)
    def _perform[T](self, operation: Callable[[], T], completed: Callable[[T], None]) -> None:
        worker = cast(Worker[object], get_current_worker())
        try:
            with self._operation_lock:
                if worker.is_cancelled:
                    return
                value = operation()
            if not worker.is_cancelled:
                self.call_from_thread(completed, value)
        except Exception as exc:
            if not worker.is_cancelled:
                self.call_from_thread(self._failed_operation, str(exc))

    def _failed_operation(self, message: str) -> None:
        self._busy = False
        self._status(f"Operation failed: {message}", error=True)

    def action_refresh_data(self) -> None:
        self._refresh_generation += 1
        generation = self._refresh_generation
        search = self.search_text

        def load() -> tuple[Result[LedgerSnapshot, Error], tuple[tuple[str, str], ...]]:
            return self.service.snapshot(), self.service.filters()

        def loaded(
            data: tuple[Result[LedgerSnapshot, Error], tuple[tuple[str, str], ...]],
        ) -> None:
            if generation != self._refresh_generation:
                return
            result, filters = data
            if isinstance(result, Err):
                self._status(result.error.message, error=True)
                return
            self.snapshot = result.value
            self._filtered_entries = _matching_entries(result.value, search)
            self._saved_filters = filters
            self._render_snapshot()

        self._perform(load, loaded)

    def _render_snapshot(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        at = snapshot.provenance.at.value.strftime("%Y-%m-%d %H:%M UTC")
        self.query_one("#context-bar", Static).update(
            f"ACTUAL RECORDS  ·  {at}  ·  revision {snapshot.revision}  ·  integrations OFF"
        )
        total = len(snapshot.entries)
        pending = tuple(draft for draft in snapshot.drafts if draft.status == "pending")
        self.query_one("#overview-summary", Static).update(
            f"NET WORTH  Unavailable · incomplete coverage     "
            f"{len(snapshot.accounts)} accounts  /  {total} posted  /  {len(pending)} drafts"
        )
        self.query_one("#overview-text-content", Static).update(
            "RECORDED BALANCES\n" + snapshot.coverage + "\n"
            "Whole-profile net worth: unknown. Values below are known for recorded accounts only.\n"
            "Cash planning: Cash [3]. Debt terms and payoff: Debts [4]. "
            "Valuations: Assets [5]. Operating flow: Nodes [6].\n"
            "Next action: add ledger accounts and review each model's missing inputs.\n"
        )
        if snapshot.reconcile_view is not None:
            portfolio = snapshot.reconcile_view.portfolio
            self.query_one("#overview-text-content", Static).update(
                str(self.query_one("#overview-text-content", Static).content)
                + f"Reconciliation: {len(portfolio.closes)} closed periods; "
                + f"{sum(not item.closed and not item.closable for item in portfolio.statements)} "
                + "statements still need review. Open tab 8 for exact differences.\n"
            )
        overview = self._table("#overview-table")
        overview.clear()
        for balance in snapshot.balances:
            overview.add_row(
                Text(safe_display(balance.account.name)),
                balance.account.account_type.value,
                balance.money.format(),
                key=balance.account.id.value,
            )
        values = tuple((balance.account.name, balance.money) for balance in snapshot.balances)
        history = recorded_liquid_history(snapshot.accounts, snapshot.entries)
        history_summary = (
            "\n".join(
                f"{item.currency} recorded liquid cash: "
                f"{_format_total(item.points[-1].minor, item.currency)} on "
                f"{item.points[-1].effective_date}; low "
                f"{_low_total(item)}."
                for item in history
                if item.points
            )
            or "No posted liquid cash history yet."
        )
        self.query_one("#overview-text-content", Static).update(
            str(self.query_one("#overview-text-content", Static).content)
            + "\n"
            + history_summary
            + "\n"
        )
        self.query_one("#overview-chart", Static).update(_bars(values))
        self.query_one("#overview-history", Static).update(_history_chart(history))
        self.query_one("#ledger-chart", Static).update(_bars(values))
        self.query_one("#cash-chart", Static).update(
            _bars(
                tuple(
                    (balance.account.name, balance.money)
                    for balance in snapshot.balances
                    if balance.account.liquid
                )
            )
        )
        self.query_one("#cash-history", Static).update(_history_chart(history))
        self._render_cash()
        self._render_models()
        self._render_register(pending)
        self._render_reconciliation()
        self.action_mode(self.view_mode)

    def _render_reconciliation(self) -> None:
        snapshot = self.snapshot
        if snapshot is None or snapshot.reconcile_view is None:
            return
        portfolio = snapshot.reconcile_view.portfolio
        table = self._table("#reconcile-table")
        table.clear()
        lines = [
            "MONTHLY STATEMENT REVIEW · balances and line movements use exact account currency",
            "Record statements, accept matches, explain exceptions, resolve contradictions, "
            "and close a fully reviewed month with [u].",
        ]
        graph = Text("MATCHED OR EXPLAINED · ITEM PROGRESS\n", style="bold #22c6bc")
        for item in portfolio.statements:
            statement = item.statement
            decided = item.matched_count * 2 + item.exception_count
            total = decided + len(item.unresolved_lines) + len(item.unresolved_entries)
            state = "CLOSED" if item.closed else "READY" if item.closable else "REVIEW"
            table.add_row(
                Text(safe_display(item.account.name)),
                f"{statement.start_on:%Y-%m}",
                item.opening_difference.format(),
                item.closing_difference.format(),
                item.line_difference.format(),
                f"{decided}/{total}",
                state,
                key=statement.id.value,
            )
            lines.append(
                f"{safe_display(item.account.name)} {statement.start_on:%Y-%m}: {state}; "
                f"ledger closing {item.ledger_closing.format()}, "
                f"statement closing {statement.closing.format()}"
            )
            lines.extend(f"  Needs: {safe_display(reason)}" for reason in item.missing_inputs)
            if item.duplicate_lines:
                lines.append(f"  Possible duplicate lines: {len(item.duplicate_lines)}")
            if item.stale and not item.closed:
                lines.append("  Older than 45 days; review outstanding evidence")
            for suggestion in item.suggestions:
                lines.append(
                    f"  Suggested match {suggestion.line_id.value} → "
                    f"{suggestion.entry_id.value}: {suggestion.reason}"
                )
            graph.append(f"{safe_display(item.account.name)} · {statement.start_on:%Y-%m}\n")
            graph.append(f"{'━' * (20 * decided // total) if total else '∅'}  {decided}/{total}\n")
        if not portfolio.statements:
            lines.append("No monthly statements recorded yet.")
            graph.append("No statement lines recorded; progress is unknown.\n")
        for start, end in portfolio.closes:
            lines.append(f"Closed period: {start} through {end}")
        for entry in portfolio.later_corrections:
            lines.append(f"Later correction {entry.id.value} on {entry.content.effective_date}")
        lines.append(f"Core calculation provenance: {snapshot.reconcile_view.provenance.id.value}")
        self.query_one("#reconcile-summary", Static).update(
            f"RECONCILIATION · {len(portfolio.statements)} current statements · "
            f"{len(portfolio.closes)} closed periods"
        )
        self.query_one("#reconcile-text-content", Static).update("\n".join(lines))
        self.query_one("#reconcile-chart", Static).update(graph)

    def _render_models(self) -> None:
        snapshot = self.snapshot
        if snapshot is None or snapshot.model_view is None:
            return
        view = snapshot.model_view
        debts = self._table("#debts-table")
        debts.clear()
        debt_lines = [
            "RECORDED LIABILITY BALANCES · posted ledger only",
            "APR and minimums are manual terms. Payoff comparisons never post payments.",
            "Interest policy: monthly APR/12, rounded up to one minor unit per debt.",
            "Use m to set terms, classify a posted payment, or compare plans.",
        ]
        for item in view.debts.debts:
            apr = f"{Decimal(item.terms.apr_basis_points) / 100}%" if item.terms else "Unknown"
            minimum = item.terms.minimum_payment.format() if item.terms else "Unknown"
            debts.add_row(
                Text(safe_display(item.account.name)),
                item.balance.format(),
                apr,
                minimum,
                str(item.next_due or "Unknown"),
            )
            if (
                item.classified_principal.minor
                or item.classified_interest.minor
                or item.classified_fees.minor
            ):
                debt_lines.append(
                    f"{safe_display(item.account.name)} classified payments: principal "
                    f"{item.classified_principal.format()}, interest "
                    f"{item.classified_interest.format()}, fees {item.classified_fees.format()}."
                )
        for currency, apr in view.debts.weighted_apr_percent:
            apr_text = f"{apr:.2f}% (display rounded)" if apr is not None else "unknown or N/A"
            debt_lines.append(f"{currency} weighted APR: {apr_text}")
        debt_lines.extend(view.debts.missing_inputs)
        if (
            self._debt_comparison is not None
            and self._debt_comparison_revision == snapshot.revision
        ):
            debt_lines.append("PAYOFF COMPARISON · conditional monthly simulation")
            for scenario in self._debt_comparison.scenarios:
                interest_text = (
                    scenario.total_interest.format()
                    if scenario.total_interest is not None
                    else "unknown"
                )
                debt_lines.append(
                    f"{scenario.strategy.value.replace('_', ' ').title()}: "
                    + (
                        f"{scenario.payoff_months} months · interest {interest_text}"
                        if scenario.payoff_months is not None
                        else f"Unavailable · {scenario.missing_reason}"
                    )
                )
        elif not view.debts.debts:
            debt_lines.append("No liability accounts are recorded. APR is not applicable.")
        self.query_one("#debts-text-content", Static).update("\n".join(debt_lines))
        self.query_one("#debts-summary", Static).update(
            f"DEBTS · {len(view.debts.debts)} liability accounts · "
            "payoff is conditional on reviewed terms and budget"
        )
        debt_chart = _bars(tuple((item.account.name, item.balance) for item in view.debts.debts))
        if (
            self._debt_comparison is not None
            and self._debt_comparison_revision == snapshot.revision
        ):
            debt_chart.append(_debt_scenario_chart(self._debt_comparison))
        self.query_one("#debts-chart", Static).update(debt_chart)

        assets = self._table("#assets-table")
        assets.clear()
        asset_lines = [
            "MANUAL VALUATIONS · stale and missing values are never graphed as zero",
            "Gain = ending value − starting value − external contributions + withdrawals.",
            "Internal transfers do not change gain. Flow coverage must be reviewed.",
            "Use m to define a position, value it, classify flows, and review coverage.",
        ]
        known_values: list[tuple[str, Money]] = []
        for item in view.assets:
            value = item.value.format() if item.value is not None else "Unavailable"
            unrealized = (
                item.unrealized_gain.format() if item.unrealized_gain is not None else "Unknown"
            )
            gain = item.period_gain.format() if item.period_gain is not None else "Unknown"
            assets.add_row(
                Text(safe_display(item.position.name)),
                item.position.category.value,
                value,
                unrealized,
                gain,
                "STALE" if item.stale else "Current" if item.value is not None else "Unknown",
            )
            if item.value is not None:
                known_values.append((item.position.name, item.value))
            if item.latest is not None:
                quantity_text = (
                    str(item.latest.quantity) if item.latest.quantity is not None else "unknown"
                )
                basis_text = (
                    item.latest.cost_basis.format()
                    if item.latest.cost_basis is not None
                    else "unknown"
                )
                asset_lines.append(
                    f"{safe_display(item.position.name)} observed {item.latest.observed_on}; "
                    f"quantity {quantity_text}; cost basis {basis_text}."
                )
            asset_lines.extend(
                f"{safe_display(item.position.name)}: {reason}" for reason in item.missing_inputs
            )
        if not view.assets:
            asset_lines.append("No non-liquid asset positions are defined.")
        self.query_one("#assets-text-content", Static).update("\n".join(asset_lines))
        self.query_one("#assets-summary", Static).update(
            f"ASSETS · {len(view.assets)} positions · "
            f"{len(known_values)} current valuations · no cross-currency total"
        )
        self.query_one("#assets-chart", Static).update(_bars(tuple(known_values)))

        nodes = self._table("#nodes-table")
        nodes.clear()
        node_lines = [
            "RECORDED OPERATING PERFORMANCE · trailing 30 local dates",
            "Net cash flow = posted revenue − posted operating expenses.",
            "Capital and withdrawals are shown separately and excluded from operating net.",
            "Use m to define a node or classify a posted funding transfer.",
        ]
        for item in view.nodes:
            runway = (
                str(item.runway_months) + " months"
                if item.runway_months is not None
                else "N/A"
                if item.net_cash_flow.minor >= 0
                else "Unknown"
            )
            nodes.add_row(
                Text(safe_display(item.node.name)),
                item.revenue.format(),
                item.operating_expenses.format(),
                item.net_cash_flow.format(),
                item.capital.format(),
                runway,
            )
            node_lines.append(
                f"{safe_display(item.node.name)}: capital {item.capital.format()}, "
                f"withdrawals {item.withdrawals.format()}, assumed monthly net "
                f"{item.assumption_net.format() if item.assumption_net else 'unknown'}."
            )
            if item.node.milestone_target is not None:
                node_lines.append(
                    f"Milestone: {item.node.milestone_target.format()} by {item.node.milestone_on}."
                )
            node_lines.extend(
                f"{safe_display(item.node.name)}: {reason}" for reason in item.missing_inputs
            )
        if not view.nodes:
            node_lines.append("No income nodes are defined.")
        self.query_one("#nodes-text-content", Static).update("\n".join(node_lines))
        self.query_one("#nodes-summary", Static).update(
            f"NODES · {len(view.nodes)} recorded operating boundaries · funding separate"
        )
        self.query_one("#nodes-chart", Static).update(
            _bars(tuple((item.node.name, item.net_cash_flow) for item in view.nodes))
        )

    def _cash_plan(self) -> CashPlanView | None:
        if self.snapshot is None:
            return None
        return next(
            (
                plan
                for plan in self.snapshot.cash_plans
                if plan.projection.horizon_days == self.cash_horizon
            ),
            None,
        )

    def _render_cash(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        for horizon in (30, 60, 90):
            self.query_one(f"#cash-horizon-{horizon}", Button).variant = (
                "primary" if horizon == self.cash_horizon else "default"
            )
        table = self._table("#cash-table")
        table.clear()
        self._cash_event_rows.clear()
        recorded = (
            "; ".join(
                f"{safe_display(balance.account.name)} {balance.money.format()}"
                for balance in snapshot.balances
                if balance.account.liquid
            )
            or "No liquid accounts recorded"
        )
        plan = self._cash_plan()
        if plan is None:
            self.query_one("#cash-summary", Static).update(
                "CASH PLANNING · Safe to allocate: unavailable"
            )
            self.query_one("#cash-text-content", Static).update(
                f"RECORDED · {recorded}\nNo cash projection is available. Press c to manage inputs."
            )
            self.query_one("#cash-projection-chart", Static).update(
                "Projection unavailable; missing values are not graphed as zero."
            )
            return
        projection = plan.projection
        summaries = [
            f"{self.cash_horizon}-DAY CASH PLAN · {projection.start_on} → {projection.through_on}"
        ]
        for currency in projection.currencies:
            summaries.append(
                f"{currency.currency}  Planned low {_maybe_money(currency.minimum_balance)}"
                f" on {currency.minimum_on or 'unknown'}"
                f"  ·  Safe to allocate {_maybe_money(currency.safe_to_allocate)}"
            )
        if not projection.currencies:
            summaries.append(
                "Safe to allocate: unavailable · add liquid accounts and review inputs"
            )
        self.query_one("#cash-summary", Static).update("\n".join(summaries))
        records = snapshot.cash_records
        lines = [
            f"RECORDED · {recorded}",
            "PROJECTED · planned boundaries below; no scheduled money is posted automatically.",
            f"{len(records.schedules)} schedules / {len(records.allocations)} allocations / "
            f"{len(records.holds)} holds · c: manage · Enter: boundary details · x: evidence",
        ]
        if projection.missing_inputs:
            lines.append("MISSING · " + "; ".join(projection.missing_inputs[:3]))
            if len(projection.missing_inputs) > 3:
                lines.append(
                    f"{len(projection.missing_inputs) - 3} more missing inputs · x to inspect"
                )
        else:
            lines.append(
                "Coverage reviewed for this horizon. Values remain conditional on the plan."
            )
        for currency in projection.currencies:
            lines.append(
                f"{currency.currency} current unallocated: "
                f"{_maybe_money(currency.current_unallocated)}"
            )
            if currency.points:
                opening = currency.points[0]
                lines.append(
                    f"Protected {opening.protected.format()} · holds {opening.holds.format()} · "
                    f"floor {_maybe_money(opening.floor)}"
                )
            for index, point in enumerate(currency.points):
                key = f"{currency.currency}:{index}"
                self._cash_event_rows[key] = (currency.currency, point)
                table.add_row(
                    str(point.on),
                    Text(safe_display(f"{point.label} · {currency.currency}")),
                    point.delta.format(),
                    point.balance.format(),
                    _maybe_money(point.available),
                    key=key,
                )
        self.query_one("#cash-text-content", Static).update("\n".join(lines))
        self.query_one("#cash-projection-chart", Static).update(_cash_projection_chart(projection))

    @on(Button.Pressed, ".cash-horizon")
    def _horizon_pressed(self, event: Button.Pressed) -> None:
        self.cash_horizon = int((event.button.id or "").rsplit("-", 1)[1])
        self._render_cash()

    def action_cash_horizon(self) -> None:
        self.action_tab("cash")
        self.cash_horizon = {30: 60, 60: 90, 90: 30}[self.cash_horizon]
        self._render_cash()

    @on(Button.Pressed, "#cash-manage")
    def action_cash_manage(self) -> None:
        if self.snapshot is None:
            self._status("Wait for the local profile to finish loading.", error=True)
            return
        self.action_tab("cash")
        self.push_screen(CashActionMenu(), self._cash_action_chosen)

    def _cash_action_chosen(self, action: CashAction | None) -> None:
        if action is None or self.snapshot is None:
            return
        if action in ("schedule", "observation") and not any(
            account.liquid for account in self.snapshot.accounts
        ):
            self._status(
                "Create a liquid asset account before scheduling or observing cash.", error=True
            )
            return
        self.push_screen(
            CashInputForm(
                action,
                self.snapshot.accounts,
                self.snapshot.cash_records,
                self.snapshot.provenance.at.value.date(),
            ),
            self._cash_form_finished,
        )

    def _cash_form_finished(self, request: CashRequest | None) -> None:
        if request is None:
            return
        self._status("Saving reviewed cash planning input…")
        if isinstance(request, ScheduleRequest):
            self._perform(
                lambda: self.service.add_cash_schedule(
                    request.account_id,
                    request.label,
                    request.amount,
                    request.start_date,
                    request.cadence,
                    request.timezone,
                    request.end_date,
                ),
                self._cash_saved,
            )
        elif isinstance(request, AllocationRequest):
            self._perform(
                lambda: self.service.add_cash_allocation(
                    request.label,
                    request.amount,
                    request.active_from,
                    request.linked_occurrence,
                ),
                self._cash_saved,
            )
        elif isinstance(request, HoldRequest):
            self._perform(
                lambda: self.service.add_cash_hold(
                    request.label,
                    request.amount,
                    request.active_from,
                    request.release_date,
                ),
                self._cash_saved,
            )
        elif isinstance(request, FloorRequest):
            self._perform(
                lambda: self.service.add_cash_floor(
                    request.currency,
                    request.effective_date,
                    request.amount,
                ),
                self._cash_saved,
            )
        elif isinstance(request, ObservationRequest):
            self._perform(
                lambda: self.service.record_cash_balance(
                    request.account_id,
                    request.observed,
                    request.observed_on,
                    request.fresh_through,
                    request.source,
                ),
                self._cash_saved,
            )
        elif isinstance(request, CoverageRequest):
            self._perform(
                lambda: self.service.declare_cash_coverage(
                    request.currency,
                    request.as_of,
                    request.through_date,
                    request.accounts_complete,
                    request.schedules_complete,
                    request.source,
                ),
                self._cash_saved,
            )
        else:
            self._perform(
                lambda: self.service.retire_cash_item(
                    request.target_id,
                    request.effective_date,
                    request.reason,
                ),
                self._cash_saved,
            )

    def _cash_saved[T](self, result: Result[T, Error]) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status("Cash plan saved. Ledger entries remain unchanged.")
        self.action_refresh_data()

    @on(DataTable.RowSelected, "#cash-table")
    def _cash_event_detail(self) -> None:
        table = self._table("#cash-table")
        if not table.row_count:
            self._status("No projected event boundaries to inspect yet.")
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        selected = self._cash_event_rows.get(key or "")
        if selected is None:
            return
        currency, point = selected
        self.push_screen(
            InfoScreen(
                "CASH BOUNDARY · CONDITIONAL PROJECTION",
                "\n".join(
                    (
                        f"{point.on} · {point.kind.replace('_', ' ')} · {currency}",
                        point.label,
                        f"Cash movement: {point.delta.format()}",
                        f"Projected balance: {point.balance.format()}",
                        f"Protected allocations: {point.protected.format()}",
                        f"Active holds: {point.holds.format()}",
                        f"Cash floor: {_maybe_money(point.floor)}",
                        f"Available after constraints: {_maybe_money(point.available)}",
                        "",
                        "This boundary is a calculation from recorded data and planning inputs.",
                        "A scheduled payment is not a posted event. Press x for provenance.",
                    )
                ),
            )
        )

    def _explain_cash(self) -> None:
        plan = self._cash_plan()
        if plan is None or self.snapshot is None:
            self._status("Cash projection is unavailable. Add liquid accounts and planning inputs.")
            return
        projection, provenance = plan.projection, plan.provenance
        lines = [
            "CASH PLAN · CONDITIONAL PROJECTION",
            "",
            f"{projection.start_on} through {projection.through_on} · "
            f"{projection.horizon_days} days",
            "Safe allocation is per currency, conditional on reviewed coverage and fresh evidence.",
            "Allocations protect cash. Linking a scheduled occurrence avoids reserving it twice.",
            "Holds constrain availability while active. Unknown inputs are never treated as zero.",
            "Same-day boundaries are ordered explicitly; inspect the event register for each step.",
            "",
        ]
        for currency in projection.currencies:
            lines.extend(
                (
                    currency.currency,
                    f"Recorded opening: {_maybe_money(currency.starting_balance)}",
                    f"Current unallocated: {_maybe_money(currency.current_unallocated)}",
                    f"Projected minimum: {_maybe_money(currency.minimum_balance)}"
                    f" on {currency.minimum_on or 'unknown'}",
                    f"Safe to allocate: {_maybe_money(currency.safe_to_allocate)}",
                    "",
                )
            )
        lines.append("MISSING INPUTS")
        lines.extend(projection.missing_inputs or ("No missing inputs for this horizon.",))
        lines.extend(("", "PLANNING RECORDS"))
        records = self.snapshot.cash_records
        for schedule in records.schedules:
            lines.append(
                f"Schedule: {schedule.label} · {schedule.amount.format()} · "
                f"{schedule.cadence.value} from {schedule.start_date} · {schedule.timezone}"
            )
        for allocation in records.allocations:
            link = allocation.linked_occurrence
            linked = f"linked to {link.schedule.id.value} on {link.due_on}" if link else "unlinked"
            lines.append(
                f"Allocation: {allocation.label} · {allocation.amount.format()} · {linked}"
            )
        for hold in records.holds:
            lines.append(
                f"Hold: {hold.label} · {hold.amount.format()} · "
                f"{hold.active_from} → {hold.release_date or 'no release date'}"
            )
        for floor in records.floor_changes:
            lines.append(f"Floor: {floor.amount.format()} from {floor.effective_date}")
        for observation in records.observations:
            lines.append(
                f"Observed: {observation.observed.format()} on {observation.observed_on} · "
                f"fresh through {observation.fresh_through} · {observation.source}"
            )
        for coverage in records.coverage:
            lines.append(
                f"Coverage: {coverage.currency} through {coverage.through_date} · "
                f"accounts={coverage.accounts_complete}, schedules={coverage.schedules_complete}"
            )
        for retirement in records.retirements:
            lines.append(
                f"Retired: {retirement.target.id.value} from {retirement.effective_date} · "
                + retirement.reason
            )
        lines.extend(
            (
                "",
                f"Calculation: {provenance.transform_name} v{provenance.transform_version}",
                f"Provenance: {provenance.id.kind.value}:{provenance.id.value}",
                f"Generated: {provenance.at.value.isoformat()}",
                "",
                "RETAINED SOURCE REFERENCES",
            )
        )
        lines.extend(
            f"{ref.id.kind.value}:{ref.id.value} namespace={ref.namespace}"
            for ref in provenance.inputs
        )
        self.push_screen(InfoScreen("EXPLAIN CASH HORIZON", "\n".join(lines)))

    def _render_register(self, pending: tuple[Draft, ...]) -> None:
        if self.snapshot is None:
            return
        names = {account.id: account.name for account in self.snapshot.accounts}
        pending = tuple(
            draft
            for draft in pending
            if _matches_search(draft.content, self.search_text.casefold().strip(), names)
            and (self.date_start is None or draft.content.effective_date >= self.date_start)
            and (self.date_end is None or draft.content.effective_date <= self.date_end)
        )
        entries = tuple(
            entry
            for entry in self._filtered_entries
            if (self.date_start is None or entry.content.effective_date >= self.date_start)
            and (self.date_end is None or entry.content.effective_date <= self.date_end)
        )
        table = self._table("#entry-table")
        table.clear()
        self._rows = {}
        for record in (*pending, *reversed(entries)):
            content = record.content
            if isinstance(record, Draft):
                recorded, status = record.created_at, "DRAFT · review"
            else:
                recorded, status = record.recorded_at, "POSTED"
                if content.reversal_of:
                    status = "REVERSAL"
                elif any(
                    entry.content.reversal_of and entry.content.reversal_of.id == record.id
                    for entry in self.snapshot.entries
                ):
                    status = "REVERSED"
            currency = content.postings[0].money.currency
            debit_total = sum(post.money.minor for post in content.postings if post.money.minor > 0)
            volume = (
                Money(debit_total, currency).format()
                if debit_total <= MAX_MINOR
                else f"Exceeds supported total · {currency}"
            )
            table.add_row(
                str(content.effective_date),
                recorded.value.strftime("%m-%d %H:%M"),
                Text(safe_display(content.description)),
                Text(
                    ", ".join(
                        safe_display(names.get(post.account.id, post.account.id.value))
                        for post in content.postings
                    )
                ),
                Text(", ".join(safe_display(tag) for tag in content.tags)),
                Text(safe_display(content.source)),
                status,
                volume,
                key=record.id.value,
            )
            self._rows[record.id.value] = record
        date_range = f"{self.date_start or 'start'} → {self.date_end or 'latest'}"
        self.query_one("#ledger-summary", Static).update(
            f"REGISTER  {len(entries)} posted / {len(pending)} pending  ·  {date_range}  "
            f"·  search: {self.search_text or 'all'}"
        )
        self.query_one("#ledger-text-content", Static).update(
            "Enter: detail / review  ·  r: reverse  ·  d: duplicate  ·  f: saved filters\n"
            "i: import  ·  e: export  ·  [: dates  ·  [8]: statement review and close\n"
            + ("No entries yet. Press n to prepare a balanced entry.\n" if not self._rows else "")
        )

    def action_tab(self, name: str) -> None:
        self.query_one("#finance-tabs", TabbedContent).active = name
        if name == "ledger":
            self._table("#entry-table").focus()
        elif name in ("cash", "debts", "assets", "nodes", "reconcile"):
            self._table(f"#{name}-table").focus()

    def action_mode(self, mode: str) -> None:
        self.view_mode = mode
        for name in ("overview", "ledger", "cash", "debts", "assets", "nodes", "reconcile"):
            self.query_one(f"#{name}-text").display = mode != "graph"
            self.query_one(f"#{name}-graph").display = mode != "text"
        self.query_one("#mode-hints", Static).update(
            f"[t] TEXT  [g] GRAPH  [b] SPLIT  ·  {mode.upper()}  ·  Ctrl+P Commands"
        )

    @on(Button.Pressed, "#debts-manage")
    def _manage_debts_pressed(self) -> None:
        self.action_tab("debts")
        self.action_model_manage()

    @on(Button.Pressed, "#assets-manage")
    def _manage_assets_pressed(self) -> None:
        self.action_tab("assets")
        self.action_model_manage()

    @on(Button.Pressed, "#nodes-manage")
    def _manage_nodes_pressed(self) -> None:
        self.action_tab("nodes")
        self.action_model_manage()

    def action_model_manage(self) -> None:
        tab = self.query_one("#finance-tabs", TabbedContent).active
        if tab not in ("debts", "assets", "nodes"):
            self._status("Open Debts, Assets, or Nodes to manage its inputs.")
            return
        if self.snapshot is None:
            self._status("Wait for the local profile to finish loading.", error=True)
            return
        self.push_screen(ModelActionMenu(tab), self._model_action_chosen)

    @on(Button.Pressed, "#reconcile-manage")
    def _reconcile_manage_pressed(self) -> None:
        self.action_reconcile_manage()

    def action_reconcile_manage(self) -> None:
        if self.snapshot is None or self.snapshot.reconcile_view is None:
            self._status("Wait for the local profile to finish loading.", error=True)
            return
        self.action_tab("reconcile")
        self.push_screen(ReconcileActionMenu(), self._reconcile_action_chosen)

    def _reconcile_action_chosen(self, action: ReconcileAction | None) -> None:
        snapshot = self.snapshot
        if action is None or snapshot is None or snapshot.reconcile_view is None:
            return
        self.push_screen(
            ReconcileInputForm(
                action,
                snapshot.accounts,
                snapshot.reconciliation,
                snapshot.reconcile_view.portfolio,
            ),
            self._reconcile_form_finished,
        )

    def _reconcile_form_finished(self, request: ReconcileRequest | None) -> None:
        if request is None:
            return
        if request.action == "statement":
            account_id, start, end, opening, closing, lines, complete, source = cast(
                tuple[
                    Id,
                    date,
                    date,
                    Money,
                    Money,
                    tuple[tuple[date, Money, str, str | None], ...],
                    bool,
                    str,
                ],
                request.values,
            )
            account = (
                next((item for item in self.snapshot.accounts if item.id == account_id), None)
                if self.snapshot is not None
                else None
            )
            if account is None:
                self._status(
                    "Account changed during statement review. Refresh and retry.", error=True
                )
                return

            def reviewed(value: str | None) -> None:
                if value != "RECORD":
                    self._status("Statement not recorded.")
                    return
                self._perform(
                    lambda: self.service.record_statement(
                        account_id, start, end, opening, closing, lines, complete, source
                    ),
                    self._reconcile_saved,
                )

            self.push_screen(
                TextPrompt(
                    "REVIEW STATEMENT",
                    f"{safe_display(account.name)} · {start} through {end}\n"
                    f"Opening {opening.format()} · closing {closing.format()} · "
                    f"{len(lines)} lines · inventory {'complete' if complete else 'incomplete'}\n"
                    "Type RECORD to retain this exact statement version.",
                ),
                reviewed,
            )
            return
        if request.action == "close":
            year, month, rationale = cast(tuple[int, int, str], request.values)
            monthly = (
                tuple(
                    item
                    for item in self.snapshot.reconcile_view.portfolio.statements
                    if item.statement.start_on.year == year
                    and item.statement.start_on.month == month
                )
                if self.snapshot is not None and self.snapshot.reconcile_view is not None
                else ()
            )
            review_lines = "\n".join(
                f"{safe_display(item.account.name)} · "
                f"opening Δ {item.opening_difference.format()} · "
                f"closing Δ {item.closing_difference.format()} · "
                f"lines Δ {item.line_difference.format()} · "
                f"{item.matched_count} matches / {item.exception_count} exceptions"
                for item in monthly
            )

            def reviewed(value: str | None) -> None:
                if value != "CLOSE":
                    self._status("Month remains open.")
                    return
                self._perform(
                    lambda: self.service.close_month(year, month, rationale),
                    self._reconcile_saved,
                )

            self.push_screen(
                TextPrompt(
                    "CLOSE REVIEWED MONTH",
                    f"{year:04d}-{month:02d} · {safe_display(rationale)}\n{review_lines}\n"
                    "This locks dated ledger writes in the month. "
                    "Later corrections use a later open date. Type CLOSE to confirm.",
                ),
                reviewed,
            )
            return
        if request.action == "match":
            line, entry = cast(tuple[Id, Id], request.values)
            self._perform(
                lambda: self.service.match_statement_line(line, entry),
                self._reconcile_saved,
            )
        elif request.action == "exception":
            statement, target, rationale = cast(tuple[Id, Id, str], request.values)
            self._perform(
                lambda: self.service.explain_reconcile_item(statement, target, rationale),
                self._reconcile_saved,
            )
        else:
            issue, rationale = cast(tuple[Id, str], request.values)
            self._perform(
                lambda: self.service.resolve_reconcile_issue(issue, rationale),
                self._reconcile_saved,
            )

    def _reconcile_saved[T](self, result: Result[T, Error]) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status("Statement review decision retained.")
        self.action_refresh_data()

    def _model_action_chosen(self, action: ModelAction | None) -> None:
        if action is None or self.snapshot is None:
            return
        snapshot = self.snapshot
        self.push_screen(
            ModelInputForm(
                action,
                snapshot.accounts,
                snapshot.entries,
                snapshot.models,
                snapshot.provenance.at.value.astimezone().date(),
            ),
            self._model_form_finished,
        )

    def _model_form_finished(self, request: ModelRequest | None) -> None:
        if request is None:
            return
        action, values = request.action, request.values
        self._status("Reviewing local model input…")
        if action == "debt_terms":
            account_id, apr, minimum, due, source = cast(tuple[Id, int, Money, int, str], values)
            self._perform(
                lambda: self.service.set_debt_terms(account_id, apr, minimum, due, source),
                self._model_saved,
            )
        elif action == "debt_payment":
            account_id, entry_id, principal, interest, fees, source = cast(
                tuple[Id, Id, Money, Money, Money, str], values
            )
            self._perform(
                lambda: self.service.classify_debt_payment(
                    account_id, entry_id, principal, interest, fees, source
                ),
                self._model_saved,
            )
        elif action == "debt_compare":
            currency, extra, order = cast(tuple[str, Money, tuple[Id, ...]], values)
            revision = self.snapshot.revision if self.snapshot is not None else -1

            def compared(result: Result[DebtComparisonView, Error]) -> None:
                self._debt_comparison_ready(result, revision)

            self._perform(
                lambda: self.service.compare_debt_scenarios(currency, extra, order),
                compared,
            )
        elif action == "asset_position":
            account_id, name, category, source = cast(tuple[Id, str, AssetCategory, str], values)
            self._perform(
                lambda: self.service.create_asset_position(account_id, name, category, source),
                self._model_saved,
            )
        elif action == "asset_valuation":
            position_id, value, quantity, basis, observed_on, fresh_through, source = cast(
                tuple[Id, Money, Decimal | None, Money | None, date, date, str], values
            )
            self._perform(
                lambda: self.service.record_asset_valuation(
                    position_id, value, quantity, basis, observed_on, fresh_through, source
                ),
                self._model_saved,
            )
        elif action == "asset_flow":
            position_id, entry_id, kind, amount, source = cast(
                tuple[Id, Id, AssetFlowKind, Money, str], values
            )
            self._perform(
                lambda: self.service.classify_asset_flow(
                    position_id, entry_id, kind, amount, source
                ),
                self._model_saved,
            )
        elif action == "asset_coverage":
            position_id, from_on, through_on, complete, source = cast(
                tuple[Id, date, date, bool, str], values
            )
            self._perform(
                lambda: self.service.declare_asset_flow_coverage(
                    position_id, from_on, through_on, complete, source
                ),
                self._model_saved,
            )
        elif action == "node_create":
            name, revenue, expense, cash, monthly_revenue, monthly_expense, target, on, source = (
                cast(
                    tuple[
                        str,
                        Id,
                        Id,
                        Id | None,
                        Money | None,
                        Money | None,
                        Money | None,
                        date | None,
                        str,
                    ],
                    values,
                )
            )
            self._perform(
                lambda: self.service.create_income_node(
                    name,
                    revenue,
                    expense,
                    cash,
                    monthly_revenue,
                    monthly_expense,
                    target,
                    on,
                    source,
                ),
                self._model_saved,
            )
        else:
            node_id, entry_id, kind, amount, source = cast(
                tuple[Id, Id, FundingKind, Money, str], values
            )
            self._perform(
                lambda: self.service.classify_node_funding(node_id, entry_id, kind, amount, source),
                self._model_saved,
            )

    def _model_saved[T](self, result: Result[T, Error]) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status("Reviewed input saved. Posted ledger entries remain unchanged.")
        self.action_refresh_data()

    def _debt_comparison_ready(
        self, result: Result[DebtComparisonView, Error], revision: int
    ) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        if self.snapshot is None or self.snapshot.revision != revision:
            self._status("Inputs changed while comparing; refresh and retry.")
            return
        self._debt_comparison = result.value
        self._debt_comparison_revision = revision
        self._render_models()
        self._status("Debt payoff comparison ready; no payment was posted.")

    def _explain_models(self, tab: str) -> None:
        snapshot = self.snapshot
        if snapshot is None or snapshot.model_view is None:
            self._status("Model projection is unavailable.")
            return
        provenance = snapshot.model_view.provenance
        lines = [
            f"{tab.upper()} · EVIDENCE AND ASSUMPTIONS",
            "",
            str(self.query_one(f"#{tab}-text-content", Static).content),
            "",
            f"Calculation: {provenance.transform_name} v{provenance.transform_version}",
            f"Provenance: {provenance.id.kind.value}:{provenance.id.value}",
            f"Generated: {provenance.at.value.isoformat()}",
            f"Parent derivations: {len(provenance.parents)}",
            "",
            "RETAINED SOURCE REFERENCES",
        ]
        lines.extend(
            f"{ref.id.kind.value}:{ref.id.value} namespace={ref.namespace}"
            for ref in provenance.inputs
        )
        if tab == "debts" and self._debt_comparison is not None:
            lines.extend(
                (
                    "",
                    f"Payoff comparison: {self._debt_comparison.provenance.transform_name} "
                    f"v{self._debt_comparison.provenance.transform_version}",
                    f"Comparison provenance: {self._debt_comparison.provenance.id.value}",
                )
            )
        self.push_screen(InfoScreen(f"EXPLAIN {tab.upper()}", "\n".join(lines)))

    @on(Button.Pressed, "#new-account")
    def action_new_account(self) -> None:
        self.push_screen(AccountForm(), self._account_form_finished)

    def _account_form_finished(self, values: tuple[str, AccountType, str, bool] | None) -> None:
        if values is None:
            return
        name, kind, currency, liquid = values
        self._perform(
            lambda: self.service.create_account(name, kind, currency, liquid=liquid),
            self._account_created,
        )

    def _account_created(self, result: Result[Account, Error]) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status(f"Created account: {result.value.name}")
        self.action_refresh_data()

    @on(Button.Pressed, "#new-entry")
    def action_new_entry(self) -> None:
        if self.snapshot is None or len(self.snapshot.accounts) < 2:
            self._status("Create at least two accounts before preparing an entry.", error=True)
            return
        self.push_screen(
            EntryForm(self.snapshot.accounts, self.snapshot.provenance.at.value.date()),
            self._entry_form_finished,
        )

    def _entry_form_finished(self, content: EntryContent | None) -> None:
        if content is not None:
            self._perform(lambda: self.service.prepare_entry(content), self._draft_prepared)

    def _draft_prepared(self, result: Result[Draft, Error]) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status("Draft saved. Review exact content before posting.")
        self.action_refresh_data()
        self._review(result.value)

    def _review(self, draft: Draft) -> None:
        if self.snapshot is None:
            return

        def reviewed(approved: bool | None) -> None:
            if approved:
                self._busy = True
                self._status("Posting reviewed content…")
                self._perform(
                    lambda: self.service.post_draft(
                        draft.id, draft.content_hash, f"local-review:{draft.id.value}"
                    ),
                    self._posted,
                )
            else:
                self._status(
                    "Draft retained for later review. Select it in Ledger and press Enter."
                )

        self.push_screen(ReviewScreen(draft, self.snapshot.accounts), reviewed)

    def _posted(self, result: Result[JournalEntry, Error]) -> None:
        self._busy = False
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status(f"Posted: {result.value.content.description}")
        self.action_tab("ledger")
        self.action_refresh_data()

    def _selected_record(self) -> JournalEntry | Draft | None:
        table = self._table("#entry-table")
        if not table.row_count:
            self._status("Select an entry in the Ledger register first.", error=True)
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return self._rows.get(row_key or "")

    @on(DataTable.RowSelected, "#entry-table")
    def action_detail(self) -> None:
        active = self.query_one("#finance-tabs", TabbedContent).active
        if active == "cash":
            self._cash_event_detail()
            return
        if active == "reconcile":
            self._explain_reconciliation()
            return
        selected = self._selected_record()
        if selected is None or self.snapshot is None:
            return
        if isinstance(selected, Draft):
            self._review(selected)
            return
        reconciliation = self.snapshot.reconciliation
        if any(item.entry.id == selected.id for item in reconciliation.matches):
            reconcile_status = "matched to a statement line"
        elif any(item.target.id == selected.id for item in reconciliation.exceptions):
            reconcile_status = "explained as an exception"
        elif any(
            item.start_on <= selected.content.effective_date <= item.through_on
            for item in reconciliation.closes
        ):
            reconcile_status = "inside a closed month"
        else:
            reconcile_status = "no statement decision recorded"
        self.push_screen(
            InfoScreen(
                "POSTED ENTRY · IMMUTABLE HISTORY",
                "\n".join(
                    (
                        content_text(selected.content, self.snapshot.accounts),
                        "",
                        f"Entry ID: {selected.id.value}",
                        f"Recorded: {selected.recorded_at.value.isoformat()}",
                        f"Principal: {selected.principal}",
                        f"Execution sequence: {selected.sequence}",
                        f"Reconciliation: {reconcile_status}",
                    )
                ),
            )
        )

    def action_reverse(self) -> None:
        selected = self._selected_record()
        if not isinstance(selected, JournalEntry) or self.snapshot is None:
            self._status("Choose a posted entry to reverse.", error=True)
            return

        def chosen(value: str | None) -> None:
            if value is None:
                return
            try:
                effective = date.fromisoformat(value)
            except ValueError:
                self._status("Use an effective date in YYYY-MM-DD format.", error=True)
                return
            self._perform(
                lambda: self.service.prepare_reversal(selected.id, effective), self._draft_prepared
            )

        self.push_screen(
            TextPrompt(
                "PREPARE REVERSAL",
                "Effective date for the reversing entry",
                str(self.snapshot.provenance.at.value.date()),
            ),
            chosen,
        )

    def action_duplicate(self) -> None:
        selected = self._selected_record()
        if selected is not None and self.snapshot is not None:
            self.push_screen(
                EntryForm(
                    self.snapshot.accounts,
                    self.snapshot.provenance.at.value.date(),
                    selected.content,
                ),
                self._entry_form_finished,
            )

    def action_search(self) -> None:
        def chosen(value: str | None) -> None:
            if value is not None:
                self.search_text = value.strip()
                self.action_tab("ledger")
                self.action_refresh_data()

        self.push_screen(
            TextPrompt(
                "SEARCH LEDGER", "Leave blank to show all posted entries.", self.search_text
            ),
            chosen,
        )

    def action_date_range(self) -> None:
        def chosen(value: str | None) -> None:
            if value is None:
                return
            try:
                if not value.strip():
                    self.date_start = self.date_end = None
                else:
                    start, end = value.split("..", 1)
                    start_date, end_date = (
                        date.fromisoformat(start),
                        date.fromisoformat(end),
                    )
                    if end_date < start_date:
                        raise ValueError("End date must follow start date")
                    self.date_start, self.date_end = start_date, end_date
            except ValueError:
                self._status("Use YYYY-MM-DD..YYYY-MM-DD, or blank for all dates.", error=True)
                return
            self.action_refresh_data()

        self.push_screen(
            TextPrompt("EFFECTIVE DATE RANGE", "YYYY-MM-DD..YYYY-MM-DD; blank for all"), chosen
        )

    def action_filters(self) -> None:
        choices = (
            "\n".join(f"{name}: {query}" for name, query in self._saved_filters)
            or "No saved filters."
        )

        def chosen(value: str | None) -> None:
            if value is None or not value.strip():
                return
            if "=" in value:
                name, query = value.split("=", 1)
                self._perform(
                    lambda: self.service.save_filter(name.strip(), query.strip()),
                    self._filter_saved,
                )
                self._status(f"Saving search filter: {name.strip()}")
            else:
                filters = dict(self._saved_filters)
                if value not in filters:
                    self._status("Saved filter not found. Save one with name=query.", error=True)
                    return
                self.search_text = filters[value]
                self.action_tab("ledger")
                self.action_refresh_data()

        self.push_screen(
            TextPrompt(
                "SAVED SEARCH FILTERS", choices + "\nType a name to apply, or name=query to save."
            ),
            chosen,
        )

    def _filter_saved(self, result: Result[None, Error]) -> None:
        if isinstance(result, Err):
            self._status(result.error.message, error=True)
            return
        self._status("Search filter saved.")
        self.action_refresh_data()

    def action_import_csv(self) -> None:
        def chosen(value: str | None) -> None:
            if value:
                self._perform(lambda: stage_csv(self.service, Path(value)), self._imported)

        self.push_screen(
            TextPrompt(
                "STAGE CSV IMPORT",
                "Local path. Columns: date, description, "
                "account, counter_account, amount, currency, tags.\n"
                "Imported rows become drafts requiring individual review.",
            ),
            chosen,
        )

    def _imported(self, report: ImportReport) -> None:
        self._status(
            f"Staged {len(report.drafts)} drafts; {report.duplicates} duplicates; "
            f"{len(report.errors)} row errors.",
            error=bool(report.errors),
        )
        self.action_tab("ledger")
        self.action_refresh_data()
        if report.errors:
            self.push_screen(
                InfoScreen(
                    "IMPORT ROW ERRORS",
                    "\n".join(
                        f"Row {issue.row}: {safe_display(issue.message)}" for issue in report.errors
                    ),
                )
            )

    def action_export_csv(self) -> None:
        def chosen(value: str | None) -> None:
            if value:

                def exported(count: int) -> None:
                    self._status(f"Exported {count} entries to {value}")

                self._perform(
                    lambda: export_csv(self.service, Path(value)),
                    exported,
                )

        self.push_screen(
            TextPrompt(
                "EXPORT POSTED LEDGER",
                "Destination CSV path. Contains private "
                "financial records; use a protected local folder.",
            ),
            chosen,
        )

    def _explain_reconciliation(self) -> None:
        snapshot = self.snapshot
        if snapshot is None or snapshot.reconcile_view is None:
            return
        table = self._table("#reconcile-table")
        selected_id = (
            table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            if table.row_count
            else None
        )
        view = next(
            (
                item
                for item in snapshot.reconcile_view.portfolio.statements
                if item.statement.id.value == selected_id
            ),
            None,
        )
        provenance = snapshot.reconcile_view.provenance
        lines = [
            "Statement comparison uses posted entries on their effective dates.",
            "Suggestions do not count as accepted matches.",
            f"Calculation: {provenance.transform_name} v{provenance.transform_version}",
            f"Provenance: {provenance.id.kind.value}/{provenance.id.value}",
            f"As of: {provenance.at.value.isoformat()}",
        ]
        if view is not None:
            statement = view.statement
            lines.extend(
                (
                    "",
                    f"Account: {safe_display(view.account.name)}",
                    f"Statement: {statement.id.value}",
                    f"Source: {safe_display(statement.source)}",
                    f"Month: {statement.start_on} through {statement.through_on}",
                    f"Opening: statement {statement.opening.format()} · "
                    f"ledger {view.ledger_opening.format()} · "
                    f"difference {view.opening_difference.format()}",
                    f"Closing: statement {statement.closing.format()} · "
                    f"ledger {view.ledger_closing.format()} · "
                    f"difference {view.closing_difference.format()}",
                    f"Statement line bridge difference: {view.line_difference.format()}",
                    f"Accepted matches {view.matched_count}; exceptions {view.exception_count}; "
                    f"unresolved issues {view.unresolved_issues}",
                )
            )
            lines.extend(f"Needs: {safe_display(reason)}" for reason in view.missing_inputs)
            lines.extend(
                f"Unmatched line {line.id.value}: {line.on} {line.movement.format()} "
                f"{safe_display(line.description)}"
                for line in view.unresolved_lines
            )
            lines.extend(
                f"Unmatched entry {entry.id.value}: {entry.content.effective_date} "
                f"{safe_display(entry.content.description)}"
                for entry in view.unresolved_entries
            )
        self.push_screen(InfoScreen("RECONCILIATION EVIDENCE", "\n".join(lines)))

    @on(Button.Pressed, "#explain")
    def action_explain(self) -> None:
        active = self.query_one("#finance-tabs", TabbedContent).active
        if active == "reconcile":
            self._explain_reconciliation()
            return
        if active == "cash":
            self._explain_cash()
            return
        if active in ("debts", "assets", "nodes"):
            self._explain_models(active)
            return
        snapshot = self.snapshot
        if snapshot is None:
            return
        provenance = snapshot.provenance
        context = provenance.context
        lines = [
            "RECORDED BALANCES · ACTUAL",
            "",
            "Sum finalized signed postings per account and currency, then apply account sign.",
            "Original and reversing entries both remain included. Drafts are excluded.",
            "Whole-profile net worth remains unknown because coverage is incomplete.",
            "",
            f"Calculation: {provenance.transform_name} v{provenance.transform_version}",
            f"Provenance: {provenance.id.kind.value}:{provenance.id.value}",
            f"As of: {provenance.at.value.isoformat()}",
            f"Context scope: {context.scope if context else 'unavailable'}",
            f"Context units: {context.units if context else 'unavailable'}",
            f"Coverage: {snapshot.coverage}",
            "",
            "RETAINED SOURCE REFERENCES",
        ]
        lines.extend(
            f"{ref.id.kind.value}:{ref.id.value}  namespace={ref.namespace}"
            for ref in provenance.inputs
        )
        if not provenance.inputs:
            lines.append("No posted source entries. Recorded-account balances may be known zero.")
        lines.extend(("", "Parent derivations: " + str(len(provenance.parents))))
        self.push_screen(InfoScreen("EXPLAIN THIS SNAPSHOT", "\n".join(lines)))
