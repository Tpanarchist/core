# Pass 5 preregistration — `state`, `constraint`, `transform`

Pass-local API decisions not already fixed by SPECIFICATION.md/ARCHITECTURE.md. Implement, test, commit; do not reopen the frozen ontology or completed lower passes. This is the final implementation pass — Pass 6 is architectural closure only.

## Scope and dependencies (post-correction)

```text
state      → identity, time, context, event
constraint → value, context, error, identity, time
transform  → value, identity, time, context, result, error, effect, provenance
```

`state` does not import Constraint. `transform` does not import Constraint, Trace, State, Event, Observation, Relation, or Epistemic machinery. No ambient clocks, Id allocators, effect sinks, provenance registries, or execution contexts.

## Well-known local `Kind` values

Module-level `Kind` constants are pure and not an import-time effect (law 20): `constraint.py` defines `_ERROR_ID_KIND = Kind("core.error")` and `_CONSTRAINT_VIOLATION_KIND = Kind("core.constraint.violation")`; `transform.py` defines `_ERROR_ID_KIND`, `_PROVENANCE_ID_KIND = Kind("core.provenance")`, `_REQUIREMENT_REJECTED_KIND`, `_REQUIREMENT_EXCEPTION_KIND`, `_EXECUTION_EXCEPTION_KIND`. Duplicate construction of `Kind("core.error")` across modules is fine — `Kind` is an immutable value with canonical equality, not a singleton registry.

## `State[T]`

Frozen, slotted, keyword-only, **not** Entity-bearing: `subject: Id | Ref`, `value: T`, `at: WallInstant`, `context: Context`. Content equality. No constructor reads a clock — `at` is explicit.

## `Transition[T]`

Frozen, slotted, keyword-only, Entity-bearing: `id`, `before: State[T]`, `event: Event`, `operation: str` (non-empty — a name, not a categorical taxonomy, so plain `str` not `Kind`), `after: State[T]`. A derived, read-only `subject` property returns `before.subject` (never separately stored). Construction enforces `identity_of(before.subject) == identity_of(after.subject)` and `after.at >= before.at`, both raising `ValueError` otherwise. No invariant on `event.at` relative to the two State timestamps — the frozen semantics don't require it. Structural equality; semantic identity is `.id`.

## `History[T]`

Append-only, single-writer, mutable: `History(subject: Id | Ref)`, `subject` read-only. `append(transition)` requires: (1) `identity_of(transition.before.subject) == identity_of(history.subject)`; (2) the Transition's `id` not already recorded in this History; (3) if non-empty, `previous.after == transition.before` (exact State continuity, not just matching subject). All `ValueError` on failure. `entries()` returns an immutable tuple snapshot, unaffected by later appends. No removal/insertion/replacement/sorting/replay in v0.

## `ContractError`

`class ContractError(Exception)`: `__init__(self, error: Error)`, message is the Error's message, `self.error = error`. Constructs/alters nothing; not an `Error` subclass.

## `require` / `ensure` / `invariant`

Authoritative signature (identical for all three): `(condition: bool, message: str, *, ids: IdSource, clock: Clock, context: Context | None = None) -> None`. One private shared mechanism, three names for documentation. Empty `message` raises `ValueError` **regardless of `condition`**, checked first. When `condition` is true: return `None`, call neither `ids.new()` nor `clock.now()`, construct nothing. When false: construct exactly one `Error` (`id=ids.new(Kind("core.error"))`, `kind=Kind("core.constraint.violation")`, `message`, `at=clock.now()`, `context`, `operation="require"|"ensure"|"invariant"`, `recoverable=False`, `cause=None`, `exception=None`) and raise exactly one `ContractError(error)`. If the supplied `IdSource`/`Clock` itself raises while constructing the Error, that exception propagates — Core cannot manufacture a trustworthy Error when the capability needed for its identity/timestamp has failed.

## `Transform[A, B]`

Slotted, `eq=False` with custom `__eq__`/`__hash__` by `.id` (same pattern as `Event` — semantic identity is `transform.id`, not structural equality over callables): `id`, `name`/`version` (non-empty), `fn: Callable[[A], B]`, `requires: tuple[Callable[[A], bool], ...] = ()`, `effect_specs: tuple[EffectSpec, ...] = ()` (both normalized to tuples). No constructor invokes `fn`, requirements, clocks, Id sources, or effects. `effect_specs` are declarations only.

### `apply()`

Authoritative signature: `(input, *, context=None, clock: Clock, monotonic_clock: MonotonicClock, ids: IdSource, input_refs: tuple[Ref, ...] = (), parents: tuple[Provenance, ...] = ()) -> Result[Traced[B], Error]`. No reflection over `input`.

**Timing**: `started = monotonic_clock.now()` before the first requirement. On success only: `finished = monotonic_clock.now()`, `duration = finished - started`. On any failure, no second monotonic reading is taken — there is nowhere in v0 to retain a failure duration.

