"""Local command line and trusted human review surface."""

from __future__ import annotations

import argparse
import csv
import sys
from calendar import monthrange
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from core.identity import Id, Ref
from core.result import Err
from core.value import Kind
from personal_finance.adapters.coinbase import CoinbaseReadError
from personal_finance.adapters.coinbase import preview as coinbase_preview
from personal_finance.adapters.memory import FinanceMemory
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.application.service import FinanceService
from personal_finance.bootstrap import default_data_dir, open_service
from personal_finance.csv_io import export_csv, stage_csv
from personal_finance.display import content_text, safe_display
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import AssetCategory, AssetFlowKind
from personal_finance.domain.cash import Cadence, ScheduleOccurrence
from personal_finance.domain.ledger import Draft, EntryContent, Posting
from personal_finance.domain.money import Money
from personal_finance.domain.nodes import FundingKind


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Private local finance, constructed from Core")
    parser.add_argument("--data-dir", type=Path, help="Storage directory (default: user app data)")
    parser.add_argument("--demo", action="store_true", help="Isolated, labeled synthetic demo book")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("tui", help="Open the eight-tab terminal workspace (default)")
    commands.add_parser("init", help="Initialize storage without opening the interface")
    commands.add_parser("accounts", help="List account names, identities, types and currencies")
    account = commands.add_parser(
        "add-account", help="Create an account without inventing a balance"
    )
    account.add_argument("name")
    account.add_argument("type", choices=[kind.value for kind in AccountType])
    account.add_argument("currency")
    account.add_argument("--liquid", action="store_true")
    ledger = commands.add_parser("ledger", help="List persisted entries")
    ledger.add_argument("--search", default="")
    entry = commands.add_parser("entry", help="Prepare a two-account entry for later review")
    entry.add_argument("date", help="Effective date, YYYY-MM-DD")
    entry.add_argument("description")
    entry.add_argument("debit", help="Exact account name")
    entry.add_argument("credit", help="Exact account name")
    entry.add_argument("amount", help="Positive exact decimal amount")
    entry.add_argument("currency")
    commands.add_parser("drafts", help="Inspect pending and historical drafts")
    post = commands.add_parser("post", help="Review a draft and type POST in a local terminal")
    post.add_argument("draft_id")
    reverse = commands.add_parser("reverse", help="Prepare a reversal for separate local review")
    reverse.add_argument("entry_id")
    reverse.add_argument("date", help="Correction effective date, YYYY-MM-DD")
    stage = commands.add_parser(
        "stage", help="Stage the documented CSV schema as reviewable drafts"
    )
    stage.add_argument("path", type=Path)
    export = commands.add_parser("export", help="Write a new CSV containing all posted ledger rows")
    export.add_argument("path", type=Path)
    commands.add_parser("explain", help="Recorded balances, coverage, calculation and source IDs")
    cash = commands.add_parser("cash", help="Inspect a dated 30/60/90-day cash plan")
    cash.add_argument("--horizon", type=int, choices=(30, 60, 90), default=30)
    schedule = commands.add_parser("plan-schedule", help="Record a dated future cash flow")
    schedule.add_argument("account", help="Exact liquid account name")
    schedule.add_argument("label")
    schedule.add_argument("amount", help="Signed decimal; negative is an outflow")
    schedule.add_argument("currency")
    schedule.add_argument("start_date", help="First local due date, YYYY-MM-DD")
    schedule.add_argument("cadence", choices=[kind.value for kind in Cadence])
    schedule.add_argument("timezone", help="IANA timezone, such as America/New_York")
    schedule.add_argument("--end-date", type=date.fromisoformat)
    allocation = commands.add_parser("plan-allocation", help="Protect existing cash")
    allocation.add_argument("label")
    allocation.add_argument("amount", help="Positive decimal")
    allocation.add_argument("currency")
    allocation.add_argument("active_from", type=date.fromisoformat)
    allocation.add_argument("--schedule-id")
    allocation.add_argument("--due-on", type=date.fromisoformat)
    hold = commands.add_parser("plan-hold", help="Place a cash availability hold")
    hold.add_argument("label")
    hold.add_argument("amount", help="Positive decimal")
    hold.add_argument("currency")
    hold.add_argument("active_from", type=date.fromisoformat)
    hold.add_argument("--release-date", type=date.fromisoformat)
    floor = commands.add_parser("plan-floor", help="Set a dated minimum cash floor")
    floor.add_argument("currency")
    floor.add_argument("amount", help="Nonnegative decimal")
    floor.add_argument("effective_date", type=date.fromisoformat)
    observe = commands.add_parser("plan-observe", help="Record a manual liquid balance observation")
    observe.add_argument("account", help="Exact liquid account name")
    observe.add_argument("amount")
    observe.add_argument("currency")
    observe.add_argument("observed_on", type=date.fromisoformat)
    observe.add_argument("fresh_through", type=date.fromisoformat)
    observe.add_argument("--source", default="manual CLI")
    coverage = commands.add_parser("plan-coverage", help="Declare reviewed cash input coverage")
    coverage.add_argument("currency")
    coverage.add_argument("as_of", type=date.fromisoformat)
    coverage.add_argument("through_date", type=date.fromisoformat)
    coverage.add_argument("--accounts-complete", action="store_true")
    coverage.add_argument("--schedules-complete", action="store_true")
    coverage.add_argument("--source", default="manual CLI")
    retire = commands.add_parser("plan-retire", help="Retire a schedule, allocation, or hold")
    retire.add_argument("kind", choices=("schedule", "allocation", "hold"))
    retire.add_argument("id")
    retire.add_argument("effective_date", type=date.fromisoformat)
    retire.add_argument("reason")
    debts = commands.add_parser("debts", help="Recorded debts and conditional payoff comparisons")
    debts.add_argument("--currency", default="USD")
    debts.add_argument("--extra", default="0", help="Monthly extra payoff budget")
    terms = commands.add_parser("debt-terms", help="Record reviewed APR and minimum terms")
    terms.add_argument("account", help="Exact liability account name")
    terms.add_argument("apr", help="Exact percent, for example 18.25")
    terms.add_argument("minimum")
    terms.add_argument("due_day", type=int)
    terms.add_argument("--source", default="manual CLI")
    split = commands.add_parser("debt-payment", help="Classify a posted payment")
    split.add_argument("account")
    split.add_argument("entry_id")
    split.add_argument("principal")
    split.add_argument("interest")
    split.add_argument("fees")
    split.add_argument("--source", default="manual CLI")
    commands.add_parser("assets", help="Asset valuations and conditional performance")
    position = commands.add_parser("asset-position", help="Define a non-liquid asset position")
    position.add_argument("account")
    position.add_argument("name")
    position.add_argument("category", choices=[item.value for item in AssetCategory])
    position.add_argument("--source", default="manual CLI")
    valuation = commands.add_parser("asset-value", help="Record a dated manual asset valuation")
    valuation.add_argument("position_id")
    valuation.add_argument("value")
    valuation.add_argument("currency")
    valuation.add_argument("observed_on", type=date.fromisoformat)
    valuation.add_argument("fresh_through", type=date.fromisoformat)
    valuation.add_argument("--quantity")
    valuation.add_argument("--basis")
    valuation.add_argument("--source", default="manual CLI")
    flow = commands.add_parser("asset-flow", help="Classify a posted position flow")
    flow.add_argument("position_id")
    flow.add_argument("entry_id")
    flow.add_argument("kind", choices=[item.value for item in AssetFlowKind])
    flow.add_argument("amount")
    flow.add_argument("currency")
    flow.add_argument("--source", default="manual CLI")
    asset_coverage = commands.add_parser("asset-coverage", help="Review external flow coverage")
    asset_coverage.add_argument("position_id")
    asset_coverage.add_argument("from_on", type=date.fromisoformat)
    asset_coverage.add_argument("through_on", type=date.fromisoformat)
    asset_coverage.add_argument("--complete", action="store_true")
    asset_coverage.add_argument("--source", default="manual CLI")
    commands.add_parser("nodes", help="Recorded operating performance and funding")
    node = commands.add_parser("node-create", help="Define an income node")
    node.add_argument("name")
    node.add_argument("revenue_account")
    node.add_argument("expense_account")
    node.add_argument("--cash-account")
    node.add_argument("--monthly-revenue")
    node.add_argument("--monthly-expenses")
    node.add_argument("--milestone-target")
    node.add_argument("--milestone-on", type=date.fromisoformat)
    node.add_argument("--source", default="manual CLI")
    funding = commands.add_parser("node-funding", help="Classify a posted capital transfer")
    funding.add_argument("node_id")
    funding.add_argument("entry_id")
    funding.add_argument("kind", choices=[item.value for item in FundingKind])
    funding.add_argument("amount")
    funding.add_argument("currency")
    funding.add_argument("--source", default="manual CLI")
    commands.add_parser("reconcile", help="Inspect statement differences and close readiness")
    statement = commands.add_parser("statement-add", help="Record one monthly statement from CSV")
    statement.add_argument("account", help="Exact asset or liability account name")
    statement.add_argument("month", help="YYYY-MM")
    statement.add_argument("opening", help="Signed balance in account currency")
    statement.add_argument("closing", help="Signed balance in account currency")
    statement.add_argument("lines_csv", type=Path, help="CSV: date,amount,description,external_id")
    statement.add_argument("--complete", action="store_true", help="Declare the line list complete")
    statement.add_argument("--source", default="manual CLI statement")
    match = commands.add_parser(
        "reconcile-match", help="Accept an exact suggested line/entry match"
    )
    match.add_argument("line_id")
    match.add_argument("entry_id")
    exception = commands.add_parser(
        "reconcile-exception", help="Explain one unmatched line or entry"
    )
    exception.add_argument("statement_id")
    exception.add_argument("target_kind", choices=("line", "entry"))
    exception.add_argument("target_id")
    exception.add_argument("rationale")
    resolution = commands.add_parser("reconcile-resolve", help="Resolve a recorded contradiction")
    resolution.add_argument("issue_id")
    resolution.add_argument("rationale")
    resolution.add_argument("--correction-entry")
    close = commands.add_parser("reconcile-close", help="Review and lock a reconciled month")
    close.add_argument("month", help="YYYY-MM")
    close.add_argument("reason")
    commands.add_parser("memory-sync", help="Retry optional local evidence indexing")
    coinbase = commands.add_parser(
        "coinbase-preview", help="Read Coinbase balances and recent fills without posting"
    )
    coinbase.add_argument("--key-file", type=Path, help="Downloaded read-only CDP key JSON")
    coinbase.add_argument("--fill-pages", type=int, choices=range(1, 21), default=5)
    coinbase.add_argument("--all-accounts", action="store_true")
    recall = commands.add_parser("recall", help="Find retained finance evidence in Memory")
    recall.add_argument("text", help="Literal text to find in accepted change evidence")
    recall.add_argument("--limit", type=int, default=20)
    return parser


