# Core

A personal foundational Python library, built from an explicit ontology rather than from utility code.

Read in this order:

## Core foundation

1. [`SPECIFICATION.md`](SPECIFICATION.md) — the 16 concepts, the 3 conceptual levels, and what each concept means.
2. [`LAWS.md`](LAWS.md) — the 20 laws those concepts obey.
3. [`ARCHITECTURE.md`](ARCHITECTURE.md) — the Python package derived from the specification: module layout, dependency graph, and the runtime contracts that realize the semantics.

## Memory layer

Derived constructions over Core; a consumer of the closed substrate, not a continuation of Core's own ontology.

1. [`MEMORY_SPECIFICATION.md`](MEMORY_SPECIFICATION.md) — what memory means in this system: five new derived constructions, reused Core vocabulary, and what Memory explicitly does not do.
2. [`MEMORY_LAWS.md`](MEMORY_LAWS.md) — the durable rules Memory obeys, cross-referenced to their adversarial evidence.
3. [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md) — the Python package derived from the specification, including the persistence/codec boundary and the SQLite backend's role.
4. [`MEMORY_ADVERSARIAL_MATRIX.md`](MEMORY_ADVERSARIAL_MATRIX.md) — the failure cases the theory and its implementation must survive.

Status: Core specification and architecture are frozen; implementation is complete (see `ARCHITECTURE.md`). Memory v0 is closed after four checkpointed passes (see `MEMORY_ARCHITECTURE.md` and `docs/memory-passes/`). The finance application uses Memory only through its public persistence and recall API.

## Personal finance application

`personal-finance` is an optional, local Python 3.14 application built from Core's
existing runtime types. It provides a reviewed double-entry ledger, SQLite
persistence, recorded balances, a cash plan with 30/60/90-day horizons, and
reviewed debt, asset, and income-node models in an eight-tab Textual workspace.
The initial personal book is empty. No financial
facts or balances are assumed. [Construction map and API decisions](docs/FINANCE_CONSTRUCTION.md).

### Install and run

From the repository with Python 3.14 and uv installed:

```powershell
uv sync --extra finance
uv run --extra finance personal-finance
```

The optional dependency group keeps Textual out of ordinary Core installations.
The lockfile pins the tested interface to Textual 8.2.8. The package also provides
`python -m personal_finance` and can be installed with `pip install ".[finance]"`.

`uv build` produces both a source archive and a wheel. The source archive
includes the three packages, tests, and project specifications; local settings,
prompts, caches, and finance databases are excluded. Once the build backend is
cached, `uv build --offline` works without a network connection.

Use an isolated synthetic book to explore the controls:

```powershell
uv run --extra finance personal-finance --demo
```

The `--demo` flag always selects `demo.sqlite3`, separate from `personal.sqlite3`.
Demo transactions have explicit synthetic labels and repeatable seed identities.
Closing and reopening the demo does not intentionally add its sample entries again.
To choose a storage directory, supply `--data-dir PATH` before a command.

### Local workflow

Create accounts without opening the interface, if preferred:

```powershell
uv run --extra finance personal-finance add-account Checking asset USD --liquid
uv run --extra finance personal-finance add-account Salary income USD
uv run --extra finance personal-finance add-account Groceries expense USD
uv run --extra finance personal-finance entry 2026-09-21 Paycheck Checking Salary 2500.00 USD
uv run --extra finance personal-finance drafts
uv run --extra finance personal-finance post DRAFT_ID
uv run --extra finance personal-finance explain
```

`entry` prepares a draft. The local `post` command prints its exact content and
hash, then requires an interactive terminal and the typed word `POST`. The terminal
interface provides its own review/post confirmation. Reversals are separately
reviewed drafts; original entries remain unchanged. There is no agent-callable
approval endpoint, server, financial execution, or connector in this milestone.
Imported control characters are shown as visible escapes during review, without
changing the stored source data. Tags and the reversal target are included in the
review alongside amounts, account identities and source.

The eight tabs are Overview, Ledger, Cash, Debts, Assets, Nodes, Forecast, and
Reconcile. Overview and Ledger display recorded data; Cash separates recorded
opening balances from a conditional plan. Debts, Assets, and Nodes show reviewed
model evidence and the values it supports. Each supports text, graph and split
modes. Deferred features state that they are unavailable. Actual net worth remains
unknown without complete account and valuation coverage. Cash safe-to-allocate is
unavailable until its evidence checks pass. Reconciliation shows recorded
statement differences and whether each month is open, ready, or closed.
The searchable register and recorded balances are derived from the same snapshot
revision, including when another local process posts an entry during a refresh.
Overview and Cash reconstruct recorded liquid-cash history at end-of-day effective
date boundaries. Transfers between liquid accounts net to zero in that chart;
backdated entries rebuild later points. This history is a ledger projection, not
an observed bank balance. Cash additionally shows a separate, labeled forecast.

