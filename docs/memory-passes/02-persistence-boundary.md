# Memory v0 — Pass 2 Preregistration

## Status

**Pass:** 2 of 4
**Name:** Persistence Boundary + In-Memory Reference Store
**Question answered:** What does it mean for Memory to preserve and query its records, independent of any durable database?

Pass 1 is closed.

Pass 2 introduces exactly one production module:

```text
src/memory/store.py
```

It owns:

```text
MemoryRecord
EntityMemoryRecord
PersistRecord
RetrievalQuery

MemoryStore
InMemoryStore

UnsupportedMemoryRecord
IdentityCollision
```

plus private snapshot/canonicalization/query helpers required to implement those contracts.

No SQLite appears in this pass.

The purpose of `InMemoryStore` is not convenience. It is the semantic reference implementation against which `SqliteMemoryStore` will be judged in Pass 3.

---

# 1. Central rule

Persistence preserves Memory semantics; it does not create them.

Therefore:

```text
Core / Memory records
        ↓
validation + canonical snapshot
        ↓
MemoryStore semantics
        ↓
InMemoryStore
```

and later:

```text
same MemoryStore semantics
        ↓
SqliteMemoryStore
```

`InMemoryStore` is authoritative for the meaning of:

```text
admissibility
identity collision
exact resolution
claim lookup
conflict lookup
retention history
retrieval eligibility
retrieval relevance
Episode persistence transitions
```

SQLite may optimize those operations later.

It may not reinterpret them.

---

# 2. Exact persisted-record union

The Pass-2 persisted union is frozen as:

```python
type EntityMemoryRecord = (
    Observation[object]
    | Claim[object]
    | Inference[object]
    | Contradiction
    | Event
    | Effect
    | Provenance
    | Error
    | Episode
)

type NonEntityMemoryRecord = (
    Resolution
    | RetentionMark
)

type MemoryRecord = EntityMemoryRecord | NonEntityMemoryRecord
```

`Episode` is a stored Memory record but is **not** admitted through generic `persist()` because its mutable append/close semantics require a transition-oriented store surface.

Generic persistence therefore accepts:

```python
type PersistRecord = (
    Observation[object]
    | Claim[object]
    | Inference[object]
    | Contradiction
    | Resolution
    | Event
    | Effect
    | Provenance
    | Error
    | RetentionMark
)
```

No other Core or user type is admitted in Memory v0.

Explicitly unsupported include:

```text
State
Transition
History
Trace
TraceEntry
Transform
Pipeline
EffectSpec
Relation
RelationSet
AncestorReport
WorkingSet
RecallCandidate
BeliefProjection
arbitrary user Entity implementations
arbitrary classes merely carrying .id
```

Their exclusion is not a statement that they can never matter.

They have not earned a Memory-v0 persistence contract.

---

# 3. Dependency graph after Pass 2

The `store` dependency row is no longer provisional.

```text
store
    → memory.episode
    → memory.recall
    → memory.retention
    → memory.codec

    → core.identity
    → core.time
    → core.context
    → core.value
    → core.epistemic
    → core.observation
    → core.event
    → core.effect
    → core.provenance
    → core.error
```

`store` does **not** depend on:

```text
memory.belief
memory.sqlite_store
sqlite3
core.state
core.trace
core.transform
core.relation
```

`belief_state()` remains above fetched data and independent of storage.

The eventual `sqlite_store` dependency row may be finalized in Pass 3 according to its concrete implementation, but the persisted-record union itself is now frozen.

---

# 4. Persistence is a snapshot operation

Persisting a record must not leave Memory dependent on mutable objects still owned by the caller.

Therefore:

> A successful persistence operation stores a semantic snapshot of the supplied record, not the caller's object reference.

Every object-typed payload crosses Pass 1's codec boundary.

Examples:

```text
Observation.value
Observation.source
Observation.observer

Event.payload

Effect.target
Effect.metadata values

Error.metadata values

Claim Known.value

Context object fields
Context metadata values
```

are validated through the existing `PersistedValue` machinery.

Unsupported payloads propagate `UnsupportedPersistedValue`.

No `repr()` fallback.

No pickling.

No silent omission.

No mutation of the caller's source record.

A caller mutating a mutable mapping after `persist()` must not change what the store subsequently resolves.

---

# 5. Canonical record representation

