# Core — Semantic Specification

Core is a personal foundational Python library built from an explicit ontology — sixteen irreducible concepts — and the twenty laws in `LAWS.md` those concepts must obey, rather than from utility code (files, logging, config). This document is Euclidean in spirit: definitions and axioms first, construction after. It defines *what exists* and *what it means*; `ARCHITECTURE.md` defines how that gets realized as an acyclic Python package.

This specification went through five rounds of review before being frozen. The "round N fix" annotations below are left in deliberately — they're provenance for *why* a given shape was chosen, which is exactly the kind of information this project believes should survive rather than be silently smoothed away.

## The concept → question table

The 16 concepts answer **orthogonal questions** about a computation — not mutually exclusive categories a thing belongs to. They compose freely (an Observation is also an Event; an Inference is also a Transformation; an Effect can have Provenance; a State can carry Context) while each still answering a distinct question.

| Concept | Fundamental question |
|---|---|
| Identity | Which thing? |
| Value | What information? |
| Context | Under what frame? |
| Time | When / in what order? |
| State | What holds, at this time/frame, for which thing? |
| Event | What occurred? |
| Observation | What was perceived or measured, about which thing? |
| Result | What came out of an attempt? |
| Error | How did an attempt fail? |
| Transformation | How did one thing become another? |
| Effect | What changed outside the computation itself? |
| Capability | What can this thing do? |
| Relation | How are things connected? |
| Constraint | What must or must not hold? |
| Provenance | Where did this come from? |
| Trace | What path did execution take? |

No two rows collapse onto the same question, and no obvious question about computation is left unanswered.

## Three conceptual levels

