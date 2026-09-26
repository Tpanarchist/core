"""Bounded CSV staging and create-only, spreadsheet-safe ledger export."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.identity import Ref
from core.result import Err
from core.value import Kind
from personal_finance.application.service import FinanceService
from personal_finance.domain.ledger import Draft, EntryContent, Posting
from personal_finance.domain.money import Money

IMPORT_FIELDS = ("date", "description", "account", "counter_account", "amount", "currency", "tags")
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 1000


@dataclass(frozen=True, slots=True)
class ImportIssue:
    row: int
    message: str


@dataclass(frozen=True, slots=True)
class ImportReport:
    drafts: tuple[Draft, ...]
    errors: tuple[ImportIssue, ...]
    duplicates: int


def stage_csv(service: FinanceService, path: Path) -> ImportReport:
    """Positive amounts debit account and credit counter_account. Never post.

    Identity is the exact source file digest plus physical CSV record number.
    Edited files have new source identities and must be reviewed for overlap.
    Valid rows survive bad rows; no credentials or external commands are read.
    """
    with path.open("rb") as handle:
        raw = handle.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        return ImportReport((), (ImportIssue(1, "Import exceeds the 2 MiB limit."),), 0)
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if tuple(reader.fieldnames or ()) != IMPORT_FIELDS:
            return ImportReport(
                (), (ImportIssue(1, "Expected exact header: " + ",".join(IMPORT_FIELDS)),), 0
            )
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        return ImportReport((), (ImportIssue(1, f"Invalid UTF-8 CSV: {exc}"),), 0)
    if len(rows) > MAX_IMPORT_ROWS:
        return ImportReport((), (ImportIssue(1, "Import exceeds the 1,000-row limit."),), 0)
    digest = hashlib.sha256(raw).hexdigest()
    accounts = service.accounts()
    seen = {draft.content.source for draft in service.drafts()}
    seen.update(entry.content.source for entry in service.entries())
    drafts: list[Draft] = []
    issues: list[ImportIssue] = []
    duplicates = 0
    for row_number, row in enumerate(rows, 2):
        source = f"csv:v1:{digest}:{row_number}"
        if source in seen:
            duplicates += 1
            continue
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Row must contain exactly seven fields.")
            left = tuple(account for account in accounts if account.name == row["account"])
            right = tuple(account for account in accounts if account.name == row["counter_account"])
            if len(left) != 1 or len(right) != 1:
                raise ValueError("Use existing, unambiguous account names in both account fields.")
            amount = Money.from_decimal(row["amount"], row["currency"])
            content = EntryContent(
                effective_date=date.fromisoformat(row["date"]),
                description=row["description"],
                postings=(Posting(Ref(left[0].id), amount), Posting(Ref(right[0].id), -amount)),
                tags=tuple(tag.strip() for tag in row["tags"].split(";") if tag.strip()),
                source=source,
            )
            result = service.prepare_entry(content)
            if isinstance(result, Err):
                if result.error.kind == Kind("finance.duplicate_import"):
                    duplicates += 1
                else:
                    issues.append(ImportIssue(row_number, result.error.message))
            else:
                drafts.append(result.value)
                seen.add(source)
        except (ValueError, OverflowError) as exc:
            issues.append(ImportIssue(row_number, str(exc)))
    return ImportReport(tuple(drafts), tuple(issues), duplicates)


def _safe_cell(value: str) -> str:
    # A tab/newline or formula introducer must not become spreadsheet execution.
    return (
        "'" + value
        if value.lstrip().startswith(("=", "+", "-", "@")) or (value.startswith(("\t", "\r", "\n")))
        else value
    )


def export_csv(service: FinanceService, path: Path) -> int:
    """Export one row per posting, retaining identities and effective/recorded time.

    This is an inspection export, not the simple two-account import schema.
    User-controlled text beginning with spreadsheet formulas is apostrophe escaped.
    """
    accounts = {account.id: account for account in service.accounts()}
    entries = service.entries()
    fields = (
        "schema_version",
        "entry_kind",
        "entry_id",
        "sequence",
        "effective_date",
        "recorded_at",
        "description",
        "principal",
        "source",
        "tags",
        "account_kind",
        "account_id",
        "account_namespace",
        "account_name",
        "currency",
        "minor_units",
        "reversal_kind",
        "reversal_id",
        "reversal_namespace",
    )
    # Open with mode 0600 even if the destination directory is shared. On
    # Windows the current user's inherited ACL governs the file instead.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for entry in entries:
            content = entry.content
            reversal = content.reversal_of
            for posting in content.postings:
                namespace = posting.account.namespace
                reversal_namespace = reversal.namespace if reversal else None
                writer.writerow(
                    (
                        1,
                        entry.id.kind.value,
                        _safe_cell(entry.id.value),
                        entry.sequence,
                        content.effective_date.isoformat(),
                        entry.recorded_at.value.isoformat(),
                        _safe_cell(content.description),
                        _safe_cell(entry.principal),
                        _safe_cell(content.source),
                        json.dumps(content.tags, ensure_ascii=False),
                        posting.account.id.kind.value,
                        _safe_cell(posting.account.id.value),
                        json.dumps(namespace.segments if namespace else None),
                        _safe_cell(accounts[posting.account.id].name),
                        posting.money.currency,
                        posting.money.minor,
                        reversal.id.kind.value if reversal else "",
                        _safe_cell(reversal.id.value) if reversal else "",
                        json.dumps(reversal_namespace.segments if reversal_namespace else None),
                    )
                )
    return len(entries)
