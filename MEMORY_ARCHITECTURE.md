# Memory — v0 Architecture

This document derives the Python package from `MEMORY_SPECIFICATION.md`. Signatures in that document specify semantic inputs/outputs; this document defines the authoritative Python runtime realization, exactly mirroring the relationship between Core's `SPECIFICATION.md` and `ARCHITECTURE.md`. Where exact API decisions aren't fixed by either document, they're pinned at the start of the pass that needs them — Core's own "pass preregistration, not another architecture review" discipline.

## Central rule

Memory persists and retrieves Core facts and Memory's own derived records; it adds no new ontology primitives, only grouping, selection, retention, and mechanical projection over Core's existing vocabulary. `memory.*` depends on `core.*`, never the reverse. Core v0 is closed — nothing here modifies it.

## Package shape

```text
E:\core\
├── MEMORY_SPECIFICATION.md
├── MEMORY_LAWS.md
├── MEMORY_ARCHITECTURE.md
├── MEMORY_ADVERSARIAL_MATRIX.md
│
├── src\
│   ├── core\               # closed, v0
│   └── memory\
│       ├── __init__.py     # stays essentially empty
│       ├── py.typed
│       │
│       ├── episode.py
│       ├── recall.py
│       ├── retention.py
│       ├── belief.py
│       ├── codec.py
│       │
│       ├── store.py         # MemoryStore (protocol) + InMemoryStore
│       └── sqlite_store.py  # SqliteMemoryStore
│
└── tests\
    ├── semantics\           # existing, Core
    ├── architecture\        # existing, Core
    └── memory\
        ├── semantics\
        └── architecture\
```

## Dependency graph (frozen before implementation)

Every edge is a real, named import — no `core.*` shorthand.

| Tier | Module | Owns | Depends on |
|---|---|---|---|
| 0 | `episode` | `Episode` | `core.identity`, `core.time`, `core.context` |
| 0 | `recall` | `RecallCandidate`, `WorkingSet` | `core.identity`, `core.time`, `core.context`, `core.value` |
| 0 | `retention` | `RetentionMark`, `RetentionLog` | `core.identity`, `core.time`, `core.value` |
| 0 | `belief` | `BeliefProjection`, `belief_state()` | `core.identity`, `core.context`, `core.value`, `core.epistemic`, `core.result` |
| 0 | `codec` | `PersistedValue`, `as_persisted_value()`, Core-primitive encoders | `core.value`, `core.identity`, `core.time`, `core.context` |
| 1 | `store` | `MemoryStore` (protocol), `InMemoryStore` | `episode`, `recall`, `retention`, `codec`, `core.identity`, `core.time`, `core.context`, `core.value`, `core.epistemic`, `core.observation`, `core.event`, `core.effect`, `core.provenance`, `core.error` |
| 2 | `sqlite_store` | `SqliteMemoryStore`, `StoreCorruption`, `UnsupportedSchemaVersion`, `SqliteStoreClosed` | `store`, `codec`, `recall`, `retention`, `core.identity`, `core.time`, `core.context`, `core.value`, `core.epistemic`, `core.observation`, `core.event`, `core.effect`, `core.provenance`, `core.error`, stdlib `sqlite3`/`hashlib`/`hmac`/`os`/`struct` *(frozen by Pass 3 preregistration, `docs/memory-passes/03-sqlite-backend.md` §79 — if implementation proves one of the Memory/Core edges unnecessary, omit it; no `memory.belief`/`core.state`/`core.trace`/`core.transform` edge is ever justified)* |

Tier-0 modules (`episode`, `recall`, `retention`, `belief`, `codec`) do not depend on each other — each is independently meaningful and independently testable against Core alone, exactly like Core's own tier-4 modules (`event`, `observation`, `relation`, `effect`, `provenance`, `epistemic`).

`store` depends on `codec` (round 5 correction): `InMemoryStore` is the reference implementation for admissibility, payload validation, and canonical-collision comparison, so it needs the same codec `SqliteMemoryStore` uses — otherwise the two backends could disagree exactly where `MEMORY_LAWS.md` law 15 (storage backends do not own semantic policy) forbids it.