`store.py` requires a private deterministic canonical representation of every admitted record.

It exists for two reasons:

```text
1. identity-collision comparison
2. semantic snapshot verification
```

It is not a new public serialization format.

The implementation constructs a tagged `PersistedValue` tree and encodes it through Pass 1's canonical codec.

Conceptually:

```text
(
    record_kind,
    canonical_field_1,
    canonical_field_2,
    ...
)
```

All fields that matter to the Core/Memory record's meaning participate.

Canonical equality must therefore distinguish two representations that Python equality may not.

Critical case:

```python
Event(id=X, payload="A") == Event(id=X, payload="B")
```

is true in Core because Event equality is identity equality.

For persistence collision purposes, those records are **not canonically identical**.

The second persistence attempt must be rejected.

Canonicalization must preserve:

```text
Id vs Ref distinction
Ref namespace
Known vs Unknown
None vs false vs zero
float sign, including -0.0
tuple ordering
mapping contents independent of iteration order
Context
all semantic record fields
```

---

# 6. Embedded identified records

Two admitted Core structures embed other identified admissible records directly:

```text
Inference.conclusion → Claim
Error.cause          → Error | None
```

Leaving those embedded Entities inside a parent while refusing to recognize their Ids globally would create an inconsistent store:

```text
"this Claim exists inside an Inference"
but
resolve(claim.id) → None
```

Therefore Pass 2 freezes this rule:

> Persistence is closed over directly embedded admissible Entity records.

For v0 that means:

### Inference

Persisting:

```python
Inference(
    id=I,
    conclusion=Claim(id=C, ...),
    ...
)
```

atomically registers:

```text
Claim C
Inference I
```

The conclusion participates normally in:

```text
resolve()
claims_for()
identity collision detection
lexical retrieval
```

### Error

Persisting an Error recursively registers its Core `cause` chain.

Each cause becomes independently resolvable by its own `Id`.

No generic reflection walks arbitrary Python objects.

Only structurally defined embedded admissible Entity fields receive this treatment.

---

# 7. Foreign exceptions are not persistable

`Error.exception` may contain an arbitrary Python `BaseException`.

Memory v0 has no semantic codec for arbitrary exception instances.

Therefore:

```text
Error.exception is None
    → persistable

Error.exception is not None
    → UnsupportedPersistedValue
```

Memory does not reduce an exception to:

```text
type name
message
repr()
str()
pickle
```

because none is the original semantic object.

A future explicit exception representation can be earned separately.

---

# 8. Atomicity of one `InMemoryStore` operation

Even before SQLite exists, an individual store operation must be all-or-nothing at the semantic level.

Example:

```text
persist(Inference)
    ↓
embedded Claim passes validation
    ↓
Inference itself conflicts with existing identity
```

must not leave the Claim newly stored if the top-level persistence operation fails.

Therefore each public mutating operation follows:

```text
validate complete operation
canonicalize complete operation
check every collision/precondition
then mutate store state
```

No partial registration.

This becomes the reference behavior Pass 3 transactions must reproduce.

---

# 9. Identity regime

All Entity-bearing records share one semantic Id namespace.

The store does not maintain independent identity universes per concrete Python type.

Conceptually:

```text
_entities: dict[Id, EntityMemoryRecord]
```

Therefore:

```text
Observation(id=X)
then
Event(id=X)
```

is an identity collision even if each type would otherwise live in a different backend table.

Same Id means one identified thing.

---

# 10. Identified-record collision policy

Frozen from the specification/matrix:

```text
Id absent
    → insert

same Id
+ same concrete semantic representation
    → idempotent success

same Id
+ different semantic representation
    → IdentityCollision
```

This applies across types.

So:

```text
Claim X vs identical Claim X
    → success, no second insertion

Claim X vs different Claim X
    → collision

Claim X vs Event X
    → collision

Event X(payload=A) vs Event X(payload=B)
    → collision
```

An idempotent retry does not:

```text
duplicate insertion order
duplicate conflict history
duplicate lexical material
duplicate Claim lookup entries
```

---

# 11. `IdentityCollision`

Supporting runtime exception:

```python
class IdentityCollision(ValueError):
    id: Id
    existing_type: type[object]
    incoming_type: type[object]
```

Its message must not stringify arbitrary record payloads.

Enough structured information is retained to identify:

```text
which Id collided
existing record type
incoming record type
```

