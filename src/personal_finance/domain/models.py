"""One immutable snapshot of the local debt, asset, and node input records."""

from __future__ import annotations

from dataclasses import dataclass

from personal_finance.domain.assets import (
    AssetFlow,
    AssetFlowCoverage,
    AssetPosition,
    AssetValuation,
)
from personal_finance.domain.debts import DebtPaymentSplit, DebtTerms
from personal_finance.domain.nodes import IncomeNode, NodeFunding


@dataclass(frozen=True, slots=True)
class ModelRecords:
    debt_terms: tuple[DebtTerms, ...] = ()
    debt_splits: tuple[DebtPaymentSplit, ...] = ()
    positions: tuple[AssetPosition, ...] = ()
    valuations: tuple[AssetValuation, ...] = ()
    asset_flows: tuple[AssetFlow, ...] = ()
    asset_coverage: tuple[AssetFlowCoverage, ...] = ()
    nodes: tuple[IncomeNode, ...] = ()
    node_funding: tuple[NodeFunding, ...] = ()
