"""Finance-owned SQLite store with atomic posting and database invariants.

Connections belong to one operation and its calling thread. The constructor
performs no I/O. All writes use BEGIN IMMEDIATE; no network operation belongs
inside a finance transaction. An explicitly registered aggregate uses exact
Python integers and returns only a boolean; no SQLite sum can promote money
to floating point. Connections without the finance functions fail closed.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.context import Context
from core.effect import Effect
from core.event import Event
from core.identity import Id, Namespace
from core.provenance import Provenance
from core.time import WallInstant
from core.trace import Trace
from personal_finance.application.cash_codec import cash_record_hash
from personal_finance.application.evidence_codec import (
    canonical,
    context_data,
    decode_provenance,
    encode_effect,
    encode_event,
    encode_provenance,
    encode_trace,
)
from personal_finance.application.model_codec import (
    ModelRecord,
    decode_model_record,
    encode_model_record,
    model_record_hash,
)
from personal_finance.application.ports import (
    FinanceFailure,
    FinanceUnitOfWork,
    LedgerInputs,
    LocalApproval,
    StaleProjection,
)
from personal_finance.application.reconcile_codec import (
    ReconcileRecord,
    decode_reconcile_record,
    encode_reconcile_record,
    reconcile_record_hash,
)
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.assets import (
    AssetFlow,
    AssetFlowCoverage,
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
    MonthlyPolicy,
    ScheduleOccurrence,
)
from personal_finance.domain.codec import (
    decode_content,
    decode_id,
    decode_ref,
    encode_content,
    encode_id,
    encode_ref,
)
from personal_finance.domain.debts import DebtPaymentSplit, DebtTerms
from personal_finance.domain.ledger import Draft, EntryContent, JournalEntry, Posting
from personal_finance.domain.models import ModelRecords
from personal_finance.domain.money import MAX_MINOR, Money
from personal_finance.domain.nodes import IncomeNode, NodeFunding
from personal_finance.domain.reconciliation import (
    ReconcileClose,
    ReconcileException,
    ReconcileIssue,
    ReconcileRecords,
    ReconcileResolution,
    Statement,
    StatementLine,
    StatementMatch,
)

_SCHEMA = """
CREATE TABLE accounts (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    account_type TEXT NOT NULL
        CHECK(account_type IN ('asset','liability','equity','income','expense')),
    currency TEXT NOT NULL
        CHECK(currency IN ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    liquid INTEGER NOT NULL CHECK(liquid IN (0,1)),
    CHECK(length(trim(name)) > 0), CHECK(liquid=0 OR account_type='asset'),
    CHECK(json_extract(id,'$.kind')='finance.account')
) STRICT;
CREATE TABLE drafts (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','posted')),
    posted_entry TEXT, import_source TEXT UNIQUE,
    CHECK((status='pending' AND posted_entry IS NULL) OR
          (status='posted' AND posted_entry IS NOT NULL))
) STRICT;
CREATE TABLE entries (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    effective_date TEXT NOT NULL CHECK(length(effective_date)=10 AND
        date(effective_date) IS NOT NULL AND date(effective_date)=effective_date),
    description TEXT NOT NULL CHECK(length(trim(description))>0),
    tags TEXT NOT NULL, source TEXT NOT NULL CHECK(length(trim(source))>0),
    reversal_id TEXT REFERENCES entries(id), reversal_ref TEXT, recorded_at TEXT NOT NULL,
    principal TEXT NOT NULL CHECK(principal='local-human'),
    status TEXT NOT NULL DEFAULT 'staging' CHECK(status IN ('staging','posted')),
    CHECK((reversal_id IS NULL)=(reversal_ref IS NULL)),
    CHECK(json_extract(id,'$.kind')='finance.journal_entry')
) STRICT;
CREATE TABLE postings (
    entry_id TEXT NOT NULL REFERENCES entries(id), position INTEGER NOT NULL CHECK(position>=0),
    account_id TEXT NOT NULL REFERENCES accounts(id), account_ref TEXT NOT NULL,
    minor INTEGER NOT NULL CHECK(minor!=0 AND
        minor BETWEEN -9223372036854775807 AND 9223372036854775807),
    currency TEXT NOT NULL, PRIMARY KEY(entry_id,position)
) STRICT;
CREATE TABLE closed_periods (
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, reason TEXT NOT NULL,
    CHECK(date(start_date) IS NOT NULL AND date(end_date) IS NOT NULL AND
          date(start_date)=start_date AND date(end_date)=end_date AND start_date<=end_date),
    PRIMARY KEY(start_date,end_date)
) STRICT;
CREATE TABLE approvals (
    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL UNIQUE REFERENCES drafts(id),
    content_hash TEXT NOT NULL, action TEXT NOT NULL, principal TEXT NOT NULL,
    profile TEXT NOT NULL, policy_version TEXT NOT NULL,
    approved_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    consumed_by TEXT NOT NULL REFERENCES entries(id),
    CHECK(principal='local-human'), CHECK(action IN ('post_entry','reverse_entry'))
) STRICT;
CREATE TABLE idempotency (
    key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, entry_id TEXT NOT NULL REFERENCES entries(id)
) STRICT;
CREATE TABLE events (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
CREATE TABLE effects (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
CREATE TABLE audit (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL UNIQUE,
    principal TEXT NOT NULL, input_hash TEXT NOT NULL, payload TEXT NOT NULL
) STRICT;
CREATE TABLE provenance (id TEXT PRIMARY KEY, payload TEXT NOT NULL) STRICT;
CREATE TABLE projection_cache (
    revision INTEGER PRIMARY KEY, payload TEXT NOT NULL,
    provenance_id TEXT NOT NULL REFERENCES provenance(id)
) STRICT;
CREATE TABLE saved_filters (name TEXT PRIMARY KEY COLLATE NOCASE, query TEXT NOT NULL) STRICT;
CREATE TABLE revision (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), value INTEGER NOT NULL
) STRICT;
INSERT INTO revision VALUES(1,0);
CREATE UNIQUE INDEX one_posted_reversal ON entries(reversal_id)
    WHERE reversal_id IS NOT NULL AND status='posted';

CREATE TRIGGER accounts_no_update BEFORE UPDATE ON accounts
BEGIN SELECT RAISE(ABORT,'Accounts are immutable; create a new account'); END;
CREATE TRIGGER accounts_no_delete BEFORE DELETE ON accounts
BEGIN SELECT RAISE(ABORT,'Accounts are immutable'); END;
CREATE TRIGGER accounts_revision AFTER INSERT ON accounts
BEGIN UPDATE revision SET value=value+1 WHERE singleton=1; END;
CREATE TRIGGER entries_no_direct_post BEFORE INSERT ON entries WHEN NEW.status!='staging'
BEGIN SELECT RAISE(ABORT,'Entries must be finalized after postings'); END;
CREATE TRIGGER entries_insert_open BEFORE INSERT ON entries
WHEN EXISTS(SELECT 1 FROM closed_periods WHERE NEW.effective_date BETWEEN start_date AND end_date)
BEGIN SELECT RAISE(ABORT,'Effective date is in a closed period'); END;
CREATE TRIGGER entries_insert_reversal BEFORE INSERT ON entries
WHEN NEW.reversal_id IS NOT NULL AND
 NOT EXISTS(SELECT 1 FROM entries WHERE id=NEW.reversal_id AND status='posted')
BEGIN SELECT RAISE(ABORT,'A reversal must target a posted entry'); END;
CREATE TRIGGER entries_reversal_reference BEFORE INSERT ON entries WHEN
 NEW.reversal_id IS NOT NULL AND (
 json_extract(NEW.reversal_ref,'$.id.kind') IS NOT json_extract(NEW.reversal_id,'$.kind') OR
 json_extract(NEW.reversal_ref,'$.id.value') IS NOT json_extract(NEW.reversal_id,'$.value'))
BEGIN SELECT RAISE(ABORT,'Reversal reference must match reversal identity'); END;
CREATE TRIGGER entries_no_change BEFORE UPDATE ON entries
WHEN OLD.status='posted' OR NEW.id IS NOT OLD.id OR NEW.sequence IS NOT OLD.sequence OR
 NEW.effective_date IS NOT OLD.effective_date OR NEW.description IS NOT OLD.description OR
 NEW.tags IS NOT OLD.tags OR NEW.source IS NOT OLD.source OR
 NEW.reversal_id IS NOT OLD.reversal_id OR NEW.reversal_ref IS NOT OLD.reversal_ref OR
 NEW.recorded_at IS NOT OLD.recorded_at OR NEW.principal IS NOT OLD.principal
BEGIN SELECT RAISE(ABORT,'Entry content is immutable'); END;
CREATE TRIGGER entries_no_delete BEFORE DELETE ON entries WHEN OLD.status='posted'
BEGIN SELECT RAISE(ABORT,'Posted entries are immutable'); END;
CREATE TRIGGER finalize_open BEFORE UPDATE OF status ON entries WHEN NEW.status='posted' AND
 EXISTS(SELECT 1 FROM closed_periods WHERE NEW.effective_date BETWEEN start_date AND end_date)
BEGIN SELECT RAISE(ABORT,'Effective date is in a closed period'); END;
CREATE TRIGGER finalize_count BEFORE UPDATE OF status ON entries WHEN NEW.status='posted' AND
 (SELECT count(*) FROM postings WHERE entry_id=NEW.id)<2
BEGIN SELECT RAISE(ABORT,'At least two postings are required'); END;
CREATE TRIGGER finalize_balance BEFORE UPDATE OF status ON entries WHEN NEW.status='posted' AND
 EXISTS(SELECT currency FROM postings WHERE entry_id=NEW.id GROUP BY currency
        HAVING finance_balanced(minor)!=1)
BEGIN SELECT RAISE(ABORT,'Entry must balance exactly per currency'); END;
CREATE TRIGGER finalize_currency BEFORE UPDATE OF status ON entries WHEN NEW.status='posted' AND
 (SELECT count(DISTINCT currency) FROM postings WHERE entry_id=NEW.id)!=1
BEGIN SELECT RAISE(ABORT,'Mixed-currency entries require explicit exchange policy'); END;
CREATE TRIGGER finalize_account_range BEFORE UPDATE OF status ON entries
WHEN NEW.status='posted' AND
 EXISTS(SELECT p.account_id FROM postings p JOIN entries e ON e.id=p.entry_id
        WHERE e.status='posted' OR e.id=NEW.id GROUP BY p.account_id
        HAVING finance_within_range(p.minor)!=1)
BEGIN SELECT RAISE(ABORT,'Recorded account balance exceeds signed 64-bit minor-unit range'); END;
CREATE TRIGGER finalize_reversal BEFORE UPDATE OF status ON entries WHEN NEW.status='posted' AND
 NEW.reversal_id IS NOT NULL AND (
 (SELECT count(*) FROM postings WHERE entry_id=NEW.id)!=
 (SELECT count(*) FROM postings WHERE entry_id=NEW.reversal_id) OR
 EXISTS(SELECT 1 FROM postings p LEFT JOIN postings o
        ON o.entry_id=NEW.reversal_id AND o.position=p.position
        WHERE p.entry_id=NEW.id AND (o.position IS NULL OR
        p.account_id IS NOT o.account_id OR p.account_ref IS NOT o.account_ref OR
        p.currency IS NOT o.currency OR p.minor!=-o.minor)))
BEGIN SELECT RAISE(ABORT,'Reversal must exactly negate the original postings'); END;
CREATE TRIGGER finalize_authorized BEFORE UPDATE OF status ON entries
WHEN NEW.status='posted' AND finance_post_authorized()!=1
BEGIN SELECT RAISE(ABORT,'Finalization requires the trusted finance unit of work'); END;
CREATE TRIGGER finalize_revision AFTER UPDATE OF status ON entries WHEN NEW.status='posted'
BEGIN UPDATE revision SET value=value+1 WHERE singleton=1; END;
CREATE TRIGGER postings_insert_open BEFORE INSERT ON postings WHEN
 COALESCE((SELECT status FROM entries WHERE id=NEW.entry_id),'missing')!='staging'
BEGIN SELECT RAISE(ABORT,'Cannot add postings to a finalized or missing entry'); END;
CREATE TRIGGER postings_currency_insert BEFORE INSERT ON postings WHEN
 NEW.currency IS NOT (SELECT currency FROM accounts WHERE id=NEW.account_id)
BEGIN SELECT RAISE(ABORT,'Posting currency must match account currency'); END;
CREATE TRIGGER postings_reference_insert BEFORE INSERT ON postings WHEN
 json_extract(NEW.account_ref,'$.id.kind') IS NOT json_extract(NEW.account_id,'$.kind') OR
 json_extract(NEW.account_ref,'$.id.value') IS NOT json_extract(NEW.account_id,'$.value')
BEGIN SELECT RAISE(ABORT,'Posting reference must match account identity'); END;
CREATE TRIGGER postings_no_update BEFORE UPDATE ON postings
BEGIN SELECT RAISE(ABORT,'Postings are immutable, including reparenting'); END;
CREATE TRIGGER postings_no_delete BEFORE DELETE ON postings WHEN
 (SELECT status FROM entries WHERE id=OLD.entry_id)='posted'
BEGIN SELECT RAISE(ABORT,'Posted postings are immutable'); END;
CREATE TRIGGER drafts_no_content_change BEFORE UPDATE ON drafts WHEN
 OLD.status='posted' OR NEW.id IS NOT OLD.id OR NEW.content IS NOT OLD.content OR
 NEW.content_hash IS NOT OLD.content_hash OR NEW.created_at IS NOT OLD.created_at OR
 NEW.import_source IS NOT OLD.import_source OR NEW.status!='posted'
BEGIN SELECT RAISE(ABORT,'Draft content is immutable; prepare a new draft'); END;
CREATE TRIGGER drafts_no_delete BEFORE DELETE ON drafts
BEGIN SELECT RAISE(ABORT,'Draft history is immutable'); END;
"""


_IDENTITY_GUARDS = """
CREATE TRIGGER accounts_identity_insert BEFORE INSERT ON accounts
WHEN finance_valid_id(NEW.id,'finance.account')!=1
BEGIN SELECT RAISE(ABORT,'Account identity must use the canonical finance codec'); END;
CREATE TRIGGER drafts_identity_insert BEFORE INSERT ON drafts
WHEN finance_valid_id(NEW.id,'finance.action_draft')!=1
BEGIN SELECT RAISE(ABORT,'Draft identity must use the canonical finance codec'); END;
CREATE TRIGGER entries_identity_insert BEFORE INSERT ON entries
WHEN finance_valid_id(NEW.id,'finance.journal_entry')!=1 OR
 (NEW.reversal_ref IS NOT NULL AND finance_valid_ref(NEW.reversal_ref,NEW.reversal_id)!=1)
BEGIN SELECT RAISE(ABORT,'Entry identity and reversal reference must use canonical codecs'); END;
CREATE TRIGGER postings_reference_shape_insert BEFORE INSERT ON postings
WHEN finance_valid_ref(NEW.account_ref,NEW.account_id)!=1
BEGIN SELECT RAISE(ABORT,'Posting reference must use the canonical finance codec'); END;
"""


_CASH_SCHEMA = """
CREATE TABLE cash_schedules (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_schedule')=1),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    account_ref TEXT NOT NULL CHECK(finance_valid_ref(account_ref,account_id)=1),
    amount_minor INTEGER NOT NULL CHECK(amount_minor!=0 AND
        amount_minor BETWEEN -9223372036854775807 AND 9223372036854775807),
    currency TEXT NOT NULL CHECK(currency IN
        ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    start_date TEXT NOT NULL CHECK(finance_valid_day(start_date)=1),
    cadence TEXT NOT NULL CHECK(cadence IN ('once','weekly','monthly')),
    timezone TEXT NOT NULL CHECK(finance_valid_timezone(timezone)=1),
    label TEXT NOT NULL CHECK(length(trim(label))>0),
    source TEXT NOT NULL CHECK(length(trim(source))>0),
    end_date TEXT CHECK(end_date IS NULL OR
        (finance_valid_day(end_date)=1 AND end_date>=start_date)),
    monthly_policy TEXT NOT NULL CHECK(monthly_policy IN ('clamp_to_last_day','skip_missing_day')),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1)
) STRICT;
CREATE TABLE cash_allocations (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_allocation')=1),
    amount_minor INTEGER NOT NULL CHECK(amount_minor BETWEEN 1 AND 9223372036854775807),
    currency TEXT NOT NULL CHECK(currency IN
        ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    label TEXT NOT NULL CHECK(length(trim(label))>0),
    active_from TEXT NOT NULL CHECK(finance_valid_day(active_from)=1),
    schedule_id TEXT REFERENCES cash_schedules(id),
    schedule_ref TEXT,
    due_on TEXT,
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1),
    CHECK((schedule_id IS NULL AND schedule_ref IS NULL AND due_on IS NULL) OR
        (schedule_id IS NOT NULL AND schedule_ref IS NOT NULL AND due_on IS NOT NULL AND
         finance_valid_day(due_on)=1 AND finance_valid_ref(schedule_ref,schedule_id)=1))
) STRICT;
CREATE TABLE cash_holds (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_hold')=1),
    amount_minor INTEGER NOT NULL CHECK(amount_minor BETWEEN 1 AND 9223372036854775807),
    currency TEXT NOT NULL CHECK(currency IN
        ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    label TEXT NOT NULL CHECK(length(trim(label))>0),
    active_from TEXT NOT NULL CHECK(finance_valid_day(active_from)=1),
    release_date TEXT CHECK(release_date IS NULL OR
        (finance_valid_day(release_date)=1 AND release_date>active_from)),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1)
) STRICT;
CREATE TABLE cash_floor_changes (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_floor_change')=1),
    effective_date TEXT NOT NULL CHECK(finance_valid_day(effective_date)=1),
    amount_minor INTEGER NOT NULL CHECK(amount_minor BETWEEN 0 AND 9223372036854775807),
    currency TEXT NOT NULL CHECK(currency IN
        ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1)
) STRICT;
CREATE TABLE cash_observations (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_observation')=1),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    account_ref TEXT NOT NULL CHECK(finance_valid_ref(account_ref,account_id)=1),
    observed_minor INTEGER NOT NULL CHECK(observed_minor BETWEEN
        -9223372036854775807 AND 9223372036854775807),
    currency TEXT NOT NULL CHECK(currency IN
        ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    observed_at TEXT NOT NULL CHECK(finance_valid_wall(observed_at)=1),
    observed_on TEXT NOT NULL CHECK(finance_valid_day(observed_on)=1),
    fresh_through TEXT NOT NULL CHECK(finance_valid_day(fresh_through)=1 AND
        fresh_through>=observed_on),
    source TEXT NOT NULL CHECK(length(trim(source))>0),
    context TEXT NOT NULL CHECK(finance_valid_cash_context(context)=1),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1)
) STRICT;
CREATE TABLE cash_coverage (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_coverage')=1),
    currency TEXT NOT NULL CHECK(currency IN
        ('USD','EUR','GBP','CAD','AUD','CHF','JPY','KWD','BHD')),
    as_of TEXT NOT NULL CHECK(finance_valid_day(as_of)=1),
    through_date TEXT NOT NULL CHECK(finance_valid_day(through_date)=1 AND
        through_date>=as_of),
    accounts_complete INTEGER NOT NULL CHECK(accounts_complete IN (0,1)),
    schedules_complete INTEGER NOT NULL CHECK(schedules_complete IN (0,1)),
    recorded_at TEXT NOT NULL CHECK(finance_valid_wall(recorded_at)=1),
    source TEXT NOT NULL CHECK(length(trim(source))>0),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1)
) STRICT;
CREATE TABLE cash_coverage_accounts (
    coverage_id TEXT NOT NULL REFERENCES cash_coverage(id),
    position INTEGER NOT NULL CHECK(position>=0),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    account_ref TEXT NOT NULL CHECK(finance_valid_ref(account_ref,account_id)=1),
    PRIMARY KEY(coverage_id,position), UNIQUE(coverage_id,account_ref)
) STRICT;
CREATE TABLE cash_retirements (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.cash_retirement')=1),
    target_id TEXT NOT NULL UNIQUE,
    target_ref TEXT NOT NULL CHECK(finance_valid_ref(target_ref,target_id)=1),
    effective_date TEXT NOT NULL CHECK(finance_valid_day(effective_date)=1),
    recorded_at TEXT NOT NULL CHECK(finance_valid_wall(recorded_at)=1),
    principal TEXT NOT NULL CHECK(length(trim(principal))>0),
    reason TEXT NOT NULL CHECK(length(trim(reason))>0),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    codec_version INTEGER NOT NULL DEFAULT 1 CHECK(codec_version=1),
    CHECK(json_extract(target_id,'$.kind') IN
        ('finance.cash_schedule','finance.cash_allocation','finance.cash_hold'))
) STRICT;
CREATE TABLE cash_projection_cache (
    revision INTEGER NOT NULL, horizon INTEGER NOT NULL CHECK(horizon IN (30,60,90)),
    payload TEXT NOT NULL, provenance_id TEXT NOT NULL REFERENCES provenance(id),
    PRIMARY KEY(revision,horizon)
) STRICT;
CREATE TRIGGER cash_schedule_account BEFORE INSERT ON cash_schedules WHEN
    (SELECT liquid FROM accounts WHERE id=NEW.account_id)!=1 OR
    (SELECT currency FROM accounts WHERE id=NEW.account_id) IS NOT NEW.currency
BEGIN SELECT RAISE(ABORT,'Cash schedule requires a liquid account in its currency'); END;
CREATE TRIGGER cash_observation_account BEFORE INSERT ON cash_observations WHEN
    (SELECT liquid FROM accounts WHERE id=NEW.account_id)!=1 OR
    (SELECT currency FROM accounts WHERE id=NEW.account_id) IS NOT NEW.currency
BEGIN SELECT RAISE(ABORT,'Cash observation requires a liquid account in its currency'); END;
CREATE TRIGGER cash_allocation_occurrence BEFORE INSERT ON cash_allocations WHEN
    NEW.schedule_id IS NOT NULL AND
    (SELECT currency FROM cash_schedules WHERE id=NEW.schedule_id) IS NOT NEW.currency
BEGIN SELECT RAISE(ABORT,'Linked allocation currency must match its schedule'); END;
CREATE TRIGGER cash_coverage_account_currency BEFORE INSERT ON cash_coverage_accounts WHEN
    (SELECT currency FROM accounts WHERE id=NEW.account_id) IS NOT
    (SELECT currency FROM cash_coverage WHERE id=NEW.coverage_id) OR
    (SELECT liquid FROM accounts WHERE id=NEW.account_id)!=1
BEGIN SELECT RAISE(ABORT,'Coverage accounts must be liquid and match its currency'); END;
CREATE TRIGGER cash_retirement_target BEFORE INSERT ON cash_retirements WHEN
    NOT EXISTS(SELECT 1 FROM cash_schedules WHERE id=NEW.target_id) AND
    NOT EXISTS(SELECT 1 FROM cash_allocations WHERE id=NEW.target_id) AND
    NOT EXISTS(SELECT 1 FROM cash_holds WHERE id=NEW.target_id)
BEGIN SELECT RAISE(ABORT,'Retirement must target an existing cash record'); END;
"""


def _cash_append_only_triggers() -> str:
    tables = (
        "cash_schedules",
        "cash_allocations",
        "cash_holds",
        "cash_floor_changes",
        "cash_observations",
        "cash_coverage",
        "cash_coverage_accounts",
        "cash_retirements",
    )
    return (
        "\n".join(
            f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'Cash planning evidence is immutable'); END;"
            for table in tables
            for action in ("UPDATE", "DELETE")
        )
        + "\n"
        + "\n".join(
            f"CREATE TRIGGER {table}_authorized BEFORE INSERT ON {table} "
            "WHEN finance_cash_authorized()!=1 "
            "BEGIN SELECT RAISE(ABORT,'Cash inputs require the trusted finance unit of work'); END;"
            for table in tables
        )
        + "\n"
        + "\n".join(
            f"CREATE TRIGGER {table}_revision AFTER INSERT ON {table} "
            "BEGIN UPDATE revision SET value=value+1 WHERE singleton=1; END;"
            for table in tables
        )
    )


_MODEL_SCHEMA = """
CREATE TABLE debt_terms (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.debt_terms')=1),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE debt_payment_splits (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.debt_payment_split')=1),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    UNIQUE(account_id,entry_id)
) STRICT;
CREATE TABLE asset_positions (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.asset_position')=1),
    account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE asset_valuations (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.asset_valuation')=1),
    position_id TEXT NOT NULL REFERENCES asset_positions(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE asset_flows (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.asset_flow')=1),
    position_id TEXT NOT NULL REFERENCES asset_positions(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    UNIQUE(position_id,entry_id)
) STRICT;
CREATE TABLE asset_flow_coverage (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.asset_flow_coverage')=1),
    position_id TEXT NOT NULL REFERENCES asset_positions(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE income_nodes (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.income_node')=1),
    revenue_account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    expense_account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    cash_account_id TEXT UNIQUE REFERENCES accounts(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    CHECK(revenue_account_id!=expense_account_id)
) STRICT;
CREATE TABLE node_funding (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.node_funding')=1),
    node_id TEXT NOT NULL REFERENCES income_nodes(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    UNIQUE(node_id,entry_id)
) STRICT;
CREATE TABLE model_projection_cache (
    revision INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    provenance_id TEXT NOT NULL REFERENCES provenance(id)
) STRICT;
"""


_RECONCILE_SCHEMA = """
CREATE TABLE reconcile_statements (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_statement')=1),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    start_on TEXT NOT NULL CHECK(finance_valid_day(start_on)=1),
    through_on TEXT NOT NULL CHECK(finance_valid_day(through_on)=1),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    CHECK(start_on<=through_on)
) STRICT;
CREATE TABLE reconcile_lines (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_line')=1),
    statement_id TEXT NOT NULL REFERENCES reconcile_statements(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE reconcile_matches (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_match')=1),
    statement_id TEXT NOT NULL REFERENCES reconcile_statements(id),
    line_id TEXT NOT NULL UNIQUE REFERENCES reconcile_lines(id),
    entry_id TEXT NOT NULL REFERENCES entries(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    UNIQUE(statement_id,entry_id)
) STRICT;
CREATE TABLE reconcile_exceptions (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_exception')=1),
    statement_id TEXT NOT NULL REFERENCES reconcile_statements(id),
    target_id TEXT NOT NULL,
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    UNIQUE(statement_id,target_id)
) STRICT;
CREATE TABLE reconcile_issues (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_issue')=1),
    statement_id TEXT NOT NULL UNIQUE REFERENCES reconcile_statements(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE reconcile_resolutions (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_resolution')=1),
    issue_id TEXT NOT NULL UNIQUE REFERENCES reconcile_issues(id),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64)
) STRICT;
CREATE TABLE reconcile_closes (
    id TEXT PRIMARY KEY CHECK(finance_valid_id(id,'finance.reconcile_close')=1),
    start_on TEXT NOT NULL CHECK(finance_valid_day(start_on)=1),
    through_on TEXT NOT NULL CHECK(finance_valid_day(through_on)=1),
    payload TEXT NOT NULL, content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    UNIQUE(start_on,through_on), CHECK(start_on<=through_on)
) STRICT;
CREATE TABLE reconcile_close_statements (
    close_id TEXT NOT NULL REFERENCES reconcile_closes(id),
    statement_id TEXT NOT NULL REFERENCES reconcile_statements(id),
    PRIMARY KEY(close_id,statement_id)
) STRICT;
CREATE TABLE reconcile_projection_cache (
    revision INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    provenance_id TEXT NOT NULL REFERENCES provenance(id)
) STRICT;
CREATE TRIGGER closed_periods_no_overlap BEFORE INSERT ON closed_periods
WHEN EXISTS(SELECT 1 FROM closed_periods
            WHERE NEW.start_date<=end_date AND start_date<=NEW.end_date)
BEGIN SELECT RAISE(ABORT,'Closed periods may not overlap'); END;
"""

_RECONCILE_TABLES = (
    "reconcile_statements",
    "reconcile_lines",
    "reconcile_matches",
    "reconcile_exceptions",
    "reconcile_issues",
    "reconcile_resolutions",
    "reconcile_closes",
)

_MEMORY_OUTBOX_SCHEMA = """
CREATE TABLE memory_outbox (
    effect_id TEXT PRIMARY KEY REFERENCES effects(id),
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','delivered','failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
    last_error TEXT NOT NULL DEFAULT ''
) STRICT;
INSERT INTO memory_outbox(effect_id,payload)
SELECT id,payload FROM effects ORDER BY rowid;
"""


def _reconcile_triggers() -> str:
    return (
        "\n".join(
            f"CREATE TRIGGER {table}_{action.lower()}_guard BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'Reconciliation evidence is immutable'); END;"
            for table in (*_RECONCILE_TABLES, "reconcile_close_statements")
            for action in ("UPDATE", "DELETE")
        )
        + "\n"
        + "\n".join(
            f"CREATE TRIGGER {table}_trusted_insert BEFORE INSERT ON {table} "
            "WHEN finance_reconcile_authorized()!=1 "
            "BEGIN SELECT RAISE(ABORT,'Reconciliation requires the trusted unit of work'); END;"
            for table in (*_RECONCILE_TABLES, "reconcile_close_statements")
        )
        + "\n"
        + "\n".join(
            f"CREATE TRIGGER {table}_revision AFTER INSERT ON {table} "
            "BEGIN UPDATE revision SET value=value+1 WHERE singleton=1; END;"
            for table in _RECONCILE_TABLES
        )
    )


def _reconcile_storage(
    record: ReconcileRecord,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    if isinstance(record, Statement):
        return (
            "reconcile_statements",
            ("account_id", "start_on", "through_on"),
            (
                encode_id(record.account.id),
                record.start_on.isoformat(),
                record.through_on.isoformat(),
            ),
        )
    if isinstance(record, StatementLine):
        return "reconcile_lines", ("statement_id",), (encode_id(record.statement.id),)
    if isinstance(record, StatementMatch):
        return (
            "reconcile_matches",
            ("statement_id", "line_id", "entry_id"),
            (
                encode_id(record.statement.id),
                encode_id(record.line.id),
                encode_id(record.entry.id),
            ),
        )
    if isinstance(record, ReconcileException):
        return (
            "reconcile_exceptions",
            ("statement_id", "target_id"),
            (encode_id(record.statement.id), encode_id(record.target.id)),
        )
    if isinstance(record, ReconcileIssue):
        return "reconcile_issues", ("statement_id",), (encode_id(record.statement.id),)
    if isinstance(record, ReconcileResolution):
        return "reconcile_resolutions", ("issue_id",), (encode_id(record.issue.id),)
    return (
        "reconcile_closes",
        ("start_on", "through_on"),
        (record.start_on.isoformat(), record.through_on.isoformat()),
    )


_MODEL_TABLES = (
    "debt_terms",
    "debt_payment_splits",
    "asset_positions",
    "asset_valuations",
    "asset_flows",
    "asset_flow_coverage",
    "income_nodes",
    "node_funding",
)


def _model_append_only_triggers() -> str:
    return (
        "\n".join(
            f"CREATE TRIGGER {table}_{action.lower()}_guard BEFORE {action} ON {table} "
            "BEGIN SELECT RAISE(ABORT,'Finance model evidence is immutable'); END;"
            for table in _MODEL_TABLES
            for action in ("UPDATE", "DELETE")
        )
        + "\n"
        + "\n".join(
            f"CREATE TRIGGER {table}_trusted_insert BEFORE INSERT ON {table} "
            "WHEN finance_model_authorized()!=1 "
            "BEGIN SELECT RAISE(ABORT,'Finance models require the trusted unit of work'); END;"
            for table in _MODEL_TABLES
        )
        + "\n"
        + "\n".join(
            f"CREATE TRIGGER {table}_revision AFTER INSERT ON {table} "
            "BEGIN UPDATE revision SET value=value+1 WHERE singleton=1; END;"
            for table in _MODEL_TABLES
        )
    )


def _model_storage(record: ModelRecord) -> tuple[str, tuple[str, ...], tuple[str | None, ...]]:
    if isinstance(record, DebtTerms):
        return "debt_terms", ("account_id",), (encode_id(record.account.id),)
    if isinstance(record, DebtPaymentSplit):
        return (
            "debt_payment_splits",
            ("account_id", "entry_id"),
            (encode_id(record.account.id), encode_id(record.entry.id)),
        )
    if isinstance(record, AssetPosition):
        return "asset_positions", ("account_id",), (encode_id(record.account.id),)
    if isinstance(record, AssetValuation):
        return "asset_valuations", ("position_id",), (encode_id(record.position.id),)
    if isinstance(record, AssetFlow):
        return (
            "asset_flows",
            ("position_id", "entry_id"),
            (encode_id(record.position.id), encode_id(record.entry.id)),
        )
    if isinstance(record, AssetFlowCoverage):
        return "asset_flow_coverage", ("position_id",), (encode_id(record.position.id),)
    if isinstance(record, IncomeNode):
        return (
            "income_nodes",
            ("revenue_account_id", "expense_account_id", "cash_account_id"),
            (
                encode_id(record.revenue_account.id),
                encode_id(record.expense_account.id),
                encode_id(record.cash_account.id) if record.cash_account is not None else None,
            ),
        )
    return (
        "node_funding",
        ("node_id", "entry_id"),
        (encode_id(record.node.id), encode_id(record.entry.id)),
    )


def _valid_id(raw: object, expected_kind: object) -> int:
    if not isinstance(raw, str) or not isinstance(expected_kind, str):
        return 0
    try:
        identifier = decode_id(raw)
        return int(identifier.kind.value == expected_kind and encode_id(identifier) == raw)
    except ValueError, TypeError:
        return 0


def _valid_ref(raw: object, target_id: object) -> int:
    if not isinstance(raw, str) or not isinstance(target_id, str):
        return 0
    try:
        reference = decode_ref(raw)
        return int(reference.id == decode_id(target_id) and encode_ref(reference) == raw)
    except ValueError, TypeError:
        return 0


def _valid_wall(raw: object) -> int:
    if not isinstance(raw, str):
        return 0
    try:
        return int(_wall(raw).value.isoformat() == raw)
    except ValueError, TypeError:
        return 0


def _valid_day(raw: object) -> int:
    if not isinstance(raw, str):
        return 0
    try:
        return int(date.fromisoformat(raw).isoformat() == raw)
    except ValueError:
        return 0


def _valid_timezone(raw: object) -> int:
    if not isinstance(raw, str) or not raw.strip():
        return 0
    try:
        ZoneInfo(raw)
        return 1
    except ZoneInfoNotFoundError, ValueError:
        return 0


def _cash_context_value(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, dict):
        raw = cast(dict[str, object], value)
        items = raw.get("tuple")
        if set(raw) == {"tuple"} and isinstance(items, list):
            return tuple(_cash_context_value(item) for item in cast(list[object], items))
    raise ValueError("Unsupported cash observation context value")


def _cash_context(raw: str) -> Context:
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Cash observation context must be an object")
    data = cast(dict[str, object], parsed)
    if set(data) != {
        "as_of",
        "namespace",
        "source",
        "authority",
        "version",
        "units",
        "scope",
        "metadata",
    }:
        raise ValueError("Cash observation context has an unsupported shape")
    segments = data["namespace"]
    if segments is not None and not isinstance(segments, list):
        raise ValueError("Cash observation context namespace is invalid")
    namespace = None
    if segments is not None:
        items = cast(list[object], segments)
        if any(type(segment) is not str for segment in items):
            raise ValueError("Cash observation context namespace is invalid")
        namespace = Namespace(tuple(cast(str, segment) for segment in items))
    metadata = data["metadata"]
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError("Cash observation context metadata is invalid")
    metadata_values = cast(dict[str, object], metadata) if metadata is not None else None
    as_of = data["as_of"]
    if type(as_of) is not str:
        raise ValueError("Cash observation context time is invalid")
    result = Context(
        as_of=_wall(as_of),
        namespace=namespace,
        source=_cash_context_value(data["source"]),
        authority=_cash_context_value(data["authority"]),
        version=_cash_context_value(data["version"]),
        units=_cash_context_value(data["units"]),
        scope=_cash_context_value(data["scope"]),
        metadata={key: _cash_context_value(value) for key, value in metadata_values.items()}
        if metadata_values is not None
        else None,
    )
    if canonical(context_data(result)) != raw:
        raise ValueError("Cash observation context must use the canonical codec")
    return result


def _valid_cash_context(raw: object) -> int:
    if not isinstance(raw, str):
        return 0
    try:
        _cash_context(raw)
        return 1
    except ValueError, TypeError, KeyError:
        return 0


def _execute_statements(connection: sqlite3.Connection, script: str) -> None:
    """Apply DDL within the caller's transaction; executescript would commit it."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            connection.execute(pending)
            pending = ""
    if pending.strip():
        connection.execute(pending)


def _append_only_triggers() -> str:
    return "\n".join(
        f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
        "BEGIN SELECT RAISE(ABORT,'Audit/evidence history is immutable'); END;"
        for table in (
            "approvals",
            "idempotency",
            "events",
            "effects",
            "audit",
            "provenance",
            "closed_periods",
        )
        for action in ("UPDATE", "DELETE")
    )


def _wall(value: str) -> WallInstant:
    return WallInstant(datetime.fromisoformat(value))


class _ExactBalance:
    def __init__(self) -> None:
        self.total = 0

    def step(self, value: int) -> None:
        self.total += value

    def finalize(self) -> int:
        return int(self.total == 0)


class _ExactRange(_ExactBalance):
    def finalize(self) -> int:
        return int(-MAX_MINOR <= self.total <= MAX_MINOR)


class SQLiteUnitOfWork:
    """One transaction's repositories and explicit transactional EffectSink."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._posting_authorized = False
        self._cash_authorized = False
        self._model_authorized = False
        self._reconcile_authorized = False
        connection.create_function(
            "finance_post_authorized", 0, lambda: int(self._posting_authorized)
        )
        connection.create_function("finance_cash_authorized", 0, lambda: int(self._cash_authorized))
        connection.create_function(
            "finance_model_authorized", 0, lambda: int(self._model_authorized)
        )
        connection.create_function(
            "finance_reconcile_authorized", 0, lambda: int(self._reconcile_authorized)
        )

    @contextmanager
    def _cash_write(self) -> Generator[None]:
        try:
            self._cash_authorized = True
            yield
        finally:
            self._cash_authorized = False

    @contextmanager
    def _model_write(self) -> Generator[None]:
        try:
            self._model_authorized = True
            yield
        finally:
            self._model_authorized = False

    @contextmanager
    def _reconcile_write(self) -> Generator[None]:
        try:
            self._reconcile_authorized = True
            yield
        finally:
            self._reconcile_authorized = False

    def accounts(self) -> tuple[Account, ...]:
        return tuple(
            Account(
                decode_id(row["id"]),
                row["name"],
                AccountType(row["account_type"]),
                row["currency"],
                bool(row["liquid"]),
            )
            for row in self.connection.execute("SELECT * FROM accounts ORDER BY name,id")
        )

    def draft(self, draft_id: Id) -> Draft:
        row = self.connection.execute(
            "SELECT * FROM drafts WHERE id=?", (encode_id(draft_id),)
        ).fetchone()
        if row is None:
            raise FinanceFailure("Draft was not found")
        return Draft(
            decode_id(row["id"]),
            decode_content(row["content"]),
            _wall(row["created_at"]),
            row["content_hash"],
            row["status"],
        )

    def drafts(self) -> tuple[Draft, ...]:
        return tuple(
            self.draft(decode_id(row[0]))
            for row in self.connection.execute("SELECT id FROM drafts ORDER BY created_at,id")
        )

    def revision(self) -> int:
        return cast(
            int,
            self.connection.execute("SELECT value FROM revision WHERE singleton=1").fetchone()[0],
        )

    def entry(self, entry_id: Id) -> JournalEntry:
        row = self.connection.execute(
            "SELECT * FROM entries WHERE id=? AND status='posted'", (encode_id(entry_id),)
        ).fetchone()
        if row is None:
            raise FinanceFailure("Posted entry was not found")
        postings = tuple(
            Posting(decode_ref(post["account_ref"]), Money(post["minor"], post["currency"]))
            for post in self.connection.execute(
                "SELECT * FROM postings WHERE entry_id=? ORDER BY position", (encode_id(entry_id),)
            )
        )
        tags = cast(list[str], json.loads(row["tags"]))
        content = EntryContent(
            date.fromisoformat(row["effective_date"]),
            row["description"],
            postings,
            tuple(tags),
            row["source"],
            decode_ref(row["reversal_ref"]) if row["reversal_ref"] else None,
        )
        return JournalEntry(
            entry_id, content, _wall(row["recorded_at"]), row["principal"], row["sequence"]
        )

    def entries(self, search: str = "") -> tuple[JournalEntry, ...]:
        all_entries = tuple(
            self.entry(decode_id(row[0]))
            for row in self.connection.execute(
                "SELECT id FROM entries WHERE status='posted' ORDER BY sequence DESC"
            )
        )
        if not search.strip():
            return all_entries
        query = search.casefold().strip()
        names = {account.id: account.name for account in self.accounts()}
        return tuple(
            entry
            for entry in all_entries
            if query
            in " ".join(
                (
                    entry.content.description,
                    entry.content.source,
                    *entry.content.tags,
                    entry.content.effective_date.isoformat(),
                    *(names.get(post.account.id, "") for post in entry.content.postings),
                )
            ).casefold()
        )

    def add_account(self, account: Account) -> None:
        self.connection.execute(
            "INSERT INTO accounts VALUES(?,?,?,?,?)",
            (
                encode_id(account.id),
                account.name,
                account.account_type.value,
                account.currency,
                int(account.liquid),
            ),
        )

    def add_draft(self, draft: Draft) -> None:
        source = draft.content.source if draft.content.source.startswith("csv:") else None
        if (
            source
            and self.connection.execute(
                "SELECT 1 FROM drafts WHERE import_source=?", (source,)
            ).fetchone()
        ):
            raise FinanceFailure("duplicate_import: This CSV row has already been staged")
        self.connection.execute(
            "INSERT INTO drafts VALUES(?,?,?,?,?,?,?)",
            (
                encode_id(draft.id),
                encode_content(draft.content),
                draft.content_hash,
                draft.created_at.value.isoformat(),
                draft.status,
                None,
                source,
            ),
        )

    def check_open_date(self, effective_date: str) -> None:
        if self.connection.execute(
            "SELECT 1 FROM closed_periods WHERE ? BETWEEN start_date AND end_date",
            (effective_date,),
        ).fetchone():
            raise FinanceFailure(
                "Effective date is in a closed period; use an open correction date"
            )

    def check_open_range(self, start_date: str, end_date: str) -> None:
        if self.connection.execute(
            "SELECT 1 FROM closed_periods WHERE start_date<=? AND ?<=end_date",
            (end_date, start_date),
        ).fetchone():
            raise FinanceFailure("Statement month overlaps a closed period")

    def post_entry(self, entry: JournalEntry) -> JournalEntry:
        content = entry.content
        self.connection.execute(
            "INSERT INTO entries(id,effective_date,description,tags,source,reversal_id,"
            "reversal_ref,recorded_at,principal) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                encode_id(entry.id),
                content.effective_date.isoformat(),
                content.description,
                canonical(content.tags),
                content.source,
                encode_id(content.reversal_of.id) if content.reversal_of else None,
                encode_ref(content.reversal_of) if content.reversal_of else None,
                entry.recorded_at.value.isoformat(),
                entry.principal,
            ),
        )
        self.connection.executemany(
            "INSERT INTO postings VALUES(?,?,?,?,?,?)",
            (
                (
                    encode_id(entry.id),
                    index,
                    encode_id(post.account.id),
                    encode_ref(post.account),
                    post.money.minor,
                    post.money.currency,
                )
                for index, post in enumerate(content.postings)
            ),
        )
        try:
            self._posting_authorized = True
            self.connection.execute(
                "UPDATE entries SET status='posted' WHERE id=?", (encode_id(entry.id),)
            )
        finally:
            self._posting_authorized = False
        row = self.connection.execute(
            "SELECT sequence FROM entries WHERE id=?", (encode_id(entry.id),)
        ).fetchone()
        return replace(entry, sequence=cast(int, row[0]))

    def consume_approval(self, approval: LocalApproval, entry_id: Id) -> None:
        self.connection.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                encode_id(approval.id),
                encode_id(approval.draft_id),
                approval.content_hash,
                approval.action,
                approval.principal,
                approval.profile,
                approval.policy_version,
                approval.approved_at.value.isoformat(),
                approval.expires_at.value.isoformat(),
                encode_id(entry_id),
            ),
        )

    def finish_draft(self, draft_id: Id, entry_id: Id) -> None:
        self.connection.execute(
            "UPDATE drafts SET status='posted',posted_entry=? WHERE id=?",
            (
                encode_id(entry_id),
                encode_id(draft_id),
            ),
        )

    def replay(self, key: str, fingerprint: str) -> JournalEntry | None:
        row = self.connection.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise FinanceFailure("Idempotency key was already used for different content")
        return self.entry(decode_id(row["entry_id"]))

    def remember(self, key: str, fingerprint: str, entry_id: Id) -> None:
        self.connection.execute(
            "INSERT INTO idempotency VALUES(?,?,?)",
            (
                key,
                fingerprint,
                encode_id(entry_id),
            ),
        )

    def record(self, effect: Effect) -> None:
        payload = encode_effect(effect)
        self.connection.execute(
            "INSERT INTO effects VALUES(?,?)",
            (
                encode_id(effect.id),
                payload,
            ),
        )
        self.connection.execute(
            "INSERT INTO memory_outbox(effect_id,payload) VALUES(?,?)",
            (encode_id(effect.id), payload),
        )

    def record_event(self, event: Event) -> None:
        self.connection.execute(
            "INSERT INTO events VALUES(?,?)",
            (
                encode_id(event.id),
                encode_event(event),
            ),
        )

    def record_trace(self, trace: Trace, principal: str, input_hash: str) -> None:
        self.connection.execute(
            "INSERT INTO audit(trace_id,principal,input_hash,payload) VALUES(?,?,?,?)",
            (encode_id(trace.id), principal, input_hash, encode_trace(trace)),
        )

    def save_projection(self, revision: int, payload: str, provenance: Provenance) -> None:
        current = self.connection.execute(
            "SELECT value FROM revision WHERE singleton=1"
        ).fetchone()[0]
        if current != revision:
            raise StaleProjection("Ledger changed during calculation; refresh to calculate again")
        self.connection.execute(
            "INSERT INTO provenance VALUES(?,?)",
            (
                encode_id(provenance.id),
                encode_provenance(provenance),
            ),
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO projection_cache VALUES(?,?,?)",
            (
                revision,
                payload,
                encode_id(provenance.id),
            ),
        )

    def cash_records(self) -> CashRecords:
        schedules = tuple(
            CashSchedule(
                decode_id(row["id"]),
                decode_ref(row["account_ref"]),
                Money(row["amount_minor"], row["currency"]),
                date.fromisoformat(row["start_date"]),
                Cadence(row["cadence"]),
                row["timezone"],
                row["label"],
                row["source"],
                date.fromisoformat(row["end_date"]) if row["end_date"] else None,
                MonthlyPolicy(row["monthly_policy"]),
                bool(row["active"]),
            )
            for row in self.connection.execute(
                "SELECT * FROM cash_schedules ORDER BY start_date,id"
            )
        )
        allocations = tuple(
            CashAllocation(
                decode_id(row["id"]),
                Money(row["amount_minor"], row["currency"]),
                row["label"],
                date.fromisoformat(row["active_from"]),
                ScheduleOccurrence(
                    decode_ref(row["schedule_ref"]), date.fromisoformat(row["due_on"])
                )
                if row["schedule_ref"]
                else None,
                bool(row["active"]),
            )
            for row in self.connection.execute(
                "SELECT * FROM cash_allocations ORDER BY active_from,id"
            )
        )
        holds = tuple(
            CashHold(
                decode_id(row["id"]),
                Money(row["amount_minor"], row["currency"]),
                row["label"],
                date.fromisoformat(row["active_from"]),
                date.fromisoformat(row["release_date"]) if row["release_date"] else None,
                bool(row["active"]),
            )
            for row in self.connection.execute("SELECT * FROM cash_holds ORDER BY active_from,id")
        )
        floor_changes = tuple(
            CashFloorChange(
                decode_id(row["id"]),
                date.fromisoformat(row["effective_date"]),
                Money(row["amount_minor"], row["currency"]),
            )
            for row in self.connection.execute(
                "SELECT * FROM cash_floor_changes ORDER BY effective_date,id"
            )
        )
        observations = tuple(
            CashBalanceObservation(
                decode_id(row["id"]),
                decode_ref(row["account_ref"]),
                Money(row["observed_minor"], row["currency"]),
                _wall(row["observed_at"]),
                date.fromisoformat(row["observed_on"]),
                date.fromisoformat(row["fresh_through"]),
                row["source"],
                _cash_context(row["context"]),
            )
            for row in self.connection.execute("SELECT * FROM cash_observations ORDER BY rowid")
        )
        coverage = tuple(
            CashCoverage(
                decode_id(row["id"]),
                row["currency"],
                date.fromisoformat(row["as_of"]),
                date.fromisoformat(row["through_date"]),
                bool(row["accounts_complete"]),
                bool(row["schedules_complete"]),
                tuple(
                    decode_ref(ref[0])
                    for ref in self.connection.execute(
                        "SELECT account_ref FROM cash_coverage_accounts WHERE coverage_id=? "
                        "ORDER BY position",
                        (row["id"],),
                    )
                ),
                _wall(row["recorded_at"]),
                row["source"],
            )
            for row in self.connection.execute("SELECT * FROM cash_coverage ORDER BY rowid")
        )
        retirements = tuple(
            CashRetirement(
                decode_id(row["id"]),
                decode_ref(row["target_ref"]),
                date.fromisoformat(row["effective_date"]),
                _wall(row["recorded_at"]),
                row["principal"],
                row["reason"],
            )
            for row in self.connection.execute(
                "SELECT * FROM cash_retirements ORDER BY effective_date,id"
            )
        )
        records = CashRecords(
            schedules, allocations, holds, floor_changes, observations, coverage, retirements
        )
        stored_hashes = {
            row["id"]: row["content_hash"]
            for table in (
                "cash_schedules",
                "cash_allocations",
                "cash_holds",
                "cash_floor_changes",
                "cash_observations",
                "cash_coverage",
                "cash_retirements",
            )
            for row in self.connection.execute(f"SELECT id,content_hash FROM {table}")
        }
        for record in (
            *records.schedules,
            *records.allocations,
            *records.holds,
            *records.floor_changes,
            *records.observations,
            *records.coverage,
            *records.retirements,
        ):
            if stored_hashes.get(encode_id(record.id)) != cash_record_hash(record):
                raise FinanceFailure("Cash planning record failed content integrity verification")
        return records

    def add_cash_schedule(self, schedule: CashSchedule) -> None:
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_schedules(id,account_id,account_ref,amount_minor,currency,"
                "start_date,cadence,timezone,label,source,end_date,monthly_policy,active,"
                "content_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    encode_id(schedule.id),
                    encode_id(schedule.account.id),
                    encode_ref(schedule.account),
                    schedule.amount.minor,
                    schedule.amount.currency,
                    schedule.start_date.isoformat(),
                    schedule.cadence.value,
                    schedule.timezone,
                    schedule.label,
                    schedule.source,
                    schedule.end_date.isoformat() if schedule.end_date else None,
                    schedule.monthly_policy.value,
                    int(schedule.active),
                    cash_record_hash(schedule),
                ),
            )

    def add_cash_allocation(self, allocation: CashAllocation) -> None:
        occurrence = allocation.linked_occurrence
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_allocations(id,amount_minor,currency,label,active_from,"
                "schedule_id,schedule_ref,due_on,active,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    encode_id(allocation.id),
                    allocation.amount.minor,
                    allocation.amount.currency,
                    allocation.label,
                    allocation.active_from.isoformat(),
                    encode_id(occurrence.schedule.id) if occurrence else None,
                    encode_ref(occurrence.schedule) if occurrence else None,
                    occurrence.due_on.isoformat() if occurrence else None,
                    int(allocation.active),
                    cash_record_hash(allocation),
                ),
            )

    def add_cash_hold(self, hold: CashHold) -> None:
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_holds(id,amount_minor,currency,label,active_from,"
                "release_date,active,"
                "content_hash) VALUES(?,?,?,?,?,?,?,?)",
                (
                    encode_id(hold.id),
                    hold.amount.minor,
                    hold.amount.currency,
                    hold.label,
                    hold.active_from.isoformat(),
                    hold.release_date.isoformat() if hold.release_date else None,
                    int(hold.active),
                    cash_record_hash(hold),
                ),
            )

    def add_cash_floor_change(self, change: CashFloorChange) -> None:
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_floor_changes(id,effective_date,amount_minor,currency,"
                "content_hash) VALUES(?,?,?,?,?)",
                (
                    encode_id(change.id),
                    change.effective_date.isoformat(),
                    change.amount.minor,
                    change.amount.currency,
                    cash_record_hash(change),
                ),
            )

    def add_cash_observation(self, observation: CashBalanceObservation) -> None:
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_observations(id,account_id,account_ref,observed_minor,currency,"
                "observed_at,observed_on,fresh_through,source,context,content_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    encode_id(observation.id),
                    encode_id(observation.account.id),
                    encode_ref(observation.account),
                    observation.observed.minor,
                    observation.observed.currency,
                    observation.observed_at.value.isoformat(),
                    observation.observed_on.isoformat(),
                    observation.fresh_through.isoformat(),
                    observation.source,
                    canonical(context_data(observation.context)),
                    cash_record_hash(observation),
                ),
            )

    def add_cash_coverage(self, coverage: CashCoverage) -> None:
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_coverage(id,currency,as_of,through_date,accounts_complete,"
                "schedules_complete,recorded_at,source,content_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    encode_id(coverage.id),
                    coverage.currency,
                    coverage.as_of.isoformat(),
                    coverage.through_date.isoformat(),
                    int(coverage.accounts_complete),
                    int(coverage.schedules_complete),
                    coverage.recorded_at.value.isoformat(),
                    coverage.source,
                    cash_record_hash(coverage),
                ),
            )
            self.connection.executemany(
                "INSERT INTO cash_coverage_accounts VALUES(?,?,?,?)",
                (
                    (encode_id(coverage.id), position, encode_id(ref.id), encode_ref(ref))
                    for position, ref in enumerate(coverage.account_refs)
                ),
            )

    def add_cash_retirement(self, retirement: CashRetirement) -> None:
        with self._cash_write():
            self.connection.execute(
                "INSERT INTO cash_retirements(id,target_id,target_ref,effective_date,recorded_at,"
                "principal,reason,content_hash) VALUES(?,?,?,?,?,?,?,?)",
                (
                    encode_id(retirement.id),
                    encode_id(retirement.target.id),
                    encode_ref(retirement.target),
                    retirement.effective_date.isoformat(),
                    retirement.recorded_at.value.isoformat(),
                    retirement.principal,
                    retirement.reason,
                    cash_record_hash(retirement),
                ),
            )

    def model_records(self) -> ModelRecords:
        grouped: dict[str, tuple[ModelRecord, ...]] = {}
        for table in _MODEL_TABLES:
            items: list[ModelRecord] = []
            for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                record = decode_model_record(row["payload"])
                actual_table, columns, values = _model_storage(record)
                if (
                    actual_table != table
                    or row["id"] != encode_id(record.id)
                    or row["content_hash"] != model_record_hash(record)
                    or any(
                        row[column] != value for column, value in zip(columns, values, strict=True)
                    )
                ):
                    raise FinanceFailure(
                        "Stored finance model failed its canonical integrity check"
                    )
                items.append(record)
            grouped[table] = tuple(items)
        return ModelRecords(
            cast(tuple[DebtTerms, ...], grouped["debt_terms"]),
            cast(tuple[DebtPaymentSplit, ...], grouped["debt_payment_splits"]),
            cast(tuple[AssetPosition, ...], grouped["asset_positions"]),
            cast(tuple[AssetValuation, ...], grouped["asset_valuations"]),
            cast(tuple[AssetFlow, ...], grouped["asset_flows"]),
            cast(tuple[AssetFlowCoverage, ...], grouped["asset_flow_coverage"]),
            cast(tuple[IncomeNode, ...], grouped["income_nodes"]),
            cast(tuple[NodeFunding, ...], grouped["node_funding"]),
        )

    def add_model_record(self, record: ModelRecord) -> None:
        table, columns, values = _model_storage(record)
        names = ("id", *columns, "payload", "content_hash")
        placeholders = ",".join("?" for _ in names)
        with self._model_write():
            self.connection.execute(
                f"INSERT INTO {table}({','.join(names)}) VALUES({placeholders})",
                (
                    encode_id(record.id),
                    *values,
                    encode_model_record(record),
                    model_record_hash(record),
                ),
            )

    def reconcile_records(self) -> ReconcileRecords:
        grouped: dict[str, tuple[ReconcileRecord, ...]] = {}
        for table in _RECONCILE_TABLES:
            items: list[ReconcileRecord] = []
            for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                record = decode_reconcile_record(row["payload"])
                actual_table, columns, values = _reconcile_storage(record)
                if (
                    actual_table != table
                    or row["id"] != encode_id(record.id)
                    or row["content_hash"] != reconcile_record_hash(record)
                    or any(
                        row[column] != value for column, value in zip(columns, values, strict=True)
                    )
                ):
                    raise FinanceFailure("Stored reconciliation evidence failed integrity check")
                if isinstance(record, ReconcileClose):
                    actual_refs = tuple(
                        item[0]
                        for item in self.connection.execute(
                            "SELECT statement_id FROM reconcile_close_statements "
                            "WHERE close_id=? ORDER BY statement_id",
                            (encode_id(record.id),),
                        )
                    )
                    expected_refs = tuple(sorted(encode_id(ref.id) for ref in record.statements))
                    if actual_refs != expected_refs:
                        raise FinanceFailure(
                            "Stored close statement references failed integrity check"
                        )
                items.append(record)
            grouped[table] = tuple(items)
        return ReconcileRecords(
            cast(tuple[Statement, ...], grouped["reconcile_statements"]),
            cast(tuple[StatementLine, ...], grouped["reconcile_lines"]),
            cast(tuple[StatementMatch, ...], grouped["reconcile_matches"]),
            cast(tuple[ReconcileException, ...], grouped["reconcile_exceptions"]),
            cast(tuple[ReconcileIssue, ...], grouped["reconcile_issues"]),
            cast(tuple[ReconcileResolution, ...], grouped["reconcile_resolutions"]),
            cast(tuple[ReconcileClose, ...], grouped["reconcile_closes"]),
        )

    def add_reconcile_record(self, record: ReconcileRecord) -> None:
        table, columns, values = _reconcile_storage(record)
        names = ("id", *columns, "payload", "content_hash")
        placeholders = ",".join("?" for _ in names)
        with self._reconcile_write():
            self.connection.execute(
                f"INSERT INTO {table}({','.join(names)}) VALUES({placeholders})",
                (
                    encode_id(record.id),
                    *values,
                    encode_reconcile_record(record),
                    reconcile_record_hash(record),
                ),
            )
            if isinstance(record, ReconcileClose):
                self.connection.executemany(
                    "INSERT INTO reconcile_close_statements VALUES(?,?)",
                    ((encode_id(record.id), encode_id(ref.id)) for ref in record.statements),
                )
                self.connection.execute(
                    "INSERT INTO closed_periods VALUES(?,?,?)",
                    (record.start_on.isoformat(), record.through_on.isoformat(), record.reason),
                )

    def save_cash_projection(
        self, revision: int, horizon: int, payload: str, provenance: Provenance
    ) -> None:
        current = cast(
            int,
            self.connection.execute("SELECT value FROM revision WHERE singleton=1").fetchone()[0],
        )
        if current != revision:
            raise StaleProjection("Cash or ledger inputs changed during calculation; refresh")
        encoded = encode_provenance(provenance)
        identifier = encode_id(provenance.id)
        self.connection.execute(
            "INSERT INTO provenance(id,payload) VALUES(?,?) ON CONFLICT(id) DO NOTHING",
            (identifier, encoded),
        )
        retained = self.connection.execute(
            "SELECT payload FROM provenance WHERE id=?", (identifier,)
        ).fetchone()
        if retained[0] != encoded:
            raise FinanceFailure("Provenance identity was reused for different content")
        self.connection.execute(
            "INSERT INTO cash_projection_cache VALUES(?,?,?,?) "
            "ON CONFLICT(revision,horizon) DO UPDATE SET "
            "payload=excluded.payload,provenance_id=excluded.provenance_id",
            (revision, horizon, payload, identifier),
        )

    def save_model_projection(self, revision: int, payload: str, provenance: Provenance) -> None:
        current = cast(
            int,
            self.connection.execute("SELECT value FROM revision WHERE singleton=1").fetchone()[0],
        )
        if current != revision:
            raise StaleProjection("Debt, asset, node, or ledger inputs changed; refresh")
        encoded = encode_provenance(provenance)
        identifier = encode_id(provenance.id)
        self.connection.execute(
            "INSERT INTO provenance(id,payload) VALUES(?,?) ON CONFLICT(id) DO NOTHING",
            (identifier, encoded),
        )
        retained = self.connection.execute(
            "SELECT payload FROM provenance WHERE id=?", (identifier,)
        ).fetchone()
        if retained[0] != encoded:
            raise FinanceFailure("Provenance identity was reused for different content")
        self.connection.execute(
            "INSERT INTO model_projection_cache VALUES(?,?,?) "
            "ON CONFLICT(revision) DO UPDATE SET "
            "payload=excluded.payload,provenance_id=excluded.provenance_id",
            (revision, payload, identifier),
        )

    def save_reconcile_projection(
        self, revision: int, payload: str, provenance: Provenance
    ) -> None:
        current = cast(
            int,
            self.connection.execute("SELECT value FROM revision WHERE singleton=1").fetchone()[0],
        )
        if current != revision:
            raise StaleProjection("Reconciliation or ledger inputs changed; refresh")
        encoded = encode_provenance(provenance)
        identifier = encode_id(provenance.id)
        self.connection.execute(
            "INSERT INTO provenance(id,payload) VALUES(?,?) ON CONFLICT(id) DO NOTHING",
            (identifier, encoded),
        )
        retained = self.connection.execute(
            "SELECT payload FROM provenance WHERE id=?", (identifier,)
        ).fetchone()
        if retained[0] != encoded:
            raise FinanceFailure("Provenance identity was reused for different content")
        self.connection.execute(
            "INSERT INTO reconcile_projection_cache VALUES(?,?,?) "
            "ON CONFLICT(revision) DO UPDATE SET "
            "payload=excluded.payload,provenance_id=excluded.provenance_id",
            (revision, payload, identifier),
        )

    def save_filter(self, name: str, query: str) -> None:
        self.connection.execute(
            "INSERT INTO saved_filters VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET query=excluded.query",
            (name, query),
        )


class FinanceStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.create_aggregate("finance_balanced", 1, _ExactBalance)
        connection.create_aggregate("finance_within_range", 1, _ExactRange)
        connection.create_function("finance_valid_id", 2, _valid_id, deterministic=True)
        connection.create_function("finance_valid_ref", 2, _valid_ref, deterministic=True)
        connection.create_function("finance_valid_day", 1, _valid_day, deterministic=True)
        connection.create_function("finance_valid_wall", 1, _valid_wall, deterministic=True)
        connection.create_function("finance_valid_timezone", 1, _valid_timezone, deterministic=True)
        connection.create_function(
            "finance_valid_cash_context", 1, _valid_cash_context, deterministic=True
        )
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY) STRICT"
            )
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
            if versions and (
                versions[0] != 1 or any(item not in (1, 2, 3, 4, 5, 6) for item in versions)
            ):
                raise FinanceFailure("Unsupported finance schema version; do not downgrade")
            if not versions:
                _execute_statements(connection, _SCHEMA + _append_only_triggers())
                connection.execute("INSERT INTO schema_migrations VALUES(1)")
            if 2 not in versions:
                _execute_statements(connection, _IDENTITY_GUARDS)
                connection.execute("INSERT INTO schema_migrations VALUES(2)")
            if 3 not in versions:
                _execute_statements(connection, _CASH_SCHEMA + _cash_append_only_triggers())
                connection.execute("INSERT INTO schema_migrations VALUES(3)")
            if 4 not in versions:
                _execute_statements(connection, _MODEL_SCHEMA + _model_append_only_triggers())
                connection.execute("INSERT INTO schema_migrations VALUES(4)")
            if 5 not in versions:
                _execute_statements(connection, _RECONCILE_SCHEMA + _reconcile_triggers())
                connection.execute("INSERT INTO schema_migrations VALUES(5)")
            if 6 not in versions:
                _execute_statements(connection, _MEMORY_OUTBOX_SCHEMA)
                connection.execute("INSERT INTO schema_migrations VALUES(6)")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Generator[FinanceUnitOfWork]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield SQLiteUnitOfWork(connection)
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise FinanceFailure(str(exc)) from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read(self) -> Generator[SQLiteUnitOfWork]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            yield SQLiteUnitOfWork(connection)
        except sqlite3.Error as exc:
            raise FinanceFailure(str(exc)) from exc
        finally:
            connection.rollback()
            connection.close()

    def accounts(self) -> tuple[Account, ...]:
        with self._read() as unit:
            return unit.accounts()

    def entries(self, search: str = "") -> tuple[JournalEntry, ...]:
        with self._read() as unit:
            return unit.entries(search)

    def drafts(self) -> tuple[Draft, ...]:
        with self._read() as unit:
            return unit.drafts()

    def inputs(self) -> LedgerInputs:
        with self._read() as unit:
            revision = cast(
                int,
                unit.connection.execute("SELECT value FROM revision WHERE singleton=1").fetchone()[
                    0
                ],
            )
            return LedgerInputs(
                unit.accounts(),
                unit.entries(),
                unit.drafts(),
                revision,
                unit.cash_records(),
                unit.model_records(),
                unit.reconcile_records(),
            )

    def cash_records(self) -> CashRecords:
        with self._read() as unit:
            return unit.cash_records()

    def model_records(self) -> ModelRecords:
        with self._read() as unit:
            return unit.model_records()

    def reconcile_records(self) -> ReconcileRecords:
        with self._read() as unit:
            return unit.reconcile_records()

    def filters(self) -> tuple[tuple[str, str], ...]:
        with self._read() as unit:
            return tuple(
                (row[0], row[1])
                for row in unit.connection.execute(
                    "SELECT name,query FROM saved_filters ORDER BY name"
                )
            )

    def provenance(self, provenance_id: Id) -> Provenance | None:
        with self._read() as unit:
            row = unit.connection.execute(
                "SELECT payload FROM provenance WHERE id=?", (encode_id(provenance_id),)
            ).fetchone()
            return decode_provenance(row[0]) if row else None

    def pending_memory_effects(self, limit: int = 100) -> tuple[tuple[str, str], ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("Memory delivery limit must be between 1 and 1,000")
        with self._read() as unit:
            rows = tuple(
                unit.connection.execute(
                    "SELECT o.effect_id,o.payload,e.payload FROM memory_outbox o "
                    "JOIN effects e ON e.id=o.effect_id "
                    "WHERE o.status IN ('pending','failed') "
                    "ORDER BY CASE o.status WHEN 'pending' THEN 0 ELSE 1 END, o.rowid LIMIT ?",
                    (limit,),
                )
            )
            if any(row[1] != row[2] for row in rows):
                raise FinanceFailure("Memory outbox evidence differs from finance audit")
            return tuple((str(row[0]), str(row[1])) for row in rows)

    def mark_memory_delivery(self, effect_id: str, error: str | None) -> None:
        with self.transaction() as unit:
            updated = cast(SQLiteUnitOfWork, unit).connection.execute(
                "UPDATE memory_outbox SET status=?, attempts=attempts+1, last_error=? "
                "WHERE effect_id=? AND status!='delivered'",
                ("failed" if error else "delivered", (error or "")[:500], effect_id),
            )
            if updated.rowcount != 1:
                raise FinanceFailure("Memory delivery item was not pending")

    def memory_delivery_status(self) -> tuple[int, int, int]:
        with self._read() as unit:
            counts = {
                str(row[0]): int(row[1])
                for row in unit.connection.execute(
                    "SELECT status,COUNT(*) FROM memory_outbox GROUP BY status"
                )
            }
            return (counts.get("pending", 0), counts.get("delivered", 0), counts.get("failed", 0))
