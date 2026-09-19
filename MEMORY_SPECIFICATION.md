# Memory — Semantic Specification

Memory is a personal library of derived constructions over Core's closed v0 ontology (`SPECIFICATION.md`, `LAWS.md`, `ARCHITECTURE.md`) — built to answer one question Core deliberately leaves open: what does a persistent reasoning agent need, beyond what Core already represents, to preserve, group, retrieve, bound its attention over, and govern the accessibility of what it has observed and asserted, without inventing a second, competing notion of truth? Memory adds no new ontology primitives. Everything here is either a direct reuse of a Core concept, an operation over Core concepts, or one of five new derived constructions, each earned by a concrete need Core does not meet.

This specification went through five rounds of adversarial correction before being frozen. Each is preserved below as a "round N fix," for the same reason Core preserves its own: the shape of a construction is often only intelligible in light of the counterexample that forced it.

## Central rule

Memory persists and retrieves Core facts and Memory's own derived records; it never reimplements what `core.epistemic` already does. `memory.*` depends on `core.*`, never the reverse. Core v0 is closed — nothing here modifies it, even where a gap is found (see round 3, item 1, and `MEMORY_LAWS.md` law 12).

## Vocabulary disposition

| Term | Disposition |
|---|---|
| Memory item | Reuse — any Core `Entity` (carries `Id`) |
| Observation, Event, Claim, Inference, Contradiction, Resolution | Reuse — unchanged Core constructions |
| Belief | Conceptual role — a `Claim` currently admitted; realized by the `BeliefProjection` operation, never a stored type |
| Revision | Operation, not a type — creates new `Claim`s and preserves prior ones; invokes Core's `Contradiction`/`Resolution` machinery only when statements are actually semantically incompatible in a shared relevant context; current-admission is a projection, never mutation of an old `Claim` |
| Remembering | Dropped — an emergent property of retrieval and use, not a stored fact |
| Supersession | Dropped — the non-conflicting case ("balance was $900 Monday, $1,200 Friday") is answered by `BeliefProjection` selecting among context/time-compatible `Claim`s; no separate record is needed |
| Attention | Conceptual role, no dedicated type — realized by `WorkingSet`'s bounded admission |
| Forgetting | Operation/projection, not a stored record — realized by `RetentionLog.current()`; never deletion |
| Episode | **New** derived construction |
| Retrieval, Recall | **New** operations (not types) — retrieval locates `RecallCandidate`s; recall admits them into a `WorkingSet` |
| RecallCandidate | **New** derived construction |
| WorkingSet | **New** derived construction |
| RetentionMark / RetentionLog | **New** derived construction |
| BeliefProjection | **New** derived construction — the structured result of the belief-projection operation (same category as Core's own `AncestorReport`: an operation's result, not a persisted record) |
| Consolidation | Deferred to Memory v1 — see "Explicitly not in this specification" |

## Round 1 fix — initial shape-setting

1. Revision is narrowed: it creates new `Claim`s and preserves prior ones; `Contradiction`/`Resolution` machinery applies only to genuine semantic incompatibility in a shared context, never to ordinary context/time-varying restatement.
2. Attention and Forgetting are conceptual roles, not stored types — realized by `WorkingSet`'s bound and `RetentionLog`'s projection respectively, mirroring how Core treats `Capability`/`Constraint`.
3. An Episode's grouping is its own assertion, not an inherited property of its members — `Event` carries no `subject` field of its own, so an Episode's `subject` describes the grouping, never a claim that every member intrinsically shares it.
4. An Episode's member order is authoritative by insertion position, never reconstructed by sorting `Observation`/`Event.at` — the same discipline that motivated `Trace`'s `Sequence` mechanism.

## Round 2 fix — shape hardening