**`store` does not depend on `belief`** (Pass 2 correction): `belief_state()` remains a pure function over data a caller already fetched from the store — it is never called *by* the store, and the store never calls it. They are siblings consumed together by a higher orchestration layer outside Memory v0's own scope, not a dependency chain between them.

**Frozen Pass-2 persisted-record union** (`MEMORY_ADVERSARIAL_MATRIX.md` section K, `docs/memory-passes/02-persistence-boundary.md` §2):

```text
type EntityMemoryRecord = (
    Observation[object] | Claim[object] | Inference[object] | Contradiction
    | Event | Effect | Provenance | Error | Episode
)

type NonEntityMemoryRecord = Resolution | RetentionMark

type MemoryRecord = EntityMemoryRecord | NonEntityMemoryRecord

type PersistRecord = (
    Observation[object] | Claim[object] | Inference[object] | Contradiction
    | Resolution | Event | Effect | Provenance | Error | RetentionMark
)
```

`PersistRecord` is what generic `persist()` accepts — every `MemoryRecord` except `Episode`, whose mutable append/close semantics require the dedicated `create_episode`/`append_episode`/`close_episode` surface (see "Episode persistence" below) rather than a single call. No other Core or user type is admitted in Memory v0 — explicitly excluded: `State`, `Transition`, `History`, `Trace`, `TraceEntry`, `Transform`, `Pipeline`, `EffectSpec`, `Relation`, `RelationSet`, `AncestorReport`, `WorkingSet`, `RecallCandidate`, `BeliefProjection`, and any arbitrary user type merely carrying `.id`.

## Persistence boundary

`persist()` does not take `Entity` — persistence eligibility is a Memory-level decision, independent of Core's `Entity` protocol (`Resolution` and `RetentionMark` are admissible despite carrying no `Id`; nothing in Memory promises to persist every `Entity` Core can produce). The exact admissible-record union (`PersistRecord`) is frozen above.

**Persistence is closed over directly embedded admissible Entity records** (Pass 2): two admitted structures embed other identified admissible records directly — `Inference.conclusion → Claim` and `Error.cause → Error | None`. Persisting an `Inference` atomically registers its `conclusion` `Claim` too, participating normally in `resolve()`/`claims_for()`/collision detection/lexical retrieval; persisting an `Error` recursively registers its Core `cause` chain, each independently resolvable by its own `Id`. This is structural (only these two specifically-defined fields), never generic reflection over arbitrary Python objects. `Error.exception` (a foreign `BaseException`) has no Memory v0 codec — an `Error` with `exception is not None` is rejected with `UnsupportedPersistedValue` rather than reduced to a type name, message, `repr()`, or pickle, none of which is the original semantic object.

**Atomicity**: each public mutating store operation validates and canonicalizes its complete operation — including any embedded records — before mutating any state. A `persist(Inference(...))` whose embedded `Claim` is fine but whose `Inference` itself collides leaves neither newly registered. No partial registration; this is the reference behavior Pass 3's SQLite transactions must reproduce.

Two distinct key regimes:

```text
identified records (carry a Core Id)
    semantic primary key = Id (kind, value)

append-only non-Entity records (Resolution, RetentionMark)
    storage ordering key = storage-local sequence
    semantic identity = none — never promoted to Id, never Ref-targetable
```