The original stored record remains unchanged.

---

# 12. `UnsupportedMemoryRecord`

```python
class UnsupportedMemoryRecord(TypeError):
    record_type: type[object]
```

Raised when runtime callers bypass typing and supply something outside `PersistRecord`.

Examples:

```text
State(...)
Trace(...)
custom object with .id
plain dict
user dataclass
Episode passed to generic persist()
```

For Episode specifically, the error should clearly direct the caller to `create_episode()`.

---

# 13. Non-Entity append-only records

`Resolution` and `RetentionMark` remain non-Entity after persistence.

`InMemoryStore` may internally use:

```text
list position
monotonic local integer
```

to order them.

That storage position:

```text
is not Id
is not Ref
is not exposed as semantic identity
```

Persisting the exact same `RetentionMark` twice records two marks.

Persisting the exact same `Resolution` twice records two Resolution facts.

There is no hidden deduplication because neither record possesses semantic identity.

---

# 14. Conflict-log persistence

The store maintains one append-ordered conflict history consisting of:

```text
Contradiction | Resolution
```

A newly inserted `Contradiction` enters this history once.

An idempotent re-persist of the same Contradiction does not enter it again.

A `Resolution` may be persisted only when its referenced Contradiction already exists in the store's conflict history.

This mirrors Core `ContradictionLog`.

Therefore:

```text
Resolution before Contradiction
    → ValueError

Resolution referring to an Id occupied by non-Contradiction
    → ValueError

multiple Resolutions for one Contradiction
    → allowed, append-ordered
```

Wall time never determines this ordering.

---

# 15. Retention persistence

Every persisted `RetentionMark` appends to store retention history.

No deduplication.

The store exposes:

```python
def retention_for(
    self,
    item: Id | Ref,
) -> tuple[RetentionMark, ...]:
    ...
```

It returns matching marks in append order, using:

```python
identity_of(mark.item) == identity_of(item)
```

It does not sort by `RetentionMark.at`.

The store does not invent another current-accessibility algorithm.

Where retrieval needs current accessibility, it reconstructs/delegates through `RetentionLog`.

---

# 16. Exact entity resolution

The store exposes:

```python
def resolve(
    self,
    item: Id | Ref,
) -> EntityMemoryRecord | None:
    ...
```

Resolution compares underlying identity with:

```python
identity_of(item)
```

Ref namespace does not alter entity identity.

A namespaced query does not cause the resolved record itself to acquire that namespace.

`resolve()` bypasses retention accessibility.

This distinction is deliberate:

```text
resolve()
    exact structural access

retrieve()
    attention-facing discovery governed by retention
```

Therefore an ARCHIVED entity remains resolvable exactly.

Resolution/RetentionMark cannot be resolved through this API because neither is Entity-bearing.

---

# 17. `claims_for()`

```python
def claims_for(
    self,
    subject: Id | Ref,
    predicate: Kind,
) -> tuple[Claim[object], ...]:
    ...
```

Matches:

```python
identity_of(claim.subject) == identity_of(subject)
and claim.predicate == predicate
```

Order is first-persistence order.

Idempotent persistence does not duplicate the Claim.

### Critical rule

`claims_for()` performs **no Context filtering**.

It returns every stored Claim in the structural `(subject, predicate)` slot.

This is necessary because `belief_state()` determines contradiction relevance from all slot Claims **before** query-Context filtering.

Filtering here would hide conflicts from Memory's epistemic projection.

Context compatibility belongs exclusively to `belief_state()`.

---

# 18. `conflicts_for()`

```python
def conflicts_for(
    self,
    subject: Id | Ref,
    predicate: Kind,
) -> tuple[Contradiction | Resolution, ...]:
    ...
```

Algorithm:

1. Obtain the Id set of all stored Claims returned by `claims_for(subject, predicate)`.
2. Walk conflict history in append order.
3. A Contradiction is relevant iff:

```text
identity_of(contradiction.subject)
    == identity_of(subject)

AND

at least one contradiction.statement.id
belongs to the slot Claim Id set
```

4. Once a Contradiction is relevant, subsequent Resolutions referencing it are relevant.
5. Preserve original conflict-log order.

No `Resolution.rationale` parsing.

No Context filtering.

No authority ranking.

No missing-Ref inference.

