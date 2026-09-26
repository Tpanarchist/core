"""Finance policy attached to Core's identity-bearing account records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.identity import Id
from personal_finance.domain.kinds import ACCOUNT
from personal_finance.domain.money import currency_exponent


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


@dataclass(frozen=True, slots=True)
class Account:
    id: Id
    name: str
    account_type: AccountType
    currency: str
    liquid: bool = False

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != ACCOUNT:
            raise ValueError("Account requires a finance.account identity")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("Account name must not be empty")
        if type(self.account_type) is not AccountType:
            raise TypeError("Account type must be an AccountType")
        currency_exponent(self.currency)
        if type(self.liquid) is not bool:
            raise TypeError("Account liquid flag must be a bool")
        if self.liquid and self.account_type != AccountType.ASSET:
            raise ValueError("Only asset accounts can be eligible liquid cash")

    @property
    def display_sign(self) -> int:
        return 1 if self.account_type in (AccountType.ASSET, AccountType.EXPENSE) else -1
