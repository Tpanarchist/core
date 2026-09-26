"""Immutable accounting intentions and posted facts, each with explicit identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.accounts import Account
from personal_finance.domain.kinds import ACCOUNT, ACTION_DRAFT, JOURNAL_ENTRY
from personal_finance.domain.money import MAX_MINOR, Money


def _snapshot_ref(reference: Ref, kind: Kind) -> Ref:
    if type(reference) is not Ref or type(reference.id) is not Id or reference.id.kind != kind:
        raise ValueError(f"Reference must target {kind}")
    namespace = reference.namespace
    if namespace is not None:
        namespace = Namespace(tuple(namespace.segments))
        if any(type(segment) is not str for segment in namespace.segments):
            raise TypeError("Namespace segments must be strings")
    return Ref(reference.id, namespace)


@dataclass(frozen=True, slots=True)
class Posting:
    account: Ref
    money: Money

    def __post_init__(self) -> None:
        object.__setattr__(self, "account", _snapshot_ref(self.account, ACCOUNT))
        if type(self.money) is not Money:
            raise TypeError("Posting amount must be Money")


@dataclass(frozen=True, slots=True)
class EntryContent:
    effective_date: date
    description: str
    postings: tuple[Posting, ...]
    tags: tuple[str, ...] = ()
    source: str = "manual"
    reversal_of: Ref | None = None

    def __post_init__(self) -> None:
        if type(self.effective_date) is not date:
            raise TypeError("Effective date must be a date without a time")
        if type(self.description) is not str or not self.description.strip():
            raise ValueError("Entry description must not be empty")
        if type(self.source) is not str or not self.source.strip():
            raise ValueError("Entry source must not be empty")
        postings = tuple(self.postings)
        tags = tuple(self.tags)
        object.__setattr__(self, "postings", postings)
        object.__setattr__(self, "tags", tags)
        if len(postings) < 2:
            raise ValueError("An entry needs at least two postings")
        if any(type(posting) is not Posting for posting in postings):
            raise TypeError("Entry postings must be Posting values")
        if any(posting.money.minor == 0 for posting in postings):
            raise ValueError("Zero-value postings are not permitted")
        if len({posting.money.currency for posting in postings}) != 1:
            raise ValueError("Mixed-currency entries require explicit exchange policy")
        if sum(posting.money.minor for posting in postings) != 0:
            raise ValueError("Entry postings must balance exactly")
        if any(type(tag) is not str or not tag.strip() for tag in tags):
            raise ValueError("Entry tags must be nonempty strings")
        if self.reversal_of is not None:
            object.__setattr__(self, "reversal_of", _snapshot_ref(self.reversal_of, JOURNAL_ENTRY))


def validate_accounts(content: EntryContent, accounts: tuple[Account, ...]) -> None:
    """Validate against the authoritative account inventory at commit time."""
    by_id = {account.id: account for account in accounts}
    if len(by_id) != len(accounts):
        raise ValueError("Account inventory contains duplicate identities")
    for posting in content.postings:
        account = by_id.get(posting.account.id)
        if account is None:
            raise ValueError(f"Unknown posting account: {posting.account.id.value}")
        if posting.money.currency != account.currency:
            raise ValueError(f"Posting currency does not match account: {account.name}")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: Id
    content: EntryContent
    recorded_at: WallInstant
    principal: str
    sequence: int

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != JOURNAL_ENTRY:
            raise ValueError("Journal entry requires a finance.journal_entry identity")
        if type(self.content) is not EntryContent or type(self.recorded_at) is not WallInstant:
            raise TypeError("Journal entry requires immutable content and a WallInstant")
        if type(self.principal) is not str or not self.principal.strip():
            raise ValueError("Posted entries require an authorizing principal")
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_MINOR:
            raise ValueError("Entry sequence must be a positive signed 64-bit integer")
        if self.content.reversal_of is not None and self.content.reversal_of.id == self.id:
            raise ValueError("An entry cannot reverse itself")


@dataclass(frozen=True, slots=True)
class Draft:
    id: Id
    content: EntryContent
    created_at: WallInstant
    content_hash: str
    status: str = "pending"

    def __post_init__(self) -> None:
        if type(self.id) is not Id or self.id.kind != ACTION_DRAFT:
            raise ValueError("Draft requires a finance.action_draft identity")
        if type(self.content) is not EntryContent or type(self.created_at) is not WallInstant:
            raise TypeError("Draft requires immutable content and a WallInstant")
        if (
            type(self.content_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.content_hash) is None
        ):
            raise ValueError("Draft content hash must be a canonical SHA-256 digest")
        if self.status not in {
            "pending",
            "approved",
            "posted",
            "rejected",
            "cancelled",
            "expired",
            "superseded",
        }:
            raise ValueError("Invalid draft status")