The [wide overview](docs/finance-ui-color.png),
[compact ledger](docs/finance-ui-compact-color.png), and
[cash plan](docs/finance-cash-color.png) show isolated synthetic data. The
[debt comparison](docs/finance-debts-color.png),
[asset valuations](docs/finance-assets-color.png), and
[income node](docs/finance-nodes-color.png) use a separate synthetic book. The compact
layout opens in text mode so register rows stay visible;
`g` opens the chart and `b` requests split view.

The interface exposes keyboard hints and Textual's command palette. Use `a` for a
new account, `n` for a new entry, `/` for search, and `t`, `g`, `b` for view modes.
Entry forms accept multiple posting lines for splits. Positive amounts are debits;
negative amounts are credits. The sum must be exactly zero in a single currency.
Enter opens a selected entry or draft. Drafts require review before posting.

### Cash planning

Open Cash with `3`, then press `c` to record a dated schedule, protected
allocation, availability hold, minimum cash floor, balance observation, coverage
declaration, or retirement. Use `h` or the 30/60/90 buttons to change the horizon;
`x` explains the assumptions and Core provenance; Enter on a timeline row shows
that boundary's exact amounts. The chart and timeline label the recorded opening
separately from future, conditional amounts. Planning records never post money.

For each currency, enter the eligible liquid accounts and their ledger balances,
then record a manual balance observation for each account. An observation must
match its recorded balance, identify its source, and remain fresh for at most seven
days. Set an explicit floor, even if the reviewed choice is zero. Declare account
and schedule coverage through the chosen horizon only after reviewing both. An
incomplete, stale or conflicting input leaves safe-to-allocate unavailable and
lists the reason. Coverage is a dated assertion, not a fact inferred from an empty
schedule list. Changes to the liquid-account inventory invalidate earlier coverage;
adding or retiring a schedule automatically marks schedule coverage incomplete
until you review it again.

Schedules use local due dates with a validated IANA timezone and once, weekly or
monthly recurrence. The monthly rule moves a date such as the 31st to the last day
of a shorter month. Signed amounts are external cash flows: positive is income,
negative is an outflow. Internal transfers between liquid accounts belong in the
ledger. A protected allocation claims existing cash without moving it; link it to
a specific bill occurrence when that bill is also scheduled. The bill releases the
linked earmark as its outflow occurs. Holds constrain availability while active;
they are not ledger outflows. Retiring an item appends a dated decision and keeps
the original record.

The plan orders each day's floor and constraint changes, outflows, releases, then
inflows. Safe-to-allocate is the nonnegative minimum of today's unallocated cash
and the lowest projected balance above the floor after active allocations and
holds across the horizon. The calculation includes the starting point and every
event boundary, so a later floor increase or hold can lower the answer. A future
posted entry that may duplicate a schedule withholds the answer until reviewed.
Currencies stay separate; no exchange rate or cross-currency total is inferred.
Schedules and coverage are manually maintained, so the result is conditional on
their accuracy, not a guarantee of future bank funds.

The same workflow is available without the interface. For example, after creating
and posting an opening balance for `Checking`:

```powershell
uv run --extra finance personal-finance plan-schedule Checking Rent -1200.00 USD 2026-10-01 monthly America/New_York
uv run --extra finance personal-finance plan-floor USD 500.00 2026-09-24
uv run --extra finance personal-finance plan-observe Checking 2500.00 USD 2026-09-24 2026-09-30 --source "bank statement"
uv run --extra finance personal-finance plan-coverage USD 2026-09-24 2026-12-23 --accounts-complete --schedules-complete --source "manual review"
uv run --extra finance personal-finance cash --horizon 90
```

The example amounts are illustrative; the balance observation only reconciles if
the posted ledger balance for `Checking` is exactly 2500.00 USD. Run
`personal-finance --help` for allocation, hold and retirement commands.

### Debts, assets, and income nodes

Open Debts with `4`, Assets with `5`, or Nodes with `6`. Press `m` to choose a
model action. The forms record debt terms and posted payment splits; asset
positions, dated valuations, posted contributions or withdrawals, and reviewed
external-flow coverage; or node boundaries and posted capital transfers. These
records are append-only and retain a source. `x` explains the current derivation.
Review and post the underlying ledger entry before classifying its payment or
transfer. A model action never posts money.

Debt balances come from posted liability entries effective through today. Terms
specify exact APR in hundredths of a percent, a minimum payment, and a monthly due
day. Weighted APR is balance-weighted within a currency and is unavailable if any
positive debt lacks terms. Payoff comparisons include minimum-only, avalanche,
snowball, and an explicit custom order. Avalanche, snowball, and custom use a fixed
monthly budget of all recorded minimums plus the entered extra; the minimum-only
case ignores that extra. Each month accrues interest on the opening balance at APR/12, rounded
up to one minor unit, then applies minimums and strategy-ordered extra payments.
The comparison stops at 600 months and reports unavailable if a debt cannot be
paid under that policy. These scenarios are conditional illustrations; they do
not represent lender statements, changing rates, or actual future payments.

