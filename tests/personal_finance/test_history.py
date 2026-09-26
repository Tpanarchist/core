"""Recorded liquid-cash chart input uses the ledger's effective-date boundaries."""

from __future__ import annotations

from datetime import UTC, date, datetime

from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind
from personal_finance.application.history import recorded_liquid_history
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.ledger import EntryContent, JournalEntry, Posting
from personal_finance.domain.money import Money


def _account(name: str, currency: str, liquid: bool) -> Account:
    return Account(Id(Kind("finance.account"), name), name, AccountType.ASSET, currency, liquid)


def _entry(number: int, day: int, *postings: Posting) -> JournalEntry:
    content = EntryContent(date(2026, 9, day), f"Entry {number}", tuple(postings))
    return JournalEntry(
        Id(Kind("finance.journal_entry"), str(number)),
        content,
        WallInstant(datetime(2026, 9, 23, tzinfo=UTC)),
        "local-human",
        number,
    )


def test_history_groups_effective_dates_and_does_not_double_count_transfers() -> None:
    checking = _account("Checking", "USD", True)
    savings = _account("Savings", "USD", True)
    income = Account(Id(Kind("finance.account"), "Income"), "Income", AccountType.INCOME, "USD")
    expense = Account(Id(Kind("finance.account"), "Expense"), "Expense", AccountType.EXPENSE, "USD")
    entries = (
        _entry(
            4,
            3,
            Posting(Ref(savings.id), Money(1000, "USD")),
            Posting(Ref(checking.id), Money(-1000, "USD")),
        ),
        _entry(
            3,
            2,
            Posting(Ref(expense.id), Money(2500, "USD")),
            Posting(Ref(checking.id), Money(-2500, "USD")),
        ),
        _entry(
            2,
            2,
            Posting(Ref(checking.id), Money(5000, "USD")),
            Posting(Ref(income.id), Money(-5000, "USD")),
        ),
        _entry(
            1,
            1,
            Posting(Ref(checking.id), Money(10000, "USD")),
            Posting(Ref(income.id), Money(-10000, "USD")),
        ),
    )
    result = recorded_liquid_history((checking, savings, income, expense), entries)
    assert len(result) == 1
    assert result[0].currency == "USD"
    assert [(point.effective_date.day, point.minor) for point in result[0].points] == [
        (1, 10000),
        (2, 12500),
        (3, 12500),
    ]


def test_empty_and_foreign_currency_histories_remain_separate() -> None:
    usd = _account("Checking", "USD", True)
    jpy = _account("Yen", "JPY", True)
    first = recorded_liquid_history((usd, jpy), ())
    assert [(series.currency, series.points) for series in first] == [("JPY", ()), ("USD", ())]


def test_history_uses_recorded_postings_even_when_backdated() -> None:
    cash = _account("Cash", "USD", True)
    income = Account(Id(Kind("finance.account"), "Income"), "Income", AccountType.INCOME, "USD")
    entries = (
        _entry(
            1,
            20,
            Posting(Ref(cash.id), Money(100, "USD")),
            Posting(Ref(income.id), Money(-100, "USD")),
        ),
        _entry(
            2,
            5,
            Posting(Ref(cash.id), Money(200, "USD")),
            Posting(Ref(income.id), Money(-200, "USD")),
        ),
    )
    points = recorded_liquid_history((cash, income), entries)[0].points
    assert [(point.effective_date.day, point.minor) for point in points] == [(5, 200), (20, 300)]
