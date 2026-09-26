# Personal finance construction map — slices 1–4

The user-approved master brief defines a Python 3.14 local application. This
delivery implements the ledger and interface foundation (slices 1 and 2),
local cash planning (slice 3), and debt, asset, and node models (slice 4).
Core and Memory retain their existing public
APIs and dependency graphs. Memory is not used in this milestone.

## Plan and ownership

1. Establish the existing test baseline before modifications.
2. Add immutable finance values, exact money and explicit versioned codecs, with
   executable accounting invariants and deterministic test capabilities.
3. Add SQLite migrations 1–4 and a transaction boundary; test direct invalid writes,
   rollback, reopen, review hashes, idempotency and concurrent posting.
4. Add a Textual workspace with eight tabs, account and split entry forms,
   review/post/reverse, search, saved filters, CSV staging/export and recorded
   charts. All blocking work runs outside the message loop.
5. Add immutable cash schedules, allocations, holds, floors, observations,
   coverage declarations and dated retirements. Derive 30/60/90-day event streams
   and safe-to-allocate with one revision's ledger and planning inputs.
6. Add typed debt terms/payment splits, asset positions/valuations/flows/coverage,
   and income nodes/funding with conditional comparisons and performance views.
7. Exercise keyboard workflows and compact/wide layouts, inspect screenshots,
   package the optional application and document remaining scope.

## Semantic constructions and finance policy

| Finance meaning | Actual Core API | Additional finance rule |
| --- | --- | --- |
| Account, entry and draft identities | `Id(Kind(...), value)`, structural `Entity` | Namespaced kinds; source tokens never silently equal ledger tokens |
| Account references, reversal targets and evidence | `Ref(id, namespace)` | Full kind/value/namespace round trips; namespace is preserved |
| Recorded balances | `State[Money](subject, value, at, context)` | Debit positive; display signs depend on account type |
| Incomplete personal position | `UNKNOWN`, `Maybe[Money]` | Recorded zero does not prove actual zero; inventory stays incomplete |
| Projection frame | `Context(as_of=..., namespace=..., units=..., version=...)` | Profile, recorded cutoff and calculation version remain inspectable |
| Recorded time / elapsed calculation | Injected `Clock`, `MonotonicClock` | Effective accounting date is a separate `date` |
| Balance derivation | `Transform.apply(clock=..., monotonic_clock=..., ids=..., input_refs=...)` | Deterministic versioned calculation; retain source entries and provenance |
| Ledger acceptance | `Event` | Persist only inside the successful posting transaction |
| Confirmed posting | `EffectSpec`, `Effect`, explicit `EffectSink` | A draft never claims money was posted; audit/effect/approval/replay are atomic |
| Expected rejection | `Result`, `Ok`, `Err`, `Error` | Unknown financial data differs from an operational failure |
| Inspectable operation | `Trace.append()` | Core assigns scoped positions; SQLite owns durable commit ordering |
| Reversal | Immutable entry `Ref` | Original remains unchanged; exact inverse gets its own entry |
| Manual balance evidence | `Observation[Money]`, `Context` | Account/date/source/freshness remain explicit; disagreement with the ledger withholds allocation advice |
| Cash projection | Named `Transform`, `Provenance` | One deterministic calculation per 30/60/90-day horizon, parented to the recorded balance derivation |
| Coverage of planning inputs | Dated finance record with account `Ref`s | Complete account and schedule flags are human assertions, never inferred from an empty set |
| Future cash occurrences | Finance-owned schedule and occurrence identity | Projected events never assert that money moved |
| Debt, asset, and node definitions | Immutable finance values with Core `Id` and `Ref` | Linked accounts are checked against their ledger types and currency |
| Manual asset value | `Observation[Money]` plus dated finance valuation | Source and at-most-30-day freshness remain explicit |
| Model projections and payoff scenarios | Named `Transform`, `Provenance` | Versioned calculations reference the same ledger snapshot and model input identities |

