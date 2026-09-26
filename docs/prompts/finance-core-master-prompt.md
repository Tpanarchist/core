# Master build prompt — Personal Finance, constructed from Core

You are working in `E:\core`, an existing Python 3.14 repository containing the Core ontology library and its derived Memory layer. Design and implement a local personal-finance application **constructed from this repository's existing Core concepts and runtime types**.

The central requirement is semantic composition: accounts, journal entries, financial observations, forecasts, approvals, reconciliation, and audit must have explicit meanings derived from Core. Use Core's real public APIs throughout the domain and application boundaries. Keep financial policy in the finance application. Preserve Core's closed ontology and dependency graph.

Use the working application name `personal-finance` and Python package `personal_finance`.

## 1. Inspect the existing foundation

Before changing code, read repository instructions and inspect the working tree. Preserve unrelated changes, particularly any ongoing Memory backend work. Establish the current test baseline and distinguish pre-existing failures from regressions.

Read:

- `SPECIFICATION.md`, `LAWS.md`, and `ARCHITECTURE.md`.
- The actual `src/core/` implementations and their semantic and architecture tests.
- `MEMORY_SPECIFICATION.md`, `MEMORY_LAWS.md`, `MEMORY_ARCHITECTURE.md`, and the relevant Memory implementation and pass documents before using Memory.
- `pyproject.toml`, dependency lockfile, and current packaging conventions.

Verify implementation status from source and tests rather than assuming a README status line proves a backend is finished. Pin exact finance APIs at the start of each implementation slice. Record the mapping from domain requirements to Core constructions and the new finance-specific rules those constructions require.

## 2. Dependency direction and responsibility

Use this dependency direction, where arrows mean “depends on”:

```text
Textual UI ────────────┐
MCP adapter ───────────┼──> Finance application services ──> Finance domain ──> Core
CLI ──────────────────┘                  │
                                         └──> narrow application ports

SQLite repositories / connector adapters / optional Memory adapter
    implement those ports and depend on finance contracts and Core

Memory ──> Core
```

Core owns identity, contextual information, time, state, events, observations, results, errors, transformations, effects, relations, constraints, provenance, and traces. Capability is expressed through narrow protocols; it does not require a universal capability wrapper. Finance owns accounting, money, schedules, allocation policy, forecasts, approval rules, reconciliation, and connector policy.

- Put the application in `src/personal_finance/`, alongside `core/` and `memory/`.
- Do not add finance, Textual, MCP, SQLite, or vendor dependencies to `core.*`.
- Do not add finance behavior or types to Memory's frozen persistence union.
- Import Core types from their owning modules. Keep Core's package initializer minimal.
- Keep domain logic independent of Textual, MCP, SQLite, and provider SDKs.
- Use plain immutable domain values where they suffice. Add a Core construction where its meaning is required; every arithmetic helper does not need a transformation or trace.
- Prefer a finance application optional dependency group and dedicated entry points so existing Core consumers do not acquire UI and connector dependencies simply to use Core.
- Importing an application module must not open a database, load credentials, start a server, read the clock, allocate random identities, or contact a provider. Compose runtime dependencies explicitly at startup.

## 3. Construct the finance domain from Core

### Identity and categories

Use `core.identity.Id`, `Ref`, `Entity`, and `IdSource`, and `core.value.Kind`.

Define namespaced finance kinds such as `finance.account`, `finance.journal_entry`, `finance.import_candidate`, `finance.action_draft`, and `finance.forecast_run`. An account is a finance-owned immutable record with an `Id`, account type, currency, and accounting attributes. A journal entry has its own `Id` and immutable postings. A source record and the ledger entry inferred from it have different identities, connected through evidence and domain relationships.

Every object targeted by a `Ref` must carry an `Id`. Preserve the full `(kind, value)` identity at persistence and protocol boundaries; preserve a reference's namespace where applicable. Use injected `IdSource` capabilities rather than hidden UUID calls. IDs identify tokens; matching two provider records to the same real account is an explicit reconciliation decision.

