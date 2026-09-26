"""Local reviewed inputs for debt, asset, and income-node models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from core.identity import Id
from personal_finance.display import safe_display
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import AssetCategory, AssetFlowKind
from personal_finance.domain.codec import decode_id, encode_id
from personal_finance.domain.ledger import JournalEntry
from personal_finance.domain.models import ModelRecords
from personal_finance.domain.money import Money
from personal_finance.domain.nodes import FundingKind

type ModelAction = Literal[
    "debt_terms",
    "debt_payment",
    "debt_compare",
    "asset_position",
    "asset_valuation",
    "asset_flow",
    "asset_coverage",
    "node_create",
    "node_funding",
]

_ACTIONS: dict[str, tuple[tuple[ModelAction, str], ...]] = {
    "debts": (
        ("debt_terms", "Set APR and minimum"),
        ("debt_payment", "Classify a posted payment"),
        ("debt_compare", "Compare payoff plans"),
    ),
    "assets": (
        ("asset_position", "Define a position"),
        ("asset_valuation", "Record a valuation"),
        ("asset_flow", "Classify a posted flow"),
        ("asset_coverage", "Review external flows"),
    ),
    "nodes": (
        ("node_create", "Define an income node"),
        ("node_funding", "Classify capital or withdrawal"),
    ),
}


@dataclass(frozen=True, slots=True)
class ModelRequest:
    action: ModelAction
    values: tuple[object, ...]


class ModelActionMenu(ModalScreen[ModelAction | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, tab: str) -> None:
        super().__init__()
        self.tab = tab

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label(f"{self.tab.upper()} · LOCAL REVIEW", classes="dialog-title")
            yield Static("Choose an input or comparison. Classifications never post money.")
            for action, label in _ACTIONS[self.tab]:
                yield Button(label, id=f"model-action-{action}")
            yield Button("Cancel", id="model-cancel")

    def on_mount(self) -> None:
        self.query_one(f"#model-action-{_ACTIONS[self.tab][0][0]}", Button).focus()

    @on(Button.Pressed)
    def chosen(self, event: Button.Pressed) -> None:
        identifier = event.button.id or ""
        if identifier == "model-cancel":
            self.dismiss(None)
        elif identifier.startswith("model-action-"):
            self.dismiss(cast(ModelAction, identifier.removeprefix("model-action-")))


class ModelInputForm(ModalScreen[ModelRequest | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self,
        action: ModelAction,
        accounts: tuple[Account, ...],
        entries: tuple[JournalEntry, ...],
        records: ModelRecords,
        today: date,
    ) -> None:
        super().__init__()
        self.model_action = action
        self.accounts = accounts
        self.entries = entries
        self.records = records
        self.today = today

    def _select_accounts(
        self, kind: AccountType, identifier: str, *, liquid: bool | None = None
    ) -> Select[str]:
        values = [
            (safe_display(f"{item.name} · {item.currency}"), encode_id(item.id))
            for item in self.accounts
            if item.account_type is kind and (liquid is None or item.liquid is liquid)
        ]
        return Select(values, prompt="Choose account", id=identifier)

    def _select_records(self, kind: str, identifier: str) -> Select[str]:
        if kind == "positions":
            values = [
                (
                    safe_display(f"{item.name} · {self._account(item.account.id).currency}"),
                    encode_id(item.id),
                )
                for item in self.records.positions
            ]
        else:
            values = [(safe_display(item.name), encode_id(item.id)) for item in self.records.nodes]
        return Select(values, prompt="Choose record", id=identifier)

    def _account(self, identifier: Id) -> Account:
        return next(item for item in self.accounts if item.id == identifier)

    def compose(self) -> ComposeResult:
        action = self.model_action
        label = next(name for pairs in _ACTIONS.values() for key, name in pairs if key == action)
        with VerticalScroll(classes="dialog wide-dialog cash-input-dialog"):
            yield Label(label.upper(), classes="dialog-title")
            yield Static(self._guidance(), classes="muted", markup=False)
            if action in ("debt_terms", "debt_payment"):
                yield Label("Liability account")
                yield self._select_accounts(AccountType.LIABILITY, "model-account")
            if action in ("asset_position",):
                yield Label("Non-liquid asset account")
                yield self._select_accounts(AccountType.ASSET, "model-account", liquid=False)
            if action in ("asset_valuation", "asset_flow", "asset_coverage"):
                yield Label("Asset position")
                yield self._select_records("positions", "model-position")
            if action == "node_funding":
                yield Label("Income node")
                yield self._select_records("nodes", "model-node")
            if action in ("debt_payment", "asset_flow", "node_funding"):
                yield Label("Posted ledger entry")
                yield Select(
                    [
                        (
                            safe_display(
                                f"{item.content.effective_date} · {item.content.description}"
                            ),
                            encode_id(item.id),
                        )
                        for item in self.entries
                    ],
                    prompt="Choose posted entry",
                    id="model-entry",
                )
            if action == "debt_terms":
                yield Label("APR percent · exact hundredths, for example 18.25")
                yield Input("0", id="model-apr")
                yield Label("Minimum monthly payment")
                yield Input("0.00", id="model-minimum")
                yield Label("Due day of month · 1–31")
                yield Input("1", id="model-due")
            elif action == "debt_payment":
                for key, text in (
                    ("principal", "Principal"),
                    ("interest", "Interest"),
                    ("fees", "Fees"),
                ):
                    yield Label(text)
                    yield Input("0.00", id=f"model-{key}")
            elif action == "debt_compare":
                yield Label("Currency · comparison stays within one currency")
                yield Input("USD", id="model-currency")
                yield Label("Extra monthly payment")
                yield Input("0.00", id="model-extra")
                yield Label("Optional custom debt order · comma-separated exact account names")
                yield Input(placeholder="Card, Loan", id="model-order")
            elif action == "asset_position":
                yield Label("Position name")
                yield Input(placeholder="Index fund or property", id="model-name")
                yield Label("Category")
                yield Select(
                    [(item.value.title(), item.value) for item in AssetCategory],
                    value=AssetCategory.INVESTMENT.value,
                    allow_blank=False,
                    id="model-category",
                )
            elif action == "asset_valuation":
                yield Label("Observed value")
                yield Input("0.00", id="model-value")
                yield Label("Quantity · optional exact decimal")
                yield Input(id="model-quantity")
                yield Label("Known cost basis · optional")
                yield Input(id="model-basis")
                yield Label("Observed date")
                yield Input(self.today.isoformat(), id="model-from")
                yield Label("Fresh through · at most 30 days")
                yield Input((self.today + timedelta(days=7)).isoformat(), id="model-through")
            elif action == "asset_flow":
                yield Label("Flow type relative to this position")
                yield Select(
                    [(item.value.replace("_", " ").title(), item.value) for item in AssetFlowKind],
                    value=AssetFlowKind.CONTRIBUTION.value,
                    allow_blank=False,
                    id="model-kind",
                )
                yield Label("Posted position movement amount")
                yield Input("0.00", id="model-amount")
            elif action == "asset_coverage":
                yield Label("Review begins")
                yield Input((self.today - timedelta(days=30)).isoformat(), id="model-from")
                yield Label("Reviewed through")
                yield Input(self.today.isoformat(), id="model-through")
                yield Checkbox(
                    "All external flows in this valuation period were reviewed", id="model-complete"
                )
            elif action == "node_create":
                yield Label("Node name")
                yield Input(placeholder="Side project", id="model-name")
                yield Label("Dedicated revenue account")
                yield self._select_accounts(AccountType.INCOME, "model-revenue")
                yield Label("Dedicated operating-expense account")
                yield self._select_accounts(AccountType.EXPENSE, "model-expense")
                yield Label("Optional liquid cash account · needed for runway")
                yield self._select_accounts(AccountType.ASSET, "model-cash", liquid=True)
                yield Label("Monthly revenue assumption · optional")
                yield Input(id="model-monthly-revenue")
                yield Label("Monthly expense assumption · optional")
                yield Input(id="model-monthly-expenses")
                yield Label("Milestone net target · optional")
                yield Input(id="model-milestone")
                yield Label("Milestone date · required with target")
                yield Input(id="model-milestone-on")
            elif action == "node_funding":
                yield Label("Transfer type")
                yield Select(
                    [(item.value.title(), item.value) for item in FundingKind],
                    value=FundingKind.CAPITAL.value,
                    allow_blank=False,
                    id="model-kind",
                )
                yield Label("Posted cash movement amount")
                yield Input("0.00", id="model-amount")
            if action != "debt_compare":
                yield Label("Source / basis of review")
                yield Input("manual UI", id="model-source")
            yield Static("", id="model-form-error", classes="error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(
                    "Calculate" if action == "debt_compare" else "Save reviewed input",
                    variant="primary",
                    id="model-save",
                )
                yield Button("Cancel", id="model-cancel")

    def _guidance(self) -> str:
        return {
            "debt_terms": "Debt balances come from posted liability entries. "
            "APR and minimums are reviewed terms.",
            "debt_payment": "Classify a posted payment; principal must match the liability "
            "debit and all parts the cash outflow.",
            "debt_compare": "Comparisons are conditional monthly simulations; "
            "no payment is posted.",
            "asset_position": "Use a dedicated non-liquid asset account "
            "for this valuation boundary.",
            "asset_valuation": "A dated observation is separate from the ledger. "
            "Missing or stale values stay unknown.",
            "asset_flow": "Classify an existing posting as external contribution, "
            "withdrawal, or internal transfer.",
            "asset_coverage": "Only affirm complete external-flow review when all period "
            "entries were checked.",
            "node_create": "Operating revenue and expenses use dedicated ledger accounts. "
            "Funding stays separate.",
            "node_funding": "Capital and withdrawals classify posted cash transfers, "
            "never operating revenue.",
        }[self.model_action]

    def _text(self, identifier: str) -> str:
        return self.query_one(f"#{identifier}", Input).value.strip()

    def _selected(self, identifier: str) -> Id:
        value = cast(Select[str], self.query_one(f"#{identifier}", Select)).value
        if not isinstance(value, str):
            raise ValueError("Choose a record before saving")
        return decode_id(value)

    def _optional_selected(self, identifier: str) -> Id | None:
        value = cast(Select[str], self.query_one(f"#{identifier}", Select)).value
        return decode_id(value) if isinstance(value, str) else None

    def _currency_for_position(self, position_id: Id) -> str:
        position = next(item for item in self.records.positions if item.id == position_id)
        return self._account(position.account.id).currency

    def _request(self) -> ModelRequest:
        action = self.model_action
        source = self._text("model-source") if action != "debt_compare" else ""
        if action == "debt_terms":
            account_id = self._selected("model-account")
            currency = self._account(account_id).currency
            apr = Decimal(self._text("model-apr")) * 100
            if not apr.is_finite() or apr != apr.to_integral_value():
                raise ValueError("APR must have at most two decimal places")
            return ModelRequest(
                action,
                (
                    account_id,
                    int(apr),
                    Money.from_decimal(self._text("model-minimum"), currency),
                    int(self._text("model-due")),
                    source,
                ),
            )
        if action == "debt_payment":
            account_id = self._selected("model-account")
            currency = self._account(account_id).currency
            return ModelRequest(
                action,
                (
                    account_id,
                    self._selected("model-entry"),
                    *(
                        Money.from_decimal(self._text(f"model-{part}"), currency)
                        for part in ("principal", "interest", "fees")
                    ),
                    source,
                ),
            )
        if action == "debt_compare":
            currency = self._text("model-currency")
            names = tuple(
                item.strip() for item in self._text("model-order").split(",") if item.strip()
            )
            order = tuple(
                next(account.id for account in self.accounts if account.name == name)
                for name in names
            )
            return ModelRequest(
                action, (currency, Money.from_decimal(self._text("model-extra"), currency), order)
            )
        if action == "asset_position":
            kind = cast(Select[str], self.query_one("#model-category", Select)).value
            return ModelRequest(
                action,
                (
                    self._selected("model-account"),
                    self._text("model-name"),
                    AssetCategory(str(kind)),
                    source,
                ),
            )
        if action == "asset_valuation":
            position_id = self._selected("model-position")
            currency = self._currency_for_position(position_id)
            quantity_text = self._text("model-quantity")
            basis_text = self._text("model-basis")
            return ModelRequest(
                action,
                (
                    position_id,
                    Money.from_decimal(self._text("model-value"), currency),
                    Decimal(quantity_text) if quantity_text else None,
                    Money.from_decimal(basis_text, currency) if basis_text else None,
                    date.fromisoformat(self._text("model-from")),
                    date.fromisoformat(self._text("model-through")),
                    source,
                ),
            )
        if action == "asset_flow":
            position_id = self._selected("model-position")
            currency = self._currency_for_position(position_id)
            kind = cast(Select[str], self.query_one("#model-kind", Select)).value
            return ModelRequest(
                action,
                (
                    position_id,
                    self._selected("model-entry"),
                    AssetFlowKind(str(kind)),
                    Money.from_decimal(self._text("model-amount"), currency),
                    source,
                ),
            )
        if action == "asset_coverage":
            return ModelRequest(
                action,
                (
                    self._selected("model-position"),
                    date.fromisoformat(self._text("model-from")),
                    date.fromisoformat(self._text("model-through")),
                    self.query_one("#model-complete", Checkbox).value,
                    source,
                ),
            )
        if action == "node_create":
            revenue = self._selected("model-revenue")
            currency = self._account(revenue).currency

            def optional_money(identifier: str) -> Money | None:
                raw = self._text(identifier)
                return Money.from_decimal(raw, currency) if raw else None

            target_date = self._text("model-milestone-on")
            return ModelRequest(
                action,
                (
                    self._text("model-name"),
                    revenue,
                    self._selected("model-expense"),
                    self._optional_selected("model-cash"),
                    optional_money("model-monthly-revenue"),
                    optional_money("model-monthly-expenses"),
                    optional_money("model-milestone"),
                    date.fromisoformat(target_date) if target_date else None,
                    source,
                ),
            )
        node_id = self._selected("model-node")
        node = next(item for item in self.records.nodes if item.id == node_id)
        currency = self._account(node.revenue_account.id).currency
        kind = cast(Select[str], self.query_one("#model-kind", Select)).value
        return ModelRequest(
            cast(ModelAction, action),
            (
                node_id,
                self._selected("model-entry"),
                FundingKind(str(kind)),
                Money.from_decimal(self._text("model-amount"), currency),
                source,
            ),
        )

    @on(Button.Pressed, "#model-save")
    def save(self) -> None:
        try:
            self.dismiss(self._request())
        except (ValueError, TypeError, StopIteration, InvalidOperation) as exc:
            self.query_one("#model-form-error", Static).update(safe_display(str(exc)))

    @on(Button.Pressed, "#model-cancel")
    def cancel(self) -> None:
        self.dismiss(None)
