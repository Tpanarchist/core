"""Debt, asset, and node records remain tied to posted evidence across reopen."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from core.identity import Ref
from core.result import Err, Ok
from personal_finance.application.service import FinanceService
from personal_finance.bootstrap import open_service
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import AssetCategory, AssetFlowKind
from personal_finance.domain.ledger import EntryContent, JournalEntry, Posting
from personal_finance.domain.money import Money
from personal_finance.domain.nodes import FundingKind


def account(
    service: FinanceService, name: str, kind: AccountType, *, liquid: bool = False
) -> Account:
    result = service.create_account(name, kind, "USD", liquid)
    assert isinstance(result, Ok)
    return result.value


def post(
    service: FinanceService, on: date, label: str, postings: tuple[tuple[Account, int], ...]
) -> JournalEntry:
    prepared = service.prepare_entry(
        EntryContent(
            on,
            label,
            tuple(Posting(Ref(item.id), Money(minor, "USD")) for item, minor in postings),
        )
    )
    assert isinstance(prepared, Ok)
    committed = service.post_draft(prepared.value.id, prepared.value.content_hash, f"model:{label}")
    assert isinstance(committed, Ok)
    return committed.value


def test_debt_terms_payment_classification_and_scenarios(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    today = date.today()
    cash = account(service, "Checking", AccountType.ASSET, liquid=True)
    debt = account(service, "Credit card", AccountType.LIABILITY)
    equity = account(service, "Opening equity", AccountType.EQUITY)
    interest = account(service, "Interest", AccountType.EXPENSE)
    post(service, today, "cash", ((cash, 200_000), (equity, -200_000)))
    post(service, today, "debt", ((equity, 100_000), (debt, -100_000)))
    terms = service.set_debt_terms(debt.id, 1_800, Money(10_000, "USD"), 15, "statement")
    assert isinstance(terms, Ok)
    payment = post(
        service,
        today,
        "payment",
        ((debt, 20_000), (interest, 5_000), (cash, -25_000)),
    )
    bad = service.classify_debt_payment(
        debt.id, payment.id, Money(25_000, "USD"), Money(0, "USD"), Money(0, "USD"), "bad"
    )
    assert isinstance(bad, Err)
    split = service.classify_debt_payment(
        debt.id,
        payment.id,
        Money(20_000, "USD"),
        Money(5_000, "USD"),
        Money(0, "USD"),
        "statement",
    )
    assert isinstance(split, Ok)
    snapshot = service.snapshot()
    assert isinstance(snapshot, Ok)
    assert snapshot.value.model_view is not None
    view = snapshot.value.model_view.debts.debts[0]
    assert view.balance == Money(80_000, "USD")
    assert view.classified_principal == Money(20_000, "USD")
    assert view.classified_interest == Money(5_000, "USD")
    assert snapshot.value.model_view.debts.weighted_apr_percent == (("USD", 18),)
    compared = service.compare_debt_scenarios("USD", Money(5_000, "USD"))
    assert isinstance(compared, Ok)
    strategies = {item.strategy.value: item for item in compared.value.scenarios}
    assert strategies["avalanche"].payoff_months is not None
    assert strategies["minimum_only"].payoff_months is not None
    assert strategies["avalanche"].payoff_months <= strategies["minimum_only"].payoff_months
    baseline = service.compare_debt_scenarios("USD", Money(0, "USD"))
    assert isinstance(baseline, Ok)
    baseline_by_strategy = {item.strategy.value: item for item in baseline.value.scenarios}
    assert (
        strategies["minimum_only"].total_interest
        == baseline_by_strategy["minimum_only"].total_interest
    )
    assert (
        strategies["minimum_only"].payoff_months
        == baseline_by_strategy["minimum_only"].payoff_months
    )
    assert (
        strategies["custom"].missing_reason
        == "Choose an explicit debt order for a custom comparison"
    )
    assert len(compared.value.provenance.parents) == 1
    assert compared.value.provenance.transform_name == "finance.debt_payoff_comparison"
    future = post(
        service, today + timedelta(days=7), "future payment", ((debt, 10_000), (cash, -10_000))
    )
    assert isinstance(
        service.classify_debt_payment(
            debt.id,
            future.id,
            Money(10_000, "USD"),
            Money(0, "USD"),
            Money(0, "USD"),
            "scheduled payment",
        ),
        Ok,
    )
    current = service.snapshot()
    assert isinstance(current, Ok) and current.value.model_view is not None
    assert current.value.model_view.debts.debts[0].balance == Money(80_000, "USD")
    assert current.value.model_view.debts.debts[0].classified_principal == Money(20_000, "USD")
    reopened = open_service(tmp_path)
    assert len(reopened.model_records().debt_splits) == 2


def test_multiple_debts_continue_after_first_balance_is_paid(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    today = date.today()
    equity = account(service, "Opening equity", AccountType.EQUITY)
    card = account(service, "Card", AccountType.LIABILITY)
    loan = account(service, "Loan", AccountType.LIABILITY)
    post(service, today, "card", ((equity, 30_000), (card, -30_000)))
    post(service, today, "loan", ((equity, 100_000), (loan, -100_000)))
    assert isinstance(
        service.set_debt_terms(card.id, 2_250, Money(5_000, "USD"), 12, "statement"),
        Ok,
    )
    assert isinstance(
        service.set_debt_terms(loan.id, 560, Money(8_000, "USD"), 20, "statement"),
        Ok,
    )
    comparison = service.compare_debt_scenarios("USD", Money(5_000, "USD"))
    assert isinstance(comparison, Ok)
    scenarios = {item.strategy.value: item for item in comparison.value.scenarios}
    assert scenarios["minimum_only"].payoff_months is not None
    assert scenarios["avalanche"].payoff_months is not None
    assert scenarios["snowball"].payoff_months is not None
    assert len(scenarios["avalanche"].milestones) == 2


def test_asset_gain_needs_two_fresh_valuations_and_reviewed_flows(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    today = date.today()
    cash = account(service, "Checking", AccountType.ASSET, liquid=True)
    brokerage = account(service, "Brokerage", AccountType.ASSET)
    position = service.create_asset_position(
        brokerage.id, "Index fund", AssetCategory.INVESTMENT, "manual"
    )
    assert isinstance(position, Ok)
    first = service.record_asset_valuation(
        position.value.id,
        Money(50_000, "USD"),
        None,
        Money(40_000, "USD"),
        today - timedelta(days=10),
        today - timedelta(days=1),
        "statement 1",
    )
    assert isinstance(first, Ok)
    entry = post(
        service, today - timedelta(days=5), "contribution", ((brokerage, 10_000), (cash, -10_000))
    )
    flow = service.classify_asset_flow(
        position.value.id, entry.id, AssetFlowKind.CONTRIBUTION, Money(10_000, "USD"), "manual"
    )
    assert isinstance(flow, Ok)
    latest = service.record_asset_valuation(
        position.value.id,
        Money(60_000, "USD"),
        None,
        Money(50_000, "USD"),
        today,
        today + timedelta(days=7),
        "statement 2",
    )
    assert isinstance(latest, Ok)
    incomplete = service.snapshot()
    assert isinstance(incomplete, Ok)
    assert incomplete.value.model_view is not None
    assert incomplete.value.model_view.assets[0].period_gain is None
    assert isinstance(
        service.declare_asset_flow_coverage(
            position.value.id, today - timedelta(days=10), today, True, "reviewed"
        ),
        Ok,
    )
    ready = service.snapshot()
    assert isinstance(ready, Ok)
    assert ready.value.model_view is not None
    assert ready.value.model_view.assets[0].value == Money(60_000, "USD")
    assert ready.value.model_view.assets[0].period_gain == Money(0, "USD")
    assert ready.value.model_view.assets[0].unrealized_gain == Money(10_000, "USD")
    late_entry = post(
        service,
        today - timedelta(days=3),
        "late contribution",
        ((brokerage, 2_000), (cash, -2_000)),
    )
    assert isinstance(
        service.classify_asset_flow(
            position.value.id,
            late_entry.id,
            AssetFlowKind.CONTRIBUTION,
            Money(2_000, "USD"),
            "late review",
        ),
        Ok,
    )
    needs_review = service.snapshot()
    assert isinstance(needs_review, Ok) and needs_review.value.model_view is not None
    assert needs_review.value.model_view.assets[0].period_gain is None
    assert len(open_service(tmp_path).model_records().asset_flows) == 2


def test_node_funding_does_not_inflate_operating_cash_flow(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    today = date.today()
    node_cash = account(service, "Project cash", AccountType.ASSET, liquid=True)
    personal_cash = account(service, "Personal cash", AccountType.ASSET, liquid=True)
    revenue = account(service, "Project revenue", AccountType.INCOME)
    expense = account(service, "Project expenses", AccountType.EXPENSE)
    node = service.create_income_node(
        "Side project",
        revenue.id,
        expense.id,
        node_cash.id,
        Money(20_000, "USD"),
        Money(5_000, "USD"),
        None,
        None,
        "manual",
    )
    assert isinstance(node, Ok)
    post(service, today, "sale", ((node_cash, 20_000), (revenue, -20_000)))
    post(service, today, "hosting", ((expense, 5_000), (node_cash, -5_000)))
    funding_entry = post(service, today, "capital", ((node_cash, 10_000), (personal_cash, -10_000)))
    classified = service.classify_node_funding(
        node.value.id, funding_entry.id, FundingKind.CAPITAL, Money(10_000, "USD"), "manual"
    )
    assert isinstance(classified, Ok)
    snapshot = service.snapshot()
    assert isinstance(snapshot, Ok)
    assert snapshot.value.model_view is not None
    view = snapshot.value.model_view.nodes[0]
    assert view.revenue == Money(20_000, "USD")
    assert view.operating_expenses == Money(5_000, "USD")
    assert view.net_cash_flow == Money(15_000, "USD")
    assert view.capital == Money(10_000, "USD")
    assert view.runway_months is None
    wrong_currency = service.classify_node_funding(
        node.value.id, funding_entry.id, FundingKind.CAPITAL, Money(10_000, "EUR"), "wrong"
    )
    assert isinstance(wrong_currency, Err)