If one contradiction statement is missing but another resolves to a stored Claim in the slot, the Contradiction remains relevant.

---

# 19. Episode store surface

Generic `persist()` never accepts Episode.

The exact Episode persistence surface is frozen as:

```python
def create_episode(
    self,
    *,
    id: Id,
    subject: Id | Ref,
    context: Context,
    opened_at: WallInstant,
) -> None:
    ...

def append_episode(
    self,
    episode: Id | Ref,
    item: Ref,
) -> None:
    ...

def close_episode(
    self,
    episode: Id | Ref,
    at: WallInstant,
) -> None:
    ...
```

Current Episode state is retrieved through normal:

```python
resolve(episode_id)
```

and returns an `Episode`.

---

# 20. Episode creation

`create_episode()` creates only:

```text
header
zero items
open state
```

It validates/snapshots `Context` through the existing codec boundary.

The Id enters the global Entity identity regime.

If an Entity already occupies the Id:

### Existing non-Episode

```text
→ IdentityCollision
```

### Existing Episode with different header

```text
→ IdentityCollision
```

### Existing Episode with identical header

```text
→ idempotent success
```

This remains idempotent even if that Episode has subsequently acquired items or been closed.

Creation identity concerns its immutable header, not its later append-only state.

---

# 21. Episode append

`append_episode()`:

```text
requires existing Episode
requires Episode is open
appends exact Ref
preserves duplicates
preserves namespace
preserves append order
```

Missing episode:

```text
→ KeyError
```

Id occupied by non-Episode:

```text
→ TypeError
```

Closed Episode:

```text
→ ValueError
```

The operation never follows the Ref being appended.

Self-reference and cycles remain opaque and valid.

---

# 22. Episode close

`close_episode()`:

```text
requires existing Episode
requires open
requires at >= opened_at
sets closed_at exactly once
```

Second close:

```text
→ ValueError
```

Close before opened_at:

```text
→ ValueError
```

Wall time is not used to reorder Episode items.

---

# 23. Episode resolution returns a snapshot

`resolve(Episode.id)` returns a fresh Episode representing current stored state.

The caller may mutate that returned Episode object locally.

Such local mutation does not change the store.

A later `resolve()` reflects only mutations performed through:

```text
append_episode()
close_episode()
```

This is the same snapshot principle applied to Memory's own mutable construction.

---

# 24. `RetrievalQuery`

Pass 2 freezes the retrieval-query shape:

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalQuery:
    context: Context
    identity: Id | Ref | None = None
    text: str | None = None
    include_archived: bool = False
```

Required invariants:

```text
identity is not None OR text is not None

if text is supplied:
    text must not be empty
```

No result limit exists.

Bounding attention belongs to:

```text
WorkingSet
```

not retrieval.

No score threshold exists.

No model-generated relevance field exists.

---

# 25. Retrieval selector semantics

`identity` and `text` are an OR query.

If a stored Entity matches either selector, it is a candidate.

If it matches both, it appears once with both relevance Kinds.

Relevance tuple order is deterministic:

```text
IDENTITY_MATCH
LEXICAL_MATCH
```

when both apply.

That tuple order does not itself encode a numeric ranking.

---

# 26. Lexical query contract

Pass 2 resolves the previously deferred lexical-query surface.

Memory v0 lexical semantics are:

> literal, case-sensitive substring matching over explicitly designated lexical content fields.

Therefore:

```text
FTS operators are not part of Memory's query language.

"*"
"OR"
"NEAR"
quotes
parentheses
hyphens
```

are ordinary characters in the semantic query.

No Unicode normalization.

No case folding.

No stemming.

No token inference.

This makes membership backend-independent.

Pass 3 may use FTS5 to accelerate or rank potential hits, but it must post-validate membership against this same literal rule.

SQLite syntax never becomes Memory syntax.

---

# 27. Lexical content extraction

Pass 2 freezes the fields that contribute generic recall text.

A helper owned by `store.py` defines:

```python
def lexical_content(
    record: EntityMemoryRecord,
) -> tuple[str, ...]:
    ...
