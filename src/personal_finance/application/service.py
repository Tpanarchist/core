"""Trusted local finance use cases and a retained, Core-derived projection.

``post_draft`` is the trusted local review surface: the TUI calls it only after
displaying the immutable draft and explicit human confirmation. It is not an
agent API and must never be exposed by a future MCP transport. Context labels
and client-supplied principal strings do not authenticate remote clients.
"""

from __future__ import annotations

import hashlib
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from threading import RLock

from core.context import Context
from core.effect import Effect, EffectSink, EffectSpec
from core.error import Error
from core.event import Event
from core.identity import Id, IdSource, Namespace, Ref
from core.provenance import Provenance
from core.result import Err, Ok, Result
from core.state import State
from core.time import Clock, MonotonicClock, WallInstant
from core.trace import Trace
from core.transform import Transform
from core.value import UNKNOWN, Kind, Maybe
from personal_finance.application.cash_codec import cash_record_hash
from personal_finance.application.cash_projection import CashProjection, occurs_on, project_cash
from personal_finance.application.evidence_codec import canonical
from personal_finance.application.model_codec import ModelRecord, model_record_hash
from personal_finance.application.model_projection import (
    AssetView,
    DebtPortfolio,
    DebtScenario,
    NodeView,
    compare_debt_payoff,
    project_assets,
    project_debts,
    project_nodes,
)
from personal_finance.application.ports import (
    FinanceFailure,
    FinanceRepository,
    FinanceUnitOfWork,
    LedgerInputs,
    LocalApproval,
    StaleProjection,
)
from personal_finance.application.reconcile_projection import (
    ReconcilePortfolio,
    project_reconciliation,
)
from personal_finance.application.reconcile_projection import (
    account_entry_movement as reconcile_entry_movement,
)
from personal_finance.application.reconcile_projection import (
    ledger_closing_balance as reconcile_closing_balance,
)
from personal_finance.application.reconcile_projection import (
    ledger_opening_balance as reconcile_opening_balance,
)
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import (
    AssetCategory,
    AssetFlow,
    AssetFlowCoverage,
    AssetFlowKind,
    AssetPosition,
    AssetValuation,
)
from personal_finance.domain.cash import (
    Cadence,
    CashAllocation,
    CashBalanceObservation,
    CashCoverage,
    CashFloorChange,
    CashHold,
    CashRecords,
    CashRetirement,
    CashSchedule,
    ScheduleOccurrence,
)
from personal_finance.domain.codec import content_hash, encode_id, encode_ref
from personal_finance.domain.debts import DebtPaymentSplit, DebtTerms
from personal_finance.domain.ledger import (
    Draft,
    EntryContent,
    JournalEntry,
    Posting,
    validate_accounts,
)
from personal_finance.domain.models import ModelRecords
from personal_finance.domain.money import Money
from personal_finance.domain.nodes import FundingKind, IncomeNode, NodeFunding
from personal_finance.domain.reconciliation import (
    CLOSE,
    EXCEPTION,
    ISSUE,
    LINE,
    MATCH,
    RESOLUTION,
    STATEMENT,
    ReconcileClose,
    ReconcileException,
    ReconcileIssue,
    ReconcileRecords,
    ReconcileResolution,
    Statement,
    StatementLine,
    StatementMatch,
)

POLICY_VERSION = "local-review-v1"
PROJECTION_VERSION = "1.0.0"
CASH_PROJECTION_VERSION = "1.0.0"
MODEL_PROJECTION_VERSION = "1.0.0"
RECONCILE_PROJECTION_VERSION = "1.0.0"
COVERAGE = (
    "Recorded accounts only; account inventory and opening balances are unverified. "
    "Actual net worth remains unknown. Cash projections require reviewed planning inputs; "
    "asset values are manual and may be stale."
)
POST_EFFECT = EffectSpec(
    kind=Kind("finance.ledger.entry_posted"),
    target_shape="Ref[finance.journal_entry]",
    description="Append the reviewed balanced entry to the local ledger",
)


@dataclass(frozen=True, slots=True)
class AccountBalance:
    account: Account
    money: Money
    state: State[Money]