**Identified-record collision policy**: persisting the same `Id` twice with a canonically identical record is idempotent success; persisting a different record under the same `Id` is an explicit collision error (`IdentityCollision`, a `ValueError` carrying the `Id` and both records' types — never a stringified payload). This makes retry-after-failure safe without permitting mutation-by-upsert. All Entity-bearing records share **one** semantic `Id` namespace — the store does not maintain independent identity universes per concrete Python type, so `Observation(id=X)` followed by `Event(id=X)` is a collision even though they're different types.

**Canonical representation, not Python `==`** (Pass 2): collision comparison uses a private, deterministic canonical `PersistedValue` tree built from every semantically meaningful field of a record, encoded through Pass 1's canonical codec — never the record's own `__eq__`. This matters concretely for `Event`, whose Core equality is Id-only (`Event(id=X, payload="A") == Event(id=X, payload="B")` is `True` in Core). For persistence-collision purposes those two are **not** canonically identical, and the second `persist()` call must raise `IdentityCollision`, not silently succeed. Canonicalization preserves the `Id`/`Ref` distinction, `Ref` namespace, `Known`/`Unknown`, `None`/`False`/`0`, float sign (including `-0.0`), tuple order, mapping-content-independent-of-iteration-order, and every semantic field of `Context` and the record itself. This canonical form is `store.py`'s own private implementation detail, not a new public serialization format.

`UnsupportedMemoryRecord` (a `TypeError` carrying only the offending `type`) is raised when a runtime caller bypasses typing and supplies something outside `PersistRecord` — an arbitrary Core `Entity` not in the union, a plain user object merely carrying `.id`, or an `Episode` passed to generic `persist()` (whose error message directs the caller to `create_episode()` instead).

## Episode persistence

Episode is mutable and append-only in memory, so persistence must preserve exactly three one-way transitions rather than repeatedly replacing a serialized snapshot:

```text
create episode
append item
close episode
```

Guaranteed invariants: stored items may only gain a suffix; `closed_at` moves `None → WallInstant` exactly once; existing items never change or reorder; `closed_at`, once set, never changes again. A normalized `episodes` + `episode_items` representation fits this naturally — exact table shape is pass-level detail.

**Frozen Episode store surface** (Pass 2): generic `persist()` never accepts `Episode`. Its store surface is exactly three methods, plus ordinary `resolve()` to read current state:

```python
def create_episode(self, *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant) -> None: ...
def append_episode(self, episode: Id | Ref, item: Ref) -> None: ...
def close_episode(self, episode: Id | Ref, at: WallInstant) -> None: ...
```

`create_episode()` creates only the header (zero items, open) and enters the Id into the global identity regime: an identical existing header is idempotent success (even if that Episode has since acquired items or been closed — creation identity concerns only the immutable header); a different existing header, or the Id occupied by a non-`Episode`, is `IdentityCollision`. `append_episode()` requires an existing, open Episode (`KeyError` if missing, `TypeError` if the Id names a non-`Episode`, `ValueError` if closed) and never follows the appended `Ref` — self-reference and cycles stay opaque and valid. `close_episode()` requires an existing, open Episode and `at >= opened_at`; a second close, or a close before `opened_at`, is `ValueError`. `resolve(episode.id)` returns a **fresh snapshot** of current state — a caller mutating the returned `Episode` locally never changes the store; only `append_episode()`/`close_episode()` do.

## The codec boundary

Core intentionally permits opaque Python `object` values (`Observation[T].value`, `Claim[T].value`, `Event.payload`, `Effect.target`, `Context`'s optional fields, `metadata`). SQLite cannot losslessly persist arbitrary `object`, and Memory does not solve this with `repr()` or pickling — both would turn an explicit semantic system into opaque storage.

`codec.py` owns:

```text
PersistedValue =
    None | bool | int | float | str | bytes
    | tuple[PersistedValue, ...]
    | Mapping[str, PersistedValue]
```

Two responsibilities, kept distinct:

1. **Deterministic encoders for Core's own primitive representations** — `Id`, `Kind`, `Namespace`, `Ref`, `WallInstant`, `Duration`, `Context` — each has a known, finite shape, so these encode/decode canonically with no registry.
2. **`as_persisted_value(x: object) -> PersistedValue`** — a validator, not a converter, for arbitrary domain payloads. Recursively checks conformance; raises `UnsupportedPersistedValue`, naming the exact offending path, for anything outside the domain. No registered-codec/plugin mechanism in v0 — unearned until a real payload type needs one.

**Frozen codec policy** (from the adversarial matrix):
- Floats must be finite — `inf`/`-inf`/`nan` rejected.
- Float sign is preserved exactly, never canonicalized (`-0.0` round-trips as `-0.0`) — IEEE754 encoding preserves this for free; canonicalizing would require adding special-case logic to throw information away.
- `int` is arbitrary-precision and round-trips exactly (round 5 correction): `PersistedValue` claims to be the *entire* durable-value domain, and Python `int` is unbounded, so the canonical encoding cannot rely on any backend's native integer width — `SqliteMemoryStore` never stores an `int` directly in a native `INTEGER` column when it might exceed 64-bit range; it uses a canonical width-independent encoding (e.g. a sign plus a byte/text magnitude) for every persisted `int`, not only out-of-range ones, so behavior doesn't change at a boundary.
- `as_persisted_value()` returns a defensive recursive immutable snapshot of validated container data — never a reference to the caller's (possibly still-mutable) input.
- `list` is rejected even where JSON could encode it — `PersistedValue` uses `tuple`, matching Core's own immutability discipline; no structural auto-conversion from dataclasses or other containers.

**Lexical content is a separate concern from persistence, and is restricted to `Ref`-targetable content** (round 5 correction, frozen exactly in Pass 2): only a record that can itself be a `RecallCandidate.item: Ref` target contributes generic recall text — `Resolution.rationale` and `RetentionMark.rationale` are **not** indexed for generic recall, since both records are deliberately non-`Entity`/non-`Ref`-targetable and a lexical hit on either could never be represented as a `RecallCandidate` without inventing a mapping to some other Entity; their text remains fully persisted and directly queryable through `conflicts_for()`/retention APIs instead.

`store.py` owns the frozen extraction rule as shared semantic machinery (not a new Memory construction), `lexical_content(record: EntityMemoryRecord) -> tuple[str, ...]`, called by both `InMemoryStore` and (in Pass 3) `SqliteMemoryStore`'s FTS indexing — one rule, not two independently-maintained ones:

| Record | Contributes |
|---|---|
| `Observation` | `value`/`source`/`observer`, each only when already a bare `str` — no descent into tuples/mappings/`Context` |
| `Claim` | the inner value, only when `value is Known` and it's a bare `str`; `Unknown` contributes nothing |
| `Inference` | nothing — its `conclusion` `Claim` is independently registered and searchable under its own identity |
| `Contradiction` | nothing |
| `Event` | `payload`, only when already a bare `str` |
| `Effect` | `description` always; `target` when already a bare `str`; never `metadata` |
| `Provenance` | `transform_name`, `transform_version` |
| `Error` | `message`; `operation` when not `None`; never `cause`, `exception`, `metadata`, `context` |
| `Episode` | nothing |

Never `str()`/`repr()` on anything else, and no recursive search-document extraction from nested structures in v0.

## Contradiction relevance

Core's `Contradiction` has `subject` and `statements: tuple[Ref, ...]`, but no `predicate` — so `conflicts_for(subject, predicate)` must derive relevance through the referenced `Claim`s:

> A `Contradiction` is relevant to a `(subject, predicate)` slot if its `subject` matches and at least one of its `statement` `Ref`s resolves to a `Claim` belonging to that slot.

This holds even when a Claim in the slot is contested by a statement using a *different* predicate — the slot's claim is still contested, and Memory does not pretend otherwise merely because the other statement used another predicate. SQL only locates the relevant records; `belief_state()` in `belief.py` remains the sole authority for the resulting status.

## Retrieval query and retrieval

Frozen `RetrievalQuery` (Pass 2):

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class RetrievalQuery:
    context: Context
    identity: Id | Ref | None = None
    text: str | None = None
    include_archived: bool = False
```

`identity`/`text` require at least one to be supplied (`identity is not None or text is not None`); a supplied `text` must be non-empty. No result limit, no score threshold, no model-generated relevance field — bounding attention is `WorkingSet`'s job, not retrieval's. `identity` and `text` are an OR query: a record matching either is a candidate; a record matching both appears once, with `relevance = (IDENTITY_MATCH, LEXICAL_MATCH)` — that tuple order is deterministic but encodes no numeric ranking.

The lexical query contract, frozen to make membership backend-independent: **literal, case-sensitive substring matching** over the fields `lexical_content()` extracts (see above). No FTS operators (`*`, `OR`, `NEAR`, quotes, parens, hyphens are ordinary characters), no Unicode normalization, no case folding, no stemming, no token inference. Pass 3's FTS5 may accelerate or rank candidate hits, but must post-validate membership against this same literal rule — SQLite syntax never becomes Memory syntax.

`InMemoryStore`'s reference raw order, before retention is applied: the identity-matched entity (if any) first, then remaining lexical matches in first-persistence order; a record matching both appears once. This is a deterministic reference order, not a ranking contract every future backend must reproduce — Pass 3 may rank lexical matches differently, as long as candidate membership and relevance meaning stay identical.

```text
backend search
      ↓
candidate matches
      ↓
retention accessibility (RetentionLog.current())
      ↓
ordered RecallCandidates
```

`RetentionLog.current()` remains the semantic authority for accessibility, regardless of which backend, or which stage of a query pipeline, physically applies the filter. A backend may optimize this internally later, but no implementation may interpret `ACTIVE`/`DEPRIORITIZED`/`ARCHIVED` differently from another. Frozen default retrieval meaning, and frozen final partition order (Pass 2): `ACTIVE` raw order, then `DEPRIORITIZED` raw order (eligible, always ordered after `ACTIVE`), then — only when `include_archived=True` — `ARCHIVED` raw order. A custom accessibility `Kind` outside these three makes default retrieval fail explicitly (`ValueError`) — it is never silently treated as any of the known three states; the item remains fully queryable through `RetentionLog`'s own API regardless.

## `store.py` owns a real implementer, not just a protocol

Following Core's own `EffectSink`/`MemoryEffectSink` precedent (a Capability-like protocol is introduced only alongside a real implementer — law 19), `store.py` owns both, with the full protocol frozen in Pass 2:

```python
@runtime_checkable
class MemoryStore(Protocol):
    def persist(self, record: PersistRecord) -> None: ...
    def resolve(self, item: Id | Ref) -> EntityMemoryRecord | None: ...
    def retrieve(self, query: RetrievalQuery, *, retrieved_at: WallInstant) -> tuple[RecallCandidate, ...]: ...
    def claims_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Claim[object], ...]: ...
    def conflicts_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Contradiction | Resolution, ...]: ...
    def retention_for(self, item: Id | Ref) -> tuple[RetentionMark, ...]: ...
    def create_episode(self, *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant) -> None: ...
    def append_episode(self, episode: Id | Ref, item: Ref) -> None: ...
    def close_episode(self, episode: Id | Ref, at: WallInstant) -> None: ...

