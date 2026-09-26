"""Deterministic ledger reconstruction at financial effective-date boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from personal_finance.domain.accounts import Account
from personal_finance.domain.ledger import JournalEntry


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    effective_date: date
    minor: int


@dataclass(frozen=True, slots=True)
class CurrencyHistory:
    currency: str
    points: tuple[HistoryPoint, ...]


def recorded_liquid_history(
    accounts: tuple[Account, ...], entries: tuple[JournalEntry, ...]
) -> tuple[CurrencyHistory, ...]:
    """Rebuild *recorded* liquid totals, one point after each effective date.

    Separate currencies are never combined. Transfers between eligible liquid
    accounts net to zero, while a backdated posting changes every later point.
    No point is manufactured when a currency has no posted liquid activity.
    The resulting history is not a bank balance observation or a forecast.
    """
    liquid = {account.id: account.currency for account in accounts if account.liquid}
    currencies = sorted(set(liquid.values()))
    changes: dict[str, dict[date, int]] = {currency: {} for currency in currencies}
    for entry in sorted(entries, key=lambda item: (item.content.effective_date, item.sequence)):
        day = entry.content.effective_date
        for posting in entry.content.postings:
            currency = liquid.get(posting.account.id)
            if currency is None:
                continue
            if posting.money.currency != currency:
                raise ValueError("Posting currency differs from the liquid account currency")
            changes[currency][day] = changes[currency].get(day, 0) + posting.money.minor
    result: list[CurrencyHistory] = []
    for currency in currencies:
        running = 0
        points: list[HistoryPoint] = []
        for day, delta in sorted(changes[currency].items()):
            running += delta
            points.append(HistoryPoint(day, running))
        result.append(CurrencyHistory(currency, tuple(points)))
    return tuple(result)