**Requirements**: execute in tuple order, each called once until one fails. Truthy → satisfied; falsy → rejected; raises `Exception` → crashed (do **not** catch `BaseException` — `KeyboardInterrupt`/`SystemExit` propagate). On the first falsy result: no later requirements, no `fn`, no Provenance; `Err(Error(kind=requirement_rejected, operation=name, message=f"requirement[{index}] rejected input"))` — no `repr(input)` or predicate repr in the message (could leak large/nondeterministic representations). On a requirement exception: `Err(Error(kind=requirement_exception, operation=name, message=f"requirement[{index}] raised", exception=original))`.

**Function execution**: call `fn(input)` exactly once if all requirements pass. On an ordinary exception: no Provenance, `Err(Error(kind=execution_exception, operation=name, message="transformation function raised", exception=original))`, again never catching `BaseException`.

**Every failure** allocates exactly one Error `Id` (`ids.new(Kind("core.error"))`) and reads exactly one `WallInstant` (`clock.now()`); `cause=None`; `recoverable=False`; caller `Context` retained; no Provenance identity allocated; no Effect recorded automatically. If `IdSource`/`Clock` itself fails while building the failure Error, that exception propagates rather than being wrapped.

**Success**: allocate exactly one Provenance `Id` (`ids.new(Kind("core.provenance"))`), read one `WallInstant`, construct one `Provenance` (`transform_id/name/version` from `self`; `inputs=tuple(input_refs)`; `parents=tuple(Ref(parent.id) for parent in parents)` — parent Provenance objects become un-namespaced Refs by identity, no ancestor traversal, no flattening; `at`, `duration`, `context`). Return `Ok(Traced(output, provenance))` — a successful `Transform.apply()` result therefore always carries non-`None` Provenance, even though `Traced[T]` in general permits `None`.

**Effects stay orthogonal**: `apply()` takes no `EffectSink`, records nothing automatically, never compares against `effect_specs`. An effectful `fn` receives whatever sink it needs through its own explicit API.

**Capability failures propagate**: only requirement-rejected / requirement-exception / fn-exception become `Result.Err`. A broken `IdSource.new()`, `Clock.now()`, or `MonotonicClock.now()` raises as an ordinary exception, uncaught by `Transform`/`Pipeline` — those capabilities are part of the mechanism needed to construct a truthful record; if the mechanism itself is broken, `Transform` must not pretend it successfully represented that failure.

### `.then()`

`def then[C](self, other: Transform[B, C]) -> Pipeline[A, C]` — returns a `Pipeline` containing exactly `(self, other)`. Creates no Provenance, executes nothing. No runtime type-introspection proving `B` matches — Pyright carries that relation.

## `Pipeline[A, B]`

A derived composition, not a Transformation: no `Id`, no Provenance of its own, no name/version, no hidden clock/Id source, no `EffectSpec` aggregation. Owns an ordered tuple of stages (internally `tuple[Transform[Any, Any], ...]` — a narrowly-contained internal `Any`/cast boundary is fine since it never leaks into the public `apply()` result type). `Transform.then()` is the canonical creation path.

`then[C](other: Transform[B, C]) -> Pipeline[A, C]` returns a **new** Pipeline with `other` appended; the original is unchanged; no stage executes during composition.

`apply()` mirrors `Transform`'s signature. Stage 0 gets the caller's `input`/`input_refs`/`parents` unchanged. On `Err` from any stage: return that exact `Err` unchanged, execute no later stage. On success: next stage's input is `traced.value`; `input_refs=()` and `parents=(traced.provenance,)` for every subsequent stage. Since a successful `Transform.apply()` always carries Provenance, a `None` provenance on an internal stage result violates `Transform`'s own guarantee and raises `RuntimeError` (an implementation invariant, not a normal failure). Pipeline itself allocates nothing, so **N successful stages = exactly N Provenance nodes, never N+1**. The final result is the last stage's `Traced[B]`, unchanged.

## Local invariants

`state.py` cannot depend on `core.constraint` — State/Transition/History invariants are local `ValueError`s. `constraint.py` *is* the structured contract layer. `transform.py` reports its defined failures through `Result[_, Error]`; capability failures are the one deliberate exception (they propagate).

## Equality

`State`/`Transition` are frozen/slotted with structural equality (State: full content; Transition: structural, though `.id` is still the identity question). `Transform` compares by `.id` only (like `Event`) — never over `fn`/`requires`. `Pipeline` has no semantic equality over its stages. `History` is mutable, no structural equality over changing contents.

## Import behavior

Importing `core.state`/`core.constraint`/`core.transform` allocates no Id, reads no clock, evaluates no requirement, invokes no Transform function, creates no Provenance/Error, records no Effect, mutates no global registry, performs no filesystem/network/subprocess/environment/random operation. Module-level `Kind(...)` constants are pure and permitted. Uses the shared subprocess side-effect helper (`tests/semantics/_side_effects.py`) — no `importlib.reload()`.
