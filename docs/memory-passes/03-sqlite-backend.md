# Memory v0 — Pass 3 Preregistration

## Status

**Pass:** 3 of 4
**Name:** SQLite Durable Backend
**Question answered:** Can the frozen MemoryStore semantics survive durable storage, process restart, transactional failure, indexing, and corrupted external state without SQLite acquiring semantic authority?

Pass 1 and Pass 2 are closed.

Pass 3 introduces:

```text
src/memory/sqlite_store.py
```

owning:

```text
SqliteMemoryStore

StoreCorruption
UnsupportedSchemaVersion
SqliteStoreClosed
```

plus private operation-codec, schema, replay, transaction, checksum, and FTS-index machinery.

Core remains unchanged.

`memory.store` remains the semantic authority.

`SqliteMemoryStore` must satisfy the already-frozen `MemoryStore` protocol.

Pass 3 proves adversarial-matrix sections:

```text
M — Episode SQLite transitions
N — SQLite contradiction lookup equivalence
O — retrieval + retention
P — FTS lexical indexing
Q — backend equivalence
U — crash / reopen / database integrity
```

Pass 4 remains architectural closure.

---

# 1. Central rule

SQLite is durable mechanism, not Memory semantics.

The dependency is:

```text
Memory laws
    ↓
MemoryStore protocol
    ↓
InMemoryStore reference semantics
    ↓
SqliteMemoryStore durable realization
    ↓
SQLite
```

Never:

```text
SQLite behavior
    ↓
new Memory semantics
```

If SQLite cannot represent an already-frozen behavior cleanly, SQLite's implementation changes.

Memory does not.

---

# 2. Durable architecture — append-only operation journal

Pass 3 uses an **append-only operation journal** as authoritative SQLite state.

It does not create a second table-level semantic model for every Memory record.

Authoritative durable state is:

```text
ordered MemoryStore operations
```

such as:

```text
persist(record)
create_episode(...)
append_episode(...)
close_episode(...)
```

On open:

```text
SQLite journal
      ↓
strict decode + integrity verification
      ↓
replay in sequence
      ↓
fresh InMemoryStore
```

The resulting `InMemoryStore` is the current semantic projection.

This design is deliberate.

Memory is already predominantly append-only:

```text
identified records never overwrite
Resolution appends
RetentionMark appends
Episode items append
Episode closes once
```

Persisting the operations that produced state therefore preserves more information while duplicating less semantic logic.

---

# 3. Why not one SQL table per semantic record type?

A normalized record-per-table design would require SQL to independently reimplement:

```text
identity collision semantics
embedded Inference/Claim atomicity
Error cause-chain atomicity
canonical equality
conflict-history ordering
retention projections
Episode transition validity
retrieval semantics
```

Pass 2 exists specifically so we do not have to trust two implementations of those rules.

The journal architecture instead asks SQL to prove:

```text
Can these already-valid operations be preserved,
ordered, recovered, and replayed exactly?
```

That is a narrower and safer responsibility.

Normalized/optimized projections may be earned later.

---

# 4. Authoritative vs derived SQLite state

Authoritative:

```text
schema metadata
operation journal
operation integrity digests
```

Derived and rebuildable:

```text
FTS5 lexical index
in-process InMemoryStore projection
entity-id bookkeeping used for FTS rebuilding
```

Destroying a derived index must not destroy Memory facts.

A derived index may be rebuilt entirely from the authoritative journal.

---

# 5. Frozen SQLite schema version

Pass 3 schema version:

```text
1
```

No migration framework exists in v0.

The store supports:

```text
new/empty database
schema version 1 database
```

Anything else fails explicitly.

This is schema version detection, not a migration framework.

---

# 6. SQLite schema

Conceptually:

```sql
CREATE TABLE memory_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE memory_ops (
    seq       INTEGER PRIMARY KEY,
    op_kind   TEXT NOT NULL,
    payload   BLOB NOT NULL,
    digest    BLOB NOT NULL
);

CREATE VIRTUAL TABLE memory_fts USING fts5(
    id_kind     UNINDEXED,
    id_value    UNINDEXED,
    field_index UNINDEXED,
    content
);
```

`memory_meta` contains at minimum:

```text
schema_version = "1"
```

Exact DDL syntax may vary for SQLite correctness, but these semantic roles do not.

No record table receives a semantic identity not already present in Memory.

---

# 7. Operation sequence

`memory_ops.seq` is a SQLite-local total order.

It is:

```text
storage-local
strictly increasing
contiguous from 1
never a Core Id
never a Ref target
never exposed through MemoryStore
```

A transaction computes:

```text
next_seq = current_max_seq + 1
```

and inserts that exact sequence.

Because failed operations roll back, committed journal order remains contiguous.

On reopen:

```text
1, 2, 3, ..., N
```

is required.

A missing, duplicated, reordered, or invalid sequence is corruption.

---

# 8. Operation integrity digest

Each journal row carries a SHA-256 digest.

The digest covers at least:

```text
journal format version
seq
op_kind
payload
```

Conceptually:

```text
SHA256(
    "memory.sqlite.operation.v1"
    || canonical(seq)
    || canonical(op_kind)
    || payload
)
```

The exact unambiguous byte framing is fixed in implementation and tested.

Digest purpose:

```text
detect accidental storage corruption
detect payload modification
detect op-kind modification
detect sequence modification
```