```

This is supporting semantic machinery, not a new Memory construction.

It returns only text already semantically present.

### Observation

Include any direct field among:

```text
value
source
observer
```

whose actual value is already a bare `str`.

Do not descend into tuples/mappings/Context.

### Claim

If:

```python
claim.value is Known
```

and the inner value is bare `str`, include it.

`Unknown` contributes no text.

### Inference

Contributes no separate text in v0.

Its conclusion Claim is independently registered and searchable under the Claim's own identity.

### Contradiction

No generic lexical content.

### Event

Include:

```text
payload
```

only when it is already bare `str`.

### Effect

Include:

```text
description
```

always.

Also include:

```text
target
```

when already bare `str`.

Do not recursively search metadata.

### Provenance

Include:

```text
transform_name
transform_version
```

### Error

Include:

```text
message
operation
```

when operation is not `None`.

Do not recursively index:

```text
cause
exception
metadata
Context
```

### Episode

No generic lexical content.

Nothing invokes `str()` or `repr()` on arbitrary payloads.

---

# 28. `retrieve()`

```python
def retrieve(
    self,
    query: RetrievalQuery,
    *,
    retrieved_at: WallInstant,
) -> tuple[RecallCandidate, ...]:
    ...
```

No clock is read internally.

Every candidate receives the exact caller-supplied:

```text
retrieved_at
query.context
```

---

# 29. Raw retrieval ordering

Before retention is applied, the `InMemoryStore` reference order is:

```text
1. identity-matched entity, if one exists
2. remaining lexical matches in first-persistence order
```

A record matching both appears only once.

Lexical match order is first-persistence order.

This gives the reference backend a deterministic order without claiming that all future backends must share its ranking.

Pass 3 may rank lexical matches differently.

Candidate membership and relevance meaning remain identical.

---

# 30. Retention composition

After raw matches exist, retrieval consults stored RetentionMarks through `RetentionLog`.

Frozen partition:

```text
ACTIVE
    eligible
    first partition

DEPRIORITIZED
    eligible
    second partition

ARCHIVED
    excluded by default

ARCHIVED + include_archived=True
    eligible
    third partition

any other accessibility Kind
    → ValueError
```

Ordering within each partition preserves the backend's raw order.

So the final reference order is:

```text
ACTIVE raw order
DEPRIORITIZED raw order
ARCHIVED raw order, only if explicitly included
```

A custom accessibility Kind never silently disappears and never receives guessed semantics.

---

# 31. Exact resolve vs retrieval

This distinction is frozen:

```text
resolve(X)
```

answers:

> Do you structurally store Entity X, and what is its current stored representation?

while:

```text
retrieve(query)
```

answers:

> Which stored Entities does this retrieval operation surface under the current accessibility policy?

Therefore:

```text
ARCHIVED entity
    resolve → present
    default retrieve → absent
```

and:

```text
failed lexical retrieval
```

does not imply absence.

---

# 32. `MemoryStore` protocol

Final Pass-2 protocol:

```python
@runtime_checkable
class MemoryStore(Protocol):
    def persist(self, record: PersistRecord) -> None: ...

    def resolve(
        self,
        item: Id | Ref,
    ) -> EntityMemoryRecord | None: ...

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        retrieved_at: WallInstant,
    ) -> tuple[RecallCandidate, ...]: ...

    def claims_for(
        self,
        subject: Id | Ref,
        predicate: Kind,
    ) -> tuple[Claim[object], ...]: ...

    def conflicts_for(
        self,
        subject: Id | Ref,
        predicate: Kind,
    ) -> tuple[Contradiction | Resolution, ...]: ...

    def retention_for(
        self,
        item: Id | Ref,
    ) -> tuple[RetentionMark, ...]: ...

    def create_episode(
        self,
        *,
        id: Id,
        subject: Id | Ref,
        context: Context,
        opened_at: WallInstant,
    ) -> None: ...

    def append_episode(
        self,
        episode: Id | Ref,
        item: Ref,
    ) -> None: ...

    def close_episode(
        self,
        episode: Id | Ref,
        at: WallInstant,
    ) -> None: ...
```

`InMemoryStore` must structurally satisfy this protocol.

---

# 33. No generic enumeration API

Pass 2 does not add:

```text
all_records()
list_everything()
scan()
delete()
update()
upsert()
```

A backend can internally scan its records to implement reference behavior.

That is not automatically exposed as public capability.

The agent-facing use case has not earned a general database-browser API.

---

# 34. No delete operation

Memory Law 1 remains structural.

`MemoryStore` has no:

```text
delete
remove
purge
overwrite
replace
```

surface.

Retention changes accessibility.

It does not mutate existence.

Administrative physical deletion/retention policy remains outside Memory v0.

---

# 35. Persistence ordering

Entity insertion order is first successful semantic insertion.

Idempotent retries do not receive new positions.

Embedded entities receive their first insertion position when their enclosing operation successfully commits.

For:

```text
persist(Inference(conclusion=C))
```

where C is new:

```text
C is registered before I
```

because the parent structurally depends on its conclusion.

For an Error cause chain:

```text
oldest/root cause
    before
