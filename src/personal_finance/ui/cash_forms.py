"""Local cash-planning inputs; parsing stays separate from finance policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from core.identity import Id, Ref
from personal_finance.display import safe_display
from personal_finance.domain.accounts import Account
from personal_finance.domain.cash import Cadence, CashRecords, ScheduleOccurrence
from personal_finance.domain.codec import decode_id, encode_id
from personal_finance.domain.money import Money

type CashAction = Literal[
    "schedule", "allocation", "hold", "floor", "observation", "coverage", "retire"
]

_ACTIONS: tuple[tuple[CashAction, str], ...] = (
    ("schedule", "Schedule an inflow or bill"),
    ("allocation", "Protect an allocation"),
    ("hold", "Record a cash hold"),
    ("floor", "Set a minimum cash floor"),
    ("observation", "Record a balance observation"),
    ("coverage", "Declare reviewed coverage"),
    ("retire", "Retire a plan item"),
)


@dataclass(frozen=True, slots=True)
class ScheduleRequest:
    account_id: Id
    label: str
    amount: Money
    start_date: date
    cadence: Cadence
    timezone: str
    end_date: date | None


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    label: str
    amount: Money
    active_from: date
    linked_occurrence: ScheduleOccurrence | None


@dataclass(frozen=True, slots=True)
class HoldRequest:
    label: str
    amount: Money
    active_from: date
    release_date: date | None


@dataclass(frozen=True, slots=True)
class FloorRequest:
    currency: str
    effective_date: date
    amount: Money


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    account_id: Id
    observed: Money
    observed_on: date
    fresh_through: date
    source: str


@dataclass(frozen=True, slots=True)
class CoverageRequest:
    currency: str
    as_of: date
    through_date: date
    accounts_complete: bool
    schedules_complete: bool
    source: str


@dataclass(frozen=True, slots=True)
class RetirementRequest:
    target_id: Id
    effective_date: date
    reason: str


type CashRequest = (
    ScheduleRequest
    | AllocationRequest
    | HoldRequest
    | FloorRequest
    | ObservationRequest
    | CoverageRequest
    | RetirementRequest
)


class CashActionMenu(ModalScreen[CashAction | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label("CASH PLANNING", classes="dialog-title")
            yield Static(
                "Choose a local planning action. These records do not post ledger entries.",
                markup=False,
            )
            for action, label in _ACTIONS:
                yield Button(label, id=f"cash-action-{action}", classes="cash-menu-action")
            yield Button("Cancel", id="cash-cancel")

    def on_mount(self) -> None:
        self.query_one("#cash-action-schedule", Button).focus()

    @on(Button.Pressed)
    def selected(self, event: Button.Pressed) -> None:
        action = (event.button.id or "").removeprefix("cash-action-")
        if action in {item[0] for item in _ACTIONS}:
            self.dismiss(cast(CashAction, action))
        elif event.button.id == "cash-cancel":
            self.dismiss(None)


class CashInputForm(ModalScreen[CashRequest | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self, action: CashAction, accounts: tuple[Account, ...], records: CashRecords, today: date
    ) -> None:
        super().__init__()
        self.cash_action: CashAction = action
        self.accounts = tuple(account for account in accounts if account.liquid)
        self.records = records
        self.today = today

    def compose(self) -> ComposeResult:
        action = self.cash_action
        with VerticalScroll(classes="dialog wide-dialog cash-input-dialog"):
            title = dict(_ACTIONS)[action]
            yield Label(title.upper(), classes="dialog-title")
            yield Static(self._guidance(), classes="muted", markup=False)
            if action in ("schedule", "allocation", "hold"):
                yield Label("Label")
                yield Input(placeholder="Rent, payday, or emergency reserve", id="cash-label")
            if action in ("schedule", "observation"):
                yield Label("Liquid account")
                yield Select(
                    [
                        (
                            safe_display(f"{account.name} · {account.currency}"),
                            encode_id(account.id),
                        )
                        for account in self.accounts
                    ],
                    prompt="Choose account",
                    id="cash-account",
                )
            if action == "retire":
                yield Label("Plan item to retire · original history remains")
                retired = {item.target.id for item in self.records.retirements}
                items = (*self.records.schedules, *self.records.allocations, *self.records.holds)
                yield Select(
                    [
                        (safe_display(f"{item.label} · {item.amount.format()}"), encode_id(item.id))
                        for item in items
                        if item.id not in retired
                    ],
                    prompt="Choose plan item",
                    id="cash-target",
                )
            if action not in ("retire", "schedule", "observation"):
                yield Label("Currency · each currency is planned separately")
                yield Input(
                    self.accounts[0].currency if self.accounts else "USD", id="cash-currency"
                )
            if action != "coverage" and action != "retire":
                yield Label(
                    "Signed amount · + inflow / − outflow"
                    if action == "schedule"
                    else "Amount · decimal currency units"
                )
                yield Input(placeholder="0.00", id="cash-amount")
            yield Label(
                "Observed date · YYYY-MM-DD"
                if action == "observation"
                else "Effective / start date · YYYY-MM-DD"
            )
            yield Input(str(self.today), id="cash-date")
            if action in ("schedule", "hold", "observation", "coverage"):
                labels = {
                    "schedule": "End date · optional, inclusive",
                    "hold": "Release date · optional; unavailable until released",
                    "observation": "Fresh through · maximum 7 days after observation",
                    "coverage": "Coverage through · YYYY-MM-DD",
                }
                yield Label(labels[action])
                yield Input(
                    str(self.today) if action in ("observation", "coverage") else "",
                    placeholder="YYYY-MM-DD",
                    id="cash-until",
                )
            if action == "schedule":
                yield Label("Repeats · monthly dates clamp to the last day")
                yield Select(
                    [(cadence.value.title(), cadence.value) for cadence in Cadence],
                    value=Cadence.ONCE.value,
                    allow_blank=False,
                    id="cash-cadence",
                )
                yield Label("Calendar timezone · IANA name, for example America/New_York")
                yield Input("UTC", id="cash-timezone")
            if action == "allocation":
                yield Label("Optional linked bill · avoids reserving the same occurrence twice")
                yield Select(
                    [
                        (safe_display(schedule.label), encode_id(schedule.id))
                        for schedule in self.records.schedules
                        if schedule.amount.minor < 0
                    ],
                    prompt="Unlinked allocation",
                    id="cash-linked-schedule",
                )
                yield Label("Linked occurrence date · required only when a bill is chosen")
                yield Input(placeholder="YYYY-MM-DD", id="cash-linked-date")
            if action == "coverage":
                yield Checkbox(
                    "I reviewed the complete liquid-account inventory for this currency",
                    value=False,
                    id="cash-accounts-complete",
                )
                yield Checkbox(
                    "I reviewed all expected inflows and outflows through this horizon",
                    value=False,
                    id="cash-schedules-complete",
                )
            if action in ("observation", "coverage", "retire"):
                yield Label("Reason" if action == "retire" else "Source / basis of review")
                yield Input(placeholder="Describe the evidence or decision", id="cash-source")
            yield Static("", id="cash-form-error", classes="error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(
                    "Save reviewed plan" if action != "observation" else "Save observation",
                    variant="primary",
                    id="cash-save",
                )
                yield Button("Cancel", id="cash-cancel")

    def _guidance(self) -> str:
        return {
            "schedule": "Planned external cash flow only. Internal liquid-account transfers "
            "belong in the ledger. A schedule never asserts money has moved.",
            "allocation": "A protected earmark is not an outflow. Link an existing bill occurrence "
            "when this amount is already represented by a schedule.",
            "hold": "A hold reduces available cash while active. It does not reduce the posted "
            "ledger balance and must not also be entered as a scheduled outflow.",
            "floor": "An explicit floor is required before safe-to-allocate can be known. "
            "A reviewed zero floor is allowed; a missing floor stays unknown.",
            "observation": "Record an observed balance and its source. This is evidence, not a "
            "ledger correction; a mismatch keeps allocation advice unavailable.",
            "coverage": "Only affirm what you have reviewed. Account and schedule completeness "
            "apply to this currency and date range; neither is inferred.",
            "retire": "Stop a schedule, allocation, or hold from this date. Retirement appends "
            "a decision and keeps the original record and prior history.",
        }[self.cash_action]

    def on_mount(self) -> None:
        if self.cash_action in ("schedule", "allocation", "hold"):
            self.query_one("#cash-label", Input).focus()
        elif self.cash_action == "observation":
            self.query_one("#cash-account", Select).focus()
        elif self.cash_action == "retire":
            self.query_one("#cash-target", Select).focus()
        else:
            self.query_one("#cash-currency", Input).focus()

    def _text(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _selected(self, selector: str) -> str:
        value = cast(Select[str], self.query_one(selector, Select)).value
        if not isinstance(value, str):
            raise ValueError("Choose an account or plan item before saving.")
        return value

    def _request(self) -> CashRequest:
        action = self.cash_action
        start = date.fromisoformat(self._text("#cash-date"))
        if action == "retire":
            return RetirementRequest(
                decode_id(self._selected("#cash-target")), start, self._text("#cash-source")
            )
        if action == "coverage":
            return CoverageRequest(
                self._text("#cash-currency").upper(),
                start,
                date.fromisoformat(self._text("#cash-until")),
                self.query_one("#cash-accounts-complete", Checkbox).value,
                self.query_one("#cash-schedules-complete", Checkbox).value,
                self._text("#cash-source"),
            )
        account_id: Id | None = None
        if action in ("schedule", "observation"):
            account_id = decode_id(self._selected("#cash-account"))
            currency = next(
                account.currency for account in self.accounts if account.id == account_id
            )
        else:
            currency = self._text("#cash-currency").upper()
        amount = Money.from_decimal(self._text("#cash-amount"), currency)
        if action == "floor":
            return FloorRequest(currency, start, amount)
        if action == "observation":
            assert account_id is not None
            return ObservationRequest(
                account_id,
                amount,
                start,
                date.fromisoformat(self._text("#cash-until")),
                self._text("#cash-source"),
            )
        label = self._text("#cash-label")
        if not label:
            raise ValueError("Enter a label for this cash plan item.")
        if action == "allocation":
            linked = cast(Select[str], self.query_one("#cash-linked-schedule", Select)).value
            occurrence = (
                ScheduleOccurrence(
                    Ref(decode_id(linked)), date.fromisoformat(self._text("#cash-linked-date"))
                )
                if isinstance(linked, str)
                else None
            )
            return AllocationRequest(label, amount, start, occurrence)
        until_text = self._text("#cash-until")
        until = date.fromisoformat(until_text) if until_text else None
        if action == "hold":
            return HoldRequest(label, amount, start, until)
        assert account_id is not None
        return ScheduleRequest(
            account_id,
            label,
            amount,
            start,
            Cadence(self._selected("#cash-cadence")),
            self._text("#cash-timezone"),
            until,
        )

    @on(Button.Pressed, "#cash-save")
    def submit(self) -> None:
        try:
            request = self._request()
        except (ValueError, TypeError) as exc:
            self.query_one("#cash-form-error", Static).update(safe_display(str(exc)))
            return
        self.dismiss(request)

    @on(Button.Pressed, "#cash-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