The domain uses integer minor units, supports explicitly enumerated currency
exponents, rejects binary floats and inexact input, and snapshots all collections.
SQLite signed integers establish storage bounds. Mixed-currency entries are
rejected until exchange policy exists. Amounts in different currencies are never
summed. No opening balances are invented.

## Pinned application boundary

`FinanceService` receives a finance repository port, `Clock`, `MonotonicClock`,
`IdSource`, profile and local principal. It offers account creation/listing,
entry preparation, exact-hash local posting, reversal preparation, saved filters,
cash planning input commands and a snapshot. `post_draft(draft_id, expected_hash,
idempotency_key)` is a trusted
local review surface, not an agent approval API. No MCP server exists in this
milestone. Future remote access must never expose this method directly.

`EntryContent` owns effective date, description, immutable posting tuple, tags,
source and optional reversal reference. `Draft` retains its canonical SHA-256
content hash. `JournalEntry` adds identity, recording timestamp, principal and
persisted commit sequence. `LedgerSnapshot` retains per-account `State[Money]`,
the full input entries, provenance, revision and coverage explanation. The same
snapshot carries cash records and 30/60/90-day projections, each with Core
provenance parented to the balance calculation. Snapshot inputs are read in one
transaction and projection caches are written only for the matching revision.

## Cash policy and evidence

The starting cash value is the sum of recorded balances for eligible liquid
accounts in one currency at the app's local start date. Manual observations are
retained separately from ledger entries. The latest observation for every such
account must be within its declared freshness period (no more than seven days)
and match that account's recorded balance. A newer conflict, missing floor, stale
observation, incomplete coverage declaration, account inventory change or coverage
ending before the selected horizon makes safe-to-allocate unavailable with a
specific missing-input reason. A reviewed zero floor is distinct from no floor.
Adding or retiring a schedule appends an incomplete coverage record when a prior
complete schedule review exists; a human must declare coverage again.

Schedules carry an IANA timezone and local due date. Once, weekly and anchored
monthly recurrence use date-only boundaries; the default monthly policy clamps
missing days to the month's end. The app never treats a future schedule as a
posted ledger event. A future-effective posted entry is projected as a committed
boundary; if it may duplicate a schedule on the same account, day and amount,
safe-to-allocate is withheld for review. Internal liquid transfers net to zero.

Each date orders floor/constraint changes before outflows, releases, and inflows.
An allocation protects existing cash and can link to a specific bill occurrence.
The linked allocation is released at that bill's outflow, preventing a second
subtraction of the earmark. Holds constrain availability at the starting point or
from a future activation date, then release on their dated end or retirement;
they are never cash movements. Future allocations and holds also constrain the
minimum across the horizon. Same-day outflows precede inflows, so the event low
does not assume an income arrives early enough to fund a bill. The calculation
includes the starting point and every event boundary. Safe-to-allocate is clamped
at zero and bounded by both today's unallocated cash and the lowest projected
amount above the then-effective floor and constraints. Currencies never combine.

Migration 3 stores every planning input as an append-only record with canonical
hash and checked identity/reference/currency/date fields. An application unit of
work is required to insert it; database triggers reject update/delete and bump
the shared revision on insert. Dated retirements preserve original inputs. Cash
projection cache entries are keyed by revision and horizon and are derived output,
not authoritative money movement. The local app's trusted code and filesystem
permissions remain the trust boundary.

## Debt, asset, and node policy

Migration 4 stores terms, payment splits, positions, valuations, classified flows,
flow coverage, nodes, and funding as append-only, versioned records. Each record
has canonical payload and hash, a checked identity, and foreign keys to its
referenced ledger evidence. Insert requires the trusted unit of work; triggers
reject update and delete. New records bump the shared revision, so a model
projection cannot be cached against stale ledger or model inputs. The projection
is a Core transform with parent provenance to the recorded ledger balance.