### Values and uncertainty

Build finance-specific immutable `Money`, `Quantity`, `Rate`, and valuation records as needed. Money uses signed integer minor units plus currency and an explicit supported currency exponent policy; do not assume every currency has two decimal places. Rates, prices, and quantities use `Decimal` with explicit precision and rounding policies. Do not use binary floating point for accounting.

Core records are structurally immutable, but arbitrary nested payloads are not automatically frozen. Use recursively immutable finance payloads and defensive snapshots so caller-owned lists or dictionaries cannot alter historical states, drafts, or evidence after construction.

Use `Known[T]`, `UNKNOWN`, and `Maybe[T]` when financial knowledge can be missing. Distinguish known zero, unknown, stale, and not applicable. Freshness and applicability need explicit domain metadata; `Unknown` alone does not explain why information is missing.

An empty ledger can have a known recorded balance of zero while the person's actual cash or net worth remains unknown. Never label an incomplete account inventory as a complete zero-valued financial position. Show partial totals with coverage and missing inputs. Do not invent opening balances, debt balances, prices, or user facts.

### Context

Use `core.context.Context` to carry the frame of a financial calculation or statement: as-of time, profile/book namespace, units, source, authority, calculation version, and scenario or policy references. Domain records may also need explicit typed fields for date ranges, scenario IDs, and coverage.

Use `Context.merge()` to surface incompatible frames; use `override()` with retained `ContextOverride` evidence for deliberate replacement. A frame conflict does not automatically constitute a semantic contradiction. Context metadata does not confer authentication or authorization.

### Time and ordering

Use `WallInstant` and injected `Clock` capabilities for recorded timestamps, observations, approvals, and audit. Use a separately injected `MonotonicClock` for elapsed work. Respect time-family and time-space rules.

Keep financial effective dates separate from recording time. A date-only bill due date is a domain date with a timezone/calendar policy, not an invented midnight timestamp. Preserve provider occurrence time and local retrieval time separately.

Use authoritative persisted ordering for commits and audit. Core `Trace.append()` allocates its own scoped `Sequence`; callers do not manufacture trace positions. Wall-clock order is not sufficient to establish causality or serialize concurrent commits.

### Observations, interpretations, and facts

- A bank statement balance, Coinbase balance response, or retrieved message is an `Observation[T]` with subject, source, time, and context.
- “This message appears to describe a bill” is an interpretation represented by an evidence-linked `Claim[T]` or finance import candidate; use `Inference[T]` when recording a method, premises, and a conclusion.
- A posted journal entry is a finance accounting record. Its acceptance is a `core.event.Event` such as `finance.ledger.entry_posted`, referring to the entry and decision evidence.
- A future schedule occurrence is planned data. A forecast point is projected data. Neither is an event asserting that money already moved.

An observation does not authorize posting. A claim does not become financially authoritative because it was stored or generated by a model.

### State and change

Use `State[T]` for immutable subject-specific snapshots, such as ledger-derived account balances or a draft's review state. Use `Event` for occurrences and `Transition[T]` for meaningful before/event/after explanations. Keep before and after states subject-compatible.

Use `History[T]` where a contiguous subject history is useful. It is an in-memory semantic construction, not the financial database or a concurrent event store. Backdated financial entries cause a new recorded projection revision; do not insert transitions into an immutable history or violate its time ordering.

### Computation and provenance

Implement calculations as deterministic functions. Use named, versioned `Transform` objects for significant derivations such as import normalization, cash projection, debt-plan comparison, and safe-to-allocate calculation. Compose compatible steps with `Pipeline` where useful.

Honor the real API: `Transform.apply()` requires `clock`, `monotonic_clock`, and `ids`, accepts explicit `input_refs` and parent `Provenance` records, and returns `Result[Traced[B], Error]`. It is synchronous. Do not pass async callables or assume a callable returning `Err` is automatically flattened into a failed transformation. Keep network work and async orchestration at adapter/service boundaries.

