"""Position boundaries, manual valuations, and classified posted flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from core.context import Context
from core.identity import Id, Ref
from core.observation import Observation
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.money import Money

POSITION = Kind("finance.asset_position")
VALUATION = Kind("finance.asset_valuation")
FLOW = Kind("finance.asset_flow")
FLOW_COVERAGE = Kind("finance.asset_flow_coverage")
ACCOUNT = Kind("finance.account")
ENTRY = Kind("finance.journal_entry")


class AssetCategory(StrEnum):
    INVESTMENT = "investment"
    CRYPTO = "crypto"
    PROPERTY = "property"
    OTHER = "other"


class AssetFlowKind(StrEnum):
    CONTRIBUTION = "contribution"
    WITHDRAWAL = "withdrawal"
    INTERNAL_TRANSFER = "internal_transfer"


@dataclass(frozen=True, slots=True)
class AssetPosition:
    id: Id
    account: Ref
    name: str
    category: AssetCategory
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != POSITION:
            raise ValueError("Asset position requires its own finance identity")
        if type(self.account) is not Ref or self.account.id.kind != ACCOUNT:
            raise ValueError("Asset position requires an asset account reference")
        if type(self.name) is not str or not self.name.strip():
            raise ValueError("Asset position requires a name")
        if type(self.category) is not AssetCategory:
            raise TypeError("Asset category must be explicit")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Asset position requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Asset position requires a source")


@dataclass(frozen=True, slots=True)
class AssetValuation:
    id: Id
    position: Ref
    value: Money
    quantity: Decimal | None
    cost_basis: Money | None
    observed_on: date
    fresh_through: date
    observed_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != VALUATION:
            raise ValueError("Asset valuation requires its own finance identity")
        if type(self.position) is not Ref or self.position.id.kind != POSITION:
            raise ValueError("Asset valuation requires a position reference")
        if type(self.value) is not Money or self.value.minor < 0:
            raise ValueError("Asset value must be nonnegative Money")
        if self.quantity is not None:
            if type(self.quantity) is not Decimal or not self.quantity.is_finite():
                raise ValueError("Quantity requires a finite Decimal")
            exponent = self.quantity.as_tuple().exponent
            if self.quantity < 0 or not isinstance(exponent, int) or exponent < -12:
                raise ValueError("Quantity must be nonnegative with at most 12 places")
        if self.cost_basis is not None and (
            type(self.cost_basis) is not Money
            or self.cost_basis.minor < 0
            or self.cost_basis.currency != self.value.currency
        ):
            raise ValueError("Cost basis must be nonnegative and in the value currency")
        if type(self.observed_on) is not date or type(self.fresh_through) is not date:
            raise TypeError("Valuation dates must be date-only")
        remaining = (date.max - self.observed_on).days
        limit = self.observed_on + timedelta(days=min(30, remaining))
        if not self.observed_on <= self.fresh_through <= limit:
            raise ValueError("Valuation freshness must be within 30 days")
        if type(self.observed_at) is not WallInstant:
            raise TypeError("Valuation requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Valuation requires a source")

    def as_core_observation(self) -> Observation[Money]:
        return Observation(
            id=self.id,
            subject=self.position,
            value=self.value,
            at=self.observed_at,
            source=self.source,
            context=Context(
                as_of=self.observed_at,
                source=self.source,
                authority="manual_asset_valuation",
                version="1.0.0",
                units=self.value.currency,
                scope="asset_position_value",
                metadata={"observed_on": self.observed_on.isoformat()},
            ),
        )


@dataclass(frozen=True, slots=True)
class AssetFlow:
    """Classifies a posted entry relative to one position boundary."""

    id: Id
    position: Ref
    entry: Ref
    kind: AssetFlowKind
    amount: Money
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != FLOW:
            raise ValueError("Asset flow requires its own finance identity")
        if type(self.position) is not Ref or self.position.id.kind != POSITION:
            raise ValueError("Asset flow requires a position reference")
        if type(self.entry) is not Ref or self.entry.id.kind != ENTRY:
            raise ValueError("Asset flow requires a posted entry reference")
        if type(self.kind) is not AssetFlowKind:
            raise TypeError("Asset flow kind must be explicit")
        if type(self.amount) is not Money or self.amount.minor <= 0:
            raise ValueError("Asset flow amount must be positive Money")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Asset flow requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Asset flow requires a source")


@dataclass(frozen=True, slots=True)
class AssetFlowCoverage:
    """Explicit review of external flows across a valuation period."""

    id: Id
    position: Ref
    from_on: date
    through_on: date
    complete: bool
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != FLOW_COVERAGE:
            raise ValueError("Asset coverage requires its own finance identity")
        if type(self.position) is not Ref or self.position.id.kind != POSITION:
            raise ValueError("Asset coverage requires a position reference")
        if type(self.from_on) is not date or type(self.through_on) is not date:
            raise TypeError("Asset coverage dates must be date-only")
        if self.from_on > self.through_on:
            raise ValueError("Asset coverage end precedes its start")
        if type(self.complete) is not bool:
            raise TypeError("Asset coverage completeness must be bool")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Asset coverage requires a recording instant")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Asset coverage requires a source")
