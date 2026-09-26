"""Versioned canonical codec for immutable debt, asset, and node inputs."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

from core.time import WallInstant
from personal_finance.application.evidence_codec import canonical
from personal_finance.domain.assets import (
    AssetCategory,
    AssetFlow,
    AssetFlowCoverage,
    AssetFlowKind,
    AssetPosition,
    AssetValuation,
)
from personal_finance.domain.codec import decode_id, decode_ref, encode_id, encode_ref
from personal_finance.domain.debts import DebtPaymentSplit, DebtTerms
from personal_finance.domain.money import Money
from personal_finance.domain.nodes import FundingKind, IncomeNode, NodeFunding

type ModelRecord = (
    DebtTerms
    | DebtPaymentSplit
    | AssetPosition
    | AssetValuation
    | AssetFlow
    | AssetFlowCoverage
    | IncomeNode
    | NodeFunding
)


def _money(value: Money | None) -> dict[str, object] | None:
    return None if value is None else {"minor": value.minor, "currency": value.currency}


def _read_money(value: object) -> Money:
    if type(value) is not dict:
        raise ValueError("Model money must be an object")
    data = cast(dict[str, object], value)
    return Money(cast(int, data["minor"]), cast(str, data["currency"]))


def _optional_money(value: object) -> Money | None:
    return None if value is None else _read_money(value)


def encode_model_record(record: ModelRecord) -> str:
    data: dict[str, object] = {"version": 1, "id": encode_id(record.id)}
    if isinstance(record, DebtTerms):
        data.update(
            type="debt_terms",
            account=encode_ref(record.account),
            apr_basis_points=record.apr_basis_points,
            minimum_payment=_money(record.minimum_payment),
            due_day=record.due_day,
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, DebtPaymentSplit):
        data.update(
            type="debt_payment_split",
            account=encode_ref(record.account),
            entry=encode_ref(record.entry),
            principal=_money(record.principal),
            interest=_money(record.interest),
            fees=_money(record.fees),
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, AssetPosition):
        data.update(
            type="asset_position",
            account=encode_ref(record.account),
            name=record.name,
            category=record.category.value,
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, AssetValuation):
        data.update(
            type="asset_valuation",
            position=encode_ref(record.position),
            value=_money(record.value),
            quantity=format(record.quantity, "f") if record.quantity is not None else None,
            cost_basis=_money(record.cost_basis),
            observed_on=record.observed_on.isoformat(),
            fresh_through=record.fresh_through.isoformat(),
            observed_at=record.observed_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, AssetFlow):
        data.update(
            type="asset_flow",
            position=encode_ref(record.position),
            entry=encode_ref(record.entry),
            kind=record.kind.value,
            amount=_money(record.amount),
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, AssetFlowCoverage):
        data.update(
            type="asset_flow_coverage",
            position=encode_ref(record.position),
            from_on=record.from_on.isoformat(),
            through_on=record.through_on.isoformat(),
            complete=record.complete,
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, IncomeNode):
        data.update(
            type="income_node",
            name=record.name,
            revenue_account=encode_ref(record.revenue_account),
            expense_account=encode_ref(record.expense_account),
            cash_account=encode_ref(record.cash_account) if record.cash_account else None,
            monthly_revenue_assumption=_money(record.monthly_revenue_assumption),
            monthly_expense_assumption=_money(record.monthly_expense_assumption),
            milestone_target=_money(record.milestone_target),
            milestone_on=record.milestone_on.isoformat() if record.milestone_on else None,
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    else:
        data.update(
            type="node_funding",
            node=encode_ref(record.node),
            entry=encode_ref(record.entry),
            kind=record.kind.value,
            amount=_money(record.amount),
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    return canonical(data)


def model_record_hash(record: ModelRecord) -> str:
    return hashlib.sha256(encode_model_record(record).encode("utf-8")).hexdigest()


def decode_model_record(raw: str) -> ModelRecord:
    try:
        data = cast(dict[str, object], json.loads(raw))
        if type(data) is not dict or data.get("version") != 1:
            raise ValueError("Unsupported model codec version")
        identifier = decode_id(cast(str, data["id"]))
        kind = data["type"]
        at = (
            WallInstant(datetime.fromisoformat(cast(str, data["recorded_at"])))
            if "recorded_at" in data
            else None
        )
        if kind == "debt_terms" and at is not None:
            record: ModelRecord = DebtTerms(
                identifier,
                decode_ref(cast(str, data["account"])),
                cast(int, data["apr_basis_points"]),
                _read_money(data["minimum_payment"]),
                cast(int, data["due_day"]),
                at,
                cast(str, data["source"]),
            )
        elif kind == "debt_payment_split" and at is not None:
            record = DebtPaymentSplit(
                identifier,
                decode_ref(cast(str, data["account"])),
                decode_ref(cast(str, data["entry"])),
                _read_money(data["principal"]),
                _read_money(data["interest"]),
                _read_money(data["fees"]),
                at,
                cast(str, data["source"]),
            )
        elif kind == "asset_position" and at is not None:
            record = AssetPosition(
                identifier,
                decode_ref(cast(str, data["account"])),
                cast(str, data["name"]),
                AssetCategory(cast(str, data["category"])),
                at,
                cast(str, data["source"]),
            )
        elif kind == "asset_valuation":
            quantity = data["quantity"]
            record = AssetValuation(
                identifier,
                decode_ref(cast(str, data["position"])),
                _read_money(data["value"]),
                Decimal(cast(str, quantity)) if quantity is not None else None,
                _optional_money(data["cost_basis"]),
                date.fromisoformat(cast(str, data["observed_on"])),
                date.fromisoformat(cast(str, data["fresh_through"])),
                WallInstant(datetime.fromisoformat(cast(str, data["observed_at"]))),
                cast(str, data["source"]),
            )
        elif kind == "asset_flow" and at is not None:
            record = AssetFlow(
                identifier,
                decode_ref(cast(str, data["position"])),
                decode_ref(cast(str, data["entry"])),
                AssetFlowKind(cast(str, data["kind"])),
                _read_money(data["amount"]),
                at,
                cast(str, data["source"]),
            )
        elif kind == "asset_flow_coverage" and at is not None:
            record = AssetFlowCoverage(
                identifier,
                decode_ref(cast(str, data["position"])),
                date.fromisoformat(cast(str, data["from_on"])),
                date.fromisoformat(cast(str, data["through_on"])),
                cast(bool, data["complete"]),
                at,
                cast(str, data["source"]),
            )
        elif kind == "income_node" and at is not None:
            milestone_on = data["milestone_on"]
            cash_account = data["cash_account"]
            record = IncomeNode(
                identifier,
                cast(str, data["name"]),
                decode_ref(cast(str, data["revenue_account"])),
                decode_ref(cast(str, data["expense_account"])),
                decode_ref(cast(str, cash_account)) if cash_account is not None else None,
                _optional_money(data["monthly_revenue_assumption"]),
                _optional_money(data["monthly_expense_assumption"]),
                _optional_money(data["milestone_target"]),
                date.fromisoformat(cast(str, milestone_on)) if milestone_on is not None else None,
                at,
                cast(str, data["source"]),
            )
        elif kind == "node_funding" and at is not None:
            record = NodeFunding(
                identifier,
                decode_ref(cast(str, data["node"])),
                decode_ref(cast(str, data["entry"])),
                FundingKind(cast(str, data["kind"])),
                _read_money(data["amount"]),
                at,
                cast(str, data["source"]),
            )
        else:
            raise ValueError("Unknown model record type")
        if encode_model_record(record) != raw:
            raise ValueError("Model record is not canonical")
        return record
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation) as exc:
        raise ValueError("Invalid model record encoding") from exc