wrapping Error
```

when all are new within one persistence operation.

This affects deterministic reference-store lexical ordering only.

It does not imply epistemic precedence.

---

# 36. Persistence of `Claim.value`

Core represents Claim value as:

```text
Known[T] | Unknown
```

Persistence preserves that distinction explicitly.

### Unknown

```text
Unknown
```

stores as epistemic Unknown.

It is not converted to:

```text
None
false
missing
```

### Known

The inner value must satisfy `PersistedValue`.

So:

```python
Known("hello")
Known(42)
Known(("a", "b"))
```

may persist.

```python
Known(CustomClass())
```

fails through `UnsupportedPersistedValue`.

---

# 37. Persistence of `Observation`

The following must all satisfy the durable-value boundary:

```text
value
source
observer, when present
```

Context also must be encodable by the existing Context codec.

A failure in any field rejects the entire persistence operation.

---

# 38. Persistence of `Event`

`Event.payload` must satisfy `PersistedValue`.

Optional Context must satisfy the existing Context codec.

Collision comparison uses every Event field, not Core's Id-only `__eq__`.

---

# 39. Persistence of `Effect`

`target` must satisfy `PersistedValue`.

Every metadata value must satisfy `PersistedValue`.

Description remains exact text.

Optional Context must be persistable.

---

# 40. Persistence of `Provenance`

All fields already have explicit Core representations.

No parent/input Ref is resolved as a prerequisite to persistence.

Unresolved provenance Refs remain valid evidence.

Memory persistence does not turn provenance into referential-integrity enforcement.

---

# 41. Persistence of `Contradiction`

Statement Refs need not all currently resolve.

Core already permits opaque Refs.

Persistence preserves them exactly.

However the Contradiction itself joins the store's append-ordered conflict history on first successful insertion.

---

# 42. Persistence of `Resolution`

The referenced Contradiction **must** already be present in the store's conflict history.

This is stricter than general opaque Ref persistence because Core's own `ContradictionLog.record()` imposes exactly that ordering invariant.

The Ref, including namespace, is preserved exactly.

No structured winner is inferred.

---

# 43. Persistence of `Error`

`Error` supports:

```text
structured Core cause chain
metadata satisfying PersistedValue
optional Context
operation
recoverable
```

Foreign exception persistence is unsupported:

```text
exception is not None
    → UnsupportedPersistedValue
```

The entire Error/cause-chain registration remains atomic.

---

# 44. Persistence of `RetentionMark`

A RetentionMark may refer to an Entity not yet stored.

Memory does not impose referential integrity on the mark.

Why:

```text
Ref itself is valid evidence
Memory may receive accessibility policy before the entity arrives
```

When that Entity later exists, normal `identity_of()` matching makes the mark applicable.

The mark remains append-only.

---

# 45. In-memory data structures are implementation details

A straightforward implementation may use:

```text
_entities
_entity_canonical
_entity_order
_claim_order
_conflict_entries
_retention_marks
_episode_state
```

but these names/shapes are not public API.

What is frozen is externally observable behavior.

---

# 46. Pass-2 adversarial requirements

Pass 2 must fully prove matrix:

```text
K — persistence admissibility
L — identity collisions
```

and establish the in-memory reference behavior for the parts of:

```text
N — contradiction lookup
O — retrieval + retention
Q — backend reference semantics
```

that do not require SQLite.

Pass 3 will prove SQLite produces equivalent semantics.

---

# 47. Required K coverage

Every PA case must receive a test.

Especially:

```text
PA-01 supported Observation succeeds
PA-02 Resolution succeeds
PA-03 RetentionMark succeeds
PA-04 arbitrary Core Entity rejected
PA-05 user object with .id rejected
PA-06 unsupported Core record rejected
PA-07 unsupported nested payload propagates codec error
PA-08 caller record not mutated
PA-09 non-Entity ordering identity remains internal
PA-10 non-Entity persistence does not make it Entity
PA-11 no Ref construction surface from internal position
```

---

# 48. Required L coverage

Explicitly test:

```text
first Id insertion
idempotent identical retry
different representation same Id
Event Id-equality trap
same Id across concrete record types
failed collision leaves original untouched
```

Collision tests must use canonical record representation, never ordinary record equality.

---

# 49. Additional embedded-entity tests

Required:

```text
persist Inference with new conclusion
    → resolve conclusion succeeds
    → claims_for sees conclusion