Retain the input evidence, calculation version, context, and provenance needed to explain every important metric. A cached projection must be rebuildable from authoritative inputs. Surface unresolved provenance references rather than dropping them.

`Pipeline.apply()` returns the final result, not a durable collection of all intermediate provenance records. When complete ancestry must be resolvable, explicitly orchestrate and retain each stage's returned provenance before passing it to the next stage. Do not assume a parent reference itself persists the referenced node.

### Results, errors, and executable rules

Use `Ok`, `Err`, and `Result` for expected application outcomes; use structured `core.error.Error` records at appropriate service boundaries. Preserve meaningful causes, operation identity, context, and recovery guidance while redacting secrets. Keep unknown financial information distinct from an operational failure.

Use local validation and Core's `require`, `ensure`, and `invariant` where appropriate. These helpers require explicit clocks and identity sources and raise `ContractError`; do not assume they return results. Financial rules need executable domain tests and database enforcement where applicable.

### Effects, capabilities, and trace

Use `EffectSpec` to describe potential database or external changes and `Effect` to record changes actually confirmed. Pass an explicit `EffectSink` at the execution boundary. `Transform.apply()` neither records effects nor enforces declared effect policies.

Drafting a ledger entry may persist a draft; it must never emit an effect claiming that the ledger was posted. Persist a successful financial effect and its audit record atomically with the ledger transaction so rollback leaves no successful-effect record. Represent uncertain external outcomes explicitly and reconcile them before retrying.

Express access through finance-owned protocols such as `LedgerReader`, `LedgerWriter`, `ApprovalVerifier`, `QuoteReader`, and `SecretProvider`. Protocol conformance is a code boundary; enforce client permissions and approval policy separately at runtime.

Use `Trace` for an inspectable operation path, with references to observations, errors, events, effects, and provenance. A trace is not the durable audit store. Persist audit through a finance-owned adapter. Core's mutable containers are single-writer; serialize their use rather than assuming thread safety.

### Relations and reconciliation

Use `Relation` where typed relationships add meaning: a reversal targets an entry, an observation supports a candidate, or an external account maps to a local account. Finance repositories own durable representation for these relationships.

Compare observations and ledger projections only under compatible account, currency, scope, cutoff, and timezone assumptions. Once finance policy establishes conflicting statements, record `Contradiction` and a later `Resolution` with explicit rationale and responsible principal. Both remain immutable. A resolution explains a discrepancy; any financial correction still needs a separate authorized journal entry. `Resolution` itself has no `Id` in Core v0 and cannot be a `Ref` target.

## 4. Persistence and the Memory boundary

The authoritative accounting store is a finance-owned SQLite schema. Store accounts, immutable posted entries and postings, observations, schedules, allocations, reconciliation decisions, drafts, approvals, idempotency records, durable audit, and relevant provenance with explicit versioned codecs and migrations.

Use Memory when durable evidence recall, grouped review episodes, or historical reasoning benefits the application. Memory can retain admitted Core observations, claims, inferences, events, effects, provenance, contradictions, resolutions, and errors under its existing rules. Memory's belief projection does not decide ledger truth or posting authority.

Respect the current implementation:

- Memory does not generically persist every `Entity`. It excludes `State`, `Transition`, `History`, `Trace`, `TraceEntry`, `Relation`, arbitrary domain records, and other types outside its admitted union.
- Arbitrary `Money`, `Decimal`, date, `Id`, or `Ref` objects embedded inside generic payloads do not become persistable automatically. Core record fields have their own codecs; domain payloads require an explicit finance codec into Memory's accepted primitive/tuple/string-keyed mapping value domain.
- Encode decimal values as canonical strings, money as currency plus integer minor units, and embedded references with full versioned identity/namespace fields. Reconstruct them explicitly. Do not pickle, silently stringify, or reflect over arbitrary objects.
- Memory rejects Core errors carrying a live foreign exception. Preserve an explicitly redacted diagnostic representation in finance audit, and create a separately identified persistable error summary linked to the original error identity when Memory recall is required. Do not rewrite an existing error under the same identity.
- Memory's current store operations have their own transaction boundary. Do not claim they can join a finance unit of work or share a connection through a public API that does not exist.

