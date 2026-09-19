# Pass 2 preregistration — `identity`, `time`

Pass-local API decisions not already fixed by SPECIFICATION.md/ARCHITECTURE.md. Implement, test, commit; do not reopen the frozen ontology or package graph from here.

## Scope

`core.identity`: `Id`, `Entity`, `Namespace`, `Ref`, `Key`, `IdSource`, `UuidIdSource`, `identity_of`.

`core.time`: `WallInstant`, `MonotonicInstant`, `LogicalTime`, `Sequence`, `Duration`, `Clock`, `MonotonicClock`, `LogicalClock`, `SystemClock`, `SystemMonotonicClock`, `LamportClock`, `WallDeadline`, `MonotonicDeadline`, `Deadline`.

No Context, Error, Trace, Provenance, or other higher-tier concept is imported.

## Identity decisions

- **`Id`**: frozen, slotted, `kind: Kind` + `value: str`. Empty `value` is rejected (`ValueError`); otherwise the token is preserved verbatim — no trimming, case folding, or UUID interpretation. Equality/hash are structural over `(kind, value)`. Two identical raw values under different `Kind`s are different identities.
- **`Entity`**: `@runtime_checkable Protocol`, structural `id: Id`. No inheritance requirement; a representation of Identity, not a new Capability.
- **`Namespace`**: frozen, slotted, wraps a non-empty `tuple[str, ...]`; every segment must itself be non-empty. Segments preserved verbatim — deliberately does not reuse `Kind`'s grammar (namespace segments and categorical kinds are different semantics). Empty tuple or empty segment raises `ValueError`.
- **`Ref`**: frozen, slotted, `id: Id` + `namespace: Namespace | None = None`. Equality includes both fields — two references to the same `Id` in different namespaces are different `Ref`s even though they identify the same entity.
- **`identity_of(value: Id | Ref) -> Id`**: returns an `Id` unchanged, or `Ref.id`. The one canonical mechanism for identity-equivalence across `Id | Ref` fields; namespace never changes entity identity.
- **`Key`**: frozen, slotted, `namespace: Namespace` + `name: str`. Empty `name` rejected; otherwise preserved verbatim. Structural equality/hash. Scoped identification, not an absolute `Id`.
- **`IdSource`**: `@runtime_checkable Protocol`, `new(kind: Kind) -> Id`.
- **`UuidIdSource`**: concrete, stateless. Each explicit `.new(kind)` call draws one `uuid.uuid4()`, uses its canonical lowercase hyphenated string as `Id.value`, preserves the supplied `Kind`. UUID allocation happens only on this explicit call — never at import or construction elsewhere.

## Time storage and validation

- **`WallInstant`**: frozen, slotted, one aware `datetime`. A naive `datetime` is rejected (`ValueError`); an aware non-UTC input is canonicalized to UTC at construction. Equality/hash/order operate on the canonical UTC value.
- **`MonotonicInstant`**: frozen, slotted, `space: Id` + `nanoseconds: int` (negative rejected).
- **`LogicalTime`**: frozen, slotted, `space: Id` + `counter: int` (negative rejected).
- **`Sequence`**: frozen, slotted, `space: Id` + `position: int` (negative rejected).
- **`Duration`**: frozen, slotted, `nanoseconds: int`; v0 durations are non-negative (negative rejected).

## Scoped comparison semantics

For `MonotonicInstant`, `LogicalTime`, `Sequence`: **equality is safe across spaces** (different-space readings simply compare unequal), but **ordering/precedence requires the same space** — attempting it across spaces raises `ValueError`. Comparing across families doesn't coerce; ordinary Python incompatible-type behavior applies.

`MonotonicInstant` supports same-space ordering and `later - earlier -> Duration` (both operands must share a space; a negative result raises `ValueError`). `Sequence` supports same-space ordering. `LogicalTime` does **not** expose generic rich ordering as a causal claim — its operation is `precedes(other) -> bool` (same space required), meaning `self.counter < other.counter`: necessary, not sufficient, evidence of causality.

## Clocks

- **`Clock`**: `@runtime_checkable Protocol`, `now() -> WallInstant`.
- **`SystemClock`**: concrete; `now()` reads current UTC wall time only when explicitly invoked.
- **`MonotonicClock`**: `@runtime_checkable Protocol`, `space: Id` + `now() -> MonotonicInstant`.
- **`SystemMonotonicClock`**: constructed with an already-established `space: Id` (never allocates one); `now()` calls `time.monotonic_ns()` explicitly and stamps its space.
- **`LogicalClock`**: `@runtime_checkable Protocol`, `space: Id` + `tick() -> LogicalTime` + `observe(other: LogicalTime) -> LogicalTime`.
- **`LamportClock`**: concrete, mutable. `LamportClock(space: Id, initial: int = 0)` (negative `initial` rejected). `tick()` increments by one, returns the new reading. `observe(other)` requires the same space, sets the local counter to `max(local, other.counter) + 1`, returns that reading.

Clock objects are producers and may be mutable; readings stay structurally immutable. `LamportClock` is single-writer/not thread-safe in v0.

## Deadline scope

v0 realizes only `WallDeadline` and `MonotonicDeadline` (the general family/space compatibility law stays frozen more broadly; logical/sequence deadlines wait for a concrete use case, law 19). `WallDeadline` binds `at: WallInstant` to a `Clock`. `MonotonicDeadline` binds `at: MonotonicInstant` to a `MonotonicClock`, validating `at.space == clock.space` at construction. Both expose only `reached() -> bool` (obtains a fresh reading, compares to the deadline). No `remaining()`, scheduling, sleeping, callback, timeout, or cancellation API in v0. Deadline doesn't acquire a broader identity or provenance role.

## Local failures

Pass 2 sits below `core.error`/`core.constraint`: invariants are enforced locally with `ValueError`/`TypeError`, not the higher-tier structured `Error`. Examples: empty `Id` value, malformed `Namespace`, negative time counters/readings/durations, naive `WallInstant`, cross-space monotonic ordering/subtraction, cross-space `LogicalTime.precedes`, cross-space `Sequence` ordering, `LamportClock` observing another space, `MonotonicDeadline` bound to a mismatched clock space. No exception object is created at import time.

## Structural immutability

Identity values and Time readings are frozen/slotted. Mutable producers (`LamportClock`) keep only the minimum mutable state needed to produce readings. No container exposes mutable internal storage. No constructor performs a hidden clock read or UUID generation except the explicit capability methods whose whole purpose is to do exactly that.