It is **not** claimed as protection against a malicious actor who can rewrite both the database and checksum.

No cryptographic-security claim is made beyond corruption detection.

---

# 9. Journal operation vocabulary

Closed Pass-3 operation tags:

```text
persist
create_episode
append_episode
close_episode
```

Unknown operation tags are corruption.

This is SQLite-journal vocabulary, not a new Memory ontology.

---

# 10. Operation payload format

Every operation payload is encoded through Pass 1's existing:

```text
PersistedValue
encode_persisted_value()
decode_persisted_value()
```

The operation payload itself therefore inherits:

```text
deterministic encoding
arbitrary-precision int support
finite-float requirement
negative-zero preservation
immutable mapping semantics
strict malformed-input rejection
```

No pickle.

No repr serialization.

No arbitrary Python object encoding.

---

# 11. Private record codec

`sqlite_store.py` owns a **private durable record codec** for the already-frozen `PersistRecord` union.

It is not exported as another Memory construction.

Conceptually:

```text
PersistRecord
    ↓
strict tagged PersistedValue tree
    ↓
canonical bytes
```

and:

```text
canonical bytes
    ↓
strict tagged decode
    ↓
PersistRecord
```

Round-trip reconstruction must produce a record that the existing `InMemoryStore` accepts with identical semantics.

---

# 12. Record tags

The private record codec has exactly these top-level record tags:

```text
observation
claim
inference
contradiction
resolution
event
effect
provenance
error
retention_mark
```

Episode is not a `persist` record and therefore has no generic persisted-record tag.

Episode state is represented by its three operation types.

Unknown record tags fail during replay as `StoreCorruption`.

---

# 13. Existing Core codecs are reused

The SQLite record codec uses the already-implemented codecs for:

```text
Kind
Id
Namespace
Ref
WallInstant
Duration
Context
PersistedValue
```

It does not invent alternative encodings for those concepts.

For an `Id | Ref` field, the record payload explicitly records which representation was supplied.

Thus:

```text
Id(X)
```

and:

```text
Ref(X)
```

do not collapse merely because `identity_of()` considers them to identify the same entity.

---

# 14. Observation encoding

Preserve exactly:

```text
id
subject
value
at
source
context
observer
```

The object-valued fields are already guaranteed to be `PersistedValue`-shaped by Pass 2.

Decode reconstructs a Core `Observation`.

No field may be omitted silently.

Unknown extra structural fields in a v1 encoded record are rejected rather than guessed.

---

# 15. Claim encoding

Preserve exactly:

```text
id
subject
predicate
Known vs Unknown
Known inner value, when present
context
asserted_by
evidence_refs, in tuple order
at
```

`Unknown` remains epistemic Unknown.

It never decodes as:

```text
None
missing
False
```

---

# 16. Inference encoding

Preserve exactly:

```text
id
premises
method
conclusion Claim
at
```

The conclusion is encoded structurally as a nested Claim.

During replay:

```python
reference.persist(inference)
```

again performs the frozen Pass-2 closure over the embedded Claim.

SQLite does not independently insert the conclusion as a second semantic operation.

---

# 17. Contradiction encoding

Preserve exactly:

```text
id
subject
statements
detected_at
context
```

Statement Refs remain opaque.

No referential-integrity requirement is introduced.

Replay through `InMemoryStore.persist()` reconstructs conflict-history ordering.

---

# 18. Resolution encoding

Preserve exactly:

```text
contradiction Ref
rationale
resolved_by
at
```

Resolution remains non-Entity.

The journal sequence supplies storage order only.

Replay again requires its Contradiction to have occurred earlier.

A journal containing Resolution-before-Contradiction is corruption.

---

# 19. Event encoding

Preserve exactly:

```text
id
kind
at
payload
context
```

Replay collision semantics continue to use `InMemoryStore`'s canonical full-record comparison, not Core Event's Id-only equality.

SQLite does not use Python equality as persistence equality.

---

# 20. Effect encoding

Preserve exactly:

```text
id
kind
description
target
at
context
metadata
```

No nested metadata stringification.

---

# 21. Provenance encoding

Preserve exactly:

```text
id
transform_id
transform_name
transform_version
inputs
parents
at
duration
context
```

Input/parent Refs need not resolve.

---

# 22. Error encoding

Preserve exactly:

```text
id
kind
message
at
cause
context
operation
recoverable
metadata
```

`cause` is recursively encoded.

`exception` must be `None`; Pass 2 already forbids persistence otherwise.

If durable bytes nevertheless decode to a foreign-exception field or equivalent unsupported structure, replay fails as corruption.

Cause-chain identity remains part of the encoded structure.

---

# 23. RetentionMark encoding

Preserve exactly:

```text
item
accessibility
at
rationale
```

RetentionMark remains non-Entity.

Repeated identical marks remain repeated journal operations.

No durable deduplication occurs.

---

# 24. `persist` journal payload

A `persist` operation contains exactly one top-level `PersistRecord`.

For Entity-bearing records, the record written to the journal is the **effective snapshotted representation accepted by `InMemoryStore`**, not a caller-owned mutable object.

Operationally:

```text
apply persist() to reference projection
        ↓
resolve top-level stored entity when Entity-bearing
        ↓
encode effective stored snapshot
```

For:

```text
Resolution
RetentionMark
```

the supplied records are already immutable and have no arbitrary mutable payload fields beyond structures already frozen by Core.

