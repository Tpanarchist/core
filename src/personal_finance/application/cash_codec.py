"""Canonical version-one review/audit codec for immutable cash input records.

Only the named cash domain types are accepted. Core identities and references
retain kind, token, and namespace through the existing finance codecs.
"""

from __future__ import annotations

import hashlib

from personal_finance.application.evidence_codec import canonical, context_data
from personal_finance.domain.cash import (
    CashAllocation,
    CashBalanceObservation,
    CashCoverage,
    CashFloorChange,
    CashHold,
    CashRetirement,
    CashSchedule,
)
from personal_finance.domain.codec import encode_id, encode_ref

CashRecord = (
    CashSchedule
    | CashAllocation
    | CashHold
    | CashFloorChange
    | CashBalanceObservation
    | CashCoverage
    | CashRetirement
)


def encode_cash_record(record: CashRecord) -> str:
    """Return every reviewed field in a stable, explicitly versioned encoding."""
    common: dict[str, object] = {"version": 1, "id": encode_id(record.id)}
    if isinstance(record, CashSchedule):
        common.update(
            {
                "type": "schedule",
                "account": encode_ref(record.account),
                "amount": {"minor": record.amount.minor, "currency": record.amount.currency},
                "start_date": record.start_date.isoformat(),
                "cadence": record.cadence.value,
                "timezone": record.timezone,
                "label": record.label,
                "source": record.source,
                "end_date": record.end_date.isoformat() if record.end_date else None,
                "monthly_policy": record.monthly_policy.value,
                "active": record.active,
            }
        )
    elif isinstance(record, CashAllocation):
        occurrence = record.linked_occurrence
        common.update(
            {
                "type": "allocation",
                "amount": {"minor": record.amount.minor, "currency": record.amount.currency},
                "label": record.label,
                "active_from": record.active_from.isoformat(),
                "linked_occurrence": {
                    "schedule": encode_ref(occurrence.schedule),
                    "due_on": occurrence.due_on.isoformat(),
                }
                if occurrence
                else None,
                "active": record.active,
            }
        )
    elif isinstance(record, CashHold):
        common.update(
            {
                "type": "hold",
                "amount": {"minor": record.amount.minor, "currency": record.amount.currency},
                "label": record.label,
                "active_from": record.active_from.isoformat(),
                "release_date": record.release_date.isoformat() if record.release_date else None,
                "active": record.active,
            }
        )
    elif isinstance(record, CashFloorChange):
        common.update(
            {
                "type": "floor_change",
                "effective_date": record.effective_date.isoformat(),
                "amount": {"minor": record.amount.minor, "currency": record.amount.currency},
            }
        )
    elif isinstance(record, CashBalanceObservation):
        common.update(
            {
                "type": "observation",
                "account": encode_ref(record.account),
                "observed": {"minor": record.observed.minor, "currency": record.observed.currency},
                "observed_at": record.observed_at.value.isoformat(),
                "observed_on": record.observed_on.isoformat(),
                "fresh_through": record.fresh_through.isoformat(),
                "source": record.source,
                "context": context_data(record.context),
            }
        )
    elif isinstance(record, CashCoverage):
        common.update(
            {
                "type": "coverage",
                "currency": record.currency,
                "as_of": record.as_of.isoformat(),
                "through_date": record.through_date.isoformat(),
                "accounts_complete": record.accounts_complete,
                "schedules_complete": record.schedules_complete,
                "account_refs": [encode_ref(ref) for ref in record.account_refs],
                "recorded_at": record.recorded_at.value.isoformat(),
                "source": record.source,
            }
        )
    else:
        common.update(
            {
                "type": "retirement",
                "target": encode_ref(record.target),
                "effective_date": record.effective_date.isoformat(),
                "recorded_at": record.recorded_at.value.isoformat(),
                "principal": record.principal,
                "reason": record.reason,
            }
        )
    return canonical(common)


def cash_record_hash(record: CashRecord) -> str:
    """SHA-256 of the exact versioned content shown to local review and audit."""
    return hashlib.sha256(encode_cash_record(record).encode("utf-8")).hexdigest()
