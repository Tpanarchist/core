# Memory v0 — Pass 1 Preregistration

## Status

**Pass:** 1 of 4
**Name:** Semantic Constructions
**Question answered:** What is Memory, before persistence exists?

This pass implements only the pure semantic layer established by:

- `MEMORY_SPECIFICATION.md`
- `MEMORY_LAWS.md`
- `MEMORY_ARCHITECTURE.md`
- `MEMORY_ADVERSARIAL_MATRIX.md`

Core v0 remains unchanged.

No persistence protocol, SQLite connection, filesystem interaction, FTS index, vector store, model call, clock read, UUID allocation, or hidden nondeterminism is introduced in this pass.

The target modules are:

```text
src/memory/
├── __init__.py
├── py.typed
├── episode.py
├── recall.py
├── retention.py
├── belief.py
└── codec.py
```

Pass 1 must prove adversarial-matrix sections **A–J**.

---

# 1. Pre-code documentation corrections

These are runtime-signature corrections discovered while deriving the implementation. They do not change Memory's ontology or laws. Both are already applied to `MEMORY_SPECIFICATION.md`/`MEMORY_ARCHITECTURE.md`.

## 1.1 Episode construction receives identity explicitly

`Episode` is Entity-bearing, therefore construction must require:

```python
Episode(
    *,
    id: Id,
    subject: Id | Ref,
    context: Context,
    opened_at: WallInstant,
)
```

Memory must never allocate the Episode's `Id` internally.

The shorthand in `MEMORY_SPECIFICATION.md` saying:

```text
construct(subject, context, opened_at)
```

is corrected to include `id`.

No `IdSource` dependency is introduced into `episode.py`.

## 1.2 `belief.py` depends directly on `core.result`

`belief_state()` determines Context compatibility using:

```python
claim.context.merge(query_context)
```

`Context.merge()` returns Core `Result`.

Therefore `belief.py` needs an explicit dependency on `core.result`; it must not inspect the result by duck typing or indirect imports.

The frozen Tier-0 dependency row becomes:

```text
belief
    → core.identity
    → core.context
    → core.value
    → core.epistemic
    → core.result
```

This is an architecture correction only.

---

# 2. Package-level constraints

`memory.__init__` remains essentially empty.

`py.typed` is present.

Tier-0 Memory modules do not import each other.

The Pass-1 graph is:

```text
episode
    → core.identity
    → core.time
    → core.context

recall
    → core.identity
    → core.time
    → core.context
    → core.value

retention
    → core.identity
    → core.time
    → core.value

belief
    → core.identity
    → core.context
    → core.value
    → core.epistemic
    → core.result

codec
    → core.value
    → core.identity
    → core.time
    → core.context
```

Stdlib imports are permitted where required.

No Pass-1 module imports:

```text
memory.store
memory.sqlite_store
sqlite3
core.event
core.observation
core.effect
core.provenance
core.error
core.trace
```

unless a later preregistration explicitly changes the frozen graph.

---

# 3. `episode.py`

## 3.1 Runtime shape

```python
class Episode:
    def __init__(
        self,
        *,
        id: Id,
        subject: Id | Ref,
        context: Context,
        opened_at: WallInstant,
    ) -> None: ...

    @property
    def id(self) -> Id: ...

    @property
    def subject(self) -> Id | Ref: ...

    @property
    def context(self) -> Context: ...

    @property
    def opened_at(self) -> WallInstant: ...

    @property
    def closed_at(self) -> WallInstant | None: ...

    def append(self, ref: Ref) -> Ref: ...

    def items(self) -> tuple[Ref, ...]: ...

    def close(self, at: WallInstant) -> WallInstant: ...
```

Internally:

```text
_id
_subject
_context
_opened_at
_closed_at
_items: list[Ref]
```

No caller supplies the internal list.

## 3.2 Semantics

`Episode` is Entity-bearing through its read-only `id`.

`subject`, `context`, and `opened_at` never change.

Items are admitted only by `append()`.

Append order is authoritative.

`Ref` duplicates are preserved.

Namespaces are preserved exactly.

Nested Episode Refs and self-Refs are opaque and valid.

`items()` returns a new tuple snapshot.

`close(at)` requires:

```text
not already closed
at >= opened_at
```

After closure:

```text
append() → ValueError
close()  → ValueError
```

Equal open/close instants are permitted.

Neither member wall times nor `context.as_of` determine Episode order.

No invariant is introduced between:

```text
Episode.opened_at
Episode.context.as_of
member timestamps
```

because the frozen specification does not require one.

## 3.3 Return values