@dataclass(frozen=True, slots=True)
class CashPlanView:
    projection: CashProjection
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ModelView:
    debts: DebtPortfolio
    assets: tuple[AssetView, ...]
    nodes: tuple[NodeView, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class DebtComparisonView:
    scenarios: tuple[DebtScenario, ...]
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ReconcileView:
    portfolio: ReconcilePortfolio
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    balances: tuple[AccountBalance, ...]
    entries: tuple[JournalEntry, ...]
    accounts: tuple[Account, ...]
    drafts: tuple[Draft, ...]
    provenance: Provenance
    revision: int
    actual_net_worth: Maybe[Money] = UNKNOWN
    coverage: str = COVERAGE
    cash_records: CashRecords = CashRecords()
    cash_plans: tuple[CashPlanView, ...] = ()
    models: ModelRecords = ModelRecords()
    model_view: ModelView | None = None
    reconciliation: ReconcileRecords = ReconcileRecords()
    reconcile_view: ReconcileView | None = None


def _recorded_balances(inputs: LedgerInputs) -> tuple[tuple[Account, Money], ...]:
    """Exact Python integers, account display signs, and no currency conversion."""
    totals = {account.id: 0 for account in inputs.accounts}
    for entry in inputs.entries:
        for posting in entry.content.postings:
            totals[posting.account.id] += posting.money.minor
    return tuple(
        (account, Money(totals[account.id] * account.display_sign, account.currency))
        for account in inputs.accounts
    )


BALANCE_TRANSFORM: Transform[LedgerInputs, tuple[tuple[Account, Money], ...]] = Transform(
    id=Id(Kind("core.transform"), "personal_finance.recorded_balances.v1"),
    name="finance.recorded_account_balances",
    version=PROJECTION_VERSION,
    fn=_recorded_balances,
)


@dataclass(frozen=True, slots=True)
class CashProjectionInput:
    start_on: date
    horizon_days: int
    inputs: LedgerInputs
    context: Context


def _project_cash(value: CashProjectionInput) -> CashProjection:
    return project_cash(
        value.start_on,
        value.horizon_days,
        value.inputs.accounts,
        value.inputs.entries,
        value.inputs.cash,
        value.context,
    )


CASH_TRANSFORM: Transform[CashProjectionInput, CashProjection] = Transform(
    id=Id(Kind("core.transform"), "personal_finance.cash_projection.v1"),
    name="finance.cash_projection",
    version=CASH_PROJECTION_VERSION,
    fn=_project_cash,
)


@dataclass(frozen=True, slots=True)
class ModelProjectionInput:
    inputs: LedgerInputs
    today: date


@dataclass(frozen=True, slots=True)
class ModelProjectionResult:
    debts: DebtPortfolio
    assets: tuple[AssetView, ...]
    nodes: tuple[NodeView, ...]


def _project_models(value: ModelProjectionInput) -> ModelProjectionResult:
    inputs = value.inputs
    return ModelProjectionResult(
        project_debts(inputs.accounts, inputs.entries, inputs.models, value.today),
        project_assets(inputs.models, inputs.entries, value.today),
        project_nodes(inputs.accounts, inputs.entries, inputs.models, inputs.cash, value.today),
    )


MODEL_TRANSFORM: Transform[ModelProjectionInput, ModelProjectionResult] = Transform(
    id=Id(Kind("core.transform"), "personal_finance.model_projection.v1"),
    name="finance.debt_asset_node_projection",
    version=MODEL_PROJECTION_VERSION,
    fn=_project_models,
)


@dataclass(frozen=True, slots=True)
class DebtComparisonInput:
    portfolio: DebtPortfolio
    currency: str
    monthly_extra: Money
    today: date
    custom_order: tuple[Id, ...]


def _compare_debts(value: DebtComparisonInput) -> tuple[DebtScenario, ...]:
    return compare_debt_payoff(
        value.portfolio,
        value.currency,
        value.monthly_extra,
        value.today,
        value.custom_order,
    )


DEBT_COMPARE_TRANSFORM: Transform[DebtComparisonInput, tuple[DebtScenario, ...]] = Transform(
    id=Id(Kind("core.transform"), "personal_finance.debt_comparison.v1"),
    name="finance.debt_payoff_comparison",
    version=MODEL_PROJECTION_VERSION,
    fn=_compare_debts,
)


@dataclass(frozen=True, slots=True)
class ReconcileProjectionInput:
    inputs: LedgerInputs
    today: date


RECONCILE_TRANSFORM: Transform[ReconcileProjectionInput, ReconcilePortfolio] = Transform(
    id=Id(Kind("core.transform"), "personal_finance.reconciliation.v1"),
    name="finance.statement_reconciliation",
    version=RECONCILE_PROJECTION_VERSION,
    fn=lambda value: project_reconciliation(
        value.inputs.accounts,
        value.inputs.entries,
        value.inputs.reconciliation,
        value.today,
    ),
)


def _reconcile_payload(value: ReconcilePortfolio, revision: int) -> str:
    return canonical(
        {
            "version": 1,
            "calculation": RECONCILE_PROJECTION_VERSION,
            "revision": revision,
            "statements": [
                {
                    "statement": encode_ref(Ref(item.statement.id)),
                    "opening_difference_minor": item.opening_difference.minor,
                    "closing_difference_minor": item.closing_difference.minor,
                    "line_difference_minor": item.line_difference.minor,
                    "unresolved_lines": len(item.unresolved_lines),
                    "unresolved_entries": len(item.unresolved_entries),
                    "unresolved_issues": item.unresolved_issues,
                    "closed": item.closed,
                    "missing_inputs": list(item.missing_inputs),
                }
                for item in value.statements
            ],
            "closes": [
                {"start_on": start.isoformat(), "through_on": end.isoformat()}
                for start, end in value.closes
            ],
        }
    )


def _cash_payload(projection: CashProjection, revision: int) -> str:
    """Versioned, rebuildable cache; authoritative inputs remain in ledger/planning tables."""
    return canonical(
        {
            "version": 1,
            "calculation": CASH_PROJECTION_VERSION,
            "revision": revision,
            "start_on": projection.start_on.isoformat(),
            "horizon_days": projection.horizon_days,
            "through_on": projection.through_on.isoformat(),
            "missing_inputs": list(projection.missing_inputs),
            "currencies": [
                {
                    "currency": item.currency,
                    "starting_minor": item.starting_balance.minor
                    if item.starting_balance is not None
                    else None,
                    "minimum_minor": item.minimum_balance.minor
                    if item.minimum_balance is not None
                    else None,
                    "minimum_on": item.minimum_on.isoformat()
                    if item.minimum_on is not None
                    else None,
                    "current_unallocated_minor": item.current_unallocated.minor
                    if item.current_unallocated is not None
                    else None,
                    "safe_to_allocate_minor": item.safe_to_allocate.minor
                    if item.safe_to_allocate is not None
                    else None,
                    "missing_inputs": list(item.missing_inputs),
                    "points": [
                        {
                            "on": point.on.isoformat(),
                            "kind": point.kind,
                            "label": point.label,
                            "delta_minor": point.delta.minor,
                            "balance_minor": point.balance.minor,
                            "floor_minor": point.floor.minor if point.floor else None,
                            "protected_minor": point.protected.minor,
                            "holds_minor": point.holds.minor,
                            "available_minor": point.available.minor if point.available else None,
                        }
                        for point in item.points
                    ],
                }
                for item in projection.currencies
            ],
        }
    )


def _model_payload(value: ModelProjectionResult, revision: int) -> str:
    return canonical(
        {
            "version": 1,
            "calculation": MODEL_PROJECTION_VERSION,
            "revision": revision,
            "debts": [
                {
                    "account": encode_ref(Ref(item.account.id)),
                    "balance_minor": item.balance.minor,
                    "currency": item.balance.currency,
                    "terms_known": item.terms is not None,
                }
                for item in value.debts.debts
            ],
            "assets": [
                {
                    "position": encode_ref(Ref(item.position.id)),
                    "value_minor": item.value.minor if item.value is not None else None,
                    "gain_minor": item.period_gain.minor if item.period_gain is not None else None,
                    "stale": item.stale,
                    "missing_inputs": list(item.missing_inputs),
                }
                for item in value.assets
            ],
            "nodes": [
                {
                    "node": encode_ref(Ref(item.node.id)),
                    "revenue_minor": item.revenue.minor,
                    "expense_minor": item.operating_expenses.minor,
                    "net_minor": item.net_cash_flow.minor,
                    "currency": item.revenue.currency,
                    "runway_months": item.runway_months,
                }
                for item in value.nodes
            ],
        }
    )


type CashInputRecord = (
    CashSchedule
    | CashAllocation
    | CashHold
    | CashFloorChange
    | CashBalanceObservation
    | CashCoverage
    | CashRetirement
)


class FinanceService:
    def __init__(
        self,
        store: FinanceRepository,
        clock: Clock,
        monotonic_clock: MonotonicClock,
        ids: IdSource,
        profile: str = "default",
        principal: str = "local-human",
    ) -> None:
        if not profile.strip() or not principal.strip():
            raise ValueError("Profile and principal must not be empty")
        self.store = store
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.ids = ids
        self.profile = profile
        self.principal = principal
        # Core Trace and injected deterministic clocks/ID sources are single-writer.
        self._lock = RLock()

    def _context(self, at: WallInstant) -> Context:
        return Context(
            as_of=at,
            namespace=Namespace(("personal_finance", self.profile)),
            source="posted_local_ledger",
            authority="local_review_policy",
            version=PROJECTION_VERSION,
            units="integer minor units per account currency",
            scope="recorded_accounts_only",
            metadata={"display_sign": "asset/expense debit; liability/equity/income credit"},
        )

    def _cash_context(self, at: WallInstant, currency: str | None = None) -> Context:
        return Context(
            as_of=at,
            namespace=Namespace(("personal_finance", self.profile)),
            source="posted_ledger_and_manual_cash_inputs",
            authority="local_human_cash_planning",
            version=CASH_PROJECTION_VERSION,
            units=currency or "separate integer minor units by currency",
            scope="dated_cash_plan_with_explicit_coverage",
            metadata={"calendar_start": "host_local_date", "classification": "projection"},
        )

    def _model_context(self, at: WallInstant) -> Context:
        return Context(
            as_of=at,
            namespace=Namespace(("personal_finance", self.profile)),
            source="posted_ledger_and_manual_model_inputs",
            authority="local_human_model_review",
            version=MODEL_PROJECTION_VERSION,
            units="separate integer minor units by currency",
            scope="debt_asset_node_observations_and_conditional_plans",
            metadata={"classification": "recorded_or_conditional_as_labeled"},
        )

    def _error(self, operation: str, error: Exception) -> Error:
        kind = (
            "finance.duplicate_import"
            if str(error).startswith("duplicate_import:")
            else "finance.operation_rejected"
        )
        return Error(
            id=self.ids.new(Kind("core.error")),
            kind=Kind(kind),
            message=str(error) or "Finance operation failed",
            at=self.clock.now(),
            operation=operation,
            recoverable=True,
            metadata={"recovery": "Review the input and retry; no partial change was committed"},
        )

    def _record_change(
        self,
        unit: FinanceUnitOfWork,
        *,
        kind: str,
        target: Id,
        at: WallInstant,
        description: str,
        input_hash: str = "",
        references: tuple[Ref, ...] = (),
        context: Context | None = None,
    ) -> None:
        context = context or self._context(at)
        event = Event(
            id=self.ids.new(Kind("core.event")),
            kind=Kind(kind),
            at=at,
            payload=(Ref(target), *references),
            context=context,
        )
        effect = Effect(
            id=self.ids.new(Kind("core.effect")),
            kind=Kind(kind),
            description=description,
            target=Ref(target),
            at=at,
            context=context,
        )
        trace = Trace(self.ids.new(Kind("core.trace")))
        trace.append(
            kind=Kind("finance.operation.accepted"),
            subject=Ref(target),
            observed_at=at,
            context=context,
            references=references,
        )
        trace.append(
            kind=Kind("finance.operation.persisted"),
            subject=Ref(target),
            observed_at=at,
            context=context,
            references=(Ref(event.id), Ref(effect.id)),
        )
        unit.record_event(event)
        sink: EffectSink = unit
        sink.record(effect)
        unit.record_trace(trace, self.principal, input_hash)

    def create_account(
        self,
        name: str,
        account_type: AccountType,
        currency: str,
        liquid: bool = False,
    ) -> Result[Account, Error]:
        with self._lock:
            try:
                account = Account(
                    self.ids.new(Kind("finance.account")),
                    name.strip(),
                    account_type,
                    currency,
                    liquid,
                )
                with self.store.transaction() as unit:
                    unit.add_account(account)
                    self._record_change(
                        unit,
                        kind="finance.account.created",
                        target=account.id,
                        at=self.clock.now(),
                        description=f"Created local account {account.name}",
                    )
                return Ok(account)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("create_account", exc))

    def accounts(self) -> tuple[Account, ...]:
        with self._lock:
            return self.store.accounts()

    def entries(self, search: str = "") -> tuple[JournalEntry, ...]:
        with self._lock:
            return self.store.entries(search)

    def drafts(self) -> tuple[Draft, ...]:
        with self._lock:
            return self.store.drafts()

    def cash_records(self) -> CashRecords:
        with self._lock:
            return self.store.cash_records()

    def model_records(self) -> ModelRecords:
        with self._lock:
            return self.store.model_records()

    def _record_model_change(
        self,
        unit: FinanceUnitOfWork,
        record: ModelRecord,
        at: WallInstant,
        description: str,
        references: tuple[Ref, ...] = (),
    ) -> None:
        unit.add_model_record(record)
        self._record_change(
            unit,
            kind=f"{record.id.kind.value}.recorded",
            target=record.id,
            at=at,
            description=description,
            input_hash=model_record_hash(record),
            references=references,
            context=self._model_context(at),
        )

    def _cash_account(self, unit: FinanceUnitOfWork, account_id: Id) -> Account:
        account = next((item for item in unit.accounts() if item.id == account_id), None)
        if account is None or not account.liquid:
            raise FinanceFailure("Choose an existing eligible liquid account")
        return account

    def _record_cash_change(
        self,
        unit: FinanceUnitOfWork,
        record: CashInputRecord,
        at: WallInstant,
        kind: str,
        description: str,
        references: tuple[Ref, ...] = (),
    ) -> None:
        self._record_change(
            unit,
            kind=kind,
            target=record.id,
            at=at,
            description=description,
            input_hash=cash_record_hash(record),
            references=references,
            context=self._cash_context(at),
        )

    def _invalidate_schedule_coverage(
        self, unit: FinanceUnitOfWork, currency: str, at: WallInstant
    ) -> None:
        """Require a fresh human schedule review after a schedule inventory change."""
        today = at.value.astimezone().date()
        prior_match = max(
            (
                (position, item)
                for position, item in enumerate(unit.cash_records().coverage)
                if item.currency == currency and item.as_of <= today
            ),
            key=lambda pair: (pair[1].as_of, pair[1].recorded_at.value, pair[0]),
            default=None,
        )
        prior = None if prior_match is None else prior_match[1]
        if prior is None or not prior.schedules_complete:
            return
        invalidation = CashCoverage(
            self.ids.new(Kind("finance.cash_coverage")),
            currency,
            today,
            max(prior.through_date, today),
            prior.accounts_complete,
            False,
            prior.account_refs,
            at,
            "Schedule inventory changed; review coverage again",
        )
        unit.add_cash_coverage(invalidation)
        self._record_cash_change(
            unit,
            invalidation,
            at,
            "finance.cash_coverage.invalidated",
            "Schedule coverage needs review after a planning change",
            invalidation.account_refs,
        )

    def add_cash_schedule(
        self,
        account_id: Id,
        label: str,
        amount: Money,
        start_date: date,
        cadence: Cadence,
        timezone: str,
        end_date: date | None = None,
    ) -> Result[CashSchedule, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    account = self._cash_account(unit, account_id)
                    if amount.currency != account.currency:
                        raise FinanceFailure(
                            "Scheduled flow currency must match its liquid account"
                        )
                    at = self.clock.now()
                    schedule = CashSchedule(
                        self.ids.new(Kind("finance.cash_schedule")),
                        Ref(account.id),
                        amount,
                        start_date,
                        cadence,
                        timezone,
                        label.strip(),
                        end_date=end_date,
                    )
                    unit.add_cash_schedule(schedule)
                    self._record_cash_change(
                        unit,
                        schedule,
                        at,
                        "finance.cash_schedule.created",
                        "Recorded an external future cash-flow schedule",
                        (schedule.account,),
                    )
                    self._invalidate_schedule_coverage(unit, amount.currency, at)
                return Ok(schedule)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("add_cash_schedule", exc))

    def add_cash_allocation(
        self,
        label: str,
        amount: Money,
        active_from: date,
        linked_occurrence: ScheduleOccurrence | None = None,
    ) -> Result[CashAllocation, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    if linked_occurrence is not None:
                        records = unit.cash_records()
                        schedule = next(
                            (
                                item
                                for item in records.schedules
                                if item.id == linked_occurrence.schedule.id
                            ),
                            None,
                        )
                        if (
                            schedule is None
                            or not schedule.active
                            or schedule.amount.minor >= 0
                            or schedule.amount.currency != amount.currency
                            or not occurs_on(schedule, linked_occurrence.due_on)
                        ):
                            raise FinanceFailure(
                                "Linked allocation requires a matching outflow occurrence"
                            )
                        if active_from > linked_occurrence.due_on or any(
                            retired.target.id == schedule.id
                            and retired.effective_date <= linked_occurrence.due_on
                            for retired in records.retirements
                        ):
                            raise FinanceFailure("Linked schedule is not active on its due date")
                        committed = sum(
                            item.amount.minor
                            for item in records.allocations
                            if item.active
                            and item.linked_occurrence == linked_occurrence
                            and not any(
                                retired.target.id == item.id
                                and retired.effective_date <= linked_occurrence.due_on
                                for retired in records.retirements
                            )
                        )
                        if committed + amount.minor > -schedule.amount.minor:
                            raise FinanceFailure("Linked allocations exceed the scheduled bill")
                    at = self.clock.now()
                    allocation = CashAllocation(
                        self.ids.new(Kind("finance.cash_allocation")),
                        amount,
                        label.strip(),
                        active_from,
                        linked_occurrence,
                    )
                    unit.add_cash_allocation(allocation)
                    self._record_cash_change(
                        unit,
                        allocation,
                        at,
                        "finance.cash_allocation.created",
                        "Protected existing cash without creating an outflow",
                        (linked_occurrence.schedule,) if linked_occurrence else (),
                    )
                return Ok(allocation)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("add_cash_allocation", exc))

    def add_cash_hold(
        self,
        label: str,
        amount: Money,
        active_from: date,
        release_date: date | None = None,
    ) -> Result[CashHold, Error]:
        with self._lock:
            try:
                at = self.clock.now()
                hold = CashHold(
                    self.ids.new(Kind("finance.cash_hold")),
                    amount,
                    label.strip(),
                    active_from,
                    release_date,
                )
                with self.store.transaction() as unit:
                    unit.add_cash_hold(hold)
                    self._record_cash_change(
                        unit,
                        hold,
                        at,
                        "finance.cash_hold.created",
                        "Placed a cash availability hold",
                    )
                return Ok(hold)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("add_cash_hold", exc))

    def add_cash_floor(
        self, currency: str, effective_date: date, amount: Money
    ) -> Result[CashFloorChange, Error]:
        with self._lock:
            try:
                if currency != amount.currency:
                    raise FinanceFailure("Cash floor currency must match its amount")
                at = self.clock.now()
                change = CashFloorChange(
                    self.ids.new(Kind("finance.cash_floor_change")), effective_date, amount
                )
                with self.store.transaction() as unit:
                    unit.add_cash_floor_change(change)
                    self._record_cash_change(
                        unit,
                        change,
                        at,
                        "finance.cash_floor.changed",
                        "Set a dated minimum cash floor",
                    )
                return Ok(change)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("add_cash_floor", exc))

    def record_cash_balance(
        self,
        account_id: Id,
        observed: Money,
        observed_on: date,
        fresh_through: date,
        source: str,
    ) -> Result[CashBalanceObservation, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    account = self._cash_account(unit, account_id)
                    if observed.currency != account.currency:
                        raise FinanceFailure("Observed currency must match the liquid account")
                    at = self.clock.now()
                    if observed_on > at.value.date():
                        raise FinanceFailure("A future date cannot be an observed balance")
                    context = Context(
                        as_of=at,
                        namespace=Namespace(("personal_finance", self.profile)),
                        source=source.strip(),
                        authority="manual_balance_observation",
                        version=CASH_PROJECTION_VERSION,
                        units=account.currency,
                        scope="liquid_account_balance",
                        metadata={"observed_on": observed_on.isoformat()},
                    )
                    observation = CashBalanceObservation(
                        self.ids.new(Kind("finance.cash_observation")),
                        Ref(account.id),
                        observed,
                        at,
                        observed_on,
                        fresh_through,
                        source.strip(),
                        context,
                    )
                    unit.add_cash_observation(observation)
                    self._record_cash_change(
                        unit,
                        observation,
                        at,
                        "finance.cash_observation.recorded",
                        "Retained a manual liquid-balance observation",
                        (observation.account,),
                    )
                return Ok(observation)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("record_cash_balance", exc))

    def declare_cash_coverage(
        self,
        currency: str,
        as_of: date,
        through_date: date,
        accounts_complete: bool,
        schedules_complete: bool,
        source: str,
    ) -> Result[CashCoverage, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    at = self.clock.now()
                    if as_of > at.value.date():
                        raise FinanceFailure("Coverage cannot claim a future as-of date")
                    account_refs = tuple(
                        Ref(account.id)
                        for account in unit.accounts()
                        if account.liquid and account.currency == currency
                    )
                    coverage = CashCoverage(
                        self.ids.new(Kind("finance.cash_coverage")),
                        currency,
                        as_of,
                        through_date,
                        accounts_complete,
                        schedules_complete,
                        account_refs,
                        at,
                        source.strip(),
                    )
                    unit.add_cash_coverage(coverage)
                    self._record_cash_change(
                        unit,
                        coverage,
                        at,
                        "finance.cash_coverage.declared",
                        "Recorded explicit inventory and schedule coverage claims",
                        coverage.account_refs,
                    )
                return Ok(coverage)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("declare_cash_coverage", exc))

    def retire_cash_item(
        self, target_id: Id, effective_date: date, reason: str
    ) -> Result[CashRetirement, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    records = unit.cash_records()
                    candidates = (*records.schedules, *records.allocations, *records.holds)
                    target = next((item for item in candidates if item.id == target_id), None)
                    if target is None:
                        raise FinanceFailure(
                            "Only an existing schedule, allocation, or hold can retire"
                        )
                    if any(item.target.id == target_id for item in records.retirements):
                        raise FinanceFailure("Cash plan item is already retired")
                    at = self.clock.now()
                    retirement = CashRetirement(
                        self.ids.new(Kind("finance.cash_retirement")),
                        Ref(target_id),
                        effective_date,
                        at,
                        self.principal,
                        reason.strip(),
                    )
                    unit.add_cash_retirement(retirement)
                    self._record_cash_change(
                        unit,
                        retirement,
                        at,
                        "finance.cash_item.retired",
                        "Retired a planned cash item without deleting its history",
                        (retirement.target,),
                    )
                    if isinstance(target, CashSchedule):
                        self._invalidate_schedule_coverage(unit, target.amount.currency, at)
                return Ok(retirement)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("retire_cash_item", exc))

    def _model_account(self, unit: FinanceUnitOfWork, account_id: Id) -> Account:
        account = next((item for item in unit.accounts() if item.id == account_id), None)
        if account is None:
            raise FinanceFailure("Choose an existing account")
        return account

    def set_debt_terms(
        self,
        account_id: Id,
        apr_basis_points: int,
        minimum_payment: Money,
        due_day: int,
        source: str,
    ) -> Result[DebtTerms, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    account = self._model_account(unit, account_id)
                    if account.account_type is not AccountType.LIABILITY:
                        raise FinanceFailure("Debt terms require a liability account")
                    if minimum_payment.currency != account.currency:
                        raise FinanceFailure("Debt minimum currency must match the account")
                    at = self.clock.now()
                    record = DebtTerms(
                        self.ids.new(Kind("finance.debt_terms")),
                        Ref(account.id),
                        apr_basis_points,
                        minimum_payment,
                        due_day,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit, record, at, "Reviewed debt APR and minimum", (record.account,)
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("set_debt_terms", exc))

    def classify_debt_payment(
        self,
        account_id: Id,
        entry_id: Id,
        principal: Money,
        interest: Money,
        fees: Money,
        source: str,
    ) -> Result[DebtPaymentSplit, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    account = self._model_account(unit, account_id)
                    if account.account_type is not AccountType.LIABILITY:
                        raise FinanceFailure("Payment split requires a liability account")
                    if any(
                        value.currency != account.currency for value in (principal, interest, fees)
                    ):
                        raise FinanceFailure("Payment components must match the debt currency")
                    entry = unit.entry(entry_id)
                    liability_debit = sum(
                        post.money.minor
                        for post in entry.content.postings
                        if post.account.id == account.id
                    )
                    liquid_ids = {item.id for item in unit.accounts() if item.liquid}
                    liquid_outflow = -sum(
                        post.money.minor
                        for post in entry.content.postings
                        if post.account.id in liquid_ids
                    )
                    expense_ids = {
                        item.id
                        for item in unit.accounts()
                        if item.account_type is AccountType.EXPENSE
                    }
                    expense_debit = sum(
                        post.money.minor
                        for post in entry.content.postings
                        if post.account.id in expense_ids
                    )
                    if (
                        liability_debit != principal.minor
                        or expense_debit != interest.minor + fees.minor
                        or liquid_outflow != principal.minor + interest.minor + fees.minor
                    ):
                        raise FinanceFailure(
                            "Payment principal, interest and fees must match liability, "
                            "expense and liquid-cash postings"
                        )
                    if any(
                        item.account.id == account.id and item.entry.id == entry_id
                        for item in unit.model_records().debt_splits
                    ):
                        raise FinanceFailure("This debt payment has already been classified")
                    at = self.clock.now()
                    record = DebtPaymentSplit(
                        self.ids.new(Kind("finance.debt_payment_split")),
                        Ref(account.id),
                        Ref(entry.id),
                        principal,
                        interest,
                        fees,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit,
                        record,
                        at,
                        "Classified a posted debt payment",
                        (record.account, record.entry),
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("classify_debt_payment", exc))

    def create_asset_position(
        self, account_id: Id, name: str, category: AssetCategory, source: str
    ) -> Result[AssetPosition, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    account = self._model_account(unit, account_id)
                    if account.account_type is not AccountType.ASSET or account.liquid:
                        raise FinanceFailure("Position requires a non-liquid asset account")
                    if any(
                        item.account.id == account.id for item in unit.model_records().positions
                    ):
                        raise FinanceFailure("This asset account already has a position")
                    at = self.clock.now()
                    record = AssetPosition(
                        self.ids.new(Kind("finance.asset_position")),
                        Ref(account.id),
                        name.strip(),
                        category,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit, record, at, "Defined an asset valuation boundary", (record.account,)
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("create_asset_position", exc))

    def record_asset_valuation(
        self,
        position_id: Id,
        value: Money,
        quantity: Decimal | None,
        cost_basis: Money | None,
        observed_on: date,
        fresh_through: date,
        source: str,
    ) -> Result[AssetValuation, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    position = next(
                        (item for item in unit.model_records().positions if item.id == position_id),
                        None,
                    )
                    if position is None:
                        raise FinanceFailure("Asset position was not found")
                    account = self._model_account(unit, position.account.id)
                    if value.currency != account.currency:
                        raise FinanceFailure("Valuation currency must match the position account")
                    at = self.clock.now()
                    if observed_on > at.value.astimezone().date():
                        raise FinanceFailure("A future valuation cannot be observed")
                    record = AssetValuation(
                        self.ids.new(Kind("finance.asset_valuation")),
                        Ref(position.id),
                        value,
                        quantity,
                        cost_basis,
                        observed_on,
                        fresh_through,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit, record, at, "Retained a manual asset valuation", (record.position,)
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("record_asset_valuation", exc))

    def classify_asset_flow(
        self,
        position_id: Id,
        entry_id: Id,
        kind: AssetFlowKind,
        amount: Money,
        source: str,
    ) -> Result[AssetFlow, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    models = unit.model_records()
                    position = next(
                        (item for item in models.positions if item.id == position_id), None
                    )
                    if position is None:
                        raise FinanceFailure("Asset position was not found")
                    account = self._model_account(unit, position.account.id)
                    if amount.currency != account.currency:
                        raise FinanceFailure("Asset flow currency must match the position")
                    entry = unit.entry(entry_id)
                    movement = sum(
                        post.money.minor
                        for post in entry.content.postings
                        if post.account.id == account.id
                    )
                    expected = -amount.minor if kind is AssetFlowKind.WITHDRAWAL else amount.minor
                    if kind is AssetFlowKind.INTERNAL_TRANSFER:
                        peer_ids = {
                            item.account.id for item in models.positions if item.id != position.id
                        }
                        peer_movement = sum(
                            post.money.minor
                            for post in entry.content.postings
                            if post.account.id in peer_ids
                        )
                        if abs(movement) != amount.minor or movement + peer_movement != 0:
                            raise FinanceFailure(
                                "Internal transfer needs an equal peer position posting"
                            )
                    elif movement != expected:
                        raise FinanceFailure("Classified flow must match its posted asset movement")
                    if any(
                        item.position.id == position_id and item.entry.id == entry_id
                        for item in models.asset_flows
                    ):
                        raise FinanceFailure("This position flow has already been classified")
                    at = self.clock.now()
                    record = AssetFlow(
                        self.ids.new(Kind("finance.asset_flow")),
                        Ref(position.id),
                        Ref(entry.id),
                        kind,
                        amount,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit,
                        record,
                        at,
                        "Classified a posted asset flow",
                        (record.position, record.entry),
                    )
                    reviewed = next(
                        (
                            item
                            for item in reversed(models.asset_coverage)
                            if item.position.id == position_id
                            and item.from_on <= entry.content.effective_date <= item.through_on
                        ),
                        None,
                    )
                    if reviewed is not None and reviewed.complete:
                        invalidation = AssetFlowCoverage(
                            self.ids.new(Kind("finance.asset_flow_coverage")),
                            Ref(position.id),
                            reviewed.from_on,
                            reviewed.through_on,
                            False,
                            at,
                            "Flow classification changed; review coverage again",
                        )
                        self._record_model_change(
                            unit,
                            invalidation,
                            at,
                            "Invalidated asset flow coverage",
                            (invalidation.position,),
                        )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("classify_asset_flow", exc))

    def declare_asset_flow_coverage(
        self,
        position_id: Id,
        from_on: date,
        through_on: date,
        complete: bool,
        source: str,
    ) -> Result[AssetFlowCoverage, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    if not any(item.id == position_id for item in unit.model_records().positions):
                        raise FinanceFailure("Asset position was not found")
                    at = self.clock.now()
                    if through_on > at.value.astimezone().date():
                        raise FinanceFailure("Flow coverage cannot extend into the future")
                    record = AssetFlowCoverage(
                        self.ids.new(Kind("finance.asset_flow_coverage")),
                        Ref(position_id),
                        from_on,
                        through_on,
                        complete,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit, record, at, "Reviewed external asset flows", (record.position,)
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("declare_asset_flow_coverage", exc))

    def create_income_node(
        self,
        name: str,
        revenue_account_id: Id,
        expense_account_id: Id,
        cash_account_id: Id | None,
        monthly_revenue_assumption: Money | None,
        monthly_expense_assumption: Money | None,
        milestone_target: Money | None,
        milestone_on: date | None,
        source: str,
    ) -> Result[IncomeNode, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    revenue = self._model_account(unit, revenue_account_id)
                    expense = self._model_account(unit, expense_account_id)
                    cash = self._model_account(unit, cash_account_id) if cash_account_id else None
                    if (
                        revenue.account_type is not AccountType.INCOME
                        or expense.account_type is not AccountType.EXPENSE
                    ):
                        raise FinanceFailure("Node requires income and operating-expense accounts")
                    if cash is not None and not cash.liquid:
                        raise FinanceFailure("Node cash account must be liquid")
                    if (
                        len(
                            {item.currency for item in (revenue, expense, cash) if item is not None}
                        )
                        != 1
                    ):
                        raise FinanceFailure("Node accounts must use one currency")
                    values = (
                        monthly_revenue_assumption,
                        monthly_expense_assumption,
                        milestone_target,
                    )
                    if any(
                        value is not None and value.currency != revenue.currency for value in values
                    ):
                        raise FinanceFailure("Node assumptions must match its account currency")
                    models = unit.model_records()
                    if any(
                        item.revenue_account.id == revenue.id
                        or item.expense_account.id == expense.id
                        or (cash is not None and item.cash_account == Ref(cash.id))
                        for item in models.nodes
                    ):
                        raise FinanceFailure("Node accounts are already assigned")
                    at = self.clock.now()
                    record = IncomeNode(
                        self.ids.new(Kind("finance.income_node")),
                        name.strip(),
                        Ref(revenue.id),
                        Ref(expense.id),
                        Ref(cash.id) if cash else None,
                        monthly_revenue_assumption,
                        monthly_expense_assumption,
                        milestone_target,
                        milestone_on,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit,
                        record,
                        at,
                        "Defined a node operating boundary and assumptions",
                        (record.revenue_account, record.expense_account),
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("create_income_node", exc))

    def classify_node_funding(
        self, node_id: Id, entry_id: Id, kind: FundingKind, amount: Money, source: str
    ) -> Result[NodeFunding, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    models = unit.model_records()
                    node = next((item for item in models.nodes if item.id == node_id), None)
                    if node is None or node.cash_account is None:
                        raise FinanceFailure(
                            "Node needs a linked cash account for funding classification"
                        )
                    cash_account = self._model_account(unit, node.cash_account.id)
                    if amount.currency != cash_account.currency:
                        raise FinanceFailure("Funding amount must match node currency")
                    entry = unit.entry(entry_id)
                    movement = sum(
                        post.money.minor
                        for post in entry.content.postings
                        if post.account.id == node.cash_account.id
                    )
                    expected = amount.minor if kind is FundingKind.CAPITAL else -amount.minor
                    if movement != expected or any(
                        post.account.id in (node.revenue_account.id, node.expense_account.id)
                        for post in entry.content.postings
                    ):
                        raise FinanceFailure(
                            "Funding must match a posted node-cash transfer "
                            "outside operating accounts"
                        )
                    if any(
                        item.node.id == node_id and item.entry.id == entry_id
                        for item in models.node_funding
                    ):
                        raise FinanceFailure("This node funding entry has already been classified")
                    at = self.clock.now()
                    record = NodeFunding(
                        self.ids.new(Kind("finance.node_funding")),
                        Ref(node.id),
                        Ref(entry.id),
                        kind,
                        amount,
                        at,
                        source.strip(),
                    )
                    self._record_model_change(
                        unit,
                        record,
                        at,
                        "Classified posted node funding",
                        (record.node, record.entry),
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("classify_node_funding", exc))

    def reconcile_records(self) -> ReconcileRecords:
        with self._lock:
            return self.store.reconcile_records()

    def _reconcile_context(self, at: WallInstant) -> Context:
        return Context(
            as_of=at,
            namespace=Namespace(("personal_finance", self.profile)),
            source="manual_statements_and_posted_ledger",
            authority="local_human_reconciliation",
            version=RECONCILE_PROJECTION_VERSION,
            units="exact minor units by account currency",
            scope="monthly_statement_comparison",
        )

    def record_statement(
        self,
        account_id: Id,
        start_on: date,
        through_on: date,
        opening: Money,
        closing: Money,
        lines: tuple[tuple[date, Money, str, str | None], ...],
        lines_complete: bool,
        source: str,
    ) -> Result[Statement, Error]:
        with self._lock:
            try:
                at = self.clock.now()
                today = at.value.astimezone().date()
                if (
                    start_on.day != 1
                    or start_on.year != through_on.year
                    or start_on.month != through_on.month
                    or through_on.day != monthrange(start_on.year, start_on.month)[1]
                    or through_on >= today
                ):
                    raise FinanceFailure("Use one completed calendar month for a statement")
                if len(lines) > 1_000:
                    raise FinanceFailure("A statement supports at most 1,000 lines")
                with self.store.transaction() as unit:
                    account = self._model_account(unit, account_id)
                    if account.account_type not in (AccountType.ASSET, AccountType.LIABILITY):
                        raise FinanceFailure("Statements require an asset or liability account")
                    if opening.currency != account.currency or closing.currency != account.currency:
                        raise FinanceFailure("Statement balances must match account currency")
                    unit.check_open_range(start_on.isoformat(), through_on.isoformat())
                    recorded_revision = unit.revision()
                    statement = Statement(
                        self.ids.new(STATEMENT),
                        Ref(account_id),
                        start_on,
                        through_on,
                        opening,
                        closing,
                        lines_complete,
                        at,
                        source.strip(),
                    )
                    unit.add_reconcile_record(statement)
                    line_refs: list[Ref] = []
                    for on, movement, description, external_id in lines:
                        if not start_on <= on <= through_on:
                            raise FinanceFailure("Statement line date must be inside its month")
                        if movement.currency != account.currency:
                            raise FinanceFailure("Statement line currency must match its account")
                        line = StatementLine(
                            self.ids.new(LINE),
                            Ref(statement.id),
                            on,
                            movement,
                            description.strip(),
                            external_id.strip() if external_id is not None else None,
                        )
                        unit.add_reconcile_record(line)
                        line_refs.append(Ref(line.id))
                    ledger_entries = unit.entries()
                    ledger_opening = reconcile_opening_balance(account, ledger_entries, start_on)
                    ledger_closing = reconcile_closing_balance(account, ledger_entries, through_on)
                    if opening != ledger_opening or closing != ledger_closing:
                        issue = ReconcileIssue(
                            self.ids.new(ISSUE),
                            Ref(statement.id),
                            ledger_opening,
                            ledger_closing,
                            recorded_revision,
                            at,
                        )
                        unit.add_reconcile_record(issue)
                        self._record_change(
                            unit,
                            kind="finance.reconcile.contradiction_recorded",
                            target=issue.id,
                            at=at,
                            description="Retained statement and ledger boundary contradiction",
                            references=(Ref(statement.id),),
                            context=self._reconcile_context(at),
                        )
                    self._record_change(
                        unit,
                        kind="finance.reconcile.statement_recorded",
                        target=statement.id,
                        at=at,
                        description=(
                            f"Retained {account.name} statement for {start_on:%Y-%m} and its lines"
                        ),
                        references=(statement.account, *line_refs),
                        context=self._reconcile_context(at),
                    )
                return Ok(statement)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("record_statement", exc))

    def match_statement_line(self, line_id: Id, entry_id: Id) -> Result[StatementMatch, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    records = unit.reconcile_records()
                    line = next((item for item in records.lines if item.id == line_id), None)
                    if line is None:
                        raise FinanceFailure("Statement line was not found")
                    statement = next(
                        item for item in records.statements if item.id == line.statement.id
                    )
                    if any(
                        item.account.id == statement.account.id
                        and item.start_on == statement.start_on
                        and item.through_on == statement.through_on
                        and item.id != statement.id
                        for item in records.statements[records.statements.index(statement) + 1 :]
                    ):
                        raise FinanceFailure("A newer statement version supersedes this line")
                    if any(
                        close.start_on <= statement.start_on
                        and statement.through_on <= close.through_on
                        for close in records.closes
                    ):
                        raise FinanceFailure("Closed statement matches are immutable")
                    account = self._model_account(unit, statement.account.id)
                    entry = unit.entry(entry_id)
                    if (
                        not statement.start_on
                        <= entry.content.effective_date
                        <= statement.through_on
                        or abs((entry.content.effective_date - line.on).days) > 3
                        or reconcile_entry_movement(account, entry) != line.movement.minor
                    ):
                        raise FinanceFailure(
                            "Match needs the same account amount and a date within three days"
                        )
                    if any(
                        item.line.id == line_id
                        or (item.statement.id == statement.id and item.entry.id == entry_id)
                        for item in records.matches
                    ):
                        raise FinanceFailure("This line or posted entry is already matched")
                    if any(
                        item.statement.id == statement.id and item.target.id in (line_id, entry_id)
                        for item in records.exceptions
                    ):
                        raise FinanceFailure("An explained exception cannot also be matched")
                    at = self.clock.now()
                    match = StatementMatch(
                        self.ids.new(MATCH),
                        Ref(statement.id),
                        Ref(line.id),
                        Ref(entry.id),
                        at,
                        self.principal,
                    )
                    unit.add_reconcile_record(match)
                    self._record_change(
                        unit,
                        kind="finance.reconcile.match_accepted",
                        target=match.id,
                        at=at,
                        description="Accepted an exact statement-to-posted-entry match",
                        references=(match.line, match.entry),
                        context=self._reconcile_context(at),
                    )
                return Ok(match)
            except (FinanceFailure, ValueError, TypeError, StopIteration) as exc:
                return Err(self._error("match_statement_line", exc))

    def explain_reconcile_item(
        self, statement_id: Id, target_id: Id, rationale: str
    ) -> Result[ReconcileException, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    records = unit.reconcile_records()
                    statement = next(
                        (item for item in records.statements if item.id == statement_id), None
                    )
                    if statement is None:
                        raise FinanceFailure("Statement was not found")
                    if any(
                        item.account.id == statement.account.id
                        and item.start_on == statement.start_on
                        and item.through_on == statement.through_on
                        and item.id != statement.id
                        for item in records.statements[records.statements.index(statement) + 1 :]
                    ):
                        raise FinanceFailure("A newer statement version supersedes this one")
                    if any(
                        close.start_on <= statement.start_on
                        and statement.through_on <= close.through_on
                        for close in records.closes
                    ):
                        raise FinanceFailure("Closed statement decisions are immutable")
                    if target_id.kind == LINE:
                        if not any(
                            line.id == target_id and line.statement.id == statement_id
                            for line in records.lines
                        ):
                            raise FinanceFailure("Line does not belong to this statement")
                        if any(item.line.id == target_id for item in records.matches):
                            raise FinanceFailure("Matched lines cannot be excluded")
                    elif target_id.kind == Kind("finance.journal_entry"):
                        entry = unit.entry(target_id)
                        account = self._model_account(unit, statement.account.id)
                        if (
                            not statement.start_on
                            <= entry.content.effective_date
                            <= statement.through_on
                            or reconcile_entry_movement(account, entry) == 0
                        ):
                            raise FinanceFailure("Entry is outside this account statement")
                        if any(
                            item.statement.id == statement_id and item.entry.id == target_id
                            for item in records.matches
                        ):
                            raise FinanceFailure("Matched entries cannot be excluded")
                    else:
                        raise FinanceFailure("Explain a statement line or posted entry")
                    if any(
                        item.statement.id == statement_id and item.target.id == target_id
                        for item in records.exceptions
                    ):
                        raise FinanceFailure("This exception was already explained")
                    at = self.clock.now()
                    record = ReconcileException(
                        self.ids.new(EXCEPTION),
                        Ref(statement.id),
                        Ref(target_id),
                        rationale.strip(),
                        at,
                        self.principal,
                    )
                    unit.add_reconcile_record(record)
                    self._record_change(
                        unit,
                        kind="finance.reconcile.exception_explained",
                        target=record.id,
                        at=at,
                        description="Retained an explicit exception decision; ledger unchanged",
                        references=(record.statement, record.target),
                        context=self._reconcile_context(at),
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("explain_reconcile_item", exc))

    def resolve_reconcile_issue(
        self, issue_id: Id, rationale: str, correction_entry_id: Id | None = None
    ) -> Result[ReconcileResolution, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    records = unit.reconcile_records()
                    issue = next((item for item in records.issues if item.id == issue_id), None)
                    if issue is None:
                        raise FinanceFailure("Reconciliation contradiction was not found")
                    statement = next(
                        item for item in records.statements if item.id == issue.statement.id
                    )
                    if any(
                        item.account.id == statement.account.id
                        and item.start_on == statement.start_on
                        and item.through_on == statement.through_on
                        and item.id != statement.id
                        for item in records.statements[records.statements.index(statement) + 1 :]
                    ):
                        raise FinanceFailure("A newer statement version supersedes this issue")
                    if any(
                        close.start_on <= statement.start_on
                        and statement.through_on <= close.through_on
                        for close in records.closes
                    ):
                        raise FinanceFailure("Closed statement issues are immutable")
                    if any(item.issue.id == issue_id for item in records.resolutions):
                        raise FinanceFailure("Contradiction already has a resolution")
                    correction = None
                    if correction_entry_id is not None:
                        correction = unit.entry(correction_entry_id)
                        account = self._model_account(unit, statement.account.id)
                        if reconcile_entry_movement(account, correction) == 0:
                            raise FinanceFailure("Correction must affect the statement account")
                    at = self.clock.now()
                    record = ReconcileResolution(
                        self.ids.new(RESOLUTION),
                        Ref(issue.id),
                        rationale.strip(),
                        Ref(correction.id) if correction is not None else None,
                        at,
                        self.principal,
                    )
                    unit.add_reconcile_record(record)
                    self._record_change(
                        unit,
                        kind="finance.reconcile.contradiction_resolved",
                        target=record.id,
                        at=at,
                        description="Retained rationale for a statement discrepancy",
                        references=(record.issue,)
                        + ((record.correction_entry,) if record.correction_entry else ()),
                        context=self._reconcile_context(at),
                    )
                return Ok(record)
            except (FinanceFailure, ValueError, TypeError, StopIteration) as exc:
                return Err(self._error("resolve_reconcile_issue", exc))

    def close_month(self, year: int, month: int, reason: str) -> Result[ReconcileClose, Error]:
        with self._lock:
            try:
                start_on = date(year, month, 1)
                through_on = date(year, month, monthrange(year, month)[1])
                at = self.clock.now()
                if through_on >= at.value.astimezone().date():
                    raise FinanceFailure("Only a completed month can be closed")
                with self.store.transaction() as unit:
                    records = unit.reconcile_records()
                    if any(
                        item.start_on <= through_on and start_on <= item.through_on
                        for item in records.closes
                    ):
                        raise FinanceFailure("This month overlaps a closed period")
                    accounts = tuple(
                        item
                        for item in unit.accounts()
                        if item.account_type in (AccountType.ASSET, AccountType.LIABILITY)
                    )
                    if not accounts:
                        raise FinanceFailure("No balance-sheet accounts have been recorded")
                    latest: dict[Id, Statement] = {}
                    for item in records.statements:
                        if item.start_on == start_on and item.through_on == through_on:
                            latest[item.account.id] = item
                    statements = tuple(latest.values())
                    if {item.account.id for item in statements} != {item.id for item in accounts}:
                        raise FinanceFailure(
                            "Every asset and liability account needs a statement for this month"
                        )
                    if any(
                        draft.status == "pending"
                        and start_on <= draft.content.effective_date <= through_on
                        for draft in unit.drafts()
                    ):
                        raise FinanceFailure("Review or discard pending drafts in this month")
                    portfolio = project_reconciliation(
                        unit.accounts(), unit.entries(), records, at.value.astimezone().date()
                    )
                    monthly_views = tuple(
                        view
                        for view in portfolio.statements
                        if view.statement.start_on == start_on
                        and view.statement.through_on == through_on
                    )
                    if len(monthly_views) != len(accounts) or not all(
                        item.closable for item in monthly_views
                    ):
                        reasons = tuple(
                            reason for item in monthly_views for reason in item.missing_inputs
                        )
                        raise FinanceFailure(
                            "Month is not reconciled to zero: "
                            + ("; ".join(reasons[:4]) or "review statement evidence")
                        )
                    close = ReconcileClose(
                        self.ids.new(CLOSE),
                        start_on,
                        through_on,
                        tuple(Ref(item.statement.id) for item in monthly_views),
                        reason.strip(),
                        at,
                        self.principal,
                        unit.revision(),
                    )
                    unit.add_reconcile_record(close)
                    self._record_change(
                        unit,
                        kind="finance.reconcile.month_closed",
                        target=close.id,
                        at=at,
                        description=(
                            f"Closed reviewed month {start_on:%Y-%m} "
                            "against later dated ledger writes"
                        ),
                        references=close.statements,
                        context=self._reconcile_context(at),
                    )
                return Ok(close)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("close_month", exc))

    def _validate_content(self, unit: FinanceUnitOfWork, content: EntryContent) -> None:
        validate_accounts(content, unit.accounts())
        unit.check_open_date(content.effective_date.isoformat())
        if content.reversal_of is not None:
            original = unit.entry(content.reversal_of.id)
            expected = tuple(
                Posting(post.account, -post.money) for post in original.content.postings
            )
            if content.postings != expected:
                raise FinanceFailure("Reversal postings must exactly negate the original entry")

    def prepare_entry(self, content: EntryContent) -> Result[Draft, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    self._validate_content(unit, content)
                    at = self.clock.now()
                    draft = Draft(
                        self.ids.new(Kind("finance.action_draft")),
                        content,
                        at,
                        content_hash(content),
                    )
                    unit.add_draft(draft)
                    self._record_change(
                        unit,
                        kind="finance.draft.prepared",
                        target=draft.id,
                        at=at,
                        description="Prepared immutable draft; ledger unchanged",
                        input_hash=draft.content_hash,
                        references=tuple(post.account for post in content.postings),
                    )
                return Ok(draft)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("prepare_entry", exc))

    def post_draft(
        self,
        draft_id: Id,
        expected_hash: str,
        idempotency_key: str,
    ) -> Result[JournalEntry, Error]:
        """Commit only exact content reviewed on the trusted local human surface."""
        with self._lock:
            try:
                if self.principal != "local-human":
                    raise FinanceFailure(
                        "Authoritative posting requires the trusted local review UI"
                    )
                if not idempotency_key.strip() or len(idempotency_key) > 256:
                    raise FinanceFailure("Idempotency key must contain 1 to 256 characters")
                fingerprint = hashlib.sha256(
                    canonical(
                        {
                            "version": 1,
                            "draft": encode_id(draft_id),
                            "hash": expected_hash,
                            "principal": self.principal,
                            "profile": self.profile,
                            "policy": POLICY_VERSION,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                with self.store.transaction() as unit:
                    previous = unit.replay(idempotency_key, fingerprint)
                    if previous is not None:
                        return Ok(previous)
                    draft = unit.draft(draft_id)
                    if draft.status != "pending":
                        raise FinanceFailure("Draft is no longer pending; refresh the ledger")
                    if (
                        expected_hash != draft.content_hash
                        or content_hash(draft.content) != expected_hash
                    ):
                        raise FinanceFailure(
                            "Reviewed content hash no longer matches; review again"
                        )
                    self._validate_content(unit, draft.content)
                    at = self.clock.now()
                    approval = LocalApproval(
                        self.ids.new(Kind("finance.approval")),
                        draft.id,
                        expected_hash,
                        "reverse_entry" if draft.content.reversal_of else "post_entry",
                        self.principal,
                        self.profile,
                        POLICY_VERSION,
                        at,
                        WallInstant(at.value + timedelta(minutes=5)),
                    )
                    if self.clock.now() > approval.expires_at:
                        raise FinanceFailure("Local approval expired; review again")
                    # A positive provisional sequence is replaced by SQLite's authoritative
                    # commit order. It never escapes the transaction or enters evidence.
                    entry = JournalEntry(
                        self.ids.new(Kind("finance.journal_entry")),
                        draft.content,
                        at,
                        self.principal,
                        1,
                    )
                    entry = unit.post_entry(entry)
                    unit.consume_approval(approval, entry.id)
                    unit.finish_draft(draft.id, entry.id)
                    unit.remember(idempotency_key, fingerprint, entry.id)
                    self._record_change(
                        unit,
                        kind=POST_EFFECT.kind.value,
                        target=entry.id,
                        at=at,
                        description=f"{POST_EFFECT.description}: {entry.content.description}",
                        input_hash=expected_hash,
                        references=(Ref(draft.id), Ref(approval.id)),
                    )
                return Ok(entry)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("post_draft", exc))

    def prepare_reversal(self, entry_id: Id, effective_date: date) -> Result[Draft, Error]:
        with self._lock:
            try:
                with self.store.transaction() as unit:
                    original = unit.entry(entry_id)
                content = EntryContent(
                    effective_date,
                    f"Reversal: {original.content.description}",
                    tuple(Posting(post.account, -post.money) for post in original.content.postings),
                    original.content.tags,
                    "manual_reversal",
                    Ref(original.id),
                )
                return self.prepare_entry(content)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("prepare_reversal", exc))

    def snapshot(self) -> Result[LedgerSnapshot, Error]:
        with self._lock:
            for attempt in range(2):
                try:
                    return self._snapshot_once()
                except StaleProjection as exc:
                    if attempt == 1:
                        return Err(self._error("snapshot", exc))
            raise AssertionError("Unreachable snapshot attempt")

    def _snapshot_once(self) -> Result[LedgerSnapshot, Error]:
        with self._lock:
            try:
                inputs = self.store.inputs()
                at = self.clock.now()
                context = self._context(at)
                result = BALANCE_TRANSFORM.apply(
                    inputs,
                    context=context,
                    clock=self.clock,
                    monotonic_clock=self.monotonic_clock,
                    ids=self.ids,
                    input_refs=tuple(Ref(account.id) for account in inputs.accounts)
                    + tuple(Ref(entry.id) for entry in inputs.entries),
                )
                if isinstance(result, Err):
                    return result
                provenance = result.value.provenance
                if provenance is None:
                    raise FinanceFailure("Balance calculation did not retain its provenance")
                balances = tuple(
                    AccountBalance(
                        account,
                        money,
                        State(
                            subject=Ref(account.id),
                            value=money,
                            at=context.as_of,
                            context=context,
                        ),
                    )
                    for account, money in result.value.value
                )
                cash_context = self._cash_context(at)
                cash_start = at.value.astimezone().date()
                cash_refs = tuple(
                    Ref(item.id)
                    for collection in (
                        inputs.cash.schedules,
                        inputs.cash.allocations,
                        inputs.cash.holds,
                        inputs.cash.floor_changes,
                        inputs.cash.observations,
                        inputs.cash.coverage,
                        inputs.cash.retirements,
                    )
                    for item in collection
                )
                cash_views: list[CashPlanView] = []
                for horizon_days in (30, 60, 90):
                    cash_result = CASH_TRANSFORM.apply(
                        CashProjectionInput(cash_start, horizon_days, inputs, cash_context),
                        context=cash_context,
                        clock=self.clock,
                        monotonic_clock=self.monotonic_clock,
                        ids=self.ids,
                        input_refs=tuple(Ref(account.id) for account in inputs.accounts)
                        + tuple(Ref(entry.id) for entry in inputs.entries)
                        + cash_refs,
                        parents=(provenance,),
                    )
                    if isinstance(cash_result, Err):
                        return cash_result
                    cash_provenance = cash_result.value.provenance
                    if cash_provenance is None:
                        raise FinanceFailure("Cash calculation did not retain its provenance")
                    cash_views.append(CashPlanView(cash_result.value.value, cash_provenance))
                model_context = self._model_context(at)
                model_refs = tuple(
                    Ref(item.id)
                    for collection in (
                        inputs.models.debt_terms,
                        inputs.models.debt_splits,
                        inputs.models.positions,
                        inputs.models.valuations,
                        inputs.models.asset_flows,
                        inputs.models.asset_coverage,
                        inputs.models.nodes,
                        inputs.models.node_funding,
                    )
                    for item in collection
                )
                model_result = MODEL_TRANSFORM.apply(
                    ModelProjectionInput(inputs, cash_start),
                    context=model_context,
                    clock=self.clock,
                    monotonic_clock=self.monotonic_clock,
                    ids=self.ids,
                    input_refs=tuple(Ref(account.id) for account in inputs.accounts)
                    + tuple(Ref(entry.id) for entry in inputs.entries)
                    + tuple(Ref(item.id) for item in inputs.cash.observations)
                    + model_refs,
                    parents=(provenance,),
                )
                if isinstance(model_result, Err):
                    return model_result
                model_provenance = model_result.value.provenance
                if model_provenance is None:
                    raise FinanceFailure("Model calculation did not retain its provenance")
                model_value = model_result.value.value
                model_view = ModelView(
                    model_value.debts,
                    model_value.assets,
                    model_value.nodes,
                    model_provenance,
                )
                reconcile_context = self._reconcile_context(at)
                reconcile_refs = tuple(
                    Ref(item.id)
                    for collection in (
                        inputs.reconciliation.statements,
                        inputs.reconciliation.lines,
                        inputs.reconciliation.matches,
                        inputs.reconciliation.exceptions,
                        inputs.reconciliation.issues,
                        inputs.reconciliation.resolutions,
                        inputs.reconciliation.closes,
                    )
                    for item in collection
                )
                reconcile_result = RECONCILE_TRANSFORM.apply(
                    ReconcileProjectionInput(inputs, cash_start),
                    context=reconcile_context,
                    clock=self.clock,
                    monotonic_clock=self.monotonic_clock,
                    ids=self.ids,
                    input_refs=tuple(Ref(account.id) for account in inputs.accounts)
                    + tuple(Ref(entry.id) for entry in inputs.entries)
                    + reconcile_refs,
                    parents=(provenance,),
                )
                if isinstance(reconcile_result, Err):
                    return reconcile_result
                reconcile_provenance = reconcile_result.value.provenance
                if reconcile_provenance is None:
                    raise FinanceFailure("Reconciliation calculation did not retain provenance")
                reconcile_view = ReconcileView(reconcile_result.value.value, reconcile_provenance)
                payload = canonical(
                    {
                        "version": 1,
                        "calculation": PROJECTION_VERSION,
                        "revision": inputs.revision,
                        "coverage": COVERAGE,
                        "actual_net_worth": {"status": "unknown"},
                        "balances": [
                            {
                                "account": encode_ref(Ref(balance.account.id)),
                                "minor": balance.money.minor,
                                "currency": balance.money.currency,
                            }
                            for balance in balances
                        ],
                    }
                )
                with self.store.transaction() as unit:
                    unit.save_projection(inputs.revision, payload, provenance)
                    for view in cash_views:
                        unit.save_cash_projection(
                            inputs.revision,
                            view.projection.horizon_days,
                            _cash_payload(view.projection, inputs.revision),
                            view.provenance,
                        )
                    unit.save_model_projection(
                        inputs.revision,
                        _model_payload(model_value, inputs.revision),
                        model_provenance,
                    )
                    unit.save_reconcile_projection(
                        inputs.revision,
                        _reconcile_payload(reconcile_view.portfolio, inputs.revision),
                        reconcile_provenance,
                    )
                return Ok(
                    LedgerSnapshot(
                        balances,
                        inputs.entries,
                        inputs.accounts,
                        inputs.drafts,
                        provenance,
                        inputs.revision,
                        cash_records=inputs.cash,
                        cash_plans=tuple(cash_views),
                        models=inputs.models,
                        model_view=model_view,
                        reconciliation=inputs.reconciliation,
                        reconcile_view=reconcile_view,
                    )
                )
            except StaleProjection:
                raise
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("snapshot", exc))

    def compare_debt_scenarios(
        self, currency: str, monthly_extra: Money, custom_order: tuple[Id, ...] = ()
    ) -> Result[DebtComparisonView, Error]:
        with self._lock:
            snapshot = self.snapshot()
            if isinstance(snapshot, Err):
                return snapshot
            model_view = snapshot.value.model_view
            if model_view is None:
                return Err(
                    self._error("compare_debt_scenarios", FinanceFailure("Debt view unavailable"))
                )
            at = self.clock.now()
            result = DEBT_COMPARE_TRANSFORM.apply(
                DebtComparisonInput(
                    model_view.debts,
                    currency,
                    monthly_extra,
                    at.value.astimezone().date(),
                    custom_order,
                ),
                context=self._model_context(at),
                clock=self.clock,
                monotonic_clock=self.monotonic_clock,
                ids=self.ids,
                input_refs=tuple(Ref(item.account.id) for item in model_view.debts.debts)
                + tuple(Ref(item.id) for item in snapshot.value.models.debt_terms),
                parents=(model_view.provenance,),
            )
            if isinstance(result, Err):
                return result
            provenance = result.value.provenance
            if provenance is None:
                return Err(
                    self._error(
                        "compare_debt_scenarios",
                        FinanceFailure("Debt comparison did not retain provenance"),
                    )
                )
            return Ok(DebtComparisonView(result.value.value, provenance))

    def save_filter(self, name: str, query: str) -> Result[None, Error]:
        with self._lock:
            try:
                if not name.strip() or len(name) > 80 or len(query) > 500:
                    raise FinanceFailure("Use a filter name of 1–80 characters and query under 501")
                with self.store.transaction() as unit:
                    unit.save_filter(name.strip(), query.strip())
                return Ok(None)
            except (FinanceFailure, ValueError, TypeError) as exc:
                return Err(self._error("save_filter", exc))

    def filters(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return self.store.filters()
