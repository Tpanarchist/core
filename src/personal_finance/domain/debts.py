"""Immutable debt terms and classifications of already posted payments."""

from __future__ import annotations

from dataclasses import dataclass

from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.money import Money

TERMS = Kind("finance.debt_terms")
PAYMENT_SPLIT = Kind("finance.debt_payment_split")
ACCOUNT = Kind("finance.account")
ENTRY = Kind("finance.journal_entry")


@dataclass(frozen=True, slots=True)
class DebtTerms:
    id: Id
    account: Ref
    apr_basis_points: int
    minimum_payment: Money
    due_day: int
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != TERMS:
            raise ValueError("Debt terms require their own finance identity")
        if type(self.account) is not Ref or self.account.id.kind != ACCOUNT:
            raise ValueError("Debt terms require an account reference")
        if type(self.apr_basis_points) is not int or not 0 <= self.apr_basis_points <= 100_000:
            raise ValueError("APR basis points must be an integer from 0 to 100000")
        if type(self.minimum_payment) is not Money or self.minimum_payment.minor < 0:
            raise ValueError("Debt minimum must be nonnegative Money")
        if type(self.due_day) is not int or not 1 <= self.due_day <= 31:
            raise ValueError("Debt due day must be from 1 to 31")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Debt terms require a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Debt terms require a source")


@dataclass(frozen=True, slots=True)
class DebtPaymentSplit:
    """A classification, never an extra ledger movement."""

    id: Id
    account: Ref
    entry: Ref
    principal: Money
    interest: Money
    fees: Money
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != PAYMENT_SPLIT:
            raise ValueError("Payment split requires its own finance identity")
        if type(self.account) is not Ref or self.account.id.kind != ACCOUNT:
            raise ValueError("Payment split requires a debt account reference")
        if type(self.entry) is not Ref or self.entry.id.kind != ENTRY:
            raise ValueError("Payment split requires a posted entry reference")
        amounts = (self.principal, self.interest, self.fees)
        if any(type(value) is not Money or value.minor < 0 for value in amounts):
            raise ValueError("Payment components must be nonnegative Money")
        if len({value.currency for value in amounts}) != 1:
            raise ValueError("Payment components require one currency")
        if sum(value.minor for value in amounts) <= 0:
            raise ValueError("Payment split requires a positive total")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Payment split requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Payment split requires a source")