For consistency with Core's append-only containers:

```text
append(ref) → the admitted Ref
close(at)   → the accepted closing WallInstant
```

These returns introduce no new semantics.

---

# 4. `recall.py`

## 4.1 Well-known relevance Kinds

The module may provide these initial constants:

```python
IDENTITY_MATCH   = Kind("memory.relevance.identity")
LEXICAL_MATCH    = Kind("memory.relevance.lexical")
CONTEXTUAL_MATCH = Kind("memory.relevance.contextual")
```

They are well-known values, not a closed relevance vocabulary.

Any valid `Kind` may appear as relevance evidence.

## 4.2 `RecallCandidate`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RecallCandidate:
    item: Ref
    query_context: Context
    relevance: tuple[Kind, ...]
    retrieved_at: WallInstant
```

Construction must defensively normalize `relevance` to a tuple.

Required invariants:

```text
len(relevance) >= 1
no duplicate Kind values
```

Order is preserved exactly but carries no ranking meaning.

`RecallCandidate` is not Entity-bearing.

It has no score.

It has no truth status.

It has no persistence identity.

## 4.3 `WorkingSet`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingSet:
    capacity: int
    admitted: tuple[RecallCandidate, ...]
```

Required invariants:

```text
capacity >= 0
len(admitted) <= capacity
```

`admitted` is defensively tuple-normalized.

## 4.4 Admission

```python
def admit(
    candidates: tuple[RecallCandidate, ...],
    capacity: int,
) -> tuple[WorkingSet, tuple[RecallCandidate, ...]]:
    ...
```

Behavior is exactly:

```text
capacity < 0
    → ValueError

otherwise
    admitted = candidates[:capacity]
    excluded = candidates[capacity:]
```

No deduplication.

No relevance interpretation.

No scoring.

No reranking.

No retention lookup.

No belief projection.

No mutation of input candidates.

---

# 5. `retention.py`

## 5.1 Well-known accessibility Kinds

```python
ACTIVE         = Kind("memory.retention.active")
DEPRIORITIZED  = Kind("memory.retention.deprioritized")
ARCHIVED       = Kind("memory.retention.archived")
```

These are not a closed universe.

Custom accessibility Kinds remain valid and are preserved.

## 5.2 `RetentionMark`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RetentionMark:
    item: Ref
    accessibility: Kind
    at: WallInstant
    rationale: str | None = None
```

Empty-string rationale is not silently converted to `None`.

No additional semantic rule is introduced unless the frozen specification already requires one.

## 5.3 `RetentionLog`

```python
class RetentionLog:
    def record(self, mark: RetentionMark) -> RetentionMark: ...

    def current(self, item: Id | Ref) -> Kind: ...

    def history(
        self,
        item: Id | Ref,
    ) -> tuple[RetentionMark, ...]: ...
```

Internally it is an append-only list.

`record()` always appends.

Duplicate marks are valid.

`history()` matches through:

```python
identity_of(mark.item) == identity_of(item)
```

and preserves global append order among matching marks.

`current()` returns:

```text
last matching mark's accessibility
```

or:

```text
ACTIVE
```

when no mark exists.

It never sorts by `RetentionMark.at`.

A namespaced Ref cannot bypass a mark made through another namespace.

## 5.4 What Pass 1 does not yet implement

Pass 1 does **not** implement generic retrieval accessibility policy.

The frozen meaning remains:

```text
ACTIVE
    normal default retrieval

DEPRIORITIZED
    eligible after ACTIVE results

ARCHIVED
    excluded unless archive-inclusive retrieval was requested

custom Kind
    default retrieval must fail explicitly rather than guess
```

Application of that policy belongs to the Pass-2 retrieval operation, not to `RetentionLog.current()` itself.

---

# 6. `belief.py`

## 6.1 Status Kinds

The projection status vocabulary is closed to:

```python
DETERMINED               = Kind("memory.belief.determined")
AMBIGUOUS                = Kind("memory.belief.ambiguous")
UNRESOLVED_CONFLICT      = Kind("memory.belief.unresolved_conflict")
RESOLVED_OPAQUE_CONFLICT = Kind("memory.belief.resolved_opaque_conflict")
UNKNOWN                   = Kind("memory.belief.unknown")
```

These are result states of one specific operation, not an extensible domain vocabulary.

## 6.2 `BeliefProjection`

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class BeliefProjection:
    subject: Id | Ref
    predicate: Kind
    query_context: Context
    status: Kind
    candidates: tuple[Claim[object], ...]
    conflict_entries: tuple[Contradiction | Resolution, ...]
```

