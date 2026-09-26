"""Finance-owned categories; these extend Core's open Kind vocabulary."""

from core.value import Kind

ACCOUNT = Kind("finance.account")
JOURNAL_ENTRY = Kind("finance.journal_entry")
ACTION_DRAFT = Kind("finance.action_draft")
IMPORT_CANDIDATE = Kind("finance.import_candidate")
FORECAST_RUN = Kind("finance.forecast_run")
BOOK = Kind("finance.book")
APPROVAL = Kind("finance.approval")
AUDIT = Kind("finance.audit")
ENTRY_POSTED = Kind("finance.ledger.entry_posted")
ENTRY_REVERSED = Kind("finance.ledger.entry_reversed")
BALANCE_PROJECTION = Kind("finance.balance_projection")
