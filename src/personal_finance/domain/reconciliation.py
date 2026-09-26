"""Immutable statement evidence and explicit reconciliation decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.context import Context
from core.epistemic import Contradiction, Resolution
from core.identity import Id, Ref
from core.observation import Observation
from core.relation import Relation
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.money import Money

STATEMENT = Kind("finance.reconcile_statement")
LINE = Kind("finance.reconcile_line")
MATCH = Kind("finance.reconcile_match")
EXCEPTION = Kind("finance.reconcile_exception")
ISSUE = Kind("finance.reconcile_issue")
RESOLUTION = Kind("finance.reconcile_resolution")
CLOSE = Kind("finance.reconcile_close")
ACCOUNT = Kind("finance.account")
ENTRY = Kind("finance.journal_entry")


def _id(value: Id, kind: Kind) -> None:
    if type(value) is not Id or value.kind != kind:
        raise ValueError(f"Expected {kind.value} identity")


def _ref(value: Ref, kind: Kind) -> None:
    if type(value) is not Ref or value.id.kind != kind:
        raise ValueError(f"Expected {kind.value} reference")


def _text(value: str, label: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must not be blank")


@dataclass(frozen=True, slots=True)
class Statement:
    id: Id
    account: Ref
    start_on: date
    through_on: date
    opening: Money
    closing: Money
    lines_complete: bool
    recorded_at: WallInstant
    source: str

    def __post_init__(self) -> None:
        _id(self.id, STATEMENT)
        _ref(self.account, ACCOUNT)
        if type(self.start_on) is not date or type(self.through_on) is not date:
            raise TypeError("Statement period requires dates")
        if self.start_on > self.through_on:
            raise ValueError("Statement period ends before it begins")
        if type(self.opening) is not Money or type(self.closing) is not Money:
            raise TypeError("Statement balances require exact Money")
        if self.opening.currency != self.closing.currency:
            raise ValueError("Statement balances require one currency")
        if type(self.lines_complete) is not bool:
            raise TypeError("Statement completeness must be explicit")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Statement needs a recording instant")
        _text(self.source, "Statement source")

    def as_core_observation(self, context: Context) -> Observation[Money]:
        return Observation(
            id=self.id,
            subject=self.account,
            value=self.closing,
            at=self.recorded_at,
            source=self.source,
            context=context,
        )


@dataclass(frozen=True, slots=True)
class StatementLine:
    id: Id
    statement: Ref
    on: date
    movement: Money
    description: str
    external_id: str | None

    def __post_init__(self) -> None:
        _id(self.id, LINE)
        _ref(self.statement, STATEMENT)
        if type(self.on) is not date:
            raise TypeError("Statement line requires a date")
        if type(self.movement) is not Money or self.movement.minor == 0:
            raise ValueError("Statement line requires a nonzero exact movement")
        _text(self.description, "Statement line description")
        if self.external_id is not None:
            _text(self.external_id, "External line identity")


@dataclass(frozen=True, slots=True)
class StatementMatch:
    id: Id
    statement: Ref
    line: Ref
    entry: Ref
    recorded_at: WallInstant
    principal: str

    def __post_init__(self) -> None:
        _id(self.id, MATCH)
        _ref(self.statement, STATEMENT)
        _ref(self.line, LINE)
        _ref(self.entry, ENTRY)
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Match requires a recording instant")
        _text(self.principal, "Matching principal")

    def as_core_relation(self, context: Context) -> Relation:
        return Relation(
            source=self.line,
            kind=Kind("finance.statement_line_matches_entry"),
            target=self.entry,
            at=self.recorded_at,
            context=context,
        )


@dataclass(frozen=True, slots=True)
class ReconcileException:
    id: Id
    statement: Ref
    target: Ref
    rationale: str
    recorded_at: WallInstant
    principal: str

    def __post_init__(self) -> None:
        _id(self.id, EXCEPTION)
        _ref(self.statement, STATEMENT)
        if type(self.target) is not Ref or self.target.id.kind not in (LINE, ENTRY):
            raise ValueError("Exception targets a statement line or posted entry")
        _text(self.rationale, "Exception rationale")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Exception requires a recording instant")
        _text(self.principal, "Exception principal")


@dataclass(frozen=True, slots=True)
class ReconcileIssue:
    id: Id
    statement: Ref
    ledger_opening: Money
    ledger_closing: Money
    ledger_revision: int
    detected_at: WallInstant

    def __post_init__(self) -> None:
        _id(self.id, ISSUE)
        _ref(self.statement, STATEMENT)
        if type(self.ledger_opening) is not Money or type(self.ledger_closing) is not Money:
            raise TypeError("Issue requires exact ledger boundary balances")
        if self.ledger_opening.currency != self.ledger_closing.currency:
            raise ValueError("Ledger boundary balances require one currency")
        if type(self.ledger_revision) is not int or self.ledger_revision < 0:
            raise ValueError("Issue requires a ledger revision")
        if type(self.detected_at) is not WallInstant:
            raise TypeError("Issue requires a detection instant")

    def as_core_contradiction(self, account: Ref, context: Context) -> Contradiction:
        return Contradiction(
            id=Id(Kind("core.contradiction"), self.id.value),
            subject=account,
            statements=(self.statement, Ref(self.id)),
            detected_at=self.detected_at,
            context=context,
        )


@dataclass(frozen=True, slots=True)
class ReconcileResolution:
    id: Id
    issue: Ref
    rationale: str
    correction_entry: Ref | None
    recorded_at: WallInstant
    principal: str

    def __post_init__(self) -> None:
        _id(self.id, RESOLUTION)
        _ref(self.issue, ISSUE)
        _text(self.rationale, "Resolution rationale")
        if self.correction_entry is not None:
            _ref(self.correction_entry, ENTRY)
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Resolution requires a recording instant")
        _text(self.principal, "Resolution principal")

    def as_core_resolution(self) -> Resolution:
        return Resolution(
            contradiction=Ref(Id(Kind("core.contradiction"), self.issue.id.value)),
            rationale=self.rationale,
            resolved_by=Id(Kind("finance.local_principal"), self.principal),
            at=self.recorded_at,
        )


@dataclass(frozen=True, slots=True)
class ReconcileClose:
    id: Id
    start_on: date
    through_on: date
    statements: tuple[Ref, ...]
    reason: str
    recorded_at: WallInstant
    principal: str
    ledger_revision: int

    def __post_init__(self) -> None:
        _id(self.id, CLOSE)
        if type(self.start_on) is not date or type(self.through_on) is not date:
            raise TypeError("Close requires period dates")
        if self.start_on > self.through_on:
            raise ValueError("Close period ends before it begins")
        if not self.statements or len(set(self.statements)) != len(self.statements):
            raise ValueError("Close requires distinct reviewed statements")
        for statement in self.statements:
            _ref(statement, STATEMENT)
        _text(self.reason, "Close reason")
        _text(self.principal, "Close principal")
        if type(self.recorded_at) is not WallInstant:
            raise TypeError("Close requires a recording instant")
        if type(self.ledger_revision) is not int or self.ledger_revision < 0:
            raise ValueError("Close requires a ledger revision")


@dataclass(frozen=True, slots=True)
class ReconcileRecords:
    statements: tuple[Statement, ...] = ()
    lines: tuple[StatementLine, ...] = ()
    matches: tuple[StatementMatch, ...] = ()
    exceptions: tuple[ReconcileException, ...] = ()
    issues: tuple[ReconcileIssue, ...] = ()
    resolutions: tuple[ReconcileResolution, ...] = ()
    closes: tuple[ReconcileClose, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "statements",
            "lines",
            "matches",
            "exceptions",
            "issues",
            "resolutions",
            "closes",
        ):
            value = getattr(self, name)
            if type(value) is not tuple:
                raise TypeError("Reconciliation collections must be immutable tuples")