`candidates` and `conflict_entries` are defensively tuple-normalized.

The result preserves `query_context` because that Context materially determines the projection.

The result does not carry a selected `.value`; the determining Claim itself is retained.

## 6.3 Operation

```python
def belief_state(
    *,
    subject: Id | Ref,
    predicate: Kind,
    query_context: Context,
    claims: tuple[Claim[object], ...],
    conflict_entries: tuple[Contradiction | Resolution, ...],
) -> BeliefProjection:
    ...
```

No clock.

No store.

No retrieval query.

No wall-time read.

No mutation.

## 6.4 Duplicate Claim policy — frozen for Pass 1

BP-13 is resolved as follows.

Claims are normalized by semantic `Claim.id`.

For repeated occurrences of the same Id:

```text
same Id + structurally identical Claim
    → one logical Claim; retain first occurrence position

same Id + structurally different Claim
    → ValueError
```

This prevents accidental repeated input from manufacturing ambiguity while refusing to silently discard two incompatible representations of one identity.

No "latest duplicate wins."

## 6.5 Slot matching

A Claim belongs to the requested slot iff:

```python
identity_of(claim.subject) == identity_of(subject)
and claim.predicate == predicate
```

All supplied claims are considered for slot membership before query-Context compatibility is applied.

## 6.6 Context compatibility

For every slot Claim:

```python
claim.context.merge(query_context)
```

is evaluated.

A Claim is compatible iff merge returns Core `Ok`.

A Core `Err(ContextConflict)` excludes the Claim from the candidate set.

The merged Context itself does not replace either original Context.

`Claim.at` is never consulted for selection or precedence.

No temporal carry-forward occurs.

## 6.7 Conflict-entry validity

`conflict_entries` must obey Core `ContradictionLog` ordering semantics.

Rather than reimplement those validation rules, `belief_state()` may instantiate a temporary Core `ContradictionLog` and feed the supplied entries through `.record()` in order.

Therefore malformed input such as:

```text
Resolution before its Contradiction
duplicate Contradiction Id
```

fails exactly through Core's existing log semantics.

The temporary log is validation/projection machinery only; it is not persisted.

## 6.8 Relevant contradiction

Build the set of Ids for all supplied Claims that belong to the requested `(subject, predicate)` slot **before** Context compatibility filtering.

A Contradiction is relevant iff:

```text
identity_of(contradiction.subject) == identity_of(subject)

AND

at least one contradiction.statement.id belongs
to the requested slot Claim-id set
```

This preserves cross-predicate conflicts: only one statement needs to identify a Claim in the requested slot.

A Resolution is relevant iff it references a relevant Contradiction.

`conflict_entries` in the result preserve original supplied/log order.

## 6.9 Status algorithm

After claim normalization, slot filtering, Context filtering, and relevant-conflict projection:

```text
if any relevant Contradiction remains unresolved:
    UNRESOLVED_CONFLICT

elif any relevant Contradiction exists:
    RESOLVED_OPAQUE_CONFLICT

elif len(compatible_candidates) >= 2:
    AMBIGUOUS

elif len(compatible_candidates) == 1:
    DETERMINED

else:
    UNKNOWN
```

No rationale parsing.

No authority judgment.

No timestamp tiebreak.

No retrieval-rank input.

No automatic Contradiction construction.

## 6.10 `BeliefProjection` constructor invariants

Direct construction must reject obviously impossible result shapes.

At minimum:

```text
status must be one of the five status Kinds

DETERMINED
    exactly 1 candidate
    no conflict entries

AMBIGUOUS
    at least 2 candidates
    no conflict entries

UNKNOWN
    0 candidates
    no conflict entries

UNRESOLVED_CONFLICT
    at least 1 Contradiction in conflict_entries

RESOLVED_OPAQUE_CONFLICT
    at least 1 Contradiction in conflict_entries
```

The full resolved/unresolved state is computed by `belief_state()`.

Direct construction is not a second adjudication engine.

---

# 7. `codec.py`

## 7.1 Durable value domain

```python
type PersistedValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | tuple[PersistedValue, ...]
    | Mapping[str, PersistedValue]
)
```

The runtime representation of a validated mapping is immutable.

The preferred concrete representation is:

```python
MappingProxyType
```

over a recursively snapshotted dictionary.

Tuple remains tuple.

Lists are never silently converted to tuple.

## 7.2 Supported arbitrary domain values

`as_persisted_value()` accepts only:

```text
None
bool
int
finite float
str
bytes
tuple of PersistedValue
Mapping[str, PersistedValue]
```

Validation order must distinguish:

```text
bool
```

from:

```text
int
```

because `bool` is a Python subclass of `int`.

## 7.3 Validation operation

Runtime signature:

```python
def as_persisted_value(
    value: object,
    *,
    path: tuple[str | int, ...] = (),
) -> PersistedValue:
    ...
```

The optional `path` exists so later persistence code can report:

```text
observation.value[...]
context.metadata[...]
```

without inventing a separate codec mechanism.

The function returns a defensive recursive immutable snapshot.

It does not return live mutable caller mappings.

## 7.4 Unsupported-value failure

```python
class UnsupportedPersistedValue(ValueError):
    path: tuple[str | int, ...]
    value: object
    reason: str
```

This is supporting runtime machinery, not a Memory ontology concept.

It is used for at least:

```text
unsupported object type
list
non-string mapping key
non-finite float
cyclic container
```

Failure must identify the exact nested path.

No call to arbitrary payload `repr()` or `str()` is required to determine persistability.

## 7.5 Integer semantics

Every Python `int` is part of `PersistedValue`.

There is no 64-bit semantic ceiling.

Canonical encoding must round-trip arbitrary-precision positive and negative integers exactly.

SQLite's INTEGER width is irrelevant to Memory semantics.

## 7.6 Float semantics

Only finite floats are admitted.

```text
nan
+inf
-inf
```

are rejected.

Finite IEEE-754 values round-trip exactly.

`-0.0` remains distinguishable from `+0.0` at the representation level.

Canonical encoding must preserve the sign bit.

## 7.7 Unicode semantics

Strings are preserved exactly.

No Unicode normalization is applied.

Two canonically equivalent Unicode spellings remain distinct byte sequences unless they were already identical inputs.

## 7.8 Canonical persisted-value byte encoding

Pass 1 owns deterministic encoding because the adversarial matrix already requires stable encode/decode behavior before SQLite exists.

Public operations:

```python
def encode_persisted_value(value: PersistedValue) -> bytes: ...

def decode_persisted_value(data: bytes) -> PersistedValue: ...
```

The wire representation is a versioned, tagged canonical tree.

Version:

```text
memory.persisted_value / 1
```

Each scalar/container is tagged explicitly so type identity is preserved.

Conceptual node representation:

```text
None       → ["none"]
bool       → ["bool", true|false]
int        → ["int", decimal-string]
float      → ["float", hexadecimal-float-string]
str        → ["str", exact-string]
bytes      → ["bytes", canonical-base64]
tuple      → ["tuple", [node, node, ...]]
mapping    → ["map", [[key, node], [key, node], ...]]
```

Mapping pairs are encoded in lexicographic key order regardless of original iteration order.

Float encoding uses an exact hexadecimal representation sufficient for round-trip through `float.fromhex()`, including `-0.0`.

The tagged tree is serialized as deterministic UTF-8 JSON with:

```text
no insignificant whitespace
non-ASCII preserved deterministically
NaN disabled
```

The outer envelope carries the codec version.

Unknown version or tag fails loudly.

This encoding is an implementation contract of Memory v0, not a new ontology concept.

## 7.9 Core primitive codecs

Pass 1 supplies deterministic encode/decode operations for:

```text
Kind
Id
Namespace
Ref
WallInstant
Duration
Context
```

Their representations are explicitly tagged and versioned through the same canonical codec machinery.

Required properties:

```text
encode → decode preserves semantic equality

encode → decode → encode
produces byte-identical canonical encoding
```

### Kind

Preserve `Kind.value` exactly.

Decoding reconstructs through `Kind(...)`, therefore Core validation remains authoritative.

### Id

Preserve:

```text
kind
value
```

exactly.

### Namespace

Preserve ordered segment tuple exactly.

### Ref

Preserve:

```text
Id
Namespace | None
```

including namespace distinction.

### WallInstant

Persist the canonical UTC instant exactly to Python datetime's supported precision.

Decode reconstructs through `WallInstant`, leaving Core responsible for UTC canonicalization.

### Duration

Persist `nanoseconds` as arbitrary-precision integer.

### Context

Persist:

```text
as_of
namespace
scope
environment
source
authority
version
units
metadata
```

`as_of` and `namespace` use their explicit Core codecs.

Every object-typed optional field crosses through `as_persisted_value()`.

Metadata values cross through `as_persisted_value()`.

Therefore:

```python
Context(scope=Id(...))
```

is not durable in v0 merely because Memory knows how to encode `Id` when `Id` occupies an explicitly typed Core field.

Opaque domain payloads do not acquire automatic Core-object conversion.

That distinction is intentional.

## 7.10 Malformed decode behavior