This avoids the journal retaining a representation different from the semantic state that Pass 2 accepted.

---

# 25. Episode-create journal payload

Preserve:

```text
id
subject
context
opened_at
```

No items.

No closed time.

An idempotent `create_episode()` call may still be journaled.

Replay remains idempotent under the frozen Episode header rule.

---

# 26. Episode-append journal payload

Preserve:

```text
episode Id | Ref
item Ref
```

Exact namespaces are retained.

Duplicate item Refs remain separate operations.

Self-reference is valid.

---

# 27. Episode-close journal payload

Preserve:

```text
episode Id | Ref
at
```

Replay enforces:

```text
existing Episode
open state
at >= opened_at
one close only
```

A committed journal violating those rules is corrupt.

---

# 28. Replay is the semantic decoder

Opening an existing store creates:

```python
reference = InMemoryStore()
```

Then for every journal operation in `seq` order:

```text
verify sequence
verify checksum
strictly decode operation
apply operation through InMemoryStore public API
```

Replay never mutates `InMemoryStore` internals directly.

This is the central equivalence guarantee.

If replayed durable operations cannot be accepted by the reference semantics, the database is corrupt.

---

# 29. No private `memory.store` helper imports

`sqlite_store.py` must not reach into:

```text
_canonical_record
_snapshot_context
_check
_commit
_entities
_entity_order
_conflict_entries
```

or other private `InMemoryStore` machinery.

It uses the public frozen boundary:

```text
MemoryStore methods
public type aliases
public exceptions
RetrievalQuery
lexical_content()
```

plus the public Core/codec constructions necessary for serialization.

This prevents SQLite from coupling to Pass-2 implementation internals.

---

# 30. SqliteMemoryStore query strategy

For v0, query methods delegate to the replayed reference projection:

```python
resolve(...)
claims_for(...)
conflicts_for(...)
retention_for(...)
retrieve(...)
```

delegate to:

```text
self._reference
```

This is intentional.

Pass 3's purpose is to prove durable equivalence before optimizing queries into SQL.

A later optimization may replace a delegated query with SQL only after backend-equivalence tests prove the replacement.

---

# 31. Consequence: SQLite does not own ranking

Because `retrieve()` delegates to the reference store in Pass 3:

```text
candidate membership
relevance labels
retention partitioning
default ordering
archive behavior
custom-Kind failures
```

are identical to `InMemoryStore`.

SQLite ranking does not yet alter public results.

This is stricter than the specification requires and therefore safe.

---

# 32. FTS5 role in Pass 3

FTS5 is a **derived durable-search index**, not the authority for Memory retrieval semantics.

It proves that the SQLite backend can faithfully index exactly the textual material frozen in Pass 2.

The authoritative facts remain journal operations.

FTS can always be rebuilt.

---

# 33. Why FTS5 is not authoritative yet

Memory's frozen lexical contract is:

```text
literal
case-sensitive
substring
no Unicode normalization
no query-language operators
```

Default FTS token semantics are not identical to that contract.

Therefore v0 does not pretend they are equivalent.

Using FTS5 directly as the semantic membership test would let an implementation detail silently redefine retrieval.

Pass 3 refuses that shortcut.

---

# 34. FTS source function

All FTS contents come exclusively from existing:

```python
lexical_content(record)
```

No second text-extraction policy exists in `sqlite_store.py`.

This guarantees indexing remains aligned with Pass 2.

---

# 35. FTS rows

For each Entity whose:

```python
lexical_content(entity)
```

returns strings, create one row per returned string.

Store:

```text
id.kind.value
id.value
field_index
exact text
```

The index does not contain:

```text
Resolution.rationale
RetentionMark.rationale
nested arbitrary mappings
stringified bytes
repr() output
```

because `lexical_content()` already excludes them.

---

# 36. FTS is rebuilt from semantic projection

A complete FTS rebuild does:

```text
clear FTS table
iterate known Entity ids
resolve each Entity from reference projection
lexical_content(entity)
insert resulting strings exactly
```

The index is therefore reconstructible.

No FTS row is an independent Memory fact.

---

# 37. Idempotent persistence and FTS

Idempotently persisting the same identified record must not create duplicate searchable content.

A rebuild naturally guarantees this because lexical rows are derived once from current Entity state.

This satisfies FT-11 without requiring FTS-specific semantic deduplication rules.

---

# 38. Reopen and FTS

On successful journal replay:

```text
discard/rebuild derived FTS contents
```

from the reconstructed reference state.

This proves:

```text
journal → semantic state → lexical index
```

is sufficient to reconstruct search material after process restart.

A stale/mismatched FTS index never overrides authoritative journal state.

---

# 39. Derived-index corruption

If authoritative journal/meta state is valid but the FTS table contents are stale or semantically inconsistent, the index may be discarded and rebuilt.

This is repair of derived state, not silent repair of Memory facts.

Corruption in:

```text
memory_meta
memory_ops
journal payload
journal sequence
journal checksum
```

is fatal and surfaced.

---

# 40. Constructor

Recommended public signature:

```python
class SqliteMemoryStore:
    def __init__(
        self,
        path: str | os.PathLike[str],
    ) -> None:
        ...
```

Construction is the explicit point where filesystem/database effects begin.

Importing `memory.sqlite_store` itself performs no I/O.

`:memory:` remains a valid SQLite path for tests, although reopen tests require a filesystem path.

