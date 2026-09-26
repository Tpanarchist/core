"""Income-node boundaries distinguish operations from personal funding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.money import Money

NODE = Kind("finance.income_node")
FUNDING = Kind("finance.node_funding")
ACCOUNT = Kind("finance.account")
ENTRY = Kind("finance.journal_entry")


class FundingKind(StrEnum):
    CAPITAL = "capital"
    WITHDRAWAL = "withdrawal"


@dataclass(frozen=True, slots=True)
class IncomeNode:
    id: Id
    name: str
    revenue_account: Ref
    expense_account: Ref
    cash_account: Ref | None
    monthly_revenue_assumption: Money | None
    monthly_expense_assumption: Money | None
    milestone_target: Money | None
    milestone_on: date | None
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != NODE:
            raise ValueError("Income node requires its own finance identity")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("Income node requires a name")
        for ref in (self.revenue_account, self.expense_account, self.cash_account):
            if ref is not None and (type(ref) is not Ref or ref.id.kind != ACCOUNT):
                raise ValueError("Income node accounts require account references")
        if self.revenue_account == self.expense_account:
            raise ValueError("Revenue and expense accounts must differ")
        values = (
            self.monthly_revenue_assumption,
            self.monthly_expense_assumption,
            self.milestone_target,
        )
        if any(
            value is not None and (type(value) is not Money or value.minor < 0) for value in values
        ):
            raise ValueError("Node assumptions must be nonnegative Money")
        if self.milestone_on is not None and type(self.milestone_on) is not date:
            raise TypeError("Node milestone date must be date-only")
        if (self.milestone_target is None) != (self.milestone_on is None):
            raise ValueError("Node milestone target and date must be paired")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Income node requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Income node requires a source")


@dataclass(frozen=True, slots=True)
class NodeFunding:
    """Classifies a posted cash transfer without changing operating performance."""

    id: Id
    node: Ref
    entry: Ref
    kind: FundingKind
    amount: Money
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != FUNDING:
            raise ValueError("Node funding requires its own finance identity")
        if type(self.node) is not Ref or self.node.id.kind != NODE:
            raise ValueError("Node funding requires a node reference")
        if type(self.entry) is not Ref or self.entry.id.kind != ENTRY:
            raise ValueError("Node funding requires a posted entry reference")
        if type(self.kind) is not FundingKind:
            raise TypeError("Node funding kind must be explicit")
        if type(self.amount) is not Money or self.amount.minor <= 0:
            raise ValueError("Node funding amount must be positive Money")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Node funding requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Node funding requires a source")