1. Episode's mutable state made precise: a private backing store, `.items()` returning a defensive tuple snapshot, `.close()` one-time (raises on a second call, raises if `closed_at < opened_at`), `.append()` after close raises, duplicate `Ref`s preserved rather than deduplicated.
2. `RecallCandidate.relevance: tuple[Kind, ...]` — named match kinds (identity / lexical / contextual / ...), never a bare score. This keeps Memory v0 at five constructions rather than hiding a sixth behind an `object` field, and keeps backend-specific ranking (BM25, cosine similarity, ...) out of the semantic type entirely.
3. `WorkingSet.admit()` performs no reranking, scoring, or deduplication — it accepts candidates already ordered by the caller's retrieval/selection policy, admits the first `capacity`, and always returns the exact remainder as `excluded` (information is not silently discarded). `capacity == 0` is a legitimate empty `WorkingSet`.
4. `RetentionLog.current()` compares by `identity_of()`, not raw `Ref` equality — a mark recorded against one `Ref` to an `Id` governs accessibility no matter which differently-namespaced `Ref` to the same `Id` is later queried. "Last mark wins" means append order, exactly like `ContradictionLog`, never `WallInstant` order. Unrecognized/custom accessibility `Kind`s are preserved and returned exactly, never reinterpreted.
5. `BeliefProjection.contradiction: Ref | None` is replaced by `conflict_entries: tuple[Contradiction | Resolution, ...]` — a single optional `Ref` loses information (a candidate may participate in several `Contradiction`s, and `Resolution` isn't `Entity`-bearing in Core, so it has no `Ref` to hold anyway). Status invariants are restated as independent conditions with an explicit precedence order (see the `BeliefProjection` entry below).
6. No temporal carry-forward: because `Context.as_of: WallInstant` is a required, non-optional field, `Context.merge()` treats any two differing `as_of` values as a structural conflict (verified directly against `core/context.py`) — a `Claim` whose context doesn't match a query's `as_of` is simply incompatible, never silently treated as still-applicable.

## Round 3 fix — persistence boundary

1. Persistence eligibility is not equivalent to satisfying Core's `Entity` protocol — `Resolution` and `RetentionMark` are legitimately persisted despite carrying no `Id`; conversely, nothing in Memory promises to persist every `Entity` Core can produce. The admissible record union is pinned at Pass 2 preregistration, not frozen here.
2. Identified records (those with a Core `Id`) are keyed by `(kind, value)`; non-`Entity` append-only records (`Resolution`, `RetentionMark`) receive a storage-local sequence key that is never promoted into a Core `Id` and is never `Ref`-targetable.
3. Episode persistence is three one-way transitions — create, append, close — never a snapshot replace. Stored items may only gain a suffix; `closed_at` moves `None → WallInstant` exactly once; existing items, and a set `closed_at`, never change afterward.
4. The codec boundary: `PersistedValue` — a small, closed, recursive domain (`None`, `bool`, `int`, `float`, `str`, `bytes`, and tuples and string-keyed mappings thereof) — is the only shape an `object`-typed Core payload (`Observation.value`, `Claim.value`, `Event.payload`, `Effect.target`, `Context`'s optional fields, `metadata`) may take to cross the durable-storage boundary. `as_persisted_value()` validates — it never converts or coerces — and fails loudly, naming the offending value's exact path, on anything outside the domain. Never `repr()`, never `pickle`, never a string fallback.
5. Lexical indexing is a distinct concern from persistence — only genuinely `str`-typed Core fields, or `object`-typed fields whose `PersistedValue` encoding already happens to be `str`, are indexed. Nothing is stringified for search purposes.
6. Every module dependency edge is named explicitly in `MEMORY_ARCHITECTURE.md` — no `core.*` shorthand.
7. A `Contradiction`'s relevance to a `(subject, predicate)` belief slot is established through its referenced `Claim`s, since Core's `Contradiction` carries no `predicate` field of its own: subject match, plus at least one `statement` `Ref` resolving to a `Claim` in that slot.
8. Retrieval composes backend search with `RetentionLog.current()`'s accessibility projection — `RetentionLog` remains the sole semantic authority for "currently accessible," regardless of which backend, or which stage of a query pipeline, applies the filter.

## Round 4 fix — adversarial-matrix-driven

1. `PersistedValue` floats must be finite (`inf`/`-inf`/`nan` rejected); sign is preserved exactly, never canonicalized (`-0.0` round-trips as `-0.0`).
2. `as_persisted_value()` returns a defensive recursive immutable snapshot of validated container data, never a reference to the caller's — possibly still-mutable — input.
3. Persisting the same `Id` twice with a canonically identical record is idempotent success; persisting a different record under the same `Id` is an explicit collision error, never a silent overwrite.
4. `RecallCandidate.relevance` must be non-empty with no duplicate `Kind`s.
5. `RetentionLog` accessibility gets a frozen default retrieval meaning (see the `RetentionMark`/`RetentionLog` entry below).
6. An Episode containing a `Ref` to itself, or into a cycle, is preserved opaquely — Episode groups references, it does not recursively interpret them, so self-reference is not inherently an error.
7. `store.py` owns both `MemoryStore` (the protocol) and `InMemoryStore` (a real implementer) from Pass 2 onward — mirroring Core's own rule that a Capability-like protocol is introduced only alongside a real implementer (Core's `EffectSink`/`MemoryEffectSink` precedent). `InMemoryStore` is also the reference implementation the SQLite backend is checked against.

## Round 5 fix — closing local contradictions found on review of the frozen documents against Core's own code

1. `BeliefProjection` retains `query_context: Context` rather than discarding it after use — Core's own law that context-dependent information carries its context applies to Memory's own operation results, not only to Core's records.
2. `belief_state()`'s last parameter is `conflict_entries: tuple[Contradiction | Resolution, ...]`, supplied directly (typically from a store's `conflicts_for()`), not a Core `ContradictionLog` — a pure projection should not force its caller to reconstruct a mutable log purely to invoke it.
3. Generic lexical retrieval indexes only content attached to a record that can actually be a `RecallCandidate.item: Ref` target. `Resolution` and `RetentionMark` are deliberately non-`Entity`, non-`Ref`-targetable — indexing their text for generic recall would require inventing a mapping to some other Entity that doesn't exist. Their text remains fully persisted and queryable through `conflicts_for()`/retention APIs directly; it is simply not exposed through the generic recall path.
4. `MemoryStore.retrieve()` never reads wall time implicitly — `RecallCandidate.retrieved_at` is supplied by the caller (a `WallInstant` parameter), the same discipline Core's `Transform.apply()` uses for its clocks.
5. Default retrieval's accessibility filtering fails explicitly when it encounters an accessibility `Kind` it does not recognize as `ACTIVE`/`DEPRIORITIZED`/`ARCHIVED` — it never guesses which of the three known states a custom `Kind` should be treated as. The item and its mark remain fully queryable through direct retention APIs regardless; only the *default*, accessibility-filtered retrieval path refuses to interpret an unrecognized `Kind` on its own.
6. `PersistedValue`'s `int` member is arbitrary-precision and round-trips exactly — its canonical encoding does not rely on any backend's native integer width (e.g. SQLite's 64-bit `INTEGER`). See `MEMORY_ARCHITECTURE.md`.
7. `store.py`'s dependency list includes `codec` — `InMemoryStore` is the reference implementation for admissibility, payload validation, and canonical-collision comparison, so it needs the same codec `SqliteMemoryStore` uses; otherwise the two backends could disagree exactly where `MEMORY_LAWS.md` law 15 forbids it.

## Per-concept specification

### 1. Episode

- **Definition**: an asserted, ordered grouping of references to preserved Core facts under one subject and context — the grouping is Memory's own assertion, not a property inherited from its members.
- **Question**: Which preserved facts belong together, and in what order were they admitted?
- **Distinction**: not `Trace` (opaque, execution-focused, no claim of subject-relevance; Episode explicitly asserts a subject+context grouping over content); not a claim that every member shares Episode's `subject` — `Event` carries no `subject` field at all.
- **Invariants**: append-only; insertion order (tuple position) is authoritative, never reconstructed from `WallInstant`; duplicate `Ref`s preserved, never deduplicated; `.append()` after `.close()` raises; `.close()` is one-time (a second call raises); `closed_at < opened_at` raises at close time; a returned `.items()` snapshot is defensively copied. Entity-bearing (`id: Id`) — it may itself be a retrievable memory item.
- **Relations**: members are `Ref`s to `Entity`-bearing records (typically `Observation`/`Event`); may itself be the `item` of a `RecallCandidate`.
- **Representation**: concrete, mutable, single-writer container (same concurrency family as Core's `Trace`/`History`/`ContradictionLog`) — `id: Id`, `subject: Id | Ref`, `context: Context`, a private ordered backing store exposed only via `.items() -> tuple[Ref, ...]`, `opened_at: WallInstant`, `closed_at: WallInstant | None`.
- **Operations**: construct(*, id, subject, context, opened_at) — `id` is caller-supplied, never internally allocated (Entity construction never allocates its own identity); `.append(ref)`; `.items()`; `.close(at)`.
- **Counterexample**: sorting an Episode's members by `Observation.at`/`Event.at` after the fact and treating that as authoritative order. Assuming an Observation referenced by an Episode necessarily shares the Episode's `subject`.

### 2. RecallCandidate

- **Definition**: a proposed memory item surfaced by a retrieval operation, together with structured evidence for why it was surfaced — never a claim about the item's truth, currency, or importance.
- **Question**: What was surfaced, under what query frame, and on what grounds?
- **Distinction**: not the memory item itself (points to one via `Ref`); not a ranking (see `WorkingSet`); not a guarantee of correctness.
- **Invariants**: `relevance` is non-empty with no duplicate `Kind`s; caller-supplied order within `relevance` is preserved but carries no ranking meaning. Not `Entity`-bearing in v0 — nothing targets a `RecallCandidate` by `Ref` (Ref-closure is one-directional, so omitting an `id` here doesn't violate it).
- **Relations**: `item: Ref` to any retrievable `Entity`; consumed by `WorkingSet.admit()`.
- **Representation**: concrete, immutable structure — `item: Ref`, `query_context: Context`, `relevance: tuple[Kind, ...]`, `retrieved_at: WallInstant`.
- **Operations**: construct; read fields; no mutation.
- **Counterexample**: a raw `(id, score: float)` pair with no indication of what "score" measures — exactly the conflation of similarity and identity Memory's laws forbid.

### 3. WorkingSet

- **Definition**: the bounded subset of `RecallCandidate`s currently admitted to active reasoning — the concrete realization of "attention," with no separate type for the role itself.
- **Question**: What, out of everything retrieved, is actually in front of the reasoner right now?
- **Distinction**: not a cache; not the full stored memory; not a persistence mechanism; not a ranking algorithm.
- **Invariants**: `len(admitted) <= capacity`; `capacity >= 0` (zero is a legitimate, empty `WorkingSet`); admission preserves input order exactly — no reranking, scoring, or deduplication inside `admit()`; every candidate not admitted is returned explicitly as `excluded`, never silently dropped.
- **Relations**: consumes `RecallCandidate`s produced by retrieval; independent of `RetentionLog`/`BeliefProjection` — attention and retention are separate axes.
- **Representation**: concrete, immutable snapshot (like Core's `State`) — `capacity: int`, `admitted: tuple[RecallCandidate, ...]`.
- **Operations**: `admit(candidates: tuple[RecallCandidate, ...], capacity: int) -> tuple[WorkingSet, excluded: tuple[RecallCandidate, ...]]`.
- **Counterexample**: an unbounded, ever-growing list of everything ever retrieved. `admit()` silently re-sorting candidates by a relevance heuristic it invents itself.

### 4. RetentionMark / RetentionLog

- **Definition**: an append-only history of accessibility changes to a memory item — the concrete realization of "forgetting" as a projection over history, never deletion and never a mutated flag. Direct sibling of Core's `ContradictionLog`.
- **Question**: How accessible is this item, right now, by default?
- **Distinction**: not deletion — Memory never removes a Core fact; not a judgment about truth, correctness, or importance — purely about default visibility to retrieval.
- **Invariants**: append-only; identity comparison uses `identity_of()`, so a mark recorded against one `Ref` governs accessibility regardless of which differently-namespaced `Ref` to the same `Id` is later queried; "current" means the last-appended mark for that identity, in append order — never re-sorted by `WallInstant`; unrecognized/custom accessibility `Kind`s are preserved and returned exactly, with no guessed policy; duplicate marks are permitted and preserved.
- **Relations**: `item: Ref` to any `Entity`; consulted by retrieval to filter/order `RecallCandidate`s.
- **Representation**: `RetentionMark` (concrete, immutable) — `item: Ref`, `accessibility: Kind` (well-known constants `ACTIVE`/`DEPRIORITIZED`/`ARCHIVED`, extensible via `Kind` rather than a closed enum, per Core's own convention), `at: WallInstant`, `rationale: str | None`. `RetentionLog` (concrete, mutable, single-writer, append-only) — an ordered sequence of `RetentionMark`.
- **Operations**: `RetentionLog.record(mark)`; `.current(item: Id | Ref) -> Kind` (defaults to `ACTIVE` when no mark exists); `.history(item: Id | Ref) -> tuple[RetentionMark, ...]`.
- **Default retrieval meaning** (frozen, round 4): `ACTIVE` — normal default eligibility. `DEPRIORITIZED` — eligible, ordered after `ACTIVE` results. `ARCHIVED` — excluded from default retrieval; recoverable only through an explicit archive-inclusive query. A custom `Kind` outside these three (round 5 fix): default retrieval fails explicitly rather than guessing which of the three known states it should be treated as; the mark and item remain fully queryable through `RetentionLog`'s own API regardless.
- **Counterexample**: `del store[item_id]`. A backend that encounters a custom `Kind` it doesn't recognize and silently treats it as `ACTIVE`.

### 5. BeliefProjection

- **Definition**: the structured, mechanically-derived result of asking "what is currently believed" for a `(subject, predicate)` slot under a query `Context` — an operation's result type, not a persisted record (same category as Core's own `AncestorReport`). Reports what can be projected without adjudication, and exposes — rather than resolves — what cannot.
- **Question**: Given only the `Claim`s and conflict history Memory already has, what — if anything — can be mechanically determined as currently believed?
- **Distinction**: not an epistemic judgment (Memory never adjudicates); not equivalent to `Claim(value=Unknown)` (a `Claim`'s own epistemic value is a different axis from whether the projection could determine a claim at all); not derivable from `Resolution.rationale` (never parsed).
- **Invariants** (frozen, round 2/4): filters candidates first by exact `(subject, predicate)` match (`identity_of()` for subject comparison), then by `Context.merge()` compatibility with the query context — never by `Claim.at` recency. Status is determined by independent conditions, checked in this precedence order:

  ```text
  1+ relevant Contradiction unresolved          → UNRESOLVED_CONFLICT
  else relevant Contradiction history exists,
       all resolved, no structured winner        → RESOLVED_OPAQUE_CONFLICT
  else 2+ compatible Claims                       → AMBIGUOUS
  else exactly 1 compatible Claim                 → DETERMINED
  else                                             → UNKNOWN
  ```

  A `Contradiction` is relevant to a `(subject, predicate)` slot when its `subject` matches and at least one of its `statements` resolves to a `Claim` in that slot. `Resolution.rationale` is never inspected as structured data, regardless of its contents. Candidate cardinality and conflict status are independent — a slot can have zero locally available candidates and still report a conflict status, if conflict history references it.
- **Relations**: reads Core's `Claim`, `Contradiction`, `Resolution`; never writes them.
- **Representation**: concrete, immutable structure — `subject: Id | Ref`, `predicate: Kind`, `query_context: Context`, `status: Kind` (`DETERMINED | AMBIGUOUS | UNRESOLVED_CONFLICT | RESOLVED_OPAQUE_CONFLICT | UNKNOWN`), `candidates: tuple[Claim, ...]`, `conflict_entries: tuple[Contradiction | Resolution, ...]` (in log order). `query_context` is retained, not discarded after use — it determined the result, and Core's own law that context-dependent information carries its context applies here too (round 5 fix).
- **Operations**: `belief_state(subject, predicate, query_context, claims, conflict_entries) -> BeliefProjection`, where `conflict_entries: tuple[Contradiction | Resolution, ...]` is supplied directly by the caller (typically the store's `conflicts_for()`) rather than reconstructed as a Core `ContradictionLog` — `belief_state()` never requires its caller to rebuild a log purely to call a pure projection (round 5 fix).
- **Pass 2 hazard**: `belief_state()`'s relevant-`Contradiction` rule (above) requires its `claims` parameter to be the *unfiltered* slot contents. A future `claims_for()` store method (Pass 2+, see `MEMORY_ARCHITECTURE.md`) must never pre-filter by context before calling `belief_state()`, or `Contradiction`s referencing context-filtered-out `Claim`s will silently vanish from the projection — a law-8 violation, since "conflict is surfaced, never adjudicated" implicitly requires conflicts not be hidden either.
- **Counterexample**: `max(claims, key=lambda c: c.at)` to pick between two birth-date `Claim`s. `if "claim B" in resolution.rationale: ...`.

## Persistence and the codec boundary

Persistence is governed by three commitments, realized in `MEMORY_ARCHITECTURE.md`:

1. **Persistence eligibility is not equivalent to Core `Entity` membership.** The admissible record union is a Memory-level decision, frozen at Pass 2 preregistration (`docs/memory-passes/02-persistence-boundary.md` §2; see `MEMORY_ARCHITECTURE.md`).
2. **Storage-local identity never becomes semantic identity.** A `Resolution` or `RetentionMark` persisted with a SQLite row key remains non-`Entity` after persistence; no API exists to construct a `Ref` to a storage-local key.
3. **`PersistedValue` is the entire durable-value domain**, and crossing it is validated, never converted. Anything outside the domain fails loudly, by exact path, at persist time — never stringified, pickled, or silently dropped.

**Pass 2 boundary clarification** (no new Memory construction — this narrows, not expands, the existing three commitments above): generic `persist()` excludes `Episode`, which uses its own `create_episode`/`append_episode`/`close_episode` surface instead, since its mutable append/close transitions can't be expressed as one call. Two Core structures embed other admissible Entity records directly — `Inference.conclusion` (a `Claim`) and `Error.cause` (an `Error | None`) — and persisting the parent atomically registers the embedded record too, under its own `Id`, in the same single stored-identity regime as everything else; this is structural (only these two specifically-typed fields), never generic reflection over arbitrary objects.

## Explicitly not in this specification

- **Consolidation** (deriving compressed/durable memories from raw ones) — deferred to Memory v1. It is the one construction that plausibly needs judgment (an LLM or equivalent) rather than pure Core-derived mechanism, and nothing in v0 depends on it existing.
- **A vector similarity backend** — `RecallCandidate.relevance` is designed to stay backend-neutral so one can be added later behind the same retrieval interface, but none is built in v0.
- **A general codec registry or plugin mechanism** — `PersistedValue` is a fixed, closed domain in v0; a registered-codec-per-type mechanism is unearned abstraction until a real payload type needs one.
- **Automatic search-document extraction from arbitrary payloads** — only genuinely textual content is ever indexed.
- **Any adjudication of `Contradiction`s** — Memory surfaces conflicts; it never picks a winner, in v0 or any later version, without a structured Core-level answer to rest on.
- **Any modification of Core**, including `Resolution`'s shape. Core v0 is closed; the gap this creates (no structured resolution outcome) is recorded as evidence, not fixed by editing frozen code.

## Derived constructions

The five new derived constructions this specification adds (`RetentionMark`/`RetentionLog` counted as one pair, the Retention construction):

| Construction | Built from |
|---|---|
| `Episode` | `Id` + `subject: Id \| Ref` + `Context` + ordered `Ref` items + `WallInstant` open/close |
| `RecallCandidate` | `Ref` + `Context` + `relevance: tuple[Kind, ...]` + `WallInstant` |
| `WorkingSet` | `capacity: int` + `admitted: tuple[RecallCandidate, ...]` |
| `RetentionMark` / `RetentionLog` | `Ref` + `accessibility: Kind` + `WallInstant` + optional `rationale`, held in an append-only sequence with a last-mark-wins projection |
| `BeliefProjection` | `Id \| Ref` + `Kind` (predicate) + `Context` (query) + `Kind` (status) + `tuple[Claim, ...]` + `tuple[Contradiction \| Resolution, ...]` |

## Supporting semantic/runtime machinery

Not counted among the five constructions above — one is a value-domain refinement (the same category as Core's own `Kind`/`Maybe`), the other a Capability-like protocol (the same category as Core's own `EffectSink`), neither a new ontological answer to "what is memory":

| Item | Category | Built from |
|---|---|---|
| `PersistedValue` | semantic value-domain refinement | `None \| bool \| int \| float \| str \| bytes \| tuple[PersistedValue, ...] \| Mapping[str, PersistedValue]` |
| `MemoryStore` | Capability-like protocol | a narrow persistence/query interface, realized alongside a real implementer (`InMemoryStore`), per law 19 |

## Principles frozen for the implementation

1. Five new derived constructions, no new ontology primitives — everything else is reuse or an operation over Core.
2. Memory selects; Memory does not judge — every construction that touches epistemic conflict reports rather than adjudicates.
3. Ordering belongs to the construction that owns it — Episode's own insertion order, never wall time.
4. Attention is bounded by construction (`WorkingSet.capacity`), not by convention.
5. Accessibility changes are additive history (`RetentionLog`), never destructive mutation.
6. Persistence eligibility is a Memory-level decision, independent of Core's `Entity` protocol.
7. Storage-local identity and Core semantic identity are never conflated.
8. The durable-value domain (`PersistedValue`) is closed and validated, not converted — unsupported values fail loudly.
9. Lexical indexing never invents text from non-textual values.
10. A storage backend (SQLite or any future one) proves that Memory v0's semantics survive durability — it does not define those semantics. The pure semantic modules (`episode`, `recall`, `retention`, `belief`, `codec`) are fully testable with no backend at all.
11. Core v0 remains closed; every place Memory's needs outrun what Core currently represents is recorded as evidence for a possible future Core revision, never patched around by parsing free text or inventing hidden schemas.