---

# 41. Store lifecycle

SQLite owns an external resource, so Pass 3 earns:

```python
def close(self) -> None:
    ...
```

`close()` is SQLite-backend-specific and does not expand `MemoryStore`.

After close, every public MemoryStore operation raises:

```text
SqliteStoreClosed
```

rather than silently answering from the still-present in-memory projection.

No background connection reopening occurs.

---

# 42. `SqliteStoreClosed`

Supporting runtime exception:

```python
class SqliteStoreClosed(RuntimeError):
    ...
```

No additional semantic information is required.

This is resource-lifecycle machinery, not a Memory concept.

---

# 43. New database initialization

For a database with none of Memory's schema objects:

```text
BEGIN
create memory_meta
create memory_ops
create memory_fts
insert schema_version=1
COMMIT
```

Then construct an empty `InMemoryStore`.

No journal rows.

No FTS rows.

Partial schema initialization must not be left behind on failure.

---

# 44. Existing database detection

The constructor inspects SQLite schema objects.

Three high-level states exist:

```text
no Memory schema objects
    → initialize new v1 database

complete expected v1 schema
    → validate and open

partial/inconsistent Memory schema
    → StoreCorruption
```

It does not silently create whichever tables happen to be missing from an existing Memory database.

---

# 45. Unsupported schema version

If:

```text
schema_version != 1
```

raise:

```python
class UnsupportedSchemaVersion(RuntimeError):
    found: str
    supported: int
```

Do not:

```text
guess
downgrade
upgrade
migrate
rewrite metadata
```

---

# 46. StoreCorruption

Supporting runtime exception:

```python
class StoreCorruption(RuntimeError):
    sequence: int | None
    reason: str
```

`sequence` identifies the journal operation where possible.

Examples:

```text
failed SQLite integrity check
non-contiguous operation sequence
checksum mismatch
unknown op tag
malformed operation payload
malformed record payload
journal operation rejected by InMemoryStore replay
invalid required schema structure
```

The exception preserves the underlying exception through normal exception chaining where useful.

---

# 47. SQLite integrity check

Opening an existing database performs:

```sql
PRAGMA integrity_check
```

A result other than:

```text
ok
```

fails as `StoreCorruption`.

This is in addition to Memory-specific journal validation.

SQLite structural integrity does not replace semantic replay validation.

---

# 48. Operation checksum verification

Before decoding/replay:

```text
recompute digest
constant-time compare to stored digest
```

A mismatch raises `StoreCorruption` for that sequence.

Malformed digest length is also corruption.

Checksum validation precedes semantic decode.

---

# 49. Sequence verification

Replay requires:

```text
first seq = 1
each next seq = previous + 1
```

No gaps.

No duplicates.

No reordering.

Because `seq` participates in the checksum, changing sequence position without rewriting the digest is also detected.

---

# 50. Semantic replay failure

If a well-formed durable operation causes reference semantics to raise, for example:

```text
Resolution before Contradiction
Episode append after close
identity collision inconsistent with history
foreign Error exception representation
```

opening the database fails as:

```text
StoreCorruption
```

The original semantic exception is chained.

A persisted journal is required to describe a valid history, not merely valid bytes.

---

# 51. Write transaction shape

Every public mutation is one atomic SQLite transaction.

Conceptually:

```text
apply operation to in-process reference
        ↓
produce canonical journal operation
        ↓
BEGIN IMMEDIATE
        ↓
allocate next contiguous sequence
        ↓
insert journal row
        ↓
rebuild/update derived FTS state
        ↓
COMMIT
```

If any durable step fails:

```text
ROLLBACK
reconstruct reference projection from committed journal
rebuild local bookkeeping
re-raise original database failure
```

This restores the in-process object to committed durable truth.

---

# 52. Why reference mutation occurs before journal commit

Applying the operation to `InMemoryStore` first proves:

```text
the operation is semantically legal
```

before durable state is appended.

SQLite never stores an operation that the reference semantics already reject.

The reference is then recoverable from the committed journal if the subsequent durable write fails.

---

# 53. Failed semantic operation

If reference semantics reject before durable mutation:

```text
IdentityCollision
UnsupportedMemoryRecord
UnsupportedPersistedValue
ValueError
KeyError
TypeError
```

as already defined by Pass 2:

```text
no SQLite journal row is written
no FTS change occurs
```

The original semantic exception propagates unchanged.

---

# 54. Failed SQLite operation

If semantic validation succeeds but SQLite fails:

```text
rollback transaction
reload reference from committed journal
```

Then propagate the SQLite failure.

The backend does not misreport a disk/locking/I/O failure as semantic forgetting or identity collision.

---

# 55. No retry/backoff policy

Concurrent/multi-process writing remains unsupported in v0.

No automatic retry loop.

No exponential backoff.

No hidden waiting policy is added.

SQLite lock/contention errors may propagate.

---

# 56. SQLite concurrency posture

`SqliteMemoryStore` is:

```text
single-writer
not thread-safe
no multi-process write guarantee
```

The connection uses SQLite normally but Memory makes no stronger concurrency promise.

No locks are added in Python.

No worker thread exists.

---

# 57. No hidden clock

`retrieve()` remains:

```python
retrieve(query, *, retrieved_at)
```

The explicit timestamp is forwarded to the reference implementation.

SQLite never calls:

```text
datetime.now()
time.time()
Clock.now()
```

for Memory semantics.

Database-internal timestamps are not introduced.

---

# 58. No hidden identity allocation

SqliteMemoryStore never creates Core Ids.

Journal sequence numbers are storage positions only.

No UUID generator exists in this module.

---

# 59. Entity bookkeeping for FTS

`SqliteMemoryStore` may maintain private:

```text
known Entity Id set/list
```

derived from replayed operations.

This exists solely so the FTS index can be rebuilt.

It is not exposed.

It does not replace `resolve()`.

For operations containing embedded Entity records:

```text
Inference → conclusion Claim + Inference
Error → cause chain + wrapping Error
```

the bookkeeping must include every Entity that becomes independently resolvable.

---

# 60. Embedded-entity FTS behavior

Persisting an `Inference` whose new conclusion is a searchable `Claim` causes the Claim's text to be indexed under:

```text
Claim.id
```

not under:

```text
Inference.id
```

Persisting an Error cause chain indexes each independently stored Error under its own Id.

This mirrors Pass-2 entity closure.

---

# 61. Query methods after reopen

After replay, these must behave exactly as before closing:

```text
resolve()
retrieve()
claims_for()
conflicts_for()
retention_for()
```

Episode state must likewise survive exactly.

Nothing about process restart changes:

```text
candidate relevance
Context
conflict ordering
retention ordering
identity matching
Episode item order
```

---

# 62. Snapshot behavior survives durability

A record returned by:

```text
SqliteMemoryStore.resolve()
```

must have the same caller-isolation behavior as `InMemoryStore.resolve()`.

In particular:

```text
Episode resolve returns independent snapshot
```

and durable payload mappings are immutable snapshots rather than original caller-owned mappings.

Replay through `InMemoryStore` naturally re-establishes this property.

---

# 63. Matrix M — Episode SQLite transitions

Pass 3 must prove all M cases:

```text
ES-01 create Episode
ES-02 append first item
ES-03 duplicate Ref gets new position
ES-04 member timestamps cannot reorder append order
ES-05 close
ES-06 append after close rejected transactionally
ES-07 double close rejected
ES-08 close before opened_at rejected
ES-09 failed append leaves no durable partial state
ES-10 close/reopen preserves exact Episode
ES-11 corrupted Episode transition history fails loudly
ES-12 snapshot persist is not an Episode mutation mechanism
```

For ES-11, corrupting or reordering Episode operation rows must make replay fail rather than silently reconstruct another Episode.

---

# 64. Matrix N — contradiction lookup

Pass 2 already proves reference semantics for CL-01–CL-10.

Pass 3 must:

```text
replay equivalent conflict history
produce identical conflicts_for() results
```

and prove CL-11:

> SQLite implementation may optimize storage/replay, but observable conflict results remain semantically identical to InMemoryStore.

---

# 65. Matrix O — retrieval/retention

Run RR-01–RR-12 against `SqliteMemoryStore`.

Results must match `InMemoryStore`.

Especially:

```text
ARCHIVED survives exact resolve
ARCHIVED omitted by default retrieve
archive-inclusive retrieve restores candidacy
DEPRIORITIZED follows ACTIVE
custom accessibility raises
append order beats RetentionMark.at
failed retrieval implies nothing about existence
```

---

# 66. Matrix P — FTS indexing

Prove:

```text
FT-01 Error.message indexed
FT-02 Resolution.rationale absent
FT-03 RetentionMark.rationale absent
FT-04 Observation bare-string value indexed
FT-05 Observation int not indexed as text
FT-06 nested mapping strings not recursively indexed
FT-07 Event bare-string payload indexed
FT-08 bytes not decoded/indexed
FT-09 exotic __str__ never invoked
FT-10 FTS-looking user retrieval text remains literal semantic input
FT-11 idempotent retry creates no duplicate indexed content
FT-12 rejected collision leaves existing FTS unchanged
FT-13 archived item may remain physically indexed
FT-14 reopen/rebuild reproduces same indexable content
```

FTS table contents may be inspected directly in tests using a separate SQLite connection to the test database.

No production inspection API is added merely for testing.

---

# 67. FTS exactness test

For every Entity in a populated store:

```text
multiset of FTS content rows for Entity Id
```

must equal:

```python
lexical_content(resolved_entity)
```

exactly.

This is stronger than spot-checking only individual fields.

---

# 68. Matrix Q — backend equivalence

Create paired stores:

```text
InMemoryStore
SqliteMemoryStore
```

Feed both the exact same operation trace.

After each relevant checkpoint compare:

```text
resolve
claims_for
conflicts_for
retention_for
retrieve
Episode state
exception type on invalid operation
```

The SQLite result must match reference behavior.

---

# 69. Equivalence traces

Backend-equivalence tests should include at least:

```text
ordinary identified records
idempotent retries
identity collisions
Inference with embedded Claim
Error cause chain
Contradiction + multiple Resolution entries
Retention history
Episode create/append/close
archived/deprioritized retrieval
combined identity + lexical retrieval
custom retention Kind error
```

This is stronger than testing isolated operations only.

---

# 70. Matrix U — database integrity

Pass 3 must prove all DB cases:

```text
DB-01 reopen survives exactly
DB-02 interrupted Episode append leaves no partial append
DB-03 identified record + FTS update are atomic
DB-04 RetentionMark operation + derived-index failure are atomic
DB-05 unknown schema version fails loudly
DB-06 corrupted journal payload fails loudly
DB-07 invalid/duplicate/reordered local sequence is detected
DB-08 two readers of committed state reconstruct same semantics
DB-09 unsupported concurrent-write contention is explicit/fails
DB-10 SQL-looking lexical query cannot mutate database
```

---

# 71. Failure injection — no production fault API

Do not add production:

```text
fail_after_step
test_hook
fault_injector
```

merely for tests.

Tests may induce SQLite failure externally.

Examples:

```text
create a trigger that aborts memory_ops insert
temporarily break/drop a derived FTS object
hold a competing transaction lock
modify a test database with a separate sqlite3 connection
```

Production API remains clean.

---

# 72. Transaction-failure reference rollback

Tests must prove not only database rollback but in-process rollback.

Scenario:

```text
SqliteMemoryStore open
operation semantically succeeds in reference
SQLite durable step forced to fail
```

After failure, without reconstructing the Python object externally:

```text
store.resolve(...)
store.retention_for(...)
Episode state
```

must still reflect the last committed journal state.

This proves mirror recovery actually occurred.

---

# 73. Database reopen after failed write

After a forced transaction failure:

```text
close store
repair/remove injected failure condition
reopen
```

The database must reconstruct exactly the pre-failure state.

No operation that failed to commit may appear during replay.

---

# 74. Derived FTS rebuild failure

If FTS rebuilding fails during a write:

```text
journal insertion and FTS mutation roll back together
```

because both occur in one SQLite transaction.

Reference state is then rebuilt from committed journal.

No split-brain:

```text
reference says persisted
journal says not persisted
```

may remain after the call returns with failure.

---

# 75. FTS rebuild on open

After valid journal replay:

```text
rebuild FTS in a transaction
```

If this rebuild cannot complete, opening the backend fails.

It does not return a store with a known-broken index.

Journal state itself remains untouched.

---

# 76. Partial schema handling

Examples that must fail as corruption:

```text
memory_meta exists, memory_ops missing
memory_ops exists, meta missing
wrong required columns
memory_fts replaced with non-FTS table
missing required schema_version key
duplicate schema metadata impossibility / malformed value
```

No "helpful" partial repair of authoritative schema.

Derived FTS contents may be rebuilt, but the FTS schema itself must have the expected shape.

---

# 77. Closed-store behavior

After:

```python
store.close()
```

all MemoryStore methods and Episode mutation methods raise:

```text
SqliteStoreClosed
```

Calling `close()` a second time may be idempotent.

No query is answered solely from the old in-memory mirror after the durable resource is closed.

---

# 78. Import-time side effects

Fresh-process import of:

```text
memory.sqlite_store
```

must not:

```text
open/create a database
open arbitrary files
read wall/monotonic time
allocate UUID
read randomness
connect sockets
spawn process/thread
mutate environment
```

`sqlite3` import itself is allowed.

Actual database access starts only when `SqliteMemoryStore(...)` is constructed.

---

# 79. Dependency graph after Pass 3

Finalize `sqlite_store` dependencies to the actual implementation.

Expected semantic imports:

```text
memory.store
memory.codec
memory.recall
memory.retention

core.identity
core.time
core.context
core.value
core.epistemic
core.observation
core.event
core.effect
core.provenance
core.error
```

plus stdlib implementation dependencies such as:

```text
sqlite3
hashlib
hmac
os
```

If implementation proves one of those Memory/Core imports unnecessary, omit it.

No:

```text
memory.belief
core.state
core.trace
core.transform
```

dependency is justified by Pass 3.

---

# 80. No SQLite import below tier 2

Pass 3 must not alter:

```text
episode.py
recall.py
retention.py
belief.py
codec.py
store.py
```

to import SQLite.

`sqlite3` exists only in:

```text
memory.sqlite_store
```

---

# 81. SqliteMemoryStore satisfies MemoryStore

Explicitly prove:

```python
isinstance(SqliteMemoryStore(path), MemoryStore)
```

while open.

Protocol conformance is structural; `close()` is simply an additional backend-specific operation.

---

# 82. No public journal API

Do not expose:

```text
operations()
journal()
sequence()
raw_sql()
connection
cursor
fts_query()
```

Memory consumers operate through `MemoryStore`.

Tests may inspect test database files externally.

Storage implementation is not promoted into application semantics.

---

# 83. No database path in Memory facts

The SQLite file path is backend configuration.

It does not enter:

```text
Context
Provenance
Claim
Episode
RecallCandidate
```

unless some higher application explicitly chooses to record it separately.

Persistence does not silently change semantic records to mention its own storage mechanism.

---

# 84. No delete surface

Pass 3 does not add:

```text
DELETE record
purge
vacuum-as-semantics
remove memory
```

The backend may internally clear/rebuild derived FTS rows.

It never deletes authoritative journal facts through public Memory API.

---

# 85. No compaction

The journal is not compacted in v0.

No:

```text
checkpoint rewriting
snapshot replacement
operation squashing
history pruning
```

Compaction could erase information about repeated non-Entity facts and historical operation order.

It is deferred until earned and specified.

---

# 86. No migration framework

Schema version 1 exists only so an incompatible database fails recognizably.

Pass 3 does not implement:

```text
v1 → v2 migrations
migration registry
automatic upgrade
downgrade
```

---

# 87. No async database API

No:

```text
aiosqlite
async def
background flush
write queue
```

All operations are synchronous and explicit.

This matches the rest of Core/Memory v0.

---

# 88. No connection pool

One `SqliteMemoryStore` owns one SQLite connection.

No pool.

No hidden additional connections.

Tests that need independent inspection open their own explicit test connection.

---

# 89. SQL parameterization

Every value supplied to SQL uses bound parameters.

Never interpolate:

```text
Id.value
Kind.value
user lexical text
record content
rationale
```

into executable SQL text.

Schema identifiers are fixed constants, not user input.

---

# 90. DB-10 interpretation

Because Memory lexical semantics are evaluated by the reference store, a query such as:

```text
'; DROP TABLE memory_ops; --
```

is merely a string searched within lexical content.

It is never executed as SQL.

The test confirms both:

```text
query returns semantic result/no result normally
database schema/data remain intact
```

---

# 91. Read consistency

While a `SqliteMemoryStore` instance is open, all query methods read its in-process projection of the last successfully committed journal.

External mutation of the database behind the store's back is unsupported.

The backend does not poll SQLite before every query.

Close/reopen is the explicit boundary that revalidates external durable state.

---

# 92. Second store instance

Two independently opened stores against the same completed, non-mutating database should reconstruct semantically identical state.

Concurrent mutation through both is unsupported.

DB-08 therefore tests:

```text
committed read equivalence
```

not multi-writer synchronization.

---

# 93. Resource cleanup

`close()` closes the SQLite connection.

No temporary worker/process remains.

Tests using filesystem databases remove them only after closing all connections.

No production atexit handler is installed.

---

# 94. SQLite-specific exception boundary

Semantic failures remain semantic failures:

```text
IdentityCollision
UnsupportedMemoryRecord
UnsupportedPersistedValue
ValueError / KeyError / TypeError
```

as already specified by MemoryStore operations.

Durability failures remain SQLite/runtime failures.

Corrupt existing durable state becomes:

```text
StoreCorruption
UnsupportedSchemaVersion
```

Closed-resource misuse becomes:

```text
SqliteStoreClosed
```

Do not collapse all failures into one generic "database error."

---

# 95. Journal decode strictness

Every decoded operation/record validates:

```text
expected tag
expected field set
expected field type
expected tuple/list cardinality
nested record type
required fields
absence of unknown v1 fields
```

before constructing Core objects.

Malformed external bytes must fail with controlled decode errors that opening wraps as `StoreCorruption`.

No incidental:

```text
IndexError
KeyError
TypeError
```

from malformed journal payload should leak as the top-level reopen failure.

---

# 96. Journal replay never trusts Python typing

The database is external/untrusted runtime input.

Static type annotations do not justify unchecked casts while decoding.

Every external node is checked before indexing/unpacking/construction.

This carries forward the lesson learned during Pass 1 codec review.

---

# 97. Journal record round-trip tests

For each `PersistRecord` concrete type:

```text
record
→ private durable encoding
→ private decode
→ reconstructed record
→ InMemoryStore.persist
```

must preserve observable Memory behavior.

Do not test only byte decoding in isolation.

The important property is semantic replay.

---

# 98. Special Event regression

Persist:

```text
Event(id=X, payload="A")
```

then durably encode/reopen.

Attempt:

```text
Event(id=X, payload="B")
```

must still raise:

```text
IdentityCollision
```

after reopen.

This proves durable reconstruction did not accidentally collapse Event to Core's Id-only equality.

---

# 99. Special embedded-identity regression

Persist/reopen:

```text
Inference(id=I, conclusion=Claim(id=C, ...))
```

and:

```text
Error(id=E2, cause=Error(id=E1, ...))
```

Then verify collisions involving:

```text
C
E1
E2
```

behave exactly as before restart.

This specifically protects the defects caught in Pass 2's final whole-branch review.

---

# 100. Operation replay idempotency

A journal may contain repeated successful calls such as:

```text
persist(same Entity)
create_episode(same header)
```

Replay must accept those operations exactly as the live store did.

Do not "optimize" them out of journal serialization unless such compaction is separately specified.

The durable log records successful operations, not only state deltas.

---

# 101. Non-Entity duplicate replay

Two identical persisted:

```text
RetentionMark
```

operations remain two marks after reopen.

Two identical valid:

```text
Resolution
```

operations remain two Resolution entries after reopen.

Journal replay must never apply Entity-style idempotency to non-Entity records.

---

# 102. FTS and archived records

Archiving an Entity does not remove its FTS row.

FTS indexes textual content.

Retention governs accessibility.

Therefore after:

```text
Entity persisted
RetentionMark(ARCHIVED) persisted
```

direct inspection may still find its FTS content.

Default Memory retrieval excludes it through retention semantics.

This keeps indexing and accessibility distinct.

---

# 103. FTS and reactivation

After:

```text
ARCHIVED
then ACTIVE
```

no text needs to be reinserted.

The Entity's FTS content existed throughout.

Only retention projection changed.

---

# 104. FTS and collision rejection

If:

```text
existing Entity X
incoming different Entity representation with Id X
```

raises `IdentityCollision`:

```text
journal unchanged
FTS unchanged
reference unchanged
```

Prove all three.

---

# 105. FTS and failed transaction

Any FTS failure inside a mutation transaction causes:

```text
no committed operation row
no partial FTS change
reference reloaded to prior committed state
```