Debt terms carry exact basis-point APR, a positive minimum and a due day. Payment
classification checks that its principal matches a posted liability debit, its
interest and fees together match expense debits, and its total matches a liquid
cash credit. Balances through today use effective dates, not later scheduled
postings. Weighted APR uses positive current debt balances in one currency and
is withheld if any such debt lacks terms. Zero debt has no applicable APR. The
payoff simulation accrues APR/12 each month with interest rounded upward to one
minor unit, then applies minimums and strategy-ordered extra budget. Minimum-only
uses only the stated minimums; avalanche and snowball redistribute a fixed budget
of all initial minimums plus user-entered extra; custom requires an explicit
complete debt order. No payoff within 600 months is unavailable. Changes in APR,
fees, future charges and lender-specific payment rules are outside this model.

Each position is a boundary around one non-liquid asset account. A manual
valuation has date, source, quantity if supplied, cost basis if known, and
freshness no longer than 30 days. Current value is unavailable after expiry;
unrealized gain requires known basis. Period gain is ending value minus starting
value minus external contributions plus withdrawals. Classified internal
transfers between tracked positions do not count as external flows. The period
calculation requires two dated valuations and a human declaration that external
flows across the period were reviewed. A newly classified flow in that reviewed
period invalidates coverage. Gain is an amount, not a percentage return.

An income node links dedicated revenue and expense accounts and optionally its
own liquid cash account. Posted revenue and operating expenses in the last 30
days yield operating net cash flow. Classified capital and withdrawal transfers
are displayed separately and cannot inflate revenue. A fresh cash observation
that reconciles to the node's ledger cash balance is required to show runway
when the node is burning cash; assumptions and milestones remain labeled as
manual plans. No cross-currency aggregation or exchange conversion occurs.

## Reconciliation and Memory

Migration 5 adds append-only statement observations, lines, exact matches,
explained exceptions, contradictions, resolutions, and month-close records.
The reconciliation projection is a named Core transform with parent provenance
to the posted ledger. It compares exact account opening and closing balances,
the line bridge, and each posted movement within one completed calendar month.
Suggestions have no effect until accepted. A close requires all asset/liability
accounts to have complete current statement versions, zero differences, all
items decided, and all contradictions resolved; it atomically creates a dated
closed-period guard. Superseded statement versions and their decisions remain
auditable but cannot receive new decisions.

Migration 6 adds a finance-owned Memory delivery outbox. Every accepted Core
effect and its outbox item commit together with the finance change. Older effects
are backfilled on migration. A separate dispatcher decodes the retained effect,
derives a deterministically identified Core Observation with a string value,
and persists it through Memory's public `SqliteMemoryStore` API. Delivery retries
are idempotent; status is pending, delivered, or failed. A failed Memory open or
write cannot undo or repeat the finance transaction. Finance remains the source
for exact ledger, audit, and reconciliation truth; Memory provides bounded
literal-text evidence recall with source, subject, time, and relevance. No Core
or Memory module was modified for this integration.

## Coinbase read-only boundary

The first Coinbase connector boundary is a non-persistent preview. It resolves
a downloaded CDP key JSON file only on explicit use, verifies that the live key
has view access without trade or transfer access, and reads paginated Advanced
Trade accounts and fills through the official SDK. The local preview labels
retrieval time and partial fill coverage. It does not assert completeness beyond
the key's portfolio, create Core observations, change the ledger, or persist
Coinbase content. Durable staged observations and review remain a later
construction, after verified finance backup/restore.

## Dependency boundary

Textual and CLI call finance application services. Services depend on immutable
finance domain, Core, and narrow finance ports. The SQLite adapter implements
those ports. Neither Core nor Memory imports personal_finance. Domain modules
never import UI, SQLite, network or provider code. Runtime composition is explicit
at startup, with no import-time credentials, databases, clocks or identity draws.

## Baseline before finance changes

- Python 3.14.6; 714 tests pass.
- Ruff: 12 pre-existing findings in `tests/memory/semantics/test_sqlite_store.py`.
- Strict Pyright: 51 pre-existing errors in that same Memory test file.
- Existing Memory source/test edits, `.claude/` and `docs/prompts/` are preserved.
