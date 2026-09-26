"""Canonical, versioned encoding for reconciliation evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import cast

from core.time import WallInstant
from personal_finance.application.evidence_codec import canonical
from personal_finance.domain.codec import decode_id, decode_ref, encode_id, encode_ref
from personal_finance.domain.money import Money
from personal_finance.domain.reconciliation import (
    ReconcileClose,
    ReconcileException,
    ReconcileIssue,
    ReconcileResolution,
    Statement,
    StatementLine,
    StatementMatch,
)

type ReconcileRecord = (
    Statement
    | StatementLine
    | StatementMatch
    | ReconcileException
    | ReconcileIssue
    | ReconcileResolution
    | ReconcileClose
)


def _money(value: Money) -> dict[str, object]:
    return {"minor": value.minor, "currency": value.currency}


def _read_money(value: object) -> Money:
    if type(value) is not dict:
        raise ValueError("Reconciliation money must be an object")
    data = cast(dict[str, object], value)
    return Money(cast(int, data["minor"]), cast(str, data["currency"]))


def encode_reconcile_record(record: ReconcileRecord) -> str:
    data: dict[str, object] = {"version": 1, "id": encode_id(record.id)}
    if isinstance(record, Statement):
        data.update(
            type="statement",
            account=encode_ref(record.account),
            start_on=record.start_on.isoformat(),
            through_on=record.through_on.isoformat(),
            opening=_money(record.opening),
            closing=_money(record.closing),
            lines_complete=record.lines_complete,
            recorded_at=record.recorded_at.value.isoformat(),
            source=record.source,
        )
    elif isinstance(record, StatementLine):
        data.update(
            type="line",
            statement=encode_ref(record.statement),
            on=record.on.isoformat(),
            movement=_money(record.movement),
            description=record.description,
            external_id=record.external_id,
        )
    elif isinstance(record, StatementMatch):
        data.update(
            type="match",
            statement=encode_ref(record.statement),
            line=encode_ref(record.line),
            entry=encode_ref(record.entry),
            recorded_at=record.recorded_at.value.isoformat(),
            principal=record.principal,
        )
    elif isinstance(record, ReconcileException):
        data.update(
            type="exception",
            statement=encode_ref(record.statement),
            target=encode_ref(record.target),
            rationale=record.rationale,
            recorded_at=record.recorded_at.value.isoformat(),
            principal=record.principal,
        )
    elif isinstance(record, ReconcileIssue):
        data.update(
            type="issue",
            statement=encode_ref(record.statement),
            ledger_opening=_money(record.ledger_opening),
            ledger_closing=_money(record.ledger_closing),
            ledger_revision=record.ledger_revision,
            detected_at=record.detected_at.value.isoformat(),
        )
    elif isinstance(record, ReconcileResolution):
        data.update(
            type="resolution",
            issue=encode_ref(record.issue),
            rationale=record.rationale,
            correction_entry=encode_ref(record.correction_entry)
            if record.correction_entry is not None
            else None,
            recorded_at=record.recorded_at.value.isoformat(),
            principal=record.principal,
        )
    else:
        data.update(
            type="close",
            start_on=record.start_on.isoformat(),
            through_on=record.through_on.isoformat(),
            statements=[encode_ref(item) for item in record.statements],
            reason=record.reason,
            recorded_at=record.recorded_at.value.isoformat(),
            principal=record.principal,
            ledger_revision=record.ledger_revision,
        )
    return canonical(data)


def reconcile_record_hash(record: ReconcileRecord) -> str:
    return hashlib.sha256(encode_reconcile_record(record).encode("utf-8")).hexdigest()


def decode_reconcile_record(raw: str) -> ReconcileRecord:
    try:
        data = cast(dict[str, object], json.loads(raw))
        if type(data) is not dict or data.get("version") != 1:
            raise ValueError("Unsupported reconciliation codec version")
        identifier = decode_id(cast(str, data["id"]))
        kind = data["type"]
        if kind == "statement":
            record: ReconcileRecord = Statement(
                identifier,
                decode_ref(cast(str, data["account"])),
                date.fromisoformat(cast(str, data["start_on"])),
                date.fromisoformat(cast(str, data["through_on"])),
                _read_money(data["opening"]),
                _read_money(data["closing"]),
                cast(bool, data["lines_complete"]),
                WallInstant(datetime.fromisoformat(cast(str, data["recorded_at"]))),
                cast(str, data["source"]),
            )
        elif kind == "line":
            record = StatementLine(
                identifier,
                decode_ref(cast(str, data["statement"])),
                date.fromisoformat(cast(str, data["on"])),
                _read_money(data["movement"]),
                cast(str, data["description"]),
                cast(str | None, data["external_id"]),
            )
        elif kind == "match":
            record = StatementMatch(
                identifier,
                decode_ref(cast(str, data["statement"])),
                decode_ref(cast(str, data["line"])),
                decode_ref(cast(str, data["entry"])),
                WallInstant(datetime.fromisoformat(cast(str, data["recorded_at"]))),
                cast(str, data["principal"]),
            )
        elif kind == "exception":
            record = ReconcileException(
                identifier,
                decode_ref(cast(str, data["statement"])),
                decode_ref(cast(str, data["target"])),
                cast(str, data["rationale"]),
                WallInstant(datetime.fromisoformat(cast(str, data["recorded_at"]))),
                cast(str, data["principal"]),
            )
        elif kind == "issue":
            record = ReconcileIssue(
                identifier,
                decode_ref(cast(str, data["statement"])),
                _read_money(data["ledger_opening"]),
                _read_money(data["ledger_closing"]),
                cast(int, data["ledger_revision"]),
                WallInstant(datetime.fromisoformat(cast(str, data["detected_at"]))),
            )
        elif kind == "resolution":
            correction = data["correction_entry"]
            record = ReconcileResolution(
                identifier,
                decode_ref(cast(str, data["issue"])),
                cast(str, data["rationale"]),
                decode_ref(cast(str, correction)) if correction is not None else None,
                WallInstant(datetime.fromisoformat(cast(str, data["recorded_at"]))),
                cast(str, data["principal"]),
            )
        elif kind == "close":
            refs = cast(list[str], data["statements"])
            record = ReconcileClose(
                identifier,
                date.fromisoformat(cast(str, data["start_on"])),
                date.fromisoformat(cast(str, data["through_on"])),
                tuple(decode_ref(item) for item in refs),
                cast(str, data["reason"]),
                WallInstant(datetime.fromisoformat(cast(str, data["recorded_at"]))),
                cast(str, data["principal"]),
                cast(int, data["ledger_revision"]),
            )
        else:
            raise ValueError("Unknown reconciliation record type")
        if encode_reconcile_record(record) != raw:
            raise ValueError("Reconciliation record is not canonical")
        return record
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid reconciliation record encoding") from exc
