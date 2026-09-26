"""Local statement review inputs; every decision remains explicit."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from personal_finance.application.reconcile_projection import ReconcilePortfolio
from personal_finance.display import safe_display
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.codec import decode_id, encode_id
from personal_finance.domain.money import Money
from personal_finance.domain.reconciliation import ReconcileRecords

type ReconcileAction = Literal["statement", "match", "exception", "resolve", "close"]

_ACTIONS: tuple[tuple[ReconcileAction, str], ...] = (
    ("statement", "Record monthly statement"),
    ("match", "Accept suggested match"),
    ("exception", "Explain unmatched item"),
    ("resolve", "Resolve a contradiction"),
    ("close", "Review and close month"),
)


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    action: ReconcileAction
    values: tuple[object, ...]


class ReconcileActionMenu(ModalScreen[ReconcileAction | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label("STATEMENT REVIEW", classes="dialog-title")
            yield Static("Each match, exception, and close requires a local decision.")
            for action, label in _ACTIONS:
                yield Button(label, id=f"reconcile-action-{action}")
            yield Button("Cancel", id="reconcile-cancel")

    def on_mount(self) -> None:
        self.query_one("#reconcile-action-statement", Button).focus()

    @on(Button.Pressed)
    def selected(self, event: Button.Pressed) -> None:
        action = (event.button.id or "").removeprefix("reconcile-action-")
        if action in {item[0] for item in _ACTIONS}:
            self.dismiss(cast(ReconcileAction, action))
        elif event.button.id == "reconcile-cancel":
            self.dismiss(None)


class ReconcileInputForm(ModalScreen[ReconcileRequest | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self,
        action: ReconcileAction,
        accounts: tuple[Account, ...],
        records: ReconcileRecords,
        portfolio: ReconcilePortfolio,
    ) -> None:
        super().__init__()
        self.reconcile_action: ReconcileAction = action
        self.accounts = accounts
        self.records = records
        self.portfolio = portfolio

    def compose(self) -> ComposeResult:
        action: ReconcileAction = self.reconcile_action
        with VerticalScroll(classes="dialog wide-dialog"):
            yield Label(
                next(label for item, label in _ACTIONS if item == action).upper(),
                classes="dialog-title",
            )
            if action == "statement":
                yield Label("Asset or liability account")
                yield Select(
                    [
                        (safe_display(f"{a.name} · {a.currency}"), encode_id(a.id))
                        for a in self.accounts
                        if a.account_type in (AccountType.ASSET, AccountType.LIABILITY)
                    ],
                    prompt="Choose account",
                    id="reconcile-account",
                )
                yield Label("Completed month · YYYY-MM")
                yield Input(placeholder="2026-08", id="reconcile-month")
                yield Label("Statement opening · signed decimal")
                yield Input(placeholder="0.00", id="reconcile-opening")
                yield Label("Statement closing · signed decimal")
                yield Input(placeholder="0.00", id="reconcile-closing")
                yield Label(
                    "Statement lines · one per row: YYYY-MM-DD | signed amount | "
                    "description | optional external ID"
                )
                yield TextArea(id="reconcile-lines")
                yield Checkbox("I reviewed the complete line inventory", id="reconcile-complete")
                yield Label("Source")
                yield Input("manual statement", id="reconcile-source")
            elif action == "match":
                choices = [
                    (
                        safe_display(
                            f"{v.account.name} {v.statement.start_on:%Y-%m} · "
                            f"line {s.line_id.value[:8]} → "
                            f"entry {s.entry_id.value[:8]} · {s.reason}"
                        ),
                        f"{encode_id(s.line_id)}|{encode_id(s.entry_id)}",
                    )
                    for v in self.portfolio.statements
                    if not v.closed
                    for s in v.suggestions
                ]
                yield Select(choices, prompt="Choose exact suggested pair", id="reconcile-choice")
            elif action == "exception":
                choices = [
                    (
                        safe_display(
                            f"{v.account.name} {v.statement.start_on:%Y-%m} · "
                            f"line {line.description} {line.movement.format()}"
                        ),
                        f"{encode_id(v.statement.id)}|{encode_id(line.id)}",
                    )
                    for v in self.portfolio.statements
                    if not v.closed
                    for line in v.unresolved_lines
                ]
                choices.extend(
                    (
                        safe_display(
                            f"{v.account.name} {v.statement.start_on:%Y-%m} · "
                            f"entry {entry.content.description}"
                        ),
                        f"{encode_id(v.statement.id)}|{encode_id(entry.id)}",
                    )
                    for v in self.portfolio.statements
                    if not v.closed
                    for entry in v.unresolved_entries
                )
                yield Select(choices, prompt="Choose unmatched item", id="reconcile-choice")
                yield Label("Why is this item accepted without a match?")
                yield Input(id="reconcile-rationale")
            elif action == "resolve":
                current = {v.statement.id for v in self.portfolio.statements if not v.closed}
                resolved = {r.issue.id for r in self.records.resolutions}
                yield Select(
                    [
                        (
                            safe_display(
                                f"Issue {i.id.value[:8]} · statement {i.statement.id.value[:8]}"
                            ),
                            encode_id(i.id),
                        )
                        for i in self.records.issues
                        if i.id not in resolved and i.statement.id in current
                    ],
                    prompt="Choose contradiction",
                    id="reconcile-choice",
                )
                yield Label("Rationale · a ledger correction is a separate reviewed entry")
                yield Input(id="reconcile-rationale")
            else:
                required_accounts = {
                    account.id
                    for account in self.accounts
                    if account.account_type in (AccountType.ASSET, AccountType.LIABILITY)
                }
                ready = sorted(
                    {
                        view.statement.start_on
                        for view in self.portfolio.statements
                        if {
                            item.account.id
                            for item in self.portfolio.statements
                            if item.statement.start_on == view.statement.start_on
                        }
                        == required_accounts
                        and all(
                            item.closable
                            for item in self.portfolio.statements
                            if item.statement.start_on == view.statement.start_on
                        )
                    }
                )
                yield Select(
                    [(f"{month:%Y-%m}", f"{month:%Y-%m}") for month in ready],
                    prompt="Choose month to review",
                    id="reconcile-choice",
                )
                yield Label("Close rationale")
                yield Input(id="reconcile-rationale")
            yield Static("", id="reconcile-error", classes="error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Review", variant="primary", id="reconcile-submit")
                yield Button("Cancel", id="reconcile-form-cancel")

    def _choice(self) -> str:
        value = cast(Select[str], self.query_one("#reconcile-choice", Select)).value
        if not isinstance(value, str):
            raise ValueError("Choose an item first")
        return value

    def _rationale(self) -> str:
        value = self.query_one("#reconcile-rationale", Input).value.strip()
        if not value:
            raise ValueError("Enter a review rationale")
        return value

    def _request(self) -> ReconcileRequest:
        action: ReconcileAction = self.reconcile_action
        if action == "statement":
            value = cast(Select[str], self.query_one("#reconcile-account", Select)).value
            if not isinstance(value, str):
                raise ValueError("Choose an account")
            account_id = decode_id(value)
            account = next(a for a in self.accounts if a.id == account_id)
            raw_month = self.query_one("#reconcile-month", Input).value.strip()
            if len(raw_month) != 7 or raw_month[4] != "-":
                raise ValueError("Month must be YYYY-MM")
            start = date.fromisoformat(raw_month + "-01")
            end = date(start.year, start.month, monthrange(start.year, start.month)[1])
            opening = Money.from_decimal(
                self.query_one("#reconcile-opening", Input).value, account.currency
            )
            closing = Money.from_decimal(
                self.query_one("#reconcile-closing", Input).value, account.currency
            )
            raw_lines = self.query_one("#reconcile-lines", TextArea).text.strip()
            lines: list[tuple[date, Money, str, str | None]] = []
            for row in raw_lines.splitlines() if raw_lines else ():
                fields = [field.strip() for field in row.split("|")]
                if len(fields) not in (3, 4):
                    raise ValueError("Each line needs date | amount | description | optional ID")
                lines.append(
                    (
                        date.fromisoformat(fields[0]),
                        Money.from_decimal(fields[1], account.currency),
                        fields[2],
                        fields[3] or None if len(fields) == 4 else None,
                    )
                )
            source = self.query_one("#reconcile-source", Input).value.strip()
            if not source:
                raise ValueError("Name the statement source")
            return ReconcileRequest(
                action,
                (
                    account_id,
                    start,
                    end,
                    opening,
                    closing,
                    tuple(lines),
                    self.query_one("#reconcile-complete", Checkbox).value,
                    source,
                ),
            )
        if action in ("match", "exception"):
            left, right = self._choice().split("|", 1)
            values: tuple[object, ...] = (decode_id(left), decode_id(right))
            if action == "exception":
                values += (self._rationale(),)
            return ReconcileRequest(action, values)
        if action == "resolve":
            return ReconcileRequest(action, (decode_id(self._choice()), self._rationale()))
        month = self._choice()
        return ReconcileRequest(action, (int(month[:4]), int(month[5:]), self._rationale()))

    @on(Button.Pressed, "#reconcile-submit")
    def submit(self) -> None:
        try:
            self.dismiss(self._request())
        except (ValueError, StopIteration) as exc:
            self.query_one("#reconcile-error", Static).update(safe_display(str(exc)))

    @on(Button.Pressed, "#reconcile-form-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