Commit ledger changes, approval consumption, idempotency results, durable audit, and any Memory-delivery outbox item in one finance transaction. A later idempotent dispatcher can mirror supported evidence into Memory. Track pending, delivered, and failed delivery. A Memory outage must not produce an ambiguous ledger commit or cause a financial mutation to be repeated.

Keep the authoritative evidence required to explain the ledger in finance storage even when optional Memory indexing is unavailable. Use separate database files/connections and do not couple to private Memory internals. Introduce the Memory adapter only in a slice that demonstrates a concrete recall requirement.

## 5. Product and terminal interface

Build a private local financial command center for quick weekly entries and formal monthly reconciliation. Use Python 3.14, Textual, and SQLite. The initial profile is empty or explicitly imported. Synthetic demo data is opt-in, labeled, and isolated.

Build a dense, polished terminal interface with keyboard navigation, compact tables and panels, crisp borders, clear hierarchy, and useful terminal-native charts.

Use the Exotic-20 palette:

```text
#141716  #1f2928  #203536  #3d2b79  #6f2a69
#3343ff  #456c2e  #1b7e51  #8533c4  #ff334e
#836cf2  #6a8ba7  #57b277  #22c6bc  #ea6be8
#47f232  #6bc9c3  #c1c2fa  #b0ed95  #f8ffbb
```

Use `#141716` for the base; `#1f2928` and `#203536` for raised surfaces; `#f8ffbb` for primary text; `#c1c2fa` and `#6a8ba7` for secondary text; greens for healthy states; cyan/blue for information and forecasts; magenta/violet for warnings; and `#ff334e` for urgent negative states. Verify contrast for actual foreground/background pairs. Pair color with text, symbols, or line styles.

Provide these tabs in order:

| Tab | Required behavior |
|---|---|
| Overview | Net worth and period change; spendable/protected cash and holds; upcoming inflows/outflows; 30-day low; safe-to-allocate; debts and APR; assets and gain/loss; node cash flow; reconciliation status; anomalies and explainable next actions; history/allocation/debt charts. |
| Ledger | Searchable, filterable register with effective and recorded dates, description, account, tags, source, reconciliation state, and amount. Full posting detail, quick entry, splits, review/post/reverse/duplicate, saved filters, CSV import/export. |
| Cash | Operating cash, protected allocations, holds, schedules, 30/60/90-day forecasts, event-boundary balances, projected minimum/date, cash floor, safe-to-allocate, event timeline, inflow/outflow charts. Split view pairs the horizon chart with the event register and explanation. |
| Debts | Balances, APRs, minimums, due dates, status, history; minimum-only, avalanche, snowball, and custom comparisons with payoff dates, interest, cash impact, and milestones. |
| Assets | Cash, investments, crypto, property, other assets; quantities, known cost basis, valuation timestamps; contributions, withdrawals, transfers, realized/unrealized results, allocation, and freshness. |
| Nodes | Income-producing projects/businesses/deployments with revenue, expenses, capital, withdrawals, cash flow, assumptions, milestones, health, runway, and return metrics. Distinguish operating performance from personal transfers. |
| Forecast | Base/optimistic/pessimistic/custom scenarios, inspectable assumptions and event stream, cash/net-worth/debt/asset projections, scenario comparisons, milestones, uncertainty, and freshness. |
| Reconcile | Statement observations, suggested matches, unresolved differences, duplicate/missing/stale items, difference-to-zero, explicit close, locked periods, and auditable later corrections. |

