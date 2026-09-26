"""Pure statement-to-ledger comparison; suggestions never change matching state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.identity import Id
from personal_finance.domain.accounts import Account
from personal_finance.domain.ledger import JournalEntry
from personal_finance.domain.money import MAX_MINOR, Money
from personal_finance.domain.reconciliation import ReconcileRecords, Statement, StatementLine


def ledger_opening_balance(
    account: Account, entries: tuple[JournalEntry, ...], before: date
) -> Money:
    total = sum(
        posting.money.minor
        for entry in entries
        if entry.content.effective_date < before
        for posting in entry.content.postings
        if posting.account.id == account.id
    )
    return Money(total * account.display_sign, account.currency)


def ledger_closing_balance(account: Account, entries: tuple[JournalEntry, ...], on: date) -> Money:
    boundary = on + timedelta(days=1) if on < date.max else date.max
    if on == date.max:
        total = sum(
            posting.money.minor
            for entry in entries
            for posting in entry.content.postings
            if posting.account.id == account.id
        )
        return Money(total * account.display_sign, account.currency)
    return ledger_opening_balance(account, entries, boundary)


def account_entry_movement(account: Account, entry: JournalEntry) -> int:
    return sum(
        posting.money.minor * account.display_sign
        for posting in entry.content.postings
        if posting.account.id == account.id
    )


@dataclass(frozen=True, slots=True)
class MatchSuggestion:
    line_id: Id
    entry_id: Id
    score: int
    reason: str


@dataclass(frozen=True, slots=True)
class StatementView:
    statement: Statement
    account: Account
    ledger_opening: Money
    ledger_closing: Money
    opening_difference: Money
    closing_difference: Money
    line_difference: Money
    matched_count: int
    exception_count: int
    unresolved_lines: tuple[StatementLine, ...]
    unresolved_entries: tuple[JournalEntry, ...]
    duplicate_lines: tuple[StatementLine, ...]
    suggestions: tuple[MatchSuggestion, ...]
    unresolved_issues: int
    stale: bool
    closed: bool
    closable: bool
    missing_inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReconcilePortfolio:
    statements: tuple[StatementView, ...]
    closes: tuple[tuple[date, date], ...]
    later_corrections: tuple[JournalEntry, ...]


def project_reconciliation(
    accounts: tuple[Account, ...],
    entries: tuple[JournalEntry, ...],
    records: ReconcileRecords,
    today: date,
) -> ReconcilePortfolio:
    by_account = {account.id: account for account in accounts}
    entry_by_id = {entry.id: entry for entry in entries}
    match_by_line = {match.line.id: match for match in records.matches}
    exceptions = {(item.statement.id, item.target.id) for item in records.exceptions}
    resolved = {item.issue.id for item in records.resolutions}
    views: list[StatementView] = []
    latest: dict[tuple[Id, date, date], Statement] = {}
    for statement in records.statements:
        latest[(statement.account.id, statement.start_on, statement.through_on)] = statement
    for statement in latest.values():
        account = by_account[statement.account.id]
        lines = tuple(item for item in records.lines if item.statement.id == statement.id)
        period_entries = tuple(
            entry
            for entry in entries
            if statement.start_on <= entry.content.effective_date <= statement.through_on
            and account_entry_movement(account, entry) != 0
        )
        matched_entries = {
            match.entry.id for match in records.matches if match.statement.id == statement.id
        }
        unresolved_lines = tuple(
            line
            for line in lines
            if line.id not in match_by_line and (statement.id, line.id) not in exceptions
        )
        unresolved_entries = tuple(
            entry
            for entry in period_entries
            if entry.id not in matched_entries and (statement.id, entry.id) not in exceptions
        )
        seen: set[tuple[date, int, str]] = set()
        duplicates: list[StatementLine] = []
        for line in lines:
            signature = (line.on, line.movement.minor, line.description.casefold().strip())
            if signature in seen:
                duplicates.append(line)
            seen.add(signature)
        suggestions: list[MatchSuggestion] = []
        for line in unresolved_lines:
            candidates: list[MatchSuggestion] = []
            for entry in unresolved_entries:
                gap = abs((entry.content.effective_date - line.on).days)
                if gap > 3 or account_entry_movement(account, entry) != line.movement.minor:
                    continue
                score = 100 - gap * 10
                if (
                    line.description.casefold().strip()
                    == entry.content.description.casefold().strip()
                ):
                    score += 5
                candidates.append(
                    MatchSuggestion(
                        line.id,
                        entry.id,
                        score,
                        "Exact amount and date"
                        if gap == 0
                        else f"Exact amount; {gap}-day date gap",
                    )
                )
            suggestions.extend(sorted(candidates, key=lambda item: -item.score)[:3])
        opening = ledger_opening_balance(account, entries, statement.start_on)
        closing = ledger_closing_balance(account, entries, statement.through_on)
        opening_diff = statement.opening.minor - opening.minor
        closing_diff = statement.closing.minor - closing.minor
        line_diff = (
            statement.closing.minor
            - statement.opening.minor
            - sum(line.movement.minor for line in lines)
        )
        if any(abs(value) > MAX_MINOR for value in (opening_diff, closing_diff, line_diff)):
            raise ValueError("Statement difference exceeds the supported exact money range")
        unresolved_issues = sum(
            item.statement.id == statement.id and item.id not in resolved for item in records.issues
        )
        closed = any(
            close.start_on <= statement.start_on and statement.through_on <= close.through_on
            for close in records.closes
        )
        reasons: list[str] = []
        if not statement.lines_complete:
            reasons.append("Statement line inventory was not declared complete")
        if opening_diff:
            reasons.append("Statement opening differs from the posted ledger")
        if closing_diff:
            reasons.append("Statement closing differs from the posted ledger")
        if line_diff:
            reasons.append("Statement lines do not bridge opening to closing")
        if unresolved_lines:
            reasons.append(f"{len(unresolved_lines)} statement lines need matches or decisions")
        if unresolved_entries:
            reasons.append(f"{len(unresolved_entries)} posted entries need matches or decisions")
        if unresolved_issues:
            reasons.append(f"{unresolved_issues} recorded contradictions need resolutions")
        views.append(
            StatementView(
                statement,
                account,
                opening,
                closing,
                Money(opening_diff, account.currency),
                Money(closing_diff, account.currency),
                Money(line_diff, account.currency),
                len(tuple(item for item in records.matches if item.statement.id == statement.id)),
                len(
                    tuple(item for item in records.exceptions if item.statement.id == statement.id)
                ),
                unresolved_lines,
                unresolved_entries,
                tuple(duplicates),
                tuple(suggestions),
                unresolved_issues,
                statement.through_on < today - timedelta(days=45),
                closed,
                not reasons and not closed,
                tuple(reasons),
            )
        )
    closes = tuple((item.start_on, item.through_on) for item in records.closes)
    later = tuple(
        entry
        for entry in entries
        if entry.content.reversal_of is not None
        and entry.content.reversal_of.id in entry_by_id
        and any(
            start <= entry_by_id[entry.content.reversal_of.id].content.effective_date <= end
            and entry.content.effective_date > end
            for start, end in closes
        )
    )
    return ReconcilePortfolio(tuple(views), closes, later)