class InMemoryStore:
    """A real MemoryStore implementer, and the reference behavior SQLite is checked against."""
```

`resolve()` answers "do you structurally store Entity X, and what is its current representation?" — exact structural access via `identity_of()`, bypassing retention entirely, so an `ARCHIVED` entity remains exactly resolvable even though default `retrieve()` excludes it. `retrieve()` answers "which stored Entities does this retrieval surface under the current accessibility policy?" — a different question. Neither `Resolution` nor `RetentionMark` can be resolved through `resolve()`, since neither is `Entity`-bearing. `claims_for()` performs **no** Context filtering — it returns every stored `Claim` in the structural `(subject, predicate)` slot in first-persistence order, because `belief_state()` determines contradiction relevance from all slot claims *before* Context filtering; filtering here would hide conflicts from Memory's own epistemic projection. `conflicts_for()` walks the conflict history in append order and applies the same relevance rule `belief_state()` itself uses (subject match + at least one statement resolving to a slot claim) — SQL/the store only locates; `belief_state()` remains the sole authority for the resulting status, and neither `conflicts_for()` nor anything in `store.py` ever parses `Resolution.rationale`. `retention_for()` returns matching marks in append order via `identity_of()`, never sorted by `RetentionMark.at` — the store does not invent its own current-accessibility algorithm; where one is needed, it delegates to `RetentionLog`.

`retrieve()` takes an explicit `retrieved_at: WallInstant` (round 5 correction) — `RecallCandidate.retrieved_at` must come from somewhere, and a store calling a wall clock implicitly would be exactly the hidden-clock problem Core's `Transform.apply()` already solved by taking `clock`/`monotonic_clock` as explicit parameters. The store records retrieval; it does not own a clock capability, and Pass 2 adds neither a `Clock` nor an `IdSource` to `InMemoryStore` — every identity comes from a supplied record or explicit `create_episode()` arguments.

Pass 2 adds no generic enumeration API (`all_records()`, `scan()`, …) and no mutation beyond `persist()`/the Episode surface — no `delete`/`remove`/`purge`/`overwrite`/`upsert`. Memory law 1 stays structural: retention changes accessibility, never existence.

`belief_state()` and `admit()` stay pure functions over data already in hand — a store's only job is fetching candidates (claims for a slot, relevant conflict records, retrieval matches). Neither is ever reimplemented in SQL.

## The SQLite backend is not Memory v0

`SqliteMemoryStore` is one implementation proving that Memory v0's semantics survive durability. It is not where those semantics are defined. Table structure, FTS5 ranking behavior, SQL query ordering, and any SQLite-specific limitation are implementation details — never treated as memory semantics. `InMemoryStore` and `SqliteMemoryStore` are required to be behaviorally equivalent for every operation both support (`MEMORY_ADVERSARIAL_MATRIX.md` section Q).

**Append-only operation journal, not a record-per-table schema** (frozen by Pass 3 preregistration, `docs/memory-passes/03-sqlite-backend.md`): SQLite's authoritative state is an ordered journal of `MemoryStore` operations (`persist`, `create_episode`, `append_episode`, `close_episode`), not a normalized table per `MemoryRecord` type. On open, the journal is strictly decoded, integrity-verified, and replayed in sequence through a fresh `InMemoryStore`'s public API — never through its private internals — producing the current semantic projection. This is deliberate: a record-per-table design would require SQL to independently reimplement identity collision, embedded-entity atomicity, canonical equality, conflict-history ordering, retention projection, and Episode transition validity — exactly the trust Pass 2 exists to centralize in one place. The journal instead asks SQL to prove only that already-valid operations can be preserved, ordered, recovered, and replayed exactly.

**Authoritative vs. derived durable state:** `memory_meta` (schema version) and `memory_ops` (the operation journal, each row checksummed with a SHA-256 digest covering journal-format version + sequence + operation kind + payload) are authoritative. The FTS5 lexical index and the in-process `InMemoryStore` projection are derived and fully rebuildable from the journal — destroying either never destroys a Memory fact. Schema version is frozen at `1` for v0; there is no migration framework, so any other version fails loudly as `UnsupportedSchemaVersion` rather than being guessed, upgraded, or downgraded.

**Operation sequence and checksums:** `memory_ops.seq` is a SQLite-local total order — storage-local, strictly increasing, contiguous from 1, never a Core `Id`, never a `Ref` target, never exposed through `MemoryStore`. Each row's digest detects accidental storage corruption (payload, op-kind, or sequence tampering); it is not a claim of protection against a malicious actor able to rewrite both data and checksum.

**Write transaction shape:** every public mutation applies to the in-process reference first (proving the operation is semantically legal before durable state is appended), then commits one atomic SQLite transaction (allocate next contiguous `seq` → insert journal row → rebuild/update derived FTS state → `COMMIT`). If any durable step fails, the transaction rolls back, the reference projection is reconstructed from the committed journal, and the original database failure propagates unchanged — semantic failures (`IdentityCollision`, `UnsupportedMemoryRecord`, `UnsupportedPersistedValue`, and the rest of Pass 2's exception set) never reach SQLite at all, since the reference rejects them before any durable write is attempted.

**Lifecycle and exception shapes** (all `RuntimeError` subclasses — resource-lifecycle and storage-corruption machinery, not new Memory concepts):

```text
StoreCorruption(RuntimeError):        sequence: int | None; reason: str
UnsupportedSchemaVersion(RuntimeError): found: str; supported: int
SqliteStoreClosed(RuntimeError)
```

`SqliteMemoryStore(path)` performs no I/O at import time — only construction opens/creates the database. `close()` is SQLite-backend-specific (not part of `MemoryStore`); after `close()`, every public operation raises `SqliteStoreClosed` rather than silently answering from a stale in-memory mirror. Opening an existing database runs `PRAGMA integrity_check`, verifies every operation's checksum and contiguous sequence, and replays each operation through `InMemoryStore`'s public API — a well-formed-but-semantically-invalid journal (e.g. a `Resolution` before its `Contradiction`, an `Episode` append after close) fails to open as `StoreCorruption`, chaining the original semantic exception. A persisted journal must describe a valid history, not merely valid bytes.

**FTS5 is a rebuildable derived index, never retrieval authority:** all indexed text comes exclusively from the already-frozen `lexical_content()` — no second text-extraction policy exists in `sqlite_store.py`. Because Memory's frozen lexical contract (literal, case-sensitive substring, no normalization, no query operators) is not identical to FTS5's own default token semantics, FTS5 is never used as the membership test for retrieval; it is rebuilt entirely from the semantic projection (clear table → iterate known Entity ids → resolve → `lexical_content()` → insert) whenever needed, including after reopen. A stale or corrupted FTS index may always be discarded and rebuilt without touching a single Memory fact; corruption in `memory_meta`/`memory_ops` is fatal and surfaced, never silently repaired.

**Query methods delegate to the reference projection** (v0 posture, revisitable only after backend-equivalence tests prove a replacement): `resolve`, `claims_for`, `conflicts_for`, `retention_for`, and `retrieve` all delegate to the replayed `InMemoryStore` rather than being reimplemented in SQL. Consequently candidate membership, relevance labels, retention partitioning, default ordering, archive behavior, and custom-`Kind` failures are identical to `InMemoryStore` by construction — SQLite does not yet own ranking. This is stricter than the specification requires and therefore safe as a starting posture.

**No private `memory.store` coupling:** `sqlite_store.py` builds entirely on `store.py`'s public boundary (`MemoryStore`, the four type aliases, `IdentityCollision`/`UnsupportedMemoryRecord`, `RetrievalQuery`, `lexical_content()`) plus the public Core/codec constructions needed for its own private durable record codec. It never reaches into `_canonical_record`, `_snapshot_context`, `_check`, `_commit`, or any other private `InMemoryStore` machinery — this is what keeps SQLite from coupling to Pass-2 implementation internals rather than its frozen semantics.

## Concurrency posture

`Episode`, `InMemoryStore`, and `RetentionLog` are mutable containers, exactly like Core's `Trace`/`History`/`ContradictionLog`. For v0, all are **single-writer and not thread-safe unless otherwise stated** — no locks yet (law 19: no unearned synchronization). `SqliteMemoryStore` makes no multi-process/concurrent-write guarantee in v0 beyond what SQLite itself provides; concurrent write support is explicitly unsupported rather than silently assumed.

## Implementation order — four checkpointed passes, green tests gate each transition

Each pass answers a specific question and is scoped to the `MEMORY_ADVERSARIAL_MATRIX.md` sections it must prove.

1. **Semantic constructions** — "What is memory?" `episode`, `recall`, `retention`, `belief`, `codec`. Pure, in-memory, no persistence boundary. Proves matrix sections A (Episode), B (RecallCandidate), C (WorkingSet), D (Retention), E–G (BeliefProjection ordinary/context/contradiction), H–J (codec/float/primitive encoding).
2. **Persistence boundary + in-memory reference store** — "What does it mean to persist/query memory?" `store.py`: `MemoryStore` protocol and `InMemoryStore`. Pins the exact persisted-record union and the `Episode` create/append/close store surface (not expressible as generic `persist()` — see "Episode persistence" above). Proves K (admissibility), L (Id collision).
3. **SQLite backend** — "Can those semantics survive a real durable backend?" `sqlite_store.py`: codec wired end-to-end, Episode transitions, FTS5, retrieval+retention composition, crash/reopen. Proves M (Episode SQLite transitions), N (contradiction lookup), O (retrieval/retention), P (FTS), U (crash/reopen), and backend-equivalence against pass 2's reference (Q).
4. **Architectural closure** — "Did the theory survive implementation?" Full adversarial suite run together; import-dependency graph test; import-side-effects test; strict typing/lint; manual `MEMORY_SPECIFICATION.md`/`MEMORY_LAWS.md`-to-code audit. Proves T (import/dependency), V (Ref-closure), W (law-13 information preservation), and the cross-module integration scenarios in X.

Same tooling as Core: pytest, Ruff, Pyright. Same test-layer discipline: `tests/memory/architecture/` mirrors Core's `test_import_graph.py`/`test_import_side_effects.py`, extended to cover the `memory` package; `tests/memory/semantics/` covers each module plus the cross-module scenarios in `MEMORY_ADVERSARIAL_MATRIX.md` section X.

## Explicitly not built in v0

Consolidation, a vector similarity backend, a general codec registry/plugin mechanism, automatic search-document extraction beyond explicitly textual content, any adjudication of `Contradiction`s, any modification of Core, multi-process/concurrent-write support, and a schema-migration framework. The goal of Memory v0 is not to make Memory broadly useful yet — it's to make the semantic substrate real, importable, executable, durable, and difficult to misuse, exactly as Core's own v0 did for its own layer.
