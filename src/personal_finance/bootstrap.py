"""Explicit application startup; no runtime dependencies are created on import."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from core.identity import Ref, UuidIdSource
from core.result import Err
from core.time import SystemClock, SystemMonotonicClock
from core.value import Kind
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.application.service import FinanceService
from personal_finance.domain.accounts import AccountType
from personal_finance.domain.ledger import EntryContent, Posting
from personal_finance.domain.money import Money


def default_data_dir() -> Path:
    """Resolve user-private storage only at the explicit startup boundary."""
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "personal-finance"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / (
        "personal-finance"
    )


def open_service(data_dir: Path, *, demo: bool = False) -> FinanceService:
    data_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        data_dir.chmod(0o700)
    # Demo and personal never share a file, even under a custom data directory.
    store = FinanceStore(data_dir / ("demo.sqlite3" if demo else "personal.sqlite3"))
    store.initialize()
    ids = UuidIdSource()
    clock = SystemClock()
    service = FinanceService(
        store,
        clock,
        SystemMonotonicClock(ids.new(Kind("finance.clock"))),
        ids,
        profile="demo" if demo else "personal",
    )
    if demo:
        seed_demo(service, clock.now().value.date())
    return service


def seed_demo(service: FinanceService, today: date) -> None:
    """Explicit opt-in synthetic example; durable keys permit safe restart."""
    definitions = (
        ("Demo checking", AccountType.ASSET, True),
        ("Demo savings", AccountType.ASSET, True),
        ("Demo salary", AccountType.INCOME, False),
        ("Demo living expenses", AccountType.EXPENSE, False),
        ("Demo credit card", AccountType.LIABILITY, False),
    )
    accounts = {account.name: account for account in service.accounts()}
    for name, kind, liquid in definitions:
        if name not in accounts:
            result = service.create_account(name, kind, "USD", liquid)
            if isinstance(result, Err):
                raise ValueError(result.error.message)
            accounts[name] = result.value
    samples = (
        (28, "Synthetic paycheck", "Demo checking", "Demo salary", 420000),
        (26, "Synthetic savings transfer", "Demo savings", "Demo checking", 100000),
        (24, "Synthetic rent", "Demo living expenses", "Demo checking", 135000),
        (21, "Synthetic groceries", "Demo living expenses", "Demo checking", 12650),
        (18, "Synthetic card purchase", "Demo living expenses", "Demo credit card", 8500),
        (14, "Synthetic paycheck", "Demo checking", "Demo salary", 420000),
        (10, "Synthetic utilities", "Demo living expenses", "Demo checking", 17480),
        (7, "Synthetic savings transfer", "Demo savings", "Demo checking", 125000),
        (3, "Synthetic groceries", "Demo living expenses", "Demo checking", 15420),
        (1, "Synthetic card payment", "Demo credit card", "Demo checking", 8500),
    )
    seen = {entry.content.source for entry in service.entries()}
    for index, (days, description, debit, credit, minor) in enumerate(samples):
        source = f"demo:v1:{index}"
        if source in seen:
            continue
        pending = next(
            (draft for draft in service.drafts() if draft.content.source == source), None
        )
        if pending is None:
            content = EntryContent(
                today - timedelta(days=days),
                description,
                (
                    Posting(Ref(accounts[debit].id), Money(minor, "USD")),
                    Posting(Ref(accounts[credit].id), Money(-minor, "USD")),
                ),
                tags=("synthetic",),
                source=source,
            )
            prepared = service.prepare_entry(content)
            if isinstance(prepared, Err):
                raise ValueError(prepared.error.message)
            pending = prepared.value
        posted = service.post_draft(pending.id, pending.content_hash, source)
        if isinstance(posted, Err):
            raise ValueError(posted.error.message)