Each non-liquid asset position links to one account and uses a manual value with
an observation date, a source, and at most 30 days of declared freshness. A stale
value remains visible as evidence but is not used as a current value. Unrealized
gain needs a cost basis. Period gain is an amount: ending value minus starting
value, minus posted contributions, plus posted withdrawals. It remains unavailable
until two dated valuations and explicit external-flow coverage span the period.
Classifying another flow inside a reviewed period invalidates that coverage. A
transfer between two tracked positions can be classified as internal and is
excluded from external contributions and withdrawals. Percentage return and
complete net worth are not inferred from these partial records.

An income node links dedicated income and expense accounts and optionally a
dedicated liquid cash account. Its displayed operating net is posted revenue minus
posted operating expenses over the latest 30 days. Classified capital and
withdrawals appear separately. Monthly assumptions are labeled as assumptions;
runway needs a fresh, reconciled cash observation and a recorded operating burn.
Funding a node cannot be counted as operating revenue.

### Monthly reconciliation and evidence recall

Open Reconcile with `8` and press `u` to record a completed-month statement,
accept a suggested exact amount/date match, explain an unmatched line or entry,
resolve a recorded balance contradiction, or review a month for close. `x`
explains the selected statement's differences and source identities. Statement
lines use signed movements in the account's currency; a complete line inventory
is a separate declaration. Suggestions never accept themselves. Closing requires
every asset and liability account's statement, zero opening/closing/line-bridge
differences, decisions for every line and posted movement, and resolved issues.
Closing locks that month against later dated ledger changes. Corrections to a
closed month are posted as linked reversals on a later open date.

The command line offers the same local decisions. Statement CSV uses the exact
header `date,amount,description,external_id`, is limited to 1 MB and 1,000 lines,
and is reviewed with the typed `RECORD` confirmation. A revised statement is a
new retained version; the latest version is used for close.

```powershell
uv run --extra finance personal-finance statement-add Checking 2026-08 1000.00 1250.00 bank-lines.csv --complete --source "August statement"
uv run --extra finance personal-finance reconcile
uv run --extra finance personal-finance reconcile-match LINE_ID ENTRY_ID
uv run --extra finance personal-finance reconcile-exception STATEMENT_ID line LINE_ID "Bank-only item reviewed"
uv run --extra finance personal-finance reconcile-resolve ISSUE_ID "Corrected by later reviewed entry"
uv run --extra finance personal-finance reconcile-close 2026-08 "All accounts reviewed"
```

Press `z` in the terminal interface, or use `recall TEXT`, to find retained
accepted-change evidence by literal text. Results show their source, subject ID,
time, and relevance; they are evidence to inspect, not ledger authority.
`memory-sync` retries the local Memory index. The finance transaction retains
its audit record and queues a Memory item atomically; delivery later uses
Memory's public store in a separate database. Pending or failed delivery is
reported, and a Memory outage cannot repeat or undo a finance posting. Memory
contains supported Core observations of finance effects, never finance domain
records or arbitrary payloads.

For a command-line example, after creating the accounts and posting the underlying
entries:

```powershell
uv run --extra finance personal-finance debt-terms "Credit card" 18.25 50.00 15 --source "card statement"
uv run --extra finance personal-finance debt-payment "Credit card" ENTRY_ID 100.00 4.00 0.00 --source "card statement"
uv run --extra finance personal-finance debts --currency USD --extra 75.00
uv run --extra finance personal-finance asset-position Brokerage "Index fund" investment
uv run --extra finance personal-finance assets
uv run --extra finance personal-finance node-create "Side project" "Project revenue" "Project expenses" --cash-account "Project cash"
uv run --extra finance personal-finance nodes
```

The example debt split succeeds only when that posted entry has a 100.00 USD
liability debit, a 4.00 USD expense debit, and a 104.00 USD liquid-cash credit.
`personal-finance --help` lists the valuation, flow, coverage, and funding commands.

### CSV staging and export

The import schema is deliberately explicit, with the exact ordered header:

```csv
date,description,account,counter_account,amount,currency,tags
2026-09-21,Paycheck,Checking,Salary,2500.00,USD,salary;monthly
2026-09-21,Groceries,Groceries,Checking,82.19,USD,food
```

Account names must already exist and match exactly. Positive `amount` debits
`account` and credits `counter_account`; negative amounts reverse those signs.
Imports are UTF-8, limited to 2 MiB and 1,000 rows. Invalid rows produce row-level
errors while valid rows become drafts. No import posts automatically.

