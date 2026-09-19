# Core — v0 Architecture

This document derives the Python package from `SPECIFICATION.md`. **Signatures in `SPECIFICATION.md` specify semantic inputs/outputs — the concepts and their shapes. This document defines the authoritative Python runtime signatures**, and adds explicit capabilities (an `IdSource`, a `Clock`) required to realize those semantics without hidden state. Where the two appear to differ (`Transform.apply()`, `require`/`ensure`/`invariant`), this document's signature is the one implemented.

## Central rule

The package graph is a consequence of semantic dependency. A lower module may know only the concepts required to define itself. Higher constructions may combine lower concepts. Nothing lower imports upward merely for convenience.

## Package shape

Flat modules, not fourteen nested subpackages — the ontology is already conceptually rich; the filesystem doesn't need to add ceremony. A module becomes a package only when splitting it solves an actual problem.

```text
E:\core\
├── pyproject.toml
├── README.md
├── SPECIFICATION.md
├── ARCHITECTURE.md
├── LAWS.md
├── .gitignore
│
├── src\
│   └── core\
│       ├── __init__.py       # stays essentially empty — no giant re-export surface
│       ├── py.typed
│       │
│       ├── value.py
│       ├── result.py
│       ├── identity.py
│       ├── time.py
│       ├── context.py
│       │
│       ├── error.py
│       ├── trace.py
│       │
│       ├── event.py
│       ├── observation.py
│       ├── relation.py
│       ├── effect.py
│       ├── provenance.py
│       │
│       ├── state.py
│       ├── epistemic.py
│       ├── constraint.py
│       └── transform.py
│
└── tests\
    ├── semantics\
    └── architecture\
```

Canonical usage stays explicit:

```python
from core.identity import Id, Ref
from core.time import WallInstant
from core.result import Ok, Err
```

## Dependency graph (frozen before implementation)

| Tier | Module | Owns | May depend on |
|---|---|---|---|
| 0 | `value` | `Kind`, `Known`, `Unknown`, `Maybe` | stdlib |
| 0 | `result` | `Ok`, `Err`, `Result`, `UnwrapError` | stdlib |
| 1 | `identity` | `Id`, `Entity`, `Namespace`, `Ref`, `Key`, `IdSource`, `UuidIdSource`, `identity_of` | `value` |
| 2 | `time` | temporal readings, clocks, `Deadline`, `SystemClock`, `SystemMonotonicClock`, `LamportClock` | `identity` |
| 3 | `context` | `Context`, `ContextConflict`, `ContextOverride` | `identity`, `time`, `result` |
| 4 | `error` | `Error` | `value`, `identity`, `time`, `context` |
| 4 | `trace` | `Trace`, `TraceEntry` | `value`, `identity`, `time`, `context` |
| 4 | `event` | `Event` | `value`, `identity`, `time`, `context` |
| 4 | `observation` | `Observation` | `identity`, `time`, `context` |
| 4 | `relation` | `Relation`, `RelationSet` | `value`, `identity`, `time`, `context` |
| 4 | `effect` | `EffectSpec`, `Effect`, `EffectSink`, `MemoryEffectSink` | `value`, `identity`, `time`, `context` |
| 4 | `provenance` | `Provenance`, `Traced`, `AncestorReport` | `identity`, `time`, `context` |
| 4 | `epistemic` | `Claim`, `Inference`, `Contradiction`, `Resolution`, `ContradictionLog` | `value`, `identity`, `time`, `context` |
| 5 | `state` | `State`, `Transition`, `History` | `identity`, `time`, `context`, `event` |
| 5 | `constraint` | `require`, `ensure`, `invariant`, `ContractError` | `value`, `context`, `error`, `identity`, `time` |
| 6 | `transform` | `Transform`, `Pipeline` | `value`, `identity`, `time`, `context`, `result`, `error`, `effect`, `provenance` |