persist Inference where embedded conclusion conflicts
    → entire operation fails
    → Inference absent

persist Error with cause
    → cause independently resolves

persist Error where nested cause collides
    → entire operation fails

persist Error with foreign exception
    → UnsupportedPersistedValue
    → no Error/cause partial write
```

---

# 50. Additional claim-query tests

Required:

```text
same subject Id vs Ref matches

different Ref namespace still same subject identity

different predicate excluded

Context is NOT filtered by claims_for

persistence order preserved

idempotent retry does not duplicate

Inference-embedded conclusion appears exactly once
```

Include a regression reproducing the Pass-1 hazard:

```text
Claim participating in contradiction
is incompatible with eventual query Context

claims_for still returns it
```

so `belief_state()` can correctly determine conflict relevance before Context filtering.

---

# 51. Additional conflict-query tests

Use matrix CL-01–CL-10 against `InMemoryStore` now.

Required properties include:

```text
subject mismatch excluded
no slot statement excluded
one slot statement sufficient
cross-predicate contradiction retained
missing statement does not fabricate Claim
one missing + one slot Claim still relevant
namespace does not break Claim identity
Resolution follows relevant Contradiction
multiple Resolutions preserve append order
rationale never parsed
```

CL-11 remains the Pass-3 SQL equivalence test.

---

# 52. Additional retention tests

Required:

```text
no marks → ACTIVE when retrieval consults RetentionLog

append order beats timestamp

namespace cannot bypass mark

duplicate marks preserved

ARCHIVED direct resolve still works

ARCHIVED default retrieve excluded

ARCHIVED archive-inclusive retrieve included

DEPRIORITIZED after ACTIVE

custom accessibility → retrieve raises

retention_for itself still returns custom mark normally
```

---

# 53. Retrieval-query tests

Required:

```text
query with neither identity nor text rejects

empty text rejects

identity-only retrieval

text-only retrieval

combined identity + lexical relevance

duplicate match produces one RecallCandidate

retrieved_at copied exactly

query Context copied exactly

no hidden clock read

no result limit/truncation
```

---

# 54. Lexical-contract tests

Required literal behavior:

```text
case-sensitive

substring, not token-only

FTS-looking characters treated literally

no case folding

no Unicode normalization

no str()/repr() fallback
```

Field-policy tests:

```text
Observation bare-string value searchable
Observation int value not searchable
Observation nested mapping string not recursively searchable

Claim Known[str] searchable
Claim Unknown not searchable

Event bare-string payload searchable
Event bytes not searchable

Effect.description searchable
Effect string target searchable
Effect nested metadata not recursively searchable

Error.message searchable
Error.operation searchable
Error metadata not recursively searchable

Provenance transform_name/version searchable

Inference conclusion text appears under Claim identity,
not duplicated under Inference identity

Episode has no generic lexical text
```

---

# 55. Episode store tests

Although matrix M is formally SQLite-focused, Pass 2 must establish the reference transition semantics now.

Required:

```text
create empty/open

same header create is idempotent

different header same Id collides

append exact order

append duplicate Ref preserved

append self Ref valid

close once

close twice fails

append after close fails

close before open fails

resolve returns current state

resolved Episode is a snapshot,
not live store state

caller mutation of resolved Episode does not mutate store