Pass 1 guarantees malformed/unknown codec input fails loudly with `ValueError` or a codec-specific `ValueError` subclass.

Pass 3 remains responsible for deciding how a durable backend wraps or classifies corrupted stored bytes.

The storage-corruption exception hierarchy is therefore still legitimately deferred to Pass 3.

---

# 8. Pass-1 adversarial coverage

Pass 1 must implement every case from matrix sections:

```text
A — Episode
B — RecallCandidate
C — WorkingSet
D — Retention
E — BeliefProjection ordinary
F — BeliefProjection Context/time
G — BeliefProjection contradiction
H — PersistedValue domain
I — float handling
J — Core primitive codecs
```

No matrix case in A–J may remain `xfail`, skipped, or TODO at pass completion.

Recommended test files:

```text
tests/memory/semantics/
├── test_episode.py
├── test_recall.py
├── test_retention.py
├── test_belief.py
└── test_codec.py
```

Tests should name matrix identifiers where practical:

```python
def test_ep_04_insertion_order_beats_wall_time(): ...
def test_bp_08_recency_does_not_break_ambiguity(): ...
def test_cf_03_resolution_rationale_does_not_select_winner(): ...
def test_cd_15_cycles_fail_explicitly(): ...
def test_fl_02_negative_zero_sign_round_trips(): ...
```

This keeps the audit path mechanical.

---

# 9. Additional Pass-1 regression tests

Beyond matrix A–J, explicitly prove:

```text
Episode structurally satisfies Core Entity.

Episode construction requires caller-supplied Id.

Constructing/importing Memory does not allocate identity.

No Pass-1 operation reads a clock.

No Pass-1 operation uses random/UUID state.

BeliefProjection preserves query_context.

Duplicate identical Claim Ids cannot fabricate AMBIGUOUS.

Different Claim records sharing one Id fail loudly.

Context compatibility is determined by Core Context.merge(), not reimplemented.

belief_state() never inspects Claim.at.

belief_state() never parses Resolution.rationale.

Codec never calls str()/repr() as a persistence fallback.

Codec arbitrary ints survive beyond signed 64-bit bounds.

Canonical mappings encode identically regardless of input iteration order.

Negative zero survives byte round-trip with its sign.

Context object-valued Id is rejected while explicitly typed Context.namespace is encoded correctly.
```

---

# 10. Explicitly forbidden in Pass 1

Do not introduce:

```text
MemoryStore
InMemoryStore
SqliteMemoryStore
sqlite3
schema DDL
FTS5
RetrievalQuery
Episode durability methods
filesystem storage
vector similarity
embeddings
Consolidation
LLM calls
agent logic
automatic contradiction detection
structured resolution outcomes
temporal carry-forward
source-authority ranking
general codec registry
codec plugins
thread locks
async APIs
background work
```

Do not change Core.

Do not create another abstraction merely because Pass 2 may eventually want it.

---

# 11. Quality gates

Before Pass 1 can close:

```text
all existing Core tests green
all Memory Pass-1 tests green
pytest green
Ruff green
Pyright strict green
no Core production file changed
no skipped/xfail Pass-1 adversarial case
```

The pass should also perform a direct manual check that each A–J matrix case has a test home.

Architecture import closure is formally enforced in Pass 4, but Pass 1 code should already obey the preregistered dependency graph.

---

# 12. Commit boundary

Pass 1 should be one coherent implementation checkpoint.

Its production-code surface is limited to:

```text
src/memory/__init__.py
src/memory/py.typed
src/memory/episode.py
src/memory/recall.py
src/memory/retention.py
src/memory/belief.py
src/memory/codec.py
```

plus Pass-1 tests and the two small preregistration-derived document corrections:

```text
Episode construction explicitly requires id

belief dependency row explicitly includes core.result
```

No Pass-2 code belongs in the commit.

---

# 13. Pass-1 success condition

At completion, Memory must be able to demonstrate, entirely in-process and without any store:

```text
group Core facts into an ordered Episode

surface a candidate without confusing relevance with truth

bound active attention without silently discarding overflow

change accessibility through append-only history

project a mechanically-determinable belief

surface ambiguity and conflict without adjudicating either

represent and canonically encode the exact closed durable-value domain
```

Nothing durable has been stored yet.

Nothing has been retrieved from a database yet.

Nothing has been judged by an agent yet.

Pass 1 proves only that the semantic machinery is coherent and executable.

If implementation pressure appears to require a new Memory concept, new Core concept, hidden policy, or modification to Core, the pass stops and the theory is reopened rather than smuggling the change into code.