```powershell
uv run --extra finance personal-finance stage transactions.csv
uv run --extra finance personal-finance export ledger-export.csv
```

Repeating an identical file deduplicates its source hash and row identities,
including after drafts are posted. An edited or reordered file has a different
identity and needs human overlap review. Source hashes and row numbers are retained
in draft and entry provenance. Full raw CSV contents are not retained.

Export writes one row per posting, including full account/entry identities,
reference namespaces, minor units, effective and recorded dates, principal,
source, tags and reversal references. This inspection schema is different from the
simple import schema. Spreadsheet formula introducers in free text are escaped.
Existing export files are never silently overwritten. Exports contain private
financial information and are not encrypted by the application.

### Storage, semantics and safety

On Windows the default directory is `%LOCALAPPDATA%\personal-finance`; on other
platforms it is `$XDG_DATA_HOME/personal-finance` or `~/.local/share/personal-finance`.
SQLite is authoritative. Migrations 1–6 rerun safely. Each transaction owns its
connection and writes serialize under a database transaction lock. Posted entries
and postings are protected by service validation and database triggers; corrections
append reversals. Mixed-currency entries and inexact amounts are rejected.

Posting atomically records approval consumption, the journal, Core event/effect
evidence, durable audit, Memory outbox item, and idempotency result. Repeated
same-key requests return
the original committed result; a conflicting request is rejected. A draft's
versioned canonical hash binds the reviewed content. This trusted local surface
is not an authentication boundary against arbitrary code running as your user.

Recorded balances are Core `State[Money]` values derived by a named, versioned
`Transform`. Their `Context` and `Provenance` retain the book, as-of time, source
identities and calculation version. `explain` and the interface's explanation view
make those derivations inspectable. Core `Trace` orders an operation's evidence;
SQLite commit sequences order persisted ledger history. Accounting effective dates
are independent of recording time.

Money uses signed integer minor units with explicit exponents: USD/EUR/GBP/CAD/AUD/
CHF use two places, JPY zero, KWD/BHD three. Inputs must be exact; no binary floats or
silent rounding. Unsupported currencies are rejected. Different currencies are
displayed separately, never converted or added without an exchange policy.

The dependency direction is UI/CLI → finance services → finance domain → Core.
Services use narrow repository ports; SQLite implements those ports. The optional
Memory adapter depends only on Memory's public store API. Core and Memory have no
finance dependency; authoritative accounting and explanation evidence stay in
finance storage.

### Privacy and recovery limits

This is a local single-user application with no network listener, credentials,
mail access, provider sync, external transfers, or trading. Integration and agent
mutation capabilities are unavailable. Your operating-system user and filesystem
permissions are the trust boundary. SQLite is not encrypted; someone with full
filesystem access can read it, replace it, or remove triggers. Append-only rules
protect application behavior, not against a filesystem administrator.

Automatic backup/restore and encrypted backups have **not** shipped or been
verified. Do not treat this milestone as your only copy of financial records. For
an offline copy, stop every application process before copying the storage
directory, including any SQLite sidecar files. Secure that copy using your normal
encrypted backup system. CSV exports preserve inspectable postings but are not a
complete backup of drafts, approvals, audit, or derivation records.

### Coinbase read-only preview

`personal-finance coinbase-preview` reads Coinbase Advanced Trade accounts and
recent fills using the official Python SDK. Set `core_read` to the path of a
downloaded CDP key JSON file, or pass `--key-file` with that path. Do not put
the key contents in a command, repository, or finance database. The connector
checks Coinbase's key-permissions endpoint first and refuses keys with trade or
transfer access. It calls only account and fill read endpoints. `--all-accounts`
shows zero-balance accounts; `--fill-pages` can extend the default five-page
fill preview (maximum 20). The output labels retrieval time and incomplete fill
coverage. It does not stage, post, value, or reconcile anything yet, and no
Coinbase response is persisted. The key can see only its permitted portfolio,
which might be less than the user's full Coinbase account history.

### Verification and remaining slices

```powershell
uv run --extra finance pytest tests/personal_finance -q
uv run --extra finance pytest -q
uv run ruff check src/personal_finance tests/personal_finance
uv run --extra finance pyright src/personal_finance tests/personal_finance
```

Finance checks cover exact money, codecs, double entry, rollback, reopen, direct
invalid SQL, replay/concurrent commits, imports, cash event ordering and coverage,
debt payoff, asset-flow coverage, node funding separation,
dependency direction, import-time side effects, and headless Textual keyboard and
layout flows. The baseline before finance work was 714 existing passing tests;
Core and Memory were not edited for this application.

Authenticated MCP, staged connector imports, and verified backup/restore
recovery follow in later slices specified by the brief.