def print_draft_review(draft: Draft, accounts: tuple[Account, ...]) -> None:
    print(f"Draft {safe_display(draft.id.value)} | {safe_display(draft.status)}")
    print(content_text(draft.content, accounts))
    print(f"Exact content SHA-256: {draft.content_hash}")


def _liquid_account(accounts: tuple[Account, ...], name: str) -> Account:
    matches = tuple(account for account in accounts if account.name == name and account.liquid)
    if len(matches) != 1:
        raise ValueError("Use one existing, unambiguous liquid account name")
    return matches[0]


def _named_account(accounts: tuple[Account, ...], name: str, account_type: AccountType) -> Account:
    matches = tuple(
        account
        for account in accounts
        if account.name == name and account.account_type is account_type
    )
    if len(matches) != 1:
        raise ValueError(f"Use one existing {account_type.value} account with that exact name")
    return matches[0]


def _basis_points(raw: str) -> int:
    try:
        value = Decimal(raw) * 100
    except InvalidOperation as exc:
        raise ValueError("APR requires an exact decimal percent") from exc
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError("APR supports exact hundredths of a percent")
    return int(value)


def _month(raw: str) -> tuple[date, date]:
    if len(raw) != 7 or raw[4] != "-":
        raise ValueError("Month must be YYYY-MM")
    start = date.fromisoformat(raw + "-01")
    return start, date(start.year, start.month, monthrange(start.year, start.month)[1])