`constraint`'s row includes `identity`/`time` beyond `{context, error}` — `require`/`ensure`/`invariant` need an `IdSource`/`Clock` parameter to construct a violation's `Error.id`/`Error.at` explicitly, exactly like `Transform.apply()` does, rather than allocating them silently. Both `constraint` and `transform` also depend directly on `value`: each constructs canonical `Kind` values of its own (a constraint-violation classification and an Error identity kind for `constraint`; Transform-failure classifications and Error/Provenance identity kinds for `transform`). Importing `Kind` indirectly through a module that merely happens to import `core.value` would violate the "one concept, one obvious home" rule — `Kind` lives in `value.py`, so anything that constructs one depends on `value` directly.

What's notably **absent** is as important as what's present: `trace` does not import `event`, `effect`, `provenance`, or `state`. `provenance` does not import `transform`. `epistemic` does not import `observation` (its evidence is by `Ref`). `context` does not import contradiction machinery. `identity` knows almost nothing.

## Derived constructions stay with their conceptual owner

No `derived.py` — that would immediately become a junk drawer. Each derived construction lives in the module of the primitive it's built from:

```text
identity.py:   Id, Entity, Namespace, Ref, Key, IdSource, UuidIdSource, identity_of
time.py:       WallInstant, MonotonicInstant, LogicalTime, Sequence, Duration,
               Clock, MonotonicClock, LogicalClock, LamportClock, SystemClock, SystemMonotonicClock,
               Deadline (WallDeadline | MonotonicDeadline)
context.py:    Context, ContextConflict, ContextOverride
state.py:      State, Transition, History
epistemic.py:  Claim, Inference, Contradiction, Resolution, ContradictionLog
transform.py:  Transform, Pipeline
provenance.py: Provenance, Traced, AncestorReport
relation.py:   Relation, RelationSet
effect.py:     EffectSpec, Effect, EffectSink, MemoryEffectSink
result.py:     Ok, Err, Result, UnwrapError
```

`MemoryEffectSink` and `LamportClock` are included from the start: `EffectSink` and `LogicalClock` are Capability-like protocols, and law 19 requires a real implementer alongside any protocol, not just the interface.

## Identity allocation

Who allocates identities? `Transform.apply()` creates a new `Provenance` node (needs an `Id`); a failed `require()` creates a new `Error` (needs an `Id`). Calling `uuid.uuid4()` internally would introduce exactly the hidden nondeterminism Core forbids. So `identity.py` owns:

```python
class IdSource(Protocol):
    def new(self, kind: Kind) -> Id: ...
```

with a real implementation, `UuidIdSource`. Nondeterminism becomes explicit — `ids.new(PROVENANCE_KIND)` — rather than occurring magically inside a constructor.

`identity.py` also owns `identity_of(value: Id | Ref) -> Id`. Many fields are deliberately typed `Id | Ref` (`State.subject`, `Observation.subject`, `Claim.subject`, `Contradiction.subject`, `Relation.source`/`target`), but `Id(kind, value)` and `Ref(id=Id(kind, value))` are different Python objects referring to the same identity — ordinary `==` would wrongly treat them as different subjects. Every subject-identity comparison (including `History`'s "same subject" check) uses `identity_of(a) == identity_of(b)`. A `Ref`'s own namespace stays relevant to the reference itself; it must never make the referred entity compare as a different entity.

## Time implementation discipline

```text
WallInstant:       datetime value (unscoped, UTC-aware — a naive datetime is rejected at construction,
                    or an aware-but-non-UTC input is canonicalized to UTC)
MonotonicInstant:  space: Id, ticks/nanoseconds
LogicalTime:       space: Id, counter: int
Sequence:          space: Id, position: int
Duration:          nanoseconds/timedelta-like magnitude (unscoped)
```

Only matching types compare, and for scoped readings, spaces must also match: `MonotonicInstant(space=A, ...) < MonotonicInstant(space=B, ...)` is not `False` — it's **invalid** (raises, doesn't silently evaluate). Same for `LogicalTime` and `Sequence`.