1. **Ontology primitives** — the 16 rows above.
2. **Semantic/runtime representations** — the minimal type(s) that realize a primitive at runtime, when representation is earned (law 19). Not every primitive gets one (Value's raw-information half, Capability, Constraint do not).
3. **Derived constructions** — built by *combining* primitives/representations; not new ontology dimensions. Fully inventoried below.

## Corrected mechanisms (rounds 1–2)

1. **Context** has three operations instead of one lossy `merge()`: `with_()`, `merge()` (returns `Result[Context, ContextConflict]`), `override()` (audited replacement). `ContextConflict` is structural, not the semantic `Contradiction`.
2. **Effect** splits into `EffectSpec` (declarative) and `Effect` (a record), recorded through an explicit sink — no ambient `contextvars` log.
3. **Transformation vs. composition**: `Transform` (one hop) vs. `Pipeline` (an ordered sequence, a derived construction). `.then()` returns a `Pipeline`, never another `Transform`.
4. **Contradiction handling** is append-only and immutable: `Contradiction`/`Resolution` are derived constructions; "unresolved" is a projection, never a mutated flag.
5. **Trace is genuinely primitive**: never a typed pointer to Event/Effect/Provenance/Transition. Trace knows evidence; it does not know what all possible evidence means.
6. **Identity** is `(kind, value)`. `Ref`/`Key`/`Namespace` are derived constructions built from it.
7. **Time** splits readings from producers; promises location/ordering/duration only, not simultaneity.
8. Not every concept gets a runtime wrapper (law 19).
9. **Provenance is a lazy DAG**: multiple parents represent convergence.

## Consistency and completeness fixes (round 3)

1. Derived-constructions inventory completed; `ProvenanceRef` retired in favor of `Ref`+`Id`; `Entity` resolved under Identity; `Maybe`/`Known`/`Unknown` resolved under **Value**, not Result.
2. **State** and **Observation** both gain an explicit `subject: Id | Ref`.
3. `Claim.evidence_refs: tuple[Ref, ...]` replaces a single typed `basis`; `Inference` gains an explicit `conclusion: Claim`.
4. `LogicalTime`'s operation renamed `precedes` (necessary, not sufficient, evidence of causality — real causal claims belong to Relation/Provenance). `Sequence` scoped: `(space: Id, position: int)`.
5. `Provenance` gets its own `Id`; `parents: tuple[Ref, ...]` points at other Provenance nodes. `Transform.apply()` does not inspect arbitrary input structures for `Traced` values — parent provenance is passed explicitly.
6. `Kind` is one shared semantic type for open categorical labels (refined further in round 4 below).
7. `require`/`ensure`/`invariant` raise `ContractError(Exception)` carrying an `Error`; their context parameter is a typed `Context | None`, not `**kwargs`.
8. `Deadline` binds a reading to a compatible producer of the same family.
9. Identity's equality invariant narrowed to canonical-token equality, not real-world co-reference.

## Consistency and closure fixes (round 4)

1. **Ref-closure rule, frozen**: *if a type may be the target of a `Ref`, instances of that type must implement `Entity` (carry an `id: Id`).* This is checked, not assumed — applying it adds `id: Id` to `Observation`, `Claim`, `Inference`, `Effect`, `Transition`, `Contradiction`, and `Trace` (each is a `Ref` target somewhere in this document). It does **not** add an id to `State`, `EffectSpec`, `ContextConflict`, `ContextOverride`, or `Resolution` — nothing here targets those with a `Ref`. `Provenance.inputs` is a best-effort citation of only those inputs that are themselves Entity-bearing; an anonymous/raw input contributes no `inputs` entry, but if it was `Traced`, its lineage is still fully captured through `parents`.
2. **`Transform.apply()`'s clocks are explicit parameters, not a silently-chosen default.** A Transformation needs two independent temporal capabilities — wall time (for `Provenance.at`) and monotonic time (for `Provenance.duration`) — so both are required arguments: `clock: Clock`, `monotonic_clock: MonotonicClock`. No bundling abstraction is introduced for this (law 19); no default concrete clock is specified here — which concrete `Clock` a caller supplies is an implementation/call-site decision, not a semantic one.
3. **Every `at`-shaped field is typed to a specific Time family**, closing a loophole that could otherwise undo the whole Time distinction: `Provenance.at: WallInstant` (creation) with `Provenance.duration: Duration` (separately, monotonically measured); `Observation.at: WallInstant`; `Event.at: WallInstant`; `Claim.at: WallInstant`; `Inference.at: WallInstant`; `Effect.at: WallInstant`; `Contradiction.detected_at: WallInstant`; `Resolution.at: WallInstant`; `Error.at: WallInstant`; `Context.as_of: WallInstant`; `Relation.at: WallInstant`. Trace gets special treatment (next point) rather than a bare wall-clock field, because wall time alone is not a reliable total order.
4. **Trace gains its own identity and becomes the authority for its own ordering.** `Trace.id: Id`. Each `TraceEntry`'s authoritative order is a `Sequence` scoped to that id (`Sequence(space=trace.id, position=n)`), assigned automatically by `Trace.append()` — callers never fabricate one by hand. A `TraceEntry` may *additionally* carry `observed_at: WallInstant | None` as non-authoritative observational time. `Trace.since(marker: Sequence)` therefore has a rigorous marker instead of an unspecified integer/index.
5. **`Kind` is homed as a semantic refinement of Value**: a small canonical namespaced value representing an open category (e.g. a dotted string wrapped for type safety, with well-known constants pre-built as values of the same type) — owned by **Value**'s representation, alongside `Maybe`/`Known`/`Unknown`, since both are refinements of "information," not of any other single primitive. **`Id = (kind: Kind, value: str)`**, not `(kind: str, value: str)` — Identity's own `kind` field is itself an instance of the shared `Kind` type, consistent with "every extensible categorical label uses `Kind`."
6. **The "no forward reference" rule**: not *no forward references whatsoever*, but *no forward **concrete/type** dependency* — a `Ref` may legitimately target a construction defined later in this document (e.g. `Claim.evidence_refs` pointing at an `Inference`, defined afterward) because `Ref` depends only on `Identity`, never on the target's concrete representation. Only genuine type dependencies (e.g. `Inference.conclusion: Claim`) require the referenced construction to appear earlier.
7. **The completeness claim is scoped precisely.** Constructions this ontology specifies for v0 (the derived-constructions table) are distinct from names that appear in this document but are deliberately deferred, implementation-level machinery — see "Deferred from this specification" below.

## Final closure fixes (round 5)

1. **`MonotonicInstant` and `LogicalTime` are scoped like `Sequence`**: `MonotonicInstant(space: Id, value: ...)` and `LogicalTime(space: Id, counter: int)`. Each producer (`MonotonicClock`, `LogicalClock`) carries its own `space: Id` and stamps it onto every reading it produces. Comparing two readings — of any of these three types — first requires their spaces to match; two independent processes' "monotonic" clocks are the same *family* but not the same *space*, and are not comparable.
2. **`Deadline`'s family rule is sharpened**: a `Deadline`'s reading and producer must belong to the same time family *and*, where that family is itself scoped (`MonotonicInstant`, `LogicalTime`, `Sequence`), the same time space — "same family" alone is insufficient, since two monotonic clocks in two different processes are the same family but their readings are meaningless relative to each other.
3. **The `Ref`-closure rule is strictly one-directional**: `Ref`-target ⇒ `Entity` ⇒ `Id`, never the converse. A type may carry an `Id` — for citation, provenance, equality, or future reference — without anything in this document currently holding a `Ref` to it. `Transform` is the standing example: it carries an `id` (so `Provenance.transform_id` can cite which Transformation produced a value) with no `Ref` consumer today, and that's fine.

## Per-concept specification

### 1. Identity
- **Definition**: the fact that some particular thing is that thing and not another, independent of its current properties.
- **Question**: Which thing?
- **Distinction**: not a Value; not a Ref (points at an identity, isn't one); not a Key (identifies within a structure — scoped, not absolute).
- **Invariants**: an identity is uniquely represented by its canonical `(kind, value)` pair; equality means the same identity token. Real-world co-reference between two *different* tokens is a Relation/reconciliation problem, not an equality claim.
- **Relations**: referenced by Ref, Key, Provenance, Relation (source/target).
- **Representation**: semantic type — `Id = (kind: Kind, value: str)` (`kind` is itself an instance of Value's `Kind` refinement, not a bare `str`). Plus `Entity(Protocol)` — structural, `id: Id` — capturing "has identity" without inheritance. Per the Ref-closure rule, `Entity`/`Id` is implemented by every `Ref` target — Event, Error, Observation, Claim, Inference, Effect, Transition, Contradiction, Provenance, Trace — plus, independent of that rule, `Transform`.
- **Operations**: construct with an explicit `Kind`; compare for identity-equality; test "is this an identity for kind K."
- **Counterexample**: `"42"` alone is not an identity. A database row's `user_id` column value is a Value that *represents* an identity, not the identity itself.

### 2. Value
- **Definition**: information, considered apart from any act of producing, observing, or asserting it.
- **Question**: What information?
- **Distinction**: not an Observation; not a Claim; not tied to any Context by itself.
- **Invariants**: none for plain information; where epistemic status matters, unknown must be representable as such, never silently coerced to `None`/`False` (law 6).
- **Relations**: referenced by Observation, Claim, State, Result's success branch; its `Kind` refinement is referenced by Identity, Error, Event, Effect, EffectSpec, Relation, TraceEntry.
- **Representation**: conceptual role for raw information — **no dedicated runtime class**. Two refinements *do* earn runtime representation, both owned here:
  - `Known[T]` / `Unknown` (singleton `UNKNOWN`) / `type Maybe[T] = Known[T] | Unknown` — the epistemic-status axis (law 6).
  - `Kind` — a small canonical namespaced value representing an open, extensible category, with well-known constants pre-built as values of the same type. This is the one shared type used wherever an open categorical label is needed: `Identity`'s own `kind` field, `Error.kind`, `Event.kind`, `Effect.kind`/`EffectSpec.kind`, `Relation.kind`, `TraceEntry.kind`.
- **Operations**: none for plain Value; `Maybe` supports construction and known-vs-unknown testing; `Kind` supports construction and equality/comparison as a single type (never mixed with bare strings).
- **Counterexample**: `Observation[int]` is not a Value. `None` meaning "we don't know" is exactly what `Unknown` replaces. `"contract"` compared against `Kind("core.contract")` and silently never matching is exactly what unifying on one `Kind` type prevents.

### 3. Context
- **Definition**: the frame relative to which a piece of information's meaning is fixed — namespace, scope, environment, source, authority, version, units, as-of time.
- **Question**: Under what frame?
- **Distinction**: not Time (includes `as_of: WallInstant`, but also non-temporal fields); not a bare Namespace.
- **Invariants**: never silently and partially overwritten; has no knowledge of `Contradiction`.
- **Relations**: carried by Observation, Claim, Error, Transition, Effect, Relation, Provenance.
- **Representation**: concrete structure — `as_of: WallInstant` among its fields.
- **Operations**: `with_(**changes)`; `merge(other) -> Result[Context, ContextConflict]`; `override(other, *fields) -> tuple[Context, ContextOverride]`.
- **Counterexample**: `{**a, **b}` silently lets `b`'s fields win with no record that `a`'s were discarded.

### 4. Time
- **Definition**: the dimension through which temporal location, ordering, and duration are represented — a family of distinct readings, not one scalar, and not a promise of simultaneity.
- **Question**: When / in what order?
- **Distinction**: a *reading* is not the *apparatus that produces readings*; wall-clock, monotonic, logical/causal, and local-sequence order are not interchangeable.
- **Invariants**: `MonotonicInstant`, `LogicalTime`, and `Sequence` are each scoped by a `space: Id` — two readings compare only when their spaces match, so two independent processes' monotonic clocks (same family, different space) are never mistakenly compared. `LogicalTime.precedes(other)` (same space required) is necessary-but-not-sufficient evidence of actual causal precedence.
- **Relations**: used by every `at`/`detected_at`/`as_of` field across the specification.
- **Representation**: semantic types.
  - Readings: `WallInstant` (unscoped — wall time is globally meaningful, if imprecise), `MonotonicInstant(space: Id, value: ...)`, `LogicalTime(space: Id, counter: int)`, `Sequence(space: Id, position: int)`, `Duration` (unscoped — a difference between two same-space `MonotonicInstant`s).
  - Producers: `Clock` (→ `WallInstant`), `MonotonicClock` (→ `MonotonicInstant`, carries its own `space: Id`, stamped onto every reading it produces), `LogicalClock` (`.tick()`/`.observe()` → `LogicalTime`, same space-stamping).
- **Operations**: construct a reading from its producer (inheriting the producer's `space`); compare two readings only when both family and space match; `Duration = MonotonicInstant - MonotonicInstant` (same space required); `LogicalTime.precedes(other)` (same space required).
- **Counterexample**: storing `time.time()` and diffing two of them to measure elapsed work. Treating `LogicalTime` order as proof of causation. Comparing `Sequence`, `MonotonicInstant`, or `LogicalTime` readings from unrelated spaces. A `Transform.apply()` that silently picks its own clock instead of receiving one.

### 5. State
- **Definition**: what holds for some identified subject, as of some reading of time — not necessarily *now*; yesterday's state is still a State.
- **Question**: What holds, at this time/frame, for which thing?
- **Distinction**: not the Event that caused it; not the Transition connecting a before-State to an after-State; a snapshot, not a history; requires a subject.
- **Invariants**: immutable once constructed. Not a `Ref` target anywhere in this document, so it does not carry its own `Id` — it's identified only by its subject + time, which is sufficient for everything that currently uses it.
- **Relations**: produced by Transition; referenced by History; carries a Value, a Context, a time reading, and a subject.
- **Representation**: concrete structure — `subject: Id | Ref`, `value: T`, `at: WallInstant`, `context`.
- **Operations**: construct (subject, value, at, context); read fields; compare for content equality.
- **Counterexample**: a mutable object whose `.value` gets reassigned in place. A `(value, at, context)` triple with no subject.

### 6. Event
- **Definition**: something that occurred — a discrete, dated occurrence, independent of who noticed it or what it changed.
- **Question**: What occurred?
- **Distinction**: not an Observation; not a Transition (the Event is an input to that explanation, not the explanation).
- **Invariants**: immutable, independently identifiable.
- **Relations**: input to Transition; may be the subject of an Observation; may be referenced from a Trace entry (by Id — Event satisfies Entity); may have Provenance if produced by a Transformation.
- **Representation**: concrete structure — `id: Id`, `kind: Kind`, `at: WallInstant`, `payload`, `context`.
- **Operations**: construct; compare by Id; project a summary.
- **Counterexample**: a log line with no kind/time/payload.

### 7. Observation
- **Definition**: the recorded acquisition of information about some identified subject, by some source, at some time, in some context.
- **Question**: What was perceived or measured, about which thing?
- **Distinction**: not the thing itself; not a bare Value; not a Claim; not an Inference; requires a subject.
- **Invariants**: subject, source, time, and context must remain attached. Itself `Entity`-bearing, since `Claim.evidence_refs` and `Inference.premises` may reference an Observation by `Ref`.
- **Relations**: may cite an Event; referenced (by `Ref`, never embedded) from Inference's premises and Claim's evidence_refs.
- **Representation**: concrete structure — `id: Id`, `subject: Id | Ref`, `value: T`, `at: WallInstant`, `source`, `context`, `observer`.
- **Operations**: construct; read fields; no mutation.
- **Counterexample**: `temperature = 80` is not an Observation; `Observation(id=..., subject=sensor_ref, value=80, at=..., source="sensor-7", context=...)` is.

### 8. Result
- **Definition**: the outcome of an attempted operation — either what came out, or why it didn't.
- **Question**: What came out of an attempt?
- **Distinction**: not Error alone; not Maybe/Unknown (owned by Value — a successful Result's payload can itself be a Maybe).
- **Invariants**: exactly one branch populated; failure always carries structured information.
- **Relations**: returned by Transform.apply(); wraps Error; may wrap a Traced value; returned by Context.merge().
- **Representation**: semantic type (`Ok[T] | Err[E]`).
- **Operations**: map, map_err, and_then, unwrap, unwrap_or.
- **Counterexample**: returning `None` on failure and a real value on success.

### 9. Error
- **Definition**: structured information about why an attempted operation did not succeed.
- **Question**: How did an attempt fail?
- **Distinction**: not a bare Python exception (the mechanism Error travels inside of — see `ContractError`); not a Contradiction.
- **Invariants**: `cause` chain never truncated by assignment.
- **Relations**: carried by Result's failure branch; may cite a Context and an Id; may be referenced from a Trace entry.
- **Representation**: concrete structure — `kind: Kind`, `message`, `cause: Error | None`, `exception: BaseException | None`, `context`, `operation`, `recoverable`, `metadata`, `id: Id`, `at: WallInstant`. Causation is two distinct fields, not one heterogeneous chain: `cause` wraps a prior structured Core `Error`; `exception` carries a foreign Python exception that triggered this one, if any. `.chain()` follows `Error.cause` only — one precise meaning, never guessing which kind a link is.
- **Operations**: construct; `.chain()`; classify by kind.
- **Counterexample**: `raise ValueError("bad")` with no structured payload.

### 10. Transformation
- **Definition**: a named, describable operation that turns one thing into another.
- **Question**: How did one thing become another?
- **Distinction**: not a composition of transformations (see Pipeline); not an Effect (independent axes, law 5).
- **Invariants**: describable/reproducible enough under declared conditions that its Provenance means something.
- **Relations**: produces a Result; may declare EffectSpecs; stamps Provenance on its output; composed into a Pipeline.
- **Representation**: concrete structure (`Transform[A, B]`: name, version, fn, requires, effect specs, id).
- **Operations**: `.apply()` takes both clocks explicitly, an `IdSource`, and two distinct explicit parameters — `input_refs` (which identified inputs participated → `Provenance.inputs`) and `parents` (which prior derivations feed this one → `Provenance.parents`) — never discovered by inspecting `input`'s structure. `.then(other)` returns a `Pipeline[A, C]`, never another `Transform`. See `ARCHITECTURE.md` for the exact runtime signature and full failure/success contract.
- **Counterexample**: a bare `def f(x): return x + 1` before being named/versioned. A `.apply()` that reflects over an arbitrary structure looking for `Traced` values, or that reaches for a global clock instead of taking one as a parameter.

### 11. Effect
- **Definition**: a change made to something outside the computation's own return value.
- **Question**: What changed outside the computation itself?
- **Distinction**: not the Transformation that caused it; not an Event.
- **Invariants**: declared-possible and actually-happened are always distinguishable. Now `Entity`-bearing, since a Trace entry may reference an Effect by Id.
- **Relations**: declared by Transform; recorded via an explicit sink; may be referenced from a Trace entry.
- **Representation**: `EffectSpec` (declarative: `kind: Kind`, target-shape, description — not itself an `Entity`) and `Effect` (a record: `id: Id`, `kind: Kind`, description, target, `at: WallInstant`, context, metadata).
- **Operations**: declare an EffectSpec on a Transform; record an Effect to an explicitly-passed `EffectSink`; query a sink's recorded Effects.
- **Counterexample**: a decorator quietly appending to a module-level list.

### 12. Capability
- **Definition**: what something can do, independent of what it structurally is or descends from.
- **Question**: What can this thing do?
- **Distinction**: not identity; not inheritance — satisfied structurally.
- **Invariants**: a Capability protocol is only defined once something concrete implements and uses it (law 19).
- **Relations**: implemented by concrete structures defined elsewhere.
- **Representation**: conceptual role, expressed only as `Protocol` classes, added alongside their first real implementer.
- **Operations**: none of its own; structural checks via `@runtime_checkable`.
- **Counterexample**: defining `Readable`/`Writable`/`Serializable` today with zero implementers.

### 13. Relation
- **Definition**: a connection between two identified things, of a named kind.
- **Question**: How are things connected?
- **Distinction**: not Context; not Provenance.
- **Invariants**: source and target are Identities (or Refs), never bare Values.
- **Relations**: general connection vocabulary; Provenance is a specialized, separately-represented case.
- **Representation**: concrete structure — `source`, `kind: Kind`, `target`, `context`, `at: WallInstant`, `metadata`, with an in-memory adjacency helper (`RelationSet`) — no graph engine. Not itself an `Entity` — nothing references a Relation by `Ref`.
- **Operations**: add; query by kind/source/target; `path_exists` (plain BFS).
- **Counterexample**: a foreign-key column, until lifted into an explicit `(source, kind, target)` record.

### 14. Constraint
- **Definition**: a condition that must (or must not) hold, stated so it can be checked, not just asserted in prose.
- **Question**: What must or must not hold?
- **Distinction**: not an Error; not a Contradiction; not a `ContextConflict`.
- **Invariants**: none of its own.
- **Relations**: checked via require/ensure/invariant; violations raise; discovered conflicts are recorded as Contradictions.
- **Representation**: conceptual role — plain functions, not a stored `Constraint` object.
- **Operations**: `require`, `ensure`, `invariant` — identical mechanism, distinct names for documentation. All three raise `ContractError` carrying a structured `Error` on failure. See `ARCHITECTURE.md` for the exact runtime signature.
- **Counterexample**: `# assumes x > 0`. A `require(cond, msg, **kwargs)` with untyped "context."

### 15. Provenance
- **Definition**: the derivation history of a value — which Transformation(s), from which inputs, under which conditions, produced it.
- **Question**: Where did this come from?
- **Distinction**: not a general Relation; not Trace.
- **Invariants**: a lazy DAG, never eagerly flattened; attaching Provenance never mutates the value itself; every node carries its own `Id`.
- **Relations**: stamped by Transform.apply(); may be referenced from a Trace entry.
- **Representation**: concrete structure — `id: Id`, `transform_id`/`transform_name`/`transform_version`, `inputs: tuple[Ref, ...]` (best-effort citation of Entity-bearing inputs only), `parents: tuple[Ref, ...]` (pointers, by Id, to the Provenance nodes of inputs that were themselves Traced — valid references to identified nodes, though availability through any particular resolver is not guaranteed; zero = root, one = chain, more = convergence), `at: WallInstant`, `duration: Duration`, `context` — plus a minimal carrier `Traced[T]` (value + optional provenance), opt-in rather than forced.
- **Operations**: attach (internally, by Transform.apply, given explicit `input_refs`/`parents`); `.ancestors()` — a lazy traversal resolving `parents` Refs to their nodes. See `ARCHITECTURE.md` for the exact `AncestorReport`/cycle-detection contract.
- **Counterexample**: "computed by step 3" with no reference to actual inputs or a specific Transformation identity.

### 16. Trace
- **Definition**: structured, ordered evidence of what actually happened during execution — a low-level, append-only record of entries, their ordering, timestamps, and origins, opaque to what any given entry's payload actually means.
- **Question**: What path did execution take?
- **Distinction**: not Provenance; not higher-order introspection (a future `inspect` layer, built on top of Trace).
- **Invariants**: append-only. Trace knows evidence; it does not know what all possible evidence means — never imports Event/Effect/Provenance/Transition. Itself `Entity`-bearing: `Trace.id` doubles as the authority for its own entries' ordering.
- **Relations**: entries carry opaque references (`subject: Id | Ref | None`, plus reference IDs) to whatever they concern.
- **Representation** (split by tier):
  - Low tier (v0): `Trace` (`id: Id`, an append-only sequence of entries) and `TraceEntry` (`subject: Id | Ref | None`, `kind: Kind`, `sequence: Sequence` — authoritative order, `space=trace.id`, assigned automatically by `Trace.append()`, never fabricated by callers — `observed_at: WallInstant | None` — non-authoritative, optional — `payload: object`, `context`, reference IDs).
  - High tier (deferred beyond v0): `Traceable`/`Inspectable` protocols, plus constructions like `Task`/`Checkpoint`/`Progress`/`Session`/`SeededRandom` — ordinary machinery built from the primitives, not numbered ontology members.
- **Operations**: `Trace.append(entry)` (assigns `sequence`); `.entries()`; `.since(marker: Sequence)`.
- **Counterexample**: a `print()` statement. A `TraceEntry` ordered only by `WallInstant`, which isn't a reliable total order under clock resolution limits or concurrent writers — exactly why `sequence`, not wall time, is authoritative.

## Derived constructions

| Construction | Built from |
|---|---|
| `Namespace` | a tuple of string segments; shared between Context and Identity's derived constructions |
| `Ref` | `Id` (+ optional `Namespace`) — points at any `Entity` (anything carrying an `Id`) |
| `Key` | `Namespace` + name (scoped identification within a structure) |
| `Deadline` | a time reading bound to a compatible producer of the *same family and, where that family is scoped, the same space* |
| `Transition` | `id: Id` + a before-`State` + an `Event` + a named operation + an after-`State` |
| `History` | a subject-scoped, ordered, append-only sequence of `Transition` |
| `Claim` | `id: Id` + `subject: Id \| Ref` + `predicate` + `value: Maybe[T]` + `context` + `asserted_by` + `evidence_refs: tuple[Ref, ...]` + `at: WallInstant` |
| `Inference` | `id: Id` + `premises: tuple[Ref, ...]` + `method` + `conclusion: Claim` + `at: WallInstant` |
| `Pipeline` | an ordered sequence of `Transform` |
| `ContextConflict` | two incompatible values for the same `Context` field — structural, produced by `Context.merge()` |
| `ContextOverride` | the set of `(field, old_value, new_value)` triples produced by `Context.override()` |
| `Contradiction` | `id: Id` + `subject: Id \| Ref` + `statements: tuple[Ref, ...]` (pointing at conflicting Claims) + `detected_at: WallInstant` + `context` |
| `Resolution` | `contradiction: Ref` (to a Contradiction's `Id`) + `rationale` + `resolved_by` + `at: WallInstant` |
| `ContradictionLog` | an append-only sequence of `Contradiction \| Resolution`, plus a projection for "unresolved" |
| `Traced` | a value + optional `Provenance` |
| `RelationSet` | a collection of `Relation` records + in-memory adjacency/BFS helpers |
| `EffectSink` | a narrow protocol/interface consuming `Effect` records |
| `AncestorReport` | the result of a `Provenance.ancestors()` traversal: `found`, `unresolved`, and `cycles` |

Every row's "built from" column names only primitives, primitive representations, or other rows — but a `Ref`-typed field may legitimately name a row that appears later in the table, because `Ref` depends only on `Identity`, never on the target's concrete representation. Only genuine type dependencies (e.g. `Inference.conclusion: Claim`) require the referenced row to appear earlier — and all of them do.

## Deferred from this specification

These names appear in this document because they're referenced by it, not because they were forgotten. Some are concrete v0 machinery specified in `ARCHITECTURE.md` and implemented in code: `SystemClock`/`SystemMonotonicClock`/`LamportClock` (concrete `Clock`/`MonotonicClock`/`LogicalClock` implementations), `ContractError`, `MemoryEffectSink` (the required real implementer of the `EffectSink` capability), `IdSource`/`UuidIdSource`, `identity_of`, `UnwrapError`, and `ContradictionLog`. What remains deferred *beyond* v0 entirely is only: `Traceable`/`Inspectable` (protocols for a future `inspect` layer) and `Task`/`Checkpoint`/`Progress`/`Session`/`SeededRandom` (future `inspect` constructions) — ordinary machinery built from the primitives, same as file I/O would be, just not part of this build.

## Representation classification summary

| Class | Members |
|---|---|
| **Concrete structures** | Context, Event, Observation, Error, Transform, EffectSpec, Effect, Relation, Provenance, Trace/TraceEntry |
| **Semantic types** | `Id (kind: Kind, value: str)`, `WallInstant`, `MonotonicInstant`, `LogicalTime`, `Sequence`, `Duration`, `Result` (Ok/Err), State (borderline — see spec) |
| **Value's own semantic refinements** (reused across many primitives) | `Maybe`/`Known`/`Unknown`, `Kind` |
| **Conceptual roles** (no dedicated class) | plain Value, Capability, Constraint |
| **Derived constructions** | see table above |

## Principles frozen for the implementation

1. The sixteen are orthogonal questions/dimensions, not sixteen obligatory Python classes.
2. Representation must be earned by invariants or behavior (law 19).
3. Derived constructions are explicitly distinguished from ontology primitives.
4. Low-level records never import the higher-level concepts that may later refer to them.
5. Conflict and contradiction remain different concepts — structural (`ContextConflict`) vs. semantic (`Contradiction`).
6. State is time-relative, not synonymous with "now," and always names its subject.
7. Provenance is a DAG, not fundamentally a chain, and its nodes are independently identifiable.
8. Trace records evidence without needing to understand every possible payload, and is the authority for its own entries' order via a `Sequence` scoped to its own `Id` — not wall time.
9. Time represents temporal location/order/duration without pretending all clocks establish the same facts.
10. Every categorical, extensible label — including Identity's own `kind` field — uses the same `Kind` type (a semantic refinement of Value), never a mix of enums and bare strings.
11. A violated Constraint raises a structured Error through the ordinary Python exception mechanism.
12. Nothing in this specification "discovers" structure by reflecting over arbitrary input shapes — every relationship is passed through an explicit, narrow parameter or field, including which clock a Transformation uses.
13. Anything a `Ref` may target must implement `Entity` (carry an `Id`) — checked explicitly per type, not assumed.
14. The dependency graph is *derived* from these semantic relationships, not imposed beforehand.
