"""Keyboard-accessible local forms and explicit content review."""

from __future__ import annotations

from datetime import date
from typing import cast

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from core.identity import Ref
from personal_finance.display import content_text
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.ledger import Draft, EntryContent, Posting
from personal_finance.domain.money import Money


class AccountForm(ModalScreen[tuple[str, AccountType, str, bool] | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label("NEW ACCOUNT", classes="dialog-title")
            yield Label("Name · exact account names are used by CSV imports")
            yield Input(placeholder="Checking", id="account-name")
            yield Label("Account type")
            yield Select(
                [(kind.value.title(), kind.value) for kind in AccountType],
                value="asset",
                allow_blank=False,
                id="account-type",
            )
            yield Label("Currency · supported ISO code")
            yield Input("USD", id="account-currency")
            yield Checkbox("Liquid cash account", value=True, id="account-liquid")
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Create account", variant="primary", id="create-account")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#account-name", Input).focus()

    @on(Select.Changed, "#account-type")
    def account_type_changed(self) -> None:
        selected = cast(Select[str], self.query_one("#account-type", Select)).value
        liquid = self.query_one("#account-liquid", Checkbox)
        liquid.disabled = selected != "asset"
        if liquid.disabled:
            liquid.value = False

    @on(Button.Pressed, "#create-account")
    def submit(self) -> None:
        name = self.query_one("#account-name", Input).value.strip()
        currency = self.query_one("#account-currency", Input).value.strip().upper()
        account_type = cast(Select[str], self.query_one("#account-type", Select)).value
        if not name or not currency or not isinstance(account_type, str):
            self.query_one("#form-error", Static).update("Enter a name, type, and currency.")
            return
        self.dismiss(
            (
                name,
                AccountType(account_type),
                currency,
                self.query_one("#account-liquid", Checkbox).value,
            )
        )

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class EntryForm(ModalScreen[EntryContent | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self,
        accounts: tuple[Account, ...],
        today: date,
        content: EntryContent | None = None,
    ) -> None:
        super().__init__()
        self.accounts = accounts
        self.today = today
        self.content = content

    def compose(self) -> ComposeResult:
        content = self.content
        options = [(f"{a.name} · {a.currency}", a.id.value) for a in self.accounts]
        names = {account.id: account.name for account in self.accounts}
        with VerticalScroll(classes="dialog wide-dialog"):
            yield Label("PREPARE ENTRY  /  REVIEW BEFORE POSTING", classes="dialog-title")
            yield Label("Effective date · YYYY-MM-DD")
            yield Input(str(content.effective_date if content else self.today), id="entry-date")
            yield Label("Description")
            yield Input(content.description if content else "", id="entry-description")
            yield Label("Quick entry · choose debit and credit accounts, then a positive amount")
            yield Select(options, prompt="Debit account", id="entry-debit")
            yield Select(options, prompt="Credit account", id="entry-credit")
            yield Input(placeholder="125.00", id="entry-amount")
            yield Label("OR split entry · one line per posting: exact account name | signed amount")
            yield Static(
                "Debits are positive; credits negative. All lines must balance.",
                classes="muted",
                markup=False,
            )
            split = ""
            if content:
                split = "\n".join(
                    f"{names.get(post.account.id, post.account.id.value)} | "
                    f"{post.money.decimal_string()}"
                    for post in content.postings
                )
            yield TextArea(split, id="entry-splits")
            yield Label("Tags · comma separated")
            yield Input(", ".join(content.tags) if content else "", id="entry-tags")
            yield Static("", id="form-error", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Prepare & review", variant="primary", id="prepare-entry")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#entry-description", Input).focus()

    @on(Button.Pressed, "#prepare-entry")
    def submit(self) -> None:
        try:
            effective = date.fromisoformat(self.query_one("#entry-date", Input).value)
            description = self.query_one("#entry-description", Input).value.strip()
            postings: list[Posting] = []
            splits = self.query_one("#entry-splits", TextArea).text.strip()
            if splits:
                for number, line in enumerate(splits.splitlines(), 1):
                    name, separator, amount = line.rpartition("|")
                    matches = [a for a in self.accounts if a.name == name.strip()]
                    if not separator or len(matches) != 1:
                        raise ValueError(f"Line {number}: use one exact account name | amount.")
                    account = matches[0]
                    postings.append(
                        Posting(
                            Ref(account.id), Money.from_decimal(amount.strip(), account.currency)
                        )
                    )
            else:
                debit_id = cast(Select[str], self.query_one("#entry-debit", Select)).value
                credit_id = cast(Select[str], self.query_one("#entry-credit", Select)).value
                selected = {account.id.value: account for account in self.accounts}
                if not isinstance(debit_id, str) or not isinstance(credit_id, str):
                    raise ValueError("Choose both quick-entry accounts, or enter split lines.")
                debit, credit = selected[debit_id], selected[credit_id]
                amount = Money.from_decimal(
                    self.query_one("#entry-amount", Input).value, debit.currency
                )
                if amount.minor <= 0:
                    raise ValueError("Quick-entry amount must be positive.")
                postings = [
                    Posting(Ref(debit.id), amount),
                    Posting(Ref(credit.id), Money(-amount.minor, credit.currency)),
                ]
            tags = tuple(
                tag.strip()
                for tag in self.query_one("#entry-tags", Input).value.split(",")
                if tag.strip()
            )
            self.dismiss(EntryContent(effective, description, tuple(postings), tags))
        except (ValueError, KeyError) as exc:
            self.query_one("#form-error", Static).update(str(exc))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ReviewScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Keep draft")]

    def __init__(self, draft: Draft, accounts: tuple[Account, ...]) -> None:
        super().__init__()
        self.draft = draft
        self.accounts = accounts

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide-dialog"):
            yield Label("LOCAL REVIEW  /  EXACT CONTENT", classes="dialog-title")
            yield Static(content_text(self.draft.content, self.accounts), markup=False)
            yield Static(
                f"\nDraft: {self.draft.id.value}\nContent hash: {self.draft.content_hash}",
                classes="muted",
                markup=False,
            )
            yield Static(
                "Posting creates permanent accounting history. Corrections use a reversal.",
                classes="warning",
                markup=False,
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Post this entry", variant="primary", id="confirm-post")
                yield Button("Keep draft", id="keep-draft")

    def on_mount(self) -> None:
        self.query_one("#keep-draft", Button).focus()

    @on(Button.Pressed, "#confirm-post")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#keep-draft")
    def keep(self) -> None:
        self.dismiss(False)


class TextPrompt(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, hint: str, value: str = "") -> None:
        super().__init__()
        self.heading, self.hint, self.value = title, hint, value

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label(self.heading, classes="dialog-title")
            yield Static(self.hint, markup=False)
            yield Input(self.value, id="prompt-input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Continue", variant="primary", id="prompt-submit")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    @on(Input.Submitted, "#prompt-input")
    @on(Button.Pressed, "#prompt-submit")
    def submit(self) -> None:
        self.dismiss(self.query_one("#prompt-input", Input).value)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class InfoScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.heading, self.body = title, body

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide-dialog"):
            yield Label(self.heading, classes="dialog-title")
            yield Static(Text(self.body), id="info-content")
            yield Button("Close", id="close-info")

    @on(Button.Pressed, "#close-info")
    def close_info(self) -> None:
        self.dismiss(None)