Support `[t] TEXT`, `[g] GRAPH`, and `[b] SPLIT` views where useful. Include persistent key hints, contextual commands, command palette, global search, date ranges, scenario selection, and connection/sync status. Provide complete keyboard navigation and usable compact/wide layouts.

Charts need titles, units, time range, labels/legends, actual-versus-forecast distinction, and an inspectable textual equivalent. Include line/step charts, sparklines, stacked bars, debt timelines, scenario comparisons, allocation charts, and reconciliation progress. Do not graph missing values as zero.

## 6. Accounting and calculation rules

Use an append-only double-entry ledger. Every posted entry has at least two postings and balances exactly per currency. Signed postings are positive for debit and negative for credit. Account types determine display balance sign. Reject unsupported mixed-currency operations until explicit exchange and valuation rules exist; never add incompatible currencies.

Posted entries and postings are immutable. Corrections use reversing and replacement entries. Keep the original posted entry unchanged and include both the original and its reversal in balance calculations; reversal status is derived from their relationship. Closed periods are immutable; later corrections use a permitted open effective period while retaining references to the original entries. Explicitly track effective date, recorded timestamp, authorizing principal, and sources.

Keep actual postings, observations, import candidates, schedules, forecasts, and drafts distinct. Allocations are claims on existing money and do not increase assets. Internal transfers do not create income or investment return. Node transfers do not inflate operating performance.

Split debt payments into principal, interest, and fees where applicable; only principal reduces the debt liability. Classify investment contributions/withdrawals relative to the measured portfolio's boundary, so transfers inside that portfolio do not masquerade as external flows.

Implement these definitions with explicit completeness, currency, valuation, and horizon policies:

```text
net_worth = total_assets - total_liabilities

spendable_cash = eligible_liquid_cash - protected_allocations - active_holds

projected_balance(t) = current_operating_cash
                       + scheduled_inflows_through(t)
                       - scheduled_outflows_through(t)
                       - additional_committed_outflows_through(t)

thirty_day_low = minimum projected balance over the starting point
                and every relevant event boundary in the 30-day horizon

safe_to_allocate = max(0, min(current_unallocated_cash,
                            minimum_over_horizon(projected_balance(t)
                                                 - cash_floor(t))))

weighted_debt_apr = sum(balance_i * apr_i) / sum(balance_i)

investment_gain = ending_value - starting_value - contributions + withdrawals

node_net_cash_flow = node_revenue - node_operating_expenses
```

Define reservation accounting once: an earmark is not a cash outflow, and a scheduled bill already in the forecast must not be subtracted again as a committed allocation. Deduplicate by linked obligation/occurrence identity. Document whether holds reduce eligible opening cash or appear as future constraints; do not subtract them twice.

Include cash-floor changes and same-day ordering in horizon calculations. If inputs needed for a safe allocation are unknown, report unavailable with missing inputs; do not manufacture a safe amount. With zero debt, APR is not applicable. Define behavior for negative cash, refunds, reversals, missing/stale valuations, foreign currencies, partially reconciled periods, and incomplete account coverage. Investment gain is an amount; percentage return requires a separately specified method.

## 7. Finance storage and application structure

Use migrations and narrow repositories with a finance unit of work. Enforce foreign keys and appropriate checks/triggers. Reject finalization unless postings balance and count is at least two. Reject inserting an entry directly into finalized state and adding new postings to a finalized entry. Prevent update/delete of finalized entries and postings, including reparenting postings. Guard closed-period writes and ensure reversal links cannot bypass the rules. Test all INSERT, UPDATE, and DELETE paths rather than copying the earlier illustrative SQL without review.

Deduplicate external records by connector identity/account, external ID, and record type. Keep import provenance, raw-source hash, normalized values, confidence, and match state. Minimize retained external content. CSV import uses explicit schemas and actionable row-level errors; it stages reviewable candidates.

Suggested package layout; create modules as their slices earn them:

```text
src/personal_finance/
  __init__.py, __main__.py, app.py, config.py
  domain/
    money.py, kinds.py, accounts.py, ledger.py, allocations.py
    schedules.py, debts.py, assets.py, nodes.py, scenarios.py
    reconciliation.py, approvals.py
  services/
    ledger.py, drafts.py, forecasting.py, reconciliation.py, integrations.py
  ports/
    repositories.py, unit_of_work.py, approvals.py, connectors.py, secrets.py
  projections/
    overview.py, cash.py, debts.py, assets.py, nodes.py
  forecasting/
    engine.py, event_stream.py, assumptions.py, metrics.py
  persistence/
    sqlite.py, codecs.py, repositories/, migrations/, outbox.py
  evidence/
    provenance.py, reconciliation.py, memory_adapter.py
  integrations/
    coinbase/, gmail/, calendar/
  mcp/
    server.py, authorization.py, policy.py, schemas.py, resources.py, tools.py
  tui/
    screens/, widgets/, charts/, forms/, approval_dialogs/, theme.tcss
  audit/
    records.py, sink.py, redaction.py
tests/finance/
  unit/, integration/, property/, architecture/, mcp/, acceptance/
```

These are finance-specific uses of Core, not replacements for Core types. Avoid generic `identity.py`, `result.py`, or `context.py` reimplementations.

Plan ordered migrations for: ledger; schedules/allocations; debts/assets/nodes; forecasts/reconciliation; connectors/imports; clients/drafts/approvals/audit/outbox. Add tables when implemented, with repeatable migration tracking and failure recovery. Bound SQLite integer inputs and aggregate behavior explicitly so overflow cannot silently turn exact money into floating point.

## 8. MCP and approval policy

Expose one provider-neutral MCP interface for compatible Codex, Claude, and other clients. MCP tools call the same application services used by the TUI. Agents receive neither direct SQL capabilities nor connector credentials.

Start with local STDIO; optional Streamable HTTP must bind only to loopback with client authentication. Remote access is disabled until explicitly implemented with appropriate authentication and transport security. Verify current official MCP and client APIs before building transport-specific behavior.

Contract namespace/version: `personal_finance` v1.0. Use opaque IDs, decimal strings for money, ISO dates, and RFC 3339 timestamps. Encode known/unknown explicitly in structured outputs; never substitute zero. Return request ID, generation/as-of times, data, actual/forecast classification, sources, provenance reference, calculation version, warnings, missing inputs, coverage, and freshness.

Read/analyze tools include:

```text
finance.overview.get
finance.cash.forecast
finance.ledger.search
finance.ledger.entry.get
finance.debts.list
finance.debts.plan.compare
finance.assets.summary
finance.nodes.summary
finance.forecast.run
finance.reconciliation.status
finance.import_candidates.list
finance.integrations.status
finance.metric.explain
```

Expose resources for overview, agent policy, ledger/calculation schemas, latest snapshot, reconciliation, and integration status. Provide strict input/output schemas, pagination, bounded queries, redaction, and appropriate risk annotations. Annotations describe risks; server authorization enforces policy.

Use risk classes R0 metadata, R1 private reads, R2 drafts/simulations, R3 local authoritative mutations, R4 external mutations, R5 financial execution. Draft creation is a persisted mutation even though it does not post to the ledger. Define ANALYST, BOOKKEEPER, and OPERATOR profiles with least-privileged explicit enrollment. Model brand and a client-supplied name do not establish identity or permission.

Agent-requested authoritative changes follow:

```text
prepare -> validate -> local human review -> approve exact content
        -> revalidate -> commit -> record result
```

Direct local entry uses an explicit review/post confirmation. Agents cannot grant human approval. The trusted local approval surface must be separate from agent-callable methods; an `approved=true` parameter is never sufficient.

Create narrow prepare/commit operations for entries, reversals, allocations, schedules, reconciliation decisions, and later calendar actions. Commit accepts `draft_id` and `idempotency_key`, never a replacement payload. Approval binds to canonical versioned content hash, action type, principal, policy version, scope, and expiration. Revalidate policy, permissions, data freshness, affected state/version, and closed periods under the transaction lock. Material changes require renewed review.