def _statement_lines(path: Path, currency: str) -> tuple[tuple[date, Money, str, str | None], ...]:
    if path.stat().st_size > 1_000_000:
        raise ValueError("Statement CSV exceeds the 1 MB local import limit")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = csv.DictReader(source)
        if rows.fieldnames != ["date", "amount", "description", "external_id"]:
            raise ValueError("Statement CSV header must be date,amount,description,external_id")
        lines: list[tuple[date, Money, str, str | None]] = []
        for row in rows:
            if len(lines) >= 1_000:
                raise ValueError("Statement CSV supports at most 1,000 lines")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Statement CSV row has missing or extra columns")
            lines.append(
                (
                    date.fromisoformat(cast(str, row["date"])),
                    Money.from_decimal(cast(str, row["amount"]), currency),
                    cast(str, row["description"]),
                    cast(str, row["external_id"]) or None,
                )
            )
        return tuple(lines)


def _run_new_commands(
    command: str, args: argparse.Namespace, service: FinanceService, data_dir: Path
) -> int:
    if command == "reconcile":
        snapshot = service.snapshot()
        if isinstance(snapshot, Err):
            print(safe_display(snapshot.error.message), file=sys.stderr)
            return 1
        view = snapshot.value.reconcile_view
        assert view is not None
        print(f"Reconciliation · revision {snapshot.value.revision}")
        for item in view.portfolio.statements:
            print(
                f"{safe_display(item.account.name)} {item.statement.start_on:%Y-%m} "
                f"[{item.statement.id.value}] · "
                f"opening difference {item.opening_difference.format()} · "
                f"closing difference {item.closing_difference.format()} · "
                f"line bridge {item.line_difference.format()} · "
                f"{'CLOSED' if item.closed else 'READY' if item.closable else 'OPEN'}"
            )
            for reason in item.missing_inputs:
                print(f"  Needs: {safe_display(reason)}")
            for suggestion in item.suggestions:
                print(
                    f"  Suggested: line {suggestion.line_id.value} → "
                    f"entry {suggestion.entry_id.value} ({suggestion.reason})"
                )
            for line in item.unresolved_lines:
                print(f"  Unmatched line {line.id.value}: {line.movement.format()}")
            for entry in item.unresolved_entries:
                print(
                    f"  Unmatched entry {entry.id.value}: {safe_display(entry.content.description)}"
                )
        if not view.portfolio.statements:
            print("No statements recorded. Use statement-add to begin.")
        for start, end in view.portfolio.closes:
            print(f"Closed: {start} through {end}")
        for entry in view.portfolio.later_corrections:
            print(f"Later correction: {entry.id.value} on {entry.content.effective_date}")
        print(f"Core provenance: {view.provenance.id.value}")
    elif command == "statement-add":
        start, end = _month(str(args.month))
        accounts = tuple(account for account in service.accounts() if account.name == args.account)
        if len(accounts) != 1:
            raise ValueError("Use one existing, unambiguous account name")
        account = accounts[0]
        lines = _statement_lines(args.lines_csv, account.currency)
        print(
            f"Statement review: {safe_display(account.name)}, {start} through {end}; "
            f"opening {args.opening} {account.currency}, closing {args.closing} "
            f"{account.currency}; {len(lines)} lines; "
            f"inventory {'complete' if args.complete else 'incomplete'}"
        )
        if not sys.stdin.isatty() or input("Type RECORD to retain this statement: ") != "RECORD":
            print("Statement not recorded.")
            return 0
        result = service.record_statement(
            account.id,
            start,
            end,
            Money.from_decimal(str(args.opening), account.currency),
            Money.from_decimal(str(args.closing), account.currency),
            lines,
            bool(args.complete),
            str(args.source),
        )
        if isinstance(result, Err):
            print(safe_display(result.error.message), file=sys.stderr)
            return 1
        print(f"Statement retained [{result.value.id.value}]. Use reconcile to review differences.")
    elif command == "reconcile-match":
        result = service.match_statement_line(
            Id(Kind("finance.reconcile_line"), str(args.line_id)),
            Id(Kind("finance.journal_entry"), str(args.entry_id)),
        )
        if isinstance(result, Err):
            print(safe_display(result.error.message), file=sys.stderr)
            return 1
        print(f"Exact match accepted [{result.value.id.value}].")
    elif command == "reconcile-exception":
        kind = "finance.reconcile_line" if args.target_kind == "line" else "finance.journal_entry"
        result = service.explain_reconcile_item(
            Id(Kind("finance.reconcile_statement"), str(args.statement_id)),
            Id(Kind(kind), str(args.target_id)),
            str(args.rationale),
        )
        if isinstance(result, Err):
            print(safe_display(result.error.message), file=sys.stderr)
            return 1
        print(f"Exception explanation retained [{result.value.id.value}].")
    elif command == "reconcile-resolve":
        correction = (
            Id(Kind("finance.journal_entry"), str(args.correction_entry))
            if args.correction_entry
            else None
        )
        result = service.resolve_reconcile_issue(
            Id(Kind("finance.reconcile_issue"), str(args.issue_id)),
            str(args.rationale),
            correction,
        )
        if isinstance(result, Err):
            print(safe_display(result.error.message), file=sys.stderr)
            return 1
        print(f"Contradiction resolution retained [{result.value.id.value}].")
    elif command == "reconcile-close":
        start, _ = _month(str(args.month))
        snapshot = service.snapshot()
        if isinstance(snapshot, Err):
            print(safe_display(snapshot.error.message), file=sys.stderr)
            return 1
        view = snapshot.value.reconcile_view
        assert view is not None
        monthly = tuple(
            item for item in view.portfolio.statements if item.statement.start_on == start
        )
        for item in monthly:
            print(
                f"{safe_display(item.account.name)}: "
                f"{'READY' if item.closable else 'NOT READY'}; "
                f"closing difference {item.closing_difference.format()}"
            )
        if (
            not monthly
            or not sys.stdin.isatty()
            or input("Type CLOSE to lock this month: ") != "CLOSE"
        ):
            print("Month remains open.")
            return 0
        result = service.close_month(start.year, start.month, str(args.reason))
        if isinstance(result, Err):
            print(safe_display(result.error.message), file=sys.stderr)
            return 1
        print(f"Month closed [{result.value.id.value}]. Later corrections use a later open date.")
    elif command in ("memory-sync", "recall"):
        from core.time import SystemClock

        store = cast(FinanceStore, service.store)
        bridge = FinanceMemory(
            store,
            data_dir / ("demo-memory.sqlite3" if args.demo else "personal-memory.sqlite3"),
            "demo" if args.demo else "personal",
        )
        pending, delivered, failed = bridge.deliver()
        if command == "memory-sync":
            print(f"Memory evidence: {delivered} delivered, {pending} pending, {failed} failed")
        else:
            result = bridge.recall(str(args.text), SystemClock().now(), int(args.limit))
            if result.unavailable is not None:
                print(
                    f"Memory recall unavailable: {safe_display(result.unavailable)}; "
                    f"finance evidence remains retained locally",
                    file=sys.stderr,
                )
                return 1
            print(
                f"Evidence recall: {len(result.items)} shown, {result.excluded} beyond limit; "
                f"delivery {delivered} delivered, {pending} pending, {failed} failed"
            )
            for item in result.items:
                print(
                    f"{item.at.value.isoformat()} · {safe_display(item.description)} "
                    f"[{safe_display(item.source)}]"
                )
                print(
                    f"  Subject: {item.subject.id.kind.value}/{item.subject.id.value}; "
                    f"evidence: {item.id.kind.value}/{item.id.value}; "
                    f"relevance: {', '.join(kind.value for kind in item.relevance)}"
                )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir = args.data_dir if isinstance(args.data_dir, Path) else default_data_dir()
    try:
        if args.command == "coinbase-preview":
            if args.demo:
                raise ValueError("Coinbase live access is unavailable in the synthetic demo")
            try:
                result = coinbase_preview(
                    key_file=args.key_file, max_fill_pages=int(args.fill_pages)
                )
            except CoinbaseReadError as exc:
                print(f"Coinbase: {exc}", file=sys.stderr)
                return 1
            retrieved_at = datetime.now(UTC).isoformat()
            print(
                f"Coinbase read-only preview — no finance records changed; retrieved {retrieved_at}"
            )
            shown = tuple(
                item
                for item in result.balances
                if args.all_accounts or item.available != 0 or item.hold != 0
            )
            print(f"Accounts: {len(result.balances)} returned; {len(shown)} shown")
            for item in shown:
                print(
                    f"{safe_display(item.name)} [{safe_display(item.currency)}] "
                    f"available {item.available}; hold {item.hold}"
                )
            coverage = "complete" if result.fills_complete else "partial"
            print(f"Recent fills: {len(result.fills)} returned ({coverage} coverage)")
            for item in result.fills[:20]:
                print(
                    f"{safe_display(item.trade_time)} | {safe_display(item.product_id)} | "
                    f"size {item.size} @ {item.price}; fee {item.commission}"
                )
            if len(result.fills) > 20:
                print(f"{len(result.fills) - 20} further fills omitted from display")
            if not result.fills_complete:
                print("Fill history is incomplete; no coverage or totals are asserted.")
            return 0
        service = open_service(data_dir, demo=bool(args.demo))
        if args.demo:
            print("SYNTHETIC DEMO — isolated from your personal book")
        command = str(args.command or "tui")
        if command in (
            "reconcile",
            "statement-add",
            "reconcile-match",
            "reconcile-exception",
            "reconcile-resolve",
            "reconcile-close",
            "memory-sync",
            "recall",
        ):
            return _run_new_commands(command, args, service, data_dir)
        if command == "tui":
            try:
                from personal_finance.app import FinanceApp
            except ModuleNotFoundError as exc:
                if exc.name and exc.name.startswith("textual"):
                    print("Install the interface with: uv sync --extra finance", file=sys.stderr)
                    return 2
                raise
            FinanceApp(service, demo=bool(args.demo)).run()
        elif command == "init":
            print(
                f"Book ready in {safe_display(str(data_dir))}; "
                "accounts and actual balances are not assumed."
            )
        elif command == "accounts":
            for account in service.accounts():
                print(
                    f"{safe_display(account.name)} | {account.account_type} | "
                    f"{account.currency} | {safe_display(account.id.value)}"
                )
        elif command == "add-account":
            result = service.create_account(
                str(args.name), AccountType(args.type), str(args.currency), bool(args.liquid)
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(
                f"Created {safe_display(result.value.name)} "
                f"[{safe_display(result.value.id.value)}]; recorded balance is zero."
            )
        elif command == "ledger":
            for entry in service.entries(str(args.search)):
                print(
                    f"{entry.sequence:>5} | {entry.content.effective_date} | "
                    f"{safe_display(entry.content.description)} | {safe_display(entry.id.value)}"
                )
                for posting in entry.content.postings:
                    print(
                        f"        {safe_display(posting.account.id.value)}  "
                        f"{posting.money.format()}"
                    )
        elif command == "entry":
            accounts = {account.name: account for account in service.accounts()}
            if args.debit not in accounts or args.credit not in accounts:
                raise ValueError("Use existing exact account names; see the accounts command.")
            amount = Money.from_decimal(str(args.amount), str(args.currency))
            if amount.minor <= 0:
                raise ValueError("Enter a positive amount; debit and credit determine its sign.")
            content = EntryContent(
                date.fromisoformat(str(args.date)),
                str(args.description),
                (
                    Posting(Ref(accounts[args.debit].id), amount),
                    Posting(Ref(accounts[args.credit].id), -amount),
                ),
            )
            prepared = service.prepare_entry(content)
            if isinstance(prepared, Err):
                print(safe_display(prepared.error.message), file=sys.stderr)
                return 1
            print_draft_review(prepared.value, tuple(accounts.values()))
            print("Prepared only. Review and post with the post command or the terminal interface.")
        elif command == "drafts":
            accounts = service.accounts()
            for draft in service.drafts():
                print_draft_review(draft, accounts)
        elif command == "post":
            draft = next(
                (item for item in service.drafts() if item.id.value == args.draft_id), None
            )
            if draft is None:
                raise ValueError("Draft not found.")
            print_draft_review(draft, service.accounts())
            if not sys.stdin.isatty():
                raise ValueError("Posting requires a local interactive terminal for exact review.")
            if input("Type POST to approve this exact content: ") != "POST":
                print("Draft retained; no ledger entry posted.")
                return 0
            posted = service.post_draft(draft.id, draft.content_hash, f"cli:{draft.id.value}")
            if isinstance(posted, Err):
                print(safe_display(posted.error.message), file=sys.stderr)
                return 1
            print(
                f"Posted entry {safe_display(posted.value.id.value)}; "
                f"commit #{posted.value.sequence}."
            )
        elif command == "reverse":
            original = next(
                (item for item in service.entries() if item.id.value == args.entry_id), None
            )
            if original is None:
                raise ValueError("Entry not found.")
            reversed_result = service.prepare_reversal(
                original.id, date.fromisoformat(str(args.date))
            )
            if isinstance(reversed_result, Err):
                print(safe_display(reversed_result.error.message), file=sys.stderr)
                return 1
            print_draft_review(reversed_result.value, service.accounts())
            print("Reversal prepared for review; original entry is unchanged.")
        elif command == "stage":
            report = stage_csv(service, args.path)
            print(f"{len(report.drafts)} drafts; {report.duplicates} duplicate rows skipped.")
            for issue in report.errors:
                print(f"Row {issue.row}: {safe_display(issue.message)}", file=sys.stderr)
            return 1 if report.errors else 0
        elif command == "export":
            count = export_csv(service, args.path)
            print(
                f"Exported {count} entries to {safe_display(str(args.path))}. "
                "Contains private financial data."
            )
        elif command == "explain":
            snapshot = service.snapshot()
            if isinstance(snapshot, Err):
                print(safe_display(snapshot.error.message), file=sys.stderr)
                return 1
            value = snapshot.value
            print(value.coverage)
            for balance in value.balances:
                print(f"{safe_display(balance.account.name)}: {balance.money.format()} recorded")
            provenance = value.provenance
            print(f"Calculation: {provenance.transform_name} v{provenance.transform_version}")
            print(f"Revision: {value.revision}; as of {provenance.at.value.isoformat()}")
            print(f"Context: {safe_display(str(provenance.context))}")
            print(
                f"Provenance: {safe_display(provenance.id.kind.value)}/"
                f"{safe_display(provenance.id.value)}"
            )
            print("Inputs:")
            for reference in provenance.inputs:
                print(
                    f"  {safe_display(reference.id.kind.value)}/{safe_display(reference.id.value)}"
                )
        elif command == "cash":
            snapshot = service.snapshot()
            if isinstance(snapshot, Err):
                print(safe_display(snapshot.error.message), file=sys.stderr)
                return 1
            view = next(
                item
                for item in snapshot.value.cash_plans
                if item.projection.horizon_days == args.horizon
            )
            projection = view.projection
            print(
                f"{projection.horizon_days}-day cash plan, "
                f"{projection.start_on} through {projection.through_on}; "
                f"ledger/planning revision {snapshot.value.revision}"
            )
            print("PROJECTION · manual schedules and posted ledger; future flow is not actual")
            print(f"Core provenance: {view.provenance.id.value}")
            for currency in projection.currencies:
                print(f"\n{currency.currency}")
                if currency.starting_balance is not None:
                    print(f"  Recorded opening: {currency.starting_balance.format()}")
                if currency.minimum_balance is not None:
                    print(
                        f"  Event-boundary low: {currency.minimum_balance.format()} "
                        f"on {currency.minimum_on}"
                    )
                if currency.safe_to_allocate is None:
                    print("  Safe to allocate: unavailable")
                else:
                    print(f"  Safe to allocate: {currency.safe_to_allocate.format()}")
                for reason in currency.missing_inputs:
                    print(f"  Missing: {safe_display(reason)}")
                for point in currency.points:
                    print(
                        f"  {point.on}  {safe_display(point.kind):18} "
                        f"{point.delta.format():>17}  balance {point.balance.format():>17}  "
                        f"{safe_display(point.label)}"
                    )
            if not projection.currencies:
                for reason in projection.missing_inputs:
                    print(f"Missing: {safe_display(reason)}")
        elif command == "plan-schedule":
            account = _liquid_account(service.accounts(), str(args.account))
            result = service.add_cash_schedule(
                account.id,
                str(args.label),
                Money.from_decimal(str(args.amount), str(args.currency)),
                date.fromisoformat(str(args.start_date)),
                Cadence(str(args.cadence)),
                str(args.timezone),
                args.end_date,
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Scheduled {safe_display(result.value.label)} [{result.value.id.value}].")
        elif command == "plan-allocation":
            if (args.schedule_id is None) != (args.due_on is None):
                raise ValueError("Use --schedule-id and --due-on together for a linked bill")
            linked = (
                ScheduleOccurrence(
                    Ref(Id(Kind("finance.cash_schedule"), str(args.schedule_id))), args.due_on
                )
                if args.schedule_id is not None
                else None
            )
            result = service.add_cash_allocation(
                str(args.label),
                Money.from_decimal(str(args.amount), str(args.currency)),
                args.active_from,
                linked,
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Protected cash [{result.value.id.value}]; no ledger outflow was created.")
        elif command == "plan-hold":
            result = service.add_cash_hold(
                str(args.label),
                Money.from_decimal(str(args.amount), str(args.currency)),
                args.active_from,
                args.release_date,
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Cash hold recorded [{result.value.id.value}].")
        elif command == "plan-floor":
            result = service.add_cash_floor(
                str(args.currency),
                args.effective_date,
                Money.from_decimal(str(args.amount), str(args.currency)),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Dated cash floor recorded [{result.value.id.value}].")
        elif command == "plan-observe":
            account = _liquid_account(service.accounts(), str(args.account))
            result = service.record_cash_balance(
                account.id,
                Money.from_decimal(str(args.amount), str(args.currency)),
                args.observed_on,
                args.fresh_through,
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Manual balance observation retained [{result.value.id.value}].")
        elif command == "plan-coverage":
            result = service.declare_cash_coverage(
                str(args.currency),
                args.as_of,
                args.through_date,
                bool(args.accounts_complete),
                bool(args.schedules_complete),
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Cash coverage declaration retained [{result.value.id.value}].")
        elif command == "plan-retire":
            kind = {
                "schedule": "finance.cash_schedule",
                "allocation": "finance.cash_allocation",
                "hold": "finance.cash_hold",
            }[str(args.kind)]
            result = service.retire_cash_item(
                Id(Kind(kind), str(args.id)), args.effective_date, str(args.reason)
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Cash item retired [{result.value.id.value}]; earlier history retained.")
        elif command == "debt-terms":
            account = _named_account(service.accounts(), str(args.account), AccountType.LIABILITY)
            result = service.set_debt_terms(
                account.id,
                _basis_points(str(args.apr)),
                Money.from_decimal(str(args.minimum), account.currency),
                int(args.due_day),
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Reviewed debt terms saved [{result.value.id.value}].")
        elif command == "debt-payment":
            account = _named_account(service.accounts(), str(args.account), AccountType.LIABILITY)
            result = service.classify_debt_payment(
                account.id,
                Id(Kind("finance.journal_entry"), str(args.entry_id)),
                Money.from_decimal(str(args.principal), account.currency),
                Money.from_decimal(str(args.interest), account.currency),
                Money.from_decimal(str(args.fees), account.currency),
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Posted debt payment classified [{result.value.id.value}].")
        elif command == "debts":
            snapshot = service.snapshot()
            if isinstance(snapshot, Err):
                print(safe_display(snapshot.error.message), file=sys.stderr)
                return 1
            view = snapshot.value.model_view
            assert view is not None
            for debt in view.debts.debts:
                apr = (
                    f"{Decimal(debt.terms.apr_basis_points) / 100}%"
                    if debt.terms is not None
                    else "APR unknown"
                )
                print(
                    f"{safe_display(debt.account.name)} | {debt.balance.format()} | {apr} | "
                    f"next due {debt.next_due or 'unknown'}"
                )
            for currency, apr in view.debts.weighted_apr_percent:
                apr_text = str(apr) if apr is not None else "not applicable or unknown"
                print(f"{currency} weighted APR: {apr_text}")
            comparison = service.compare_debt_scenarios(
                str(args.currency),
                Money.from_decimal(str(args.extra), str(args.currency)),
            )
            if isinstance(comparison, Err):
                print(safe_display(comparison.error.message), file=sys.stderr)
                return 1
            for scenario in comparison.value.scenarios:
                interest_text = (
                    scenario.total_interest.format()
                    if scenario.total_interest is not None
                    else "unknown"
                )
                print(
                    f"{scenario.strategy.value}: "
                    + (
                        f"{scenario.payoff_months} months, interest {interest_text}"
                        if scenario.payoff_months is not None
                        else f"unavailable — {scenario.missing_reason}"
                    )
                )
            print(f"Core provenance: {comparison.value.provenance.id.value}")
        elif command == "asset-position":
            account = _named_account(service.accounts(), str(args.account), AccountType.ASSET)
            result = service.create_asset_position(
                account.id, str(args.name), AssetCategory(str(args.category)), str(args.source)
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Asset position saved [{result.value.id.value}].")
        elif command == "asset-value":
            result = service.record_asset_valuation(
                Id(Kind("finance.asset_position"), str(args.position_id)),
                Money.from_decimal(str(args.value), str(args.currency)),
                Decimal(str(args.quantity)) if args.quantity is not None else None,
                Money.from_decimal(str(args.basis), str(args.currency))
                if args.basis is not None
                else None,
                args.observed_on,
                args.fresh_through,
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Manual valuation retained [{result.value.id.value}].")
        elif command == "asset-flow":
            result = service.classify_asset_flow(
                Id(Kind("finance.asset_position"), str(args.position_id)),
                Id(Kind("finance.journal_entry"), str(args.entry_id)),
                AssetFlowKind(str(args.kind)),
                Money.from_decimal(str(args.amount), str(args.currency)),
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Posted asset flow classified [{result.value.id.value}].")
        elif command == "asset-coverage":
            result = service.declare_asset_flow_coverage(
                Id(Kind("finance.asset_position"), str(args.position_id)),
                args.from_on,
                args.through_on,
                bool(args.complete),
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Asset flow review retained [{result.value.id.value}].")
        elif command == "assets":
            snapshot = service.snapshot()
            if isinstance(snapshot, Err):
                print(safe_display(snapshot.error.message), file=sys.stderr)
                return 1
            view = snapshot.value.model_view
            assert view is not None
            for asset in view.assets:
                period_text = asset.period_gain.format() if asset.period_gain else "unavailable"
                print(
                    f"{safe_display(asset.position.name)} | {asset.position.category.value} | "
                    f"value {asset.value.format() if asset.value else 'unavailable'} | "
                    f"period gain {period_text}"
                )
                for reason in asset.missing_inputs:
                    print(f"  Missing: {safe_display(reason)}")
            print(f"Core provenance: {view.provenance.id.value}")
        elif command == "node-create":
            accounts = service.accounts()
            revenue = _named_account(accounts, str(args.revenue_account), AccountType.INCOME)
            expense = _named_account(accounts, str(args.expense_account), AccountType.EXPENSE)
            cash = _liquid_account(accounts, str(args.cash_account)) if args.cash_account else None
            currency = revenue.currency
            result = service.create_income_node(
                str(args.name),
                revenue.id,
                expense.id,
                cash.id if cash else None,
                Money.from_decimal(str(args.monthly_revenue), currency)
                if args.monthly_revenue is not None
                else None,
                Money.from_decimal(str(args.monthly_expenses), currency)
                if args.monthly_expenses is not None
                else None,
                Money.from_decimal(str(args.milestone_target), currency)
                if args.milestone_target is not None
                else None,
                args.milestone_on,
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Income node saved [{result.value.id.value}].")
        elif command == "node-funding":
            result = service.classify_node_funding(
                Id(Kind("finance.income_node"), str(args.node_id)),
                Id(Kind("finance.journal_entry"), str(args.entry_id)),
                FundingKind(str(args.kind)),
                Money.from_decimal(str(args.amount), str(args.currency)),
                str(args.source),
            )
            if isinstance(result, Err):
                print(safe_display(result.error.message), file=sys.stderr)
                return 1
            print(f"Posted node funding classified [{result.value.id.value}].")
        elif command == "nodes":
            snapshot = service.snapshot()
            if isinstance(snapshot, Err):
                print(safe_display(snapshot.error.message), file=sys.stderr)
                return 1
            view = snapshot.value.model_view
            assert view is not None
            for node in view.nodes:
                print(
                    f"{safe_display(node.node.name)} | recorded 30-day revenue "
                    f"{node.revenue.format()} | operating expenses "
                    f"{node.operating_expenses.format()} | net {node.net_cash_flow.format()}"
                )
                print(
                    f"  Funding: capital {node.capital.format()}, withdrawals "
                    f"{node.withdrawals.format()} (excluded from operating net)"
                )
                for reason in node.missing_inputs:
                    print(f"  Missing: {safe_display(reason)}")
            print(f"Core provenance: {view.provenance.id.value}")
        return 0
    except (ValueError, OSError, InvalidOperation) as exc:
        print(f"Finance: {safe_display(str(exc))}", file=sys.stderr)
        return 1