The FTS index does not get to be "eventually consistent" in v0.

No background repair task exists.

---

# 106. Database corruption tests

At minimum directly tamper separate test databases to prove detection of:

```text
payload byte modification
digest modification
op_kind modification
sequence gap/rewrite
malformed payload that still passes SQLite typing
unsupported schema version
partial required schema
```

Do not corrupt one shared fixture repeatedly in a way that obscures which guard caught which case.

---

# 107. SQLite file corruption

Where practical, a test may corrupt raw database bytes or create a structurally malformed SQLite database and prove the constructor fails loudly.

This is supplementary to journal-level corruption tests.

Do not make the suite platform-fragile merely to force low-level file corruption if `PRAGMA integrity_check` coverage plus deterministic row tampering already proves the intended boundary.

---

# 108. FTS5 availability

If the runtime SQLite library lacks FTS5, constructing `SqliteMemoryStore` fails loudly with a clear runtime error.

Pass 3 does not silently disable indexing and claim success.

The test environment used to close Pass 3 must demonstrate FTS5 is actually available.

---

# 109. Import graph tests deferred, but obeyed now

Pass 4 formally closes the architecture graph.

Pass 3 code must already obey the final intended graph.

Do not knowingly introduce temporary dependency violations with a plan to "clean them up in Pass 4."

---

# 110. Required test files

Recommended:

```text
tests/memory/semantics/test_sqlite_store.py
```

If corruption/transaction tests make that file unwieldy, split by actual responsibility:

```text
test_sqlite_store.py
test_sqlite_integrity.py
```

Do not fragment tests merely for size aesthetics.

---

# 111. Backend-equivalence test helper

A test-only helper may execute the same operation trace against:

```text
InMemoryStore
SqliteMemoryStore
```

and compare outcomes.

This helper belongs in tests.

Do not create production "dual store" machinery.

---

# 112. Baseline quality gate

Before Pass-3 implementation starts:

```text
654 existing tests green
Ruff green
Pyright strict green
working tree clean
```

The exact test count may rise if documentation/test corrections land before implementation; the important property is a clean baseline.

---

# 113. Pass-3 closing quality gate

Before Pass 3 closes:

```text
all prior tests green
all new SQLite tests green
no skipped/xfail M/N/O/P/Q/U requirement

Ruff green
Pyright strict green

Core production files unchanged
Pass-1 semantic modules unchanged unless a real counterexample requires reopening
Pass-2 store semantics unchanged unless a real counterexample requires reopening
```

Any required semantic change pauses the pass for review.

Do not silently modify `InMemoryStore` merely to make SQLite easier.

---

# 114. Documentation updates before implementation

Create:

```text
docs/memory-passes/03-sqlite-backend.md
```

from this preregistration.

Update `MEMORY_ARCHITECTURE.md` narrowly to freeze:

```text
append-only SQLite operation-journal architecture
schema v1
SqliteMemoryStore lifecycle
corruption exception shapes
final sqlite_store dependency row
FTS5 as rebuildable derived index
reference-delegated query semantics
```

Update `MEMORY_ADVERSARIAL_MATRIX.md` section Y:

```text
store corruption/decode exception shapes
```

are no longer open after Pass-3 preregistration.

No ontology changes belong in `MEMORY_SPECIFICATION.md` unless implementation discovers a genuine theory problem.

---

# 115. Implementation-task decomposition

A sensible implementation plan should separate at least:

```text
1. SQLite schema/lifecycle/error types
2. private journal operation + record codec
3. replay and corruption validation
4. transactional mutation methods
5. query delegation + protocol conformance
6. FTS5 rebuild/index verification
7. backend-equivalence + crash/corruption closing gate
```

Exact task count is a writing-plans concern, not frozen architecture.

---

# 116. Explicitly forbidden in Pass 3

Do not introduce:

```text
new Memory ontology constructions
Core changes
SQLite semantics replacing MemoryStore semantics
query-language syntax
vector retrieval
Consolidation
LLM/model calls
agent behavior
automatic contradiction detection
resolution adjudication
temporal carry-forward
database migrations
journal compaction
public raw-SQL API
public journal API
delete/purge
async database access
connection pooling
thread synchronization
background flushing
retry/backoff framework
hidden clocks
hidden Id allocation
pickle
repr-based serialization
generic Python-object codecs
```

---

# 117. Success condition

Pass 3 succeeds when this statement is true:

> A `SqliteMemoryStore` can be closed, reopened, and queried after process-boundary-equivalent reconstruction, and every MemoryStore-visible result is the same result the frozen `InMemoryStore` semantics would produce from the same successful operation history.

Additionally:

> The durable journal detects malformed/corrupted authoritative state instead of silently inventing another history, and FTS5 can be discarded and rebuilt entirely from that authoritative state without changing Memory semantics.

At that point the architecture becomes:

```text
Core v0
   ↓
Memory semantics
   ↓
MemoryStore
   ├── InMemoryStore      ← semantic reference
   └── SqliteMemoryStore  ← durable replay-backed implementation
```

Pass 4 can then ask the final question:

> Did the whole Memory system remain architecturally legible after becoming durable?

The important shift here is that Pass 3 is no longer "design a database version of Memory." It is "make the already-finished MemoryStore operation history survive disk and restart." That is a much tighter problem, and it gives the SQLite backend almost no room to accidentally redefine the theory.