Persist approval consumption, financial mutations, audit, and idempotency result atomically. Same-key/same-request replay returns the original result; same-key/different-request fails. Concurrent commits cannot post twice. Draft cancellation, rejection, expiry, and supersession remain inspectable.

Financial execution remains disabled in the initial application. Never expose withdrawal, external asset transfer, unrestricted mail sending, deletion, leverage, or derivatives tools.

## 9. Connectors

Implement connectors after the ledger, projections, reconciliation, and approval foundation is tested. Verify official current provider contracts, supported data coverage, scopes, pagination, rate limits, and error behavior. Keep transport contracts distinct from authenticated live verification; report exactly what was tested.

All external data is untrusted evidence. Descriptions, email bodies, calendar text, and documents cannot instruct an agent, change policy, approve a draft, run commands, or trigger execution. Connector outputs enter observations/staging and require explicit review before becoming authoritative financial records.

**Coinbase:** start with read-only account, balance, position, transaction/fill/fee/reward, and valuation observations where supported by the selected APIs. Show source/freshness and missing coverage. Use read-only credentials and OS-backed secrets. Stage candidates and observations; do not silently post. Later order preparation can show quantity, price, fees, slippage, remaining cash, concentration, and plan impact. Execution is a separate future authorization and milestone with fresh quotes/balances, limits, allowlists, enhanced local approval, idempotency, and post-fill reconciliation. Transfer/withdrawal credentials and tools stay excluded.

**Gmail:** begin with configured financial labels/senders and metadata where sufficient. Full-message read-only access is a separate explicit choice. Verify which search/filter operations each scope actually permits. Preserve message IDs, hashes, extraction evidence, and confidence. Minimize retained message bodies. No automatic sending, deleting, archiving, labeling, or mailbox-wide ingestion.

**Calendar:** begin read-only for paydays, bills, renewals, close sessions, and milestones. Generate forecast candidates, not actual postings. Future writes require reviewed drafts and a dedicated Finance calendar; unrelated calendars stay outside write scope. Verify timezone and recurring-event behavior.

Support bounded incremental sync, checkpoints, deduplication, partial failures, cancellation, rate limits, and visible retry state. Store credential references and scope metadata in SQLite; store secrets in an OS-backed secret mechanism and redact them from logs, exports, and UI.

For future external writes, commit a local intent before the request, avoid holding SQLite transactions open over the network, use provider idempotency where available, and record/reconcile the actual result. A timeout means outcome unknown until checked; do not claim universal exactly-once external execution.

## 10. Audit, privacy, and recovery

Record who or what read, prepared, approved, rejected, and committed an action, tool/version, input hash, relevant records, external influence, decision, effects, resulting IDs, and structured errors. Keep durable audit append-only and transactionally tied to local authoritative changes. Distinguish operation trace, derivation provenance, accounting history, and security audit while linking them by identity.

Provide a visible kill switch for integrations and agent mutations, with consequential capabilities off by default. Protect sensitive exports/backups with appropriate access controls and encryption where practical. Document the actual local threat model; append-only application rules do not protect against a person with unrestricted filesystem access. Implement backup and restore verification before claiming recovery guarantees.

Keep blocking database, network, and expensive calculation work off the Textual message loop. Use explicit worker ownership and cancellation, serialize writes, and prevent stale worker results from overwriting newer view state. Do not share Core or Memory single-writer containers unsafely across workers.

## 11. Delivery slices and acceptance

Keep the app runnable after each slice. Preserve the product scope above while delivering executable increments.

