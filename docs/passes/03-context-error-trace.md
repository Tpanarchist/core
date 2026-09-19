# Pass 3 preregistration — `context`, `error`, `trace`

Pass-local API decisions not already fixed by SPECIFICATION.md/ARCHITECTURE.md. Implement, test, commit; do not reopen the frozen ontology or package graph from here.

## Scope

`core.context`: `Context`, `ContextConflict`, `ContextOverride`.
`core.error`: `Error`.
`core.trace`: `Trace`, `TraceEntry`.

Allowed dependencies remain exactly those frozen in ARCHITECTURE.md: `context` → `identity`, `time`, `result`; `error` → `value`, `identity`, `time`, `context`; `trace` → `value`, `identity`, `time`, `context`. `trace` must not import Event, Effect, Provenance, Transition, or Error. No constructor allocates an Id, reads a clock, or performs another hidden effect.

## `Context`

Frozen, slotted, keyword-only record: `as_of: WallInstant` (required) plus `namespace`, `scope`, `environment`, `source`, `authority`, `version`, `units` (all `object | None = None`, plain Value roles — no new wrapper types invented for these) and `metadata: Mapping[str, object] | None = None`. `None` on an optional dimension means "not supplied," not epistemic `Unknown`; a caller wanting "known to be unknown" carries `UNKNOWN` as the field's value. `metadata` keys must be non-empty strings; the mapping is defensively copied and exposed as a read-only view. Not promised hashable. Field order for merge/override is `dataclasses.fields(Context)`'s declared order — the single source of truth, not a separately hand-maintained list.

- **`with_(**changes) -> Context`**: only declared field names accepted (`ValueError` otherwise); explicitly setting an optional field to `None` clears it; no `ContextOverride` produced (the caller directly stated the replacement).
- **`merge(other) -> Result[Context, ContextConflict]`**: symmetric, conservative, non-lossy, ordinary `==` per field. Equal → unchanged; one `None` + one supplied → the supplied value; two different non-`None` values → a conflict. `metadata` is one atomic field — no recursive merge. `ContextConflict.conflicts` reports **every** conflicting field (not just the first), in declared field order, and must be non-empty.
- **`override(other, *fields) -> tuple[Context, ContextOverride]`**: explicit policy — named fields in `other` replace those in `context`, including replacement with `None`. Unknown or duplicate field names raise `ValueError`. `ContextOverride.changes` records only *effective* replacements (`old != new`) as `(field, old, new)`, in the caller's requested order; no-op overrides are allowed and yield an empty tuple. No `Contradiction` is created or imported here — `ContextConflict` stays purely structural.

## `Error`

Frozen, slotted, keyword-only, Entity-bearing: `id: Id`, `kind: Kind`, `message: str`, `at: WallInstant` (all required — no constructor allocates them), plus `cause: Error | None`, `exception: BaseException | None`, `context: Context | None`, `operation: str | None`, `recoverable: bool = False`, `metadata: Mapping[str, object] | None = None`. `message` must be non-empty; `operation`, if supplied, must be non-empty. `metadata` follows the same defensive-copy/read-only rule as Context. `Error.kind` classifies the failure independently of `Error.id.kind` — Pass 3 doesn't impose a universal `core.error` Id kind.

`cause`/`exception` stay distinct exactly as frozen: `cause` wraps a prior structured Core `Error`; `exception` carries a foreign Python exception. Both may be present; a foreign exception is never inserted into `cause`. `.chain() -> tuple[Error, ...]` returns the structured cause chain self-first, stopping at `None`; `exception` is never part of it. `.is_kind(kind) -> bool` is exact `Kind` equality — no hierarchy/wildcard/prefix matching. `Error` is not a Python exception subclass (`ContractError` is the later control-flow wrapper).

## `Trace`

`TraceEntry`: frozen, slotted — `subject: Id | Ref | None`, `kind: Kind`, `sequence: Sequence` (authoritative), `observed_at: WallInstant | None` (non-authoritative, observational only), `payload: object` (opaque, never interpreted), `context: Context | None`, `references: tuple[Ref, ...]` (opaque — `TraceEntry` never imports the concrete types a `Ref` might identify). May be constructed directly as a value, but `Trace`'s API never accepts a preconstructed entry for insertion.

`Trace`: a single-writer mutable container with stable identity — `Trace(id: Id)`, exposing `id` read-only (unchanged for the Trace's lifetime); internal entry storage is private. Not thread-safe in v0. No structural `__eq__` over changing entries — entity identity is `trace.id`, compared explicitly.

- **`append(*, kind, subject=None, payload=None, context=None, observed_at=None, references=()) -> TraceEntry`** is the only admission path; `Trace` constructs the `TraceEntry` itself and assigns `Sequence(space=trace.id, position=len(current_entries))` — starting at 0, contiguous and monotonic since there's no removal/reordering. No caller-supplied `Sequence` is accepted (no `sequence` parameter exists). `references` is copied to a tuple before storage.
- **`entries() -> tuple[TraceEntry, ...]`**: an immutable snapshot; never the live internal list; a previously returned snapshot is unaffected by later appends.
- **`since(marker: Sequence) -> tuple[TraceEntry, ...]`**: exclusive (entries strictly after the marker). Raises `ValueError` if `marker.space != trace.id`, or if `marker.position` doesn't identify a currently-existing entry (`0 <= position < len(entries)`) — never silently reinterpreted as "from the beginning" or "nothing new."

## Local failures

Pass 3 sits below `core.constraint`: invariants fail locally with `ValueError`/`TypeError`, never by importing the later structured-contract layer. Examples: malformed Context metadata keys, unknown Context field names, duplicate override field names, empty `ContextConflict.conflicts`, empty Error message, empty supplied Error operation, a Trace marker from another sequence space, a Trace marker not present in the Trace. No failure path allocates an Id or reads a clock.

## Equality and hashing

`Context`/`ContextConflict`/`ContextOverride`/`Error`/`TraceEntry` use ordinary structural dataclass equality; none is *promised* hashable (some instances will be, depending on whether they hold unhashable metadata/payload — that's fine, just not guaranteed). `Trace` is a mutable identity-bearing container with no structural equality; compare `.id` when entity identity matters.

## Import behavior

Importing `core.context`/`core.error`/`core.trace` allocates no Id, reads no clock, performs no filesystem/network/subprocess/environment/random operation, mutates no global registry, creates no ambient Context or Trace. Pass 6 provides the full architecture-wide mechanical audit; Pass 3 contains no top-level code that would violate it.
