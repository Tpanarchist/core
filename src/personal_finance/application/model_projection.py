"""Pure, currency-separated debt, asset, and income-node calculations."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from core.identity import Id
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import AssetFlowKind, AssetPosition, AssetValuation
from personal_finance.domain.cash import CashRecords
from personal_finance.domain.debts import DebtTerms
from personal_finance.domain.ledger import JournalEntry
from personal_finance.domain.models import ModelRecords
from personal_finance.domain.money import MAX_MINOR, Money
from personal_finance.domain.nodes import FundingKind, IncomeNode


def _account_balances(
    accounts: tuple[Account, ...], entries: tuple[JournalEntry, ...], as_of: date
) -> dict[Id, int]:
    totals = {account.id: 0 for account in accounts}
    for entry in entries:
        if entry.content.effective_date > as_of:
            continue
        for posting in entry.content.postings:
            if posting.account.id in totals:
                totals[posting.account.id] += posting.money.minor
    return totals


@dataclass(frozen=True, slots=True)
class DebtView:
    account: Account
    balance: Money
    terms: DebtTerms | None
    next_due: date | None
    classified_principal: Money
    classified_interest: Money
    classified_fees: Money


@dataclass(frozen=True, slots=True)
class DebtPortfolio:
    debts: tuple[DebtView, ...]
    weighted_apr_percent: tuple[tuple[str, Decimal | None], ...]
    missing_inputs: tuple[str, ...]


def _next_due(today: date, day: int) -> date:
    candidate = date(today.year, today.month, min(day, monthrange(today.year, today.month)[1]))
    if candidate >= today:
        return candidate
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(year, month, min(day, monthrange(year, month)[1]))


def project_debts(
    accounts: tuple[Account, ...],
    entries: tuple[JournalEntry, ...],
    models: ModelRecords,
    today: date,
) -> DebtPortfolio:
    totals = _account_balances(accounts, entries, today)
    entry_by_id = {entry.id: entry for entry in entries}
    latest: dict[Id, DebtTerms] = {}
    for terms in models.debt_terms:
        latest[terms.account.id] = terms
    views: list[DebtView] = []
    reasons: list[str] = []
    for account in accounts:
        if account.account_type is not AccountType.LIABILITY:
            continue
        balance = Money(-totals[account.id], account.currency)
        terms = latest.get(account.id)
        if balance.minor > 0 and terms is None:
            reasons.append(f"{account.name}: APR and minimum payment are not recorded")
        if balance.minor < 0:
            reasons.append(f"{account.name}: credit balance needs review before payoff planning")
        splits = tuple(
            item
            for item in models.debt_splits
            if item.account.id == account.id
            and entry_by_id[item.entry.id].content.effective_date <= today
        )
        views.append(
            DebtView(
                account,
                balance,
                terms,
                _next_due(today, terms.due_day) if terms is not None else None,
                Money(sum(item.principal.minor for item in splits), account.currency),
                Money(sum(item.interest.minor for item in splits), account.currency),
                Money(sum(item.fees.minor for item in splits), account.currency),
            )
        )
    weighted: list[tuple[str, Decimal | None]] = []
    for currency in sorted({view.balance.currency for view in views}):
        debts = [
            view for view in views if view.balance.currency == currency and view.balance.minor > 0
        ]
        denominator = sum(view.balance.minor for view in debts)
        if denominator == 0 or any(view.terms is None for view in debts):
            weighted.append((currency, None))
        else:
            numerator = sum(
                view.balance.minor * view.terms.apr_basis_points
                for view in debts
                if view.terms is not None
            )
            weighted.append((currency, Decimal(numerator) / Decimal(denominator * 100)))
    return DebtPortfolio(tuple(views), tuple(weighted), tuple(reasons))


class DebtStrategy(StrEnum):
    MINIMUM_ONLY = "minimum_only"
    AVALANCHE = "avalanche"
    SNOWBALL = "snowball"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class DebtScenario:
    strategy: DebtStrategy
    currency: str
    monthly_extra: Money
    payoff_months: int | None
    payoff_on: date | None
    total_interest: Money | None
    total_paid: Money | None
    milestones: tuple[tuple[str, int], ...]
    missing_reason: str | None


def compare_debt_payoff(
    portfolio: DebtPortfolio,
    currency: str,
    monthly_extra: Money,
    today: date,
    custom_order: tuple[Id, ...] = (),
) -> tuple[DebtScenario, ...]:
    if monthly_extra.currency != currency or monthly_extra.minor < 0:
        raise ValueError("Extra debt budget must be nonnegative in the selected currency")
    views = tuple(view for view in portfolio.debts if view.balance.currency == currency)
    active = tuple(view for view in views if view.balance.minor > 0)
    reason = None
    if not active:
        reason = "No positive debt balance; weighted APR and payoff date are not applicable"
    elif any(view.terms is None for view in active):
        reason = "Every positive debt requires reviewed APR and minimum terms"
    elif any(view.balance.minor < 0 for view in views):
        reason = "A credit balance must be reviewed before payoff comparison"
    elif len(set(custom_order)) != len(custom_order) or (
        custom_order and set(custom_order) != {view.account.id for view in active}
    ):
        reason = "Custom order must list each positive debt exactly once"
    elif any(view.terms is not None and view.terms.minimum_payment.minor == 0 for view in active):
        reason = "A positive debt needs a positive minimum payment"
    strategies = tuple(DebtStrategy)
    if reason is not None:
        return tuple(
            DebtScenario(strategy, currency, monthly_extra, None, None, None, None, (), reason)
            for strategy in strategies
        )
    results: list[DebtScenario] = []
    for strategy in strategies:
        balances = {view.account.id: view.balance.minor for view in active}
        interest_total = 0
        paid_total = 0
        milestones: list[tuple[str, int]] = []
        fixed_budget = (
            sum(view.terms.minimum_payment.minor for view in active if view.terms is not None)
            + monthly_extra.minor
        )
        month = 0
        while any(value > 0 for value in balances.values()) and month < 600:
            month += 1
            due: dict[Id, int] = {}
            for view in active:
                if balances[view.account.id] <= 0:
                    continue
                assert view.terms is not None
                interest = (
                    balances[view.account.id] * view.terms.apr_basis_points + 119_999
                ) // 120_000
                due[view.account.id] = balances[view.account.id] + interest
                interest_total += interest
            paid_month = 0
            for view in active:
                identifier = view.account.id
                if identifier not in due:
                    continue
                assert view.terms is not None
                minimum = min(due[identifier], view.terms.minimum_payment.minor)
                due[identifier] -= minimum
                paid_month += minimum
            extra_available = (
                0 if strategy is DebtStrategy.MINIMUM_ONLY else fixed_budget - paid_month
            )
            if strategy is DebtStrategy.AVALANCHE:
                order = sorted(
                    active,
                    key=lambda item: (
                        -(item.terms.apr_basis_points if item.terms is not None else 0),
                        item.account.name,
                    ),
                )
            elif strategy is DebtStrategy.SNOWBALL:
                order = sorted(
                    active, key=lambda item: (due.get(item.account.id, 0), item.account.name)
                )
            elif strategy is DebtStrategy.CUSTOM and custom_order:
                rank = {identifier: index for index, identifier in enumerate(custom_order)}
                order = sorted(active, key=lambda item: rank[item.account.id])
            else:
                order = list(active)
            for view in order:
                if extra_available <= 0:
                    break
                identifier = view.account.id
                if identifier not in due:
                    continue
                applied = min(due[identifier], extra_available)
                due[identifier] -= applied
                extra_available -= applied
                paid_month += applied
            paid_total += paid_month
            for view in active:
                identifier = view.account.id
                previous = balances[identifier]
                balances[identifier] = due.get(identifier, 0)
                if previous > 0 and balances[identifier] == 0:
                    milestones.append((view.account.name, month))
            if paid_month == 0 or interest_total > MAX_MINOR or paid_total > MAX_MINOR:
                break
        complete = all(value == 0 for value in balances.values())
        try:
            payoff_month = today.year * 12 + today.month - 1 + month
            year, zero_month = divmod(payoff_month, 12)
            payoff_on = date(
                year, zero_month + 1, min(today.day, monthrange(year, zero_month + 1)[1])
            )
        except ValueError:
            complete = False
            payoff_on = None
        if strategy is DebtStrategy.CUSTOM and not custom_order:
            complete = False
            missing_reason = "Choose an explicit debt order for a custom comparison"
        else:
            missing_reason = (
                None
                if complete
                else "No payoff within 600 months under this budget and interest policy"
            )
        results.append(
            DebtScenario(
                strategy,
                currency,
                monthly_extra,
                month if complete else None,
                payoff_on if complete else None,
                Money(interest_total, currency) if complete else None,
                Money(paid_total, currency) if complete else None,
                tuple(milestones),
                missing_reason,
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class AssetView:
    position: AssetPosition
    latest: AssetValuation | None
    value: Money | None
    unrealized_gain: Money | None
    period_gain: Money | None
    stale: bool
    missing_inputs: tuple[str, ...]


def project_assets(
    models: ModelRecords, entries: tuple[JournalEntry, ...], today: date
) -> tuple[AssetView, ...]:
    views: list[AssetView] = []
    entry_by_id = {entry.id: entry for entry in entries}
    for position in models.positions:
        valuations = [
            (index, value)
            for index, value in enumerate(models.valuations)
            if value.position.id == position.id and value.observed_on <= today
        ]
        valuations.sort(key=lambda pair: (pair[1].observed_on, pair[1].observed_at.value, pair[0]))
        latest = valuations[-1][1] if valuations else None
        stale = latest is not None and latest.fresh_through < today
        reasons: list[str] = []
        if latest is None:
            reasons.append("No valuation has been recorded")
        elif stale:
            reasons.append("Latest valuation is stale")
        value = latest.value if latest is not None and not stale else None
        unrealized = None
        if value is not None:
            if latest is not None and latest.cost_basis is not None:
                unrealized = Money(value.minor - latest.cost_basis.minor, value.currency)
            else:
                reasons.append("Cost basis is unknown")
        period_gain = None
        if len(valuations) < 2 or valuations[0][1].observed_on == valuations[-1][1].observed_on:
            reasons.append("Two valuations on different dates are needed for period gain")
        elif value is not None:
            assert latest is not None
            first = valuations[0][1]
            coverage = next(
                (
                    item
                    for item in reversed(models.asset_coverage)
                    if item.position.id == position.id
                    and item.from_on <= first.observed_on
                    and item.through_on >= latest.observed_on
                ),
                None,
            )
            if coverage is None or not coverage.complete:
                reasons.append("External-flow coverage is not reviewed for the valuation period")
            elif first.value.currency != value.currency:
                reasons.append("Valuations use different currencies")
            else:
                flows = (
                    item
                    for item in models.asset_flows
                    if item.position.id == position.id
                    and first.observed_on
                    < entry_by_id[item.entry.id].content.effective_date
                    <= latest.observed_on
                )
                contributions = 0
                withdrawals = 0
                for flow in flows:
                    if flow.kind is AssetFlowKind.CONTRIBUTION:
                        contributions += flow.amount.minor
                    elif flow.kind is AssetFlowKind.WITHDRAWAL:
                        withdrawals += flow.amount.minor
                gain = value.minor - first.value.minor - contributions + withdrawals
                if -MAX_MINOR <= gain <= MAX_MINOR:
                    period_gain = Money(gain, value.currency)
                else:
                    reasons.append("Period gain exceeds the supported exact money range")
        views.append(
            AssetView(position, latest, value, unrealized, period_gain, stale, tuple(reasons))
        )
    return tuple(views)


@dataclass(frozen=True, slots=True)
class NodeView:
    node: IncomeNode
    revenue: Money
    operating_expenses: Money
    net_cash_flow: Money
    capital: Money
    withdrawals: Money
    assumption_net: Money | None
    runway_months: int | None
    missing_inputs: tuple[str, ...]


def project_nodes(
    accounts: tuple[Account, ...],
    entries: tuple[JournalEntry, ...],
    models: ModelRecords,
    cash: CashRecords,
    today: date,
) -> tuple[NodeView, ...]:
    account_by_id = {account.id: account for account in accounts}
    balances = _account_balances(accounts, entries, today)
    entry_by_id = {entry.id: entry for entry in entries}
    start = today - timedelta(days=29)
    views: list[NodeView] = []
    for node in models.nodes:
        revenue_account = account_by_id[node.revenue_account.id]
        currency = revenue_account.currency
        revenue = 0
        expenses = 0
        for entry in entries:
            if not start <= entry.content.effective_date <= today:
                continue
            for posting in entry.content.postings:
                if posting.account.id == node.revenue_account.id:
                    revenue -= posting.money.minor
                elif posting.account.id == node.expense_account.id:
                    expenses += posting.money.minor
        funding = tuple(item for item in models.node_funding if item.node.id == node.id)
        capital = sum(
            item.amount.minor
            for item in funding
            if item.kind is FundingKind.CAPITAL
            and start <= entry_by_id[item.entry.id].content.effective_date <= today
        )
        withdrawals = sum(
            item.amount.minor
            for item in funding
            if item.kind is FundingKind.WITHDRAWAL
            and start <= entry_by_id[item.entry.id].content.effective_date <= today
        )
        assumption = (
            Money(
                node.monthly_revenue_assumption.minor - node.monthly_expense_assumption.minor,
                currency,
            )
            if node.monthly_revenue_assumption is not None
            and node.monthly_expense_assumption is not None
            else None
        )
        reasons: list[str] = []
        runway = None
        if node.cash_account is None:
            reasons.append("No dedicated liquid cash account is linked for runway")
        elif expenses > revenue:
            observations = [
                item
                for item in cash.observations
                if item.account.id == node.cash_account.id and item.observed_on <= today
            ]
            latest = max(
                observations,
                key=lambda item: (item.observed_on, item.observed_at.value),
                default=None,
            )
            if latest is None or latest.fresh_through < today:
                reasons.append("A fresh node cash observation is required for runway")
            elif latest.observed.minor != balances[node.cash_account.id]:
                reasons.append("Node cash observation does not reconcile to its ledger account")
            else:
                runway = max(0, latest.observed.minor // (expenses - revenue))
        if assumption is None:
            reasons.append("Monthly revenue and expense assumptions are incomplete")
        views.append(
            NodeView(
                node,
                Money(revenue, currency),
                Money(expenses, currency),
                Money(revenue - expenses, currency),
                Money(capital, currency),
                Money(withdrawals, currency),
                assumption,
                runway,
                tuple(reasons),
            )
        )
    return tuple(views)