Episode Id collides globally with another Entity type
```

Pass 3 then proves the database survives transactions/reopen/corruption cases.

---

# 56. Protocol test

Explicitly prove:

```python
isinstance(InMemoryStore(), MemoryStore)
```

through `@runtime_checkable`.

Also prove a deliberately incomplete fake does not satisfy the protocol.

---

# 57. Import-side-effect discipline

Importing:

```text
memory.store
```

must not:

```text
read wall time
read monotonic time
allocate UUID
open files
connect database
read environment
start threads/processes
```

The store constructs no singleton instance at import.

---

# 58. No clock / identity source dependencies

Pass 2 adds neither:

```text
Clock
IdSource
```

to `InMemoryStore`.

All identities come from supplied records or Episode creation arguments.

All retrieval times come from explicit:

```text
retrieved_at
```

This preserves Core's hidden-nondeterminism discipline.

---

# 59. Concurrency posture

`InMemoryStore` is:

```text
mutable
single-writer
not thread-safe
```

No locks.

No asyncio.

No transaction abstraction.

Atomicity here means:

> one public method either commits its complete semantic mutation or none of it.

It does not mean multi-thread transaction isolation.

---

# 60. Explicitly forbidden in Pass 2

Do not add:

```text
sqlite_store.py implementation
sqlite3
database files
DDL
schema versioning
FTS5
vector search
embeddings
Consolidation
agent/model calls
filesystem persistence
delete
overwrite
upsert
general query language
result limit
pagination
source-authority ranking
Context-based retrieval filtering
temporal carry-forward
automatic contradiction creation
resolution adjudication
general codec registry
thread synchronization
async APIs
```

Do not modify Core.

---

# 61. Documentation updates before implementation

Pass 2 preregistration should update the frozen Memory docs in these narrow ways.

## MEMORY_ARCHITECTURE.md

Replace provisional `store` dependency row with the final Pass-2 row.

Freeze:

```text
persisted record union
MemoryStore protocol
RetrievalQuery
resolve()
retention_for()
Episode store surface
lexical query semantics
```

Remove the statement that those items remain open.

## MEMORY_ADVERSARIAL_MATRIX.md

Section K:

```text
candidate union
```

becomes:

```text
frozen Pass-2 union
```

Section Y removes from "still open":

```text
exact persisted-record union
Episode store surface
lexical query contract
```

Store corruption/decode wrapping remains deferred to Pass 3.

## MEMORY_SPECIFICATION.md

No ontology expansion.

A short Pass-2 boundary clarification may record:

```text
generic persist excludes Episode;
Episode uses create/append/close

embedded Inference conclusions / Error causes
participate in global stored identity
```

Do not introduce a sixth Memory construction.

---

# 62. Suggested tests

```text
tests/memory/semantics/
├── existing Pass-1 files...
└── test_store.py
```

One file is sufficient unless it becomes genuinely unwieldy.

Test names should retain matrix IDs where applicable:

```python
test_pa_01_supported_observation_persists()
test_id_04_event_identity_equality_does_not_hide_collision()
test_cl_08_resolution_follows_relevant_contradiction()
test_rr_06_custom_accessibility_fails_default_retrieval()
```

Pass-2-only tests can use descriptive names.

---

# 63. Quality gates

Pass 2 closes only when:

```text
all existing Core tests green
all Pass-1 Memory tests green
all Pass-2 tests green

pytest green
Ruff green
Pyright strict green

Core production files unchanged
no skipped/xfail K/L cases
InMemoryStore structurally satisfies MemoryStore
```

Additionally perform a manual audit of:

```text
exact persisted union
every admitted record's snapshot rule
all Id-bearing nested structures
all object-typed payload fields
all public store methods
```

---

# 64. Pass-2 commit boundary

Production change:

```text
src/memory/store.py
```

plus:

```text
tests/memory/semantics/test_store.py
docs/memory-passes/02-persistence-boundary.md
necessary narrow Memory-document corrections
```

No `sqlite_store.py` implementation belongs in the Pass-2 commit.

---

# 65. Success condition

When Pass 2 closes, the following must be possible with no database:

```text
persist an Observation safely

persist and retrieve a Claim

persist an Inference and independently resolve its conclusion

preserve a conflict log and query relevant conflict history

record accessibility history

resolve an archived Entity exactly

retrieve attention candidates under retention rules

lexically retrieve only genuine textual content

create / append / close / resolve an Episode

retry identical persistence safely

reject an identity collision without changing prior state

reject unsupported durable payloads without partial writes
```

At that point Memory has a complete semantic storage boundary.

Pass 3 then has a sharply constrained job:

> reproduce the same behavior with SQLite, FTS5, transactions, reopen durability, and corruption detection.

If SQLite pressure appears to require changing any of these semantics, SQLite loses.

The Memory theory does not.