Concrete clock implementations must not silently allocate their own identity: `SystemMonotonicClock(space: Id)` and `LamportClock` both take an already-established `space: Id` in their constructor, rather than generating one internally or reaching into an `IdSource` themselves (a clock produces readings; allocating identities is a separate capability). Whoever wires up a clock allocates its space explicitly, once, using an `IdSource`, and passes it in.

For `Deadline`, two concrete representations realize one semantic construction rather than one highly parameterized generic class:

```python
class WallDeadline: ...       # WallInstant + Clock
class MonotonicDeadline: ...  # MonotonicInstant + MonotonicClock, same space
type Deadline = WallDeadline | MonotonicDeadline
```

**v0 narrows Deadline's scope explicitly**: the general same-family/same-space compatibility rule applies broadly enough to cover `LogicalTime` and `Sequence` too, but v0 only realizes `WallDeadline` and `MonotonicDeadline`. `LogicalTime` and `Sequence` do not receive Deadline constructions until a concrete use case earns them (law 19).

`LamportClock`:

```python
class LamportClock:
    def __init__(self, space: Id, initial: int = 0): ...
    def tick(self) -> LogicalTime: ...
    def observe(self, other: LogicalTime) -> LogicalTime: ...
```

`observe(other)` requires `other.space == self.space` and advances the local counter to `max(local, other.counter) + 1` (the standard Lamport merge rule) before returning the new `LogicalTime`.

## Trace owns ordering, not timestamps

Callers don't construct a completed `TraceEntry` and ask `Trace` to append it:

```python
entry = trace.append(
    kind=..., subject=..., payload=..., context=..., observed_at=..., references=...,
)
```

`Trace` constructs admitted entries itself and assigns their `Sequence`. The supported API therefore cannot insert an entry carrying a caller-selected sequence; every entry contained by a `Trace` was sequenced by that `Trace` — an API invariant, not a Python security guarantee (see "Concurrency scope" below).

## Provenance resolution stays external

No global Provenance registry. `parents` are `Ref`s and ancestor traversal requires a lookup made explicit at the call site:

```python
provenance.ancestors(resolve)   # resolve: Ref -> Provenance | None
```

Do not invent `ProvenanceRepository`, `ProvenanceStore`, service locators, registries, or plugins yet (law 19).

`.ancestors(resolve) -> AncestorReport`, where `AncestorReport(found: tuple[Provenance, ...], unresolved: tuple[Ref, ...], cycles: tuple[Ref, ...])`:

- A parent `Ref` a resolver can't currently retrieve lands in `unresolved` (information-preservation laws forbid quietly losing it).
- **Cycle detection uses the standard three-state DFS distinction, not "seen it before → cycle."** A node reached a *second* time is not automatically a cycle — reaching the same ancestor via two different converging paths (A→C and B→C) is exactly the convergence Provenance exists to represent, and lands in `found` once, not in `cycles`. States: **unseen** (traverse it), **active** — currently on the current DFS path (reaching an active node again is a genuine back-edge, a real cycle, lands in `cycles`), **done** — fully expanded and popped off the active path (reaching a done node again is ordinary convergence: record once in `found`, don't re-traverse).
- Traversal order is deterministic: depth-first, visiting each node's `parents` tuple in its stored order, first-visit-wins for `found`. Repeated runs over the same graph and resolver produce identical `AncestorReport`s.

The precise, honest guarantee: **Core-created Provenance is DAG-shaped by construction; traversal defensively detects cycles and unresolved parent references.**

## Transform: the composition boundary

```text
input → requirements → function → result → provenance → Traced output
```

```python
def apply(
    self, input: A, *,
    context: Context | None = None,
    clock: Clock,
    monotonic_clock: MonotonicClock,
    ids: IdSource,
    input_refs: tuple[Ref, ...] = (),      # which identified inputs participated → Provenance.inputs
    parents: tuple[Provenance, ...] = (),  # which prior derivations feed this one → Provenance.parents
) -> Result[Traced[B], Error]: ...
```

`input_refs` and `parents` are different information, both supplied explicitly by the caller, never inferred. Nothing is pulled from process-global state.

`Pipeline` owns an ordered tuple of `Transform` stages and defines exactly how lineage flows between them:

1. execute stage N via `.apply()`, receiving `Result[Traced[B], Error]`;
2. on `Err`, short-circuit immediately — the pipeline's result is that `Err`, no further stage runs;
3. on `Ok(traced)`, unwrap `traced.value` as stage N+1's `input`, and pass `parents=(traced.provenance,)` as stage N+1's `parents`; `input_refs` for an intermediate stage is empty by default.

Four transformations means exactly four provenance nodes, never five — `Pipeline` itself never calls anything that would create a node of its own.

`.apply()`'s full failure/success contract:
- a declared requirement evaluates to `False` → `Err(Error(...))` (no exception, so no `exception` field);
- evaluating a requirement itself raises → `Err(Error(..., exception=original_exception))`;
- the transformation function (`fn`) raises → `Err(Error(..., exception=original_exception))`;
- successful execution → exactly one `Provenance` node, with `duration` measured around the attempted transformation using `monotonic_clock`, returned as `Ok(Traced(value, provenance))`;
- failed execution (any of the three cases above) → **no output Provenance is created**, and **failure duration is not retained in v0**.

`requires` is deliberately simple for v0: an immutable tuple of predicates over `A`, not a requirement framework.

## Effect recording stays at the edge, not inside `Transform.apply()`

`Transform.apply()` does **not** take an effect sink, and does not claim to know which of a Transform's declared `EffectSpec`s actually fired. A Transform's `effect_specs` remain purely declarative; an effectful callable that needs to *record* an effect receives whatever `EffectSink` it needs through its own explicit API (e.g. the caller constructs `fn` already closed over a sink) — Core never injects, discovers, or infers it.

## `ContradictionLog`

`ContradictionLog`: an append-only sequence of `Contradiction | Resolution`, with `record(entry)`, `entries()`, `unresolved()`, `for_subject(subject)`. Two append invariants: a `Resolution` may only be appended if the `Contradiction` it references already appears earlier in that same log; **append order — not wall-clock time — is authoritative for "later."** Multiple `Resolution`s for the same `Contradiction` are permitted; `unresolved()` derives its answer fresh every call — "no `Resolution` appended after this `Contradiction` that references it" — never stored as a flag.

## Constraint's runtime signature

```python
def require(
    condition: bool, message: str, *,
    ids: IdSource, clock: Clock, context: Context | None = None,
) -> None: ...
```

Identical shape for `ensure()` and `invariant()`. All three raise `ContractError` carrying a structured `Error` on failure.

## Local invariants vs. `core.constraint`

`constraint` is Tier 5, so Tier 0–4 modules **cannot** call `core.constraint.require`/`ensure`/`invariant` without creating an upward dependency the graph forbids. Lower-tier modules enforce their own constructor/operator invariants **locally**, with plain validation and an appropriate built-in or module-local exception. Comparing two `MonotonicInstant`s from different spaces fails directly inside `time.py`. "Invariants are executable" (law 15) does not mean "every invariant must call the function named `invariant()`" — it means every invariant fails loudly, by whatever mechanism is available at that module's tier.

## Structural immutability, precisely

A frozen dataclass only prevents reassigning a field — it does not prevent mutating a mutable object reachable through that field. Core cannot generally deep-freeze arbitrary user-supplied `T`/`payload`/`metadata`.

> **Core records are structurally immutable; Core does not claim transitive immutability of arbitrary user-supplied payload values.**

For everything Core itself owns: tuples instead of exposed lists, never a mutable internal collection returned by reference. `Trace.entries()` returns a tuple; `History` does not expose its internal storage directly; `Provenance.parents`/`.inputs` are tuples; `Context`'s metadata is exposed as a read-only mapping or defensive copy. If a caller gives `State` a mutable `T` and mutates it externally afterward, Core cannot preserve historical value semantics for them.

## `Result.unwrap()` failure semantics

`result.py` defines `UnwrapError(Exception)` carrying the original `E` value; `Err(e).unwrap()` always raises `UnwrapError(e)`, regardless of what `E` is.

## `History` and `Transition` invariants

`History` is constructed with an explicit subject, `History(subject)`. `History.append(transition)` requires, checked locally:
- `identity_of(transition.before.subject) == identity_of(History.subject)` — `Transition` has no independently-stored `subject` field (it's `id + before State + Event + operation + after State`); deriving it from `before.subject` avoids three copies of the same fact that could disagree. `Transition`'s own construction already proves `identity_of(before.subject) == identity_of(after.subject)`. A read-only `Transition.subject` property returning `before.subject` may be added for ergonomics — never separately stored;
- `existing.last().after == transition.before` (vacuously true for the first append);
- the new transition's `after.at` is not earlier than its `before.at`;
- append-only — no removal or reordering operation exists.

Constructing a `Transition` itself enforces that its `before` and `after` States share the same `subject`.

## Concurrency scope for v0's mutable containers

`Trace`, `History`, `ContradictionLog`, `RelationSet`, and `MemoryEffectSink` are mutable containers even though the records they hold are structurally immutable. For v0, all of them are **single-writer and not thread-safe unless otherwise stated** — no locks yet (law 19: no unearned synchronization). This matters most for `Trace`, since `Trace.append()` allocates the next `Sequence` position; v0 does not protect against a race between concurrent appenders.

## Architecture is tested, not just documented

- `tests/architecture/test_import_graph.py` — parse imports under `src/core` with Python's `ast` module and compare against the dependency table above, including imports inside `if TYPE_CHECKING:` blocks (the rule is conceptual dependency, not merely runtime circular imports).
- `tests/architecture/test_import_side_effects.py` — import every `core.*` module in a fresh process and reject application-level filesystem writes, directory creation, subprocess launches, socket/network activity, environment mutation, UUID generation, wall-clock acquisition, and random-number generation during import.

## Implementation order — checkpointed, six passes, green tests gate each transition

Git is initialized before implementation begins: write/freeze the specification documents → `git init` → commit them → implement each pass → commit at each green boundary.

**Pass preregistration, not another architecture review.** At the start of each pass, pin down only the exact API decisions that pass needs and this document doesn't already fix — constructor signatures, validation rules, equality/hash semantics, exception behavior, immutable-storage choices, constants — then implement, test, commit, move on.

1. **Atoms**: `value`, `result`. Prove `Unknown != False != None`, `Kind` canonicality, `Result` branch exclusivity and composition.
2. **Identity and Time**: `identity` (+ `IdSource`), `time`. Prove identity equality, `Ref` closure, temporal-family separation, and time-space separation.
3. **Frame and evidence substrate**: `context`, `error`, `trace`. Prove non-lossy Context operations, structured error chaining, append-only ordered `Trace`.
4. **Facts and relationships**: `event`, `observation`, `relation`, `effect`, `provenance`, `epistemic`. Prove identity-bearing `Ref` targets, explicit evidence relationships, effect spec/record distinction, provenance DAG behavior.
5. **Change and execution**: `state`, `constraint`, `transform`. Prove immutable State/Transition history, executable contracts, one-hop provenance, explicit clocks/IDs, `Pipeline` composition without phantom nodes.
6. **Architectural closure**: full semantic suite, import-DAG verification, no-import-side-effects verification, strict static typing, linting, and a manual SPECIFICATION/LAWS-to-code audit.

Tooling from the start: pytest, Ruff, Pyright.

## Explicitly not built in v0

No filesystem wrapper, logging framework, configuration framework, networking, serialization system, plugin manager, database layer, scheduler, task framework, general dependency-injection container, service locator, global registries, `utils.py`, or premature `Readable`/`Writable`/`Serializable` protocols. `Task`, `Session`, `Checkpoint`, `Progress`, and the higher `inspect` layer stay deferred.

The goal of v0 is not to make Core broadly useful yet — it's to make the semantic substrate real, importable, executable, and difficult to misuse.