1. **Core-derived finance foundation:** write the finance construction map and executable invariants; package/entry points; immutable money/accounts/postings; SQLite migration and unit of work; balanced posting/reversal; persisted projections; eight-tab themed shell with honest empty states.
2. **Useful local ledger:** keyboard quick/split entry, detail, search, review/post/reverse, saved filters, CSV staging/export, foundational charts, and text/graph/split modes.
3. **Cash and planning:** schedules, allocations, holds, observation coverage, deterministic event stream, 30/60/90-day projections, safe-to-allocate, and provenance explanations.
4. **Debts, assets, and nodes:** typed models, valuations, performance, payoff/scenario comparisons, and corresponding views.
5. **Reconciliation:** observations, suggested matches, explicit contradictions/resolutions, close workflow, period locks, and later adjustments.
6. **MCP:** authenticated local transport, schemas/resources, read/analyze tools, draft/review/approval/commit enforcement, and complete audit.
7. **Read-only integrations:** connector ports, secrets, Coinbase/Gmail/Calendar adapters, staged imports, failure recovery, and evidence recall through Memory where justified.
8. **Hardening:** packaging, accessibility, performance, backup/restore, threat model, redaction, and documentation.

The first delivery milestone comprises slices 1 and 2 and is complete when:

- The app installs and starts through a documented command using the repository's supported Python version.
- All eight tabs render with the Exotic-20 theme and keyboard navigation; deferred functionality has explicit empty/unavailable states.
- Overview, Ledger, and Cash support text/graph/split views using implemented recorded data; forecasting remains explicitly unavailable until slice 3.
- Accounts and balanced entries can be created, listed, inspected, reviewed, posted, and reversed.
- Migrations initialize a new database and rerun safely. Posted history is protected in both service and database layers, including reopen and direct-invalid-write tests.
- Overview and Ledger read persisted data, distinguish incomplete coverage from known zero, and update after posting/reversal.
- At least one important ledger projection uses Core context and provenance with an inspectable explanation.
- Optional synthetic demo data uses a separate profile/database.
- Applicable Core/Memory regression checks and new finance tests pass; existing unrelated failures are reported accurately.
- README documents architecture, install/run/test commands, storage, safety guarantees, backup limitations, and the next slice. Add MCP/connector setup when those slices ship.

## 12. Verification and working style

Use the existing pytest, Ruff, and strict Pyright conventions. Add deterministic clocks and ID sources to finance tests. Write meaningful property/integration/contract/acceptance tests for:

- Exact money/Decimal conversion, rounding, currency separation, and overflow boundaries.
- Balance invariants, account signs, reversals, immutable history, rollback, and concurrent commit/replay behavior.
- Allocation and transfer non-double-counting; unknown/incomplete/stale input propagation.
- Forecast starting balance, event ordering, same-day boundaries, schedules/timezones, and cash-floor changes.
- Reconciliation scope/cutoff matching, retained evidence, explicit decisions, and closed-period protection.
- Import deduplication, partial sync, retries, rate limits, and unknown external outcomes.
- MCP identity/profile checks, approval isolation, hashes, expiry, policy/state changes, replay, and idempotency.
- Finance codec round trips, full identity preservation, immutable payload snapshots, and optional Memory outbox recovery.
- Provenance source retention and explicit unresolved references; trace order distinct from financial effective time.
- Import boundaries: Core and Memory never import finance; finance domain never imports UI/MCP/vendor/SQLite adapters; application imports cause no external work.
- Headless Textual keyboard interactions, empty/success/failure states, navigation, view modes, and compact/wide layouts. Inspect rendered output before claiming visual correctness.

Before implementation, provide a concise plan based on the actual repository. Then implement the agreed milestone, run relevant checks, and report concrete behavior, important design choices, verification results, known limitations, and the next highest-value slice. Keep domain rules out of UI handlers, inject runtime dependencies, preserve unrelated work, and avoid broad scaffold-only integrations.

**Definition of “made out of Core”:** the application's meaningful financial records, evidence, derivations, changes, failures, and execution boundaries compose Core's existing semantics, while finance-specific invariants and durable transactional enforcement remain explicit in the finance layer. Demonstrate that composition in working flows and tests.
