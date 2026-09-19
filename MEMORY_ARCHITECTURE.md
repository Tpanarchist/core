# Memory — v0 Architecture

This document derives the Python package from `MEMORY_SPECIFICATION.md`. Signatures in that document specify semantic inputs/outputs; this document defines the authoritative Python runtime realization, exactly mirroring the relationship between Core's `SPECIFICATION.md` and `ARCHITECTURE.md`. Where exact API decisions aren't fixed by either document, they're pinned at the start of the pass that needs them — Core's own "pass preregistration, not another architecture review" discipline.

## Central rule

Memory persists and retrieves what Core already knows how to represent; it adds no new ontology primitives, only grouping, selection, retention, and mechanical projection over Core's existing vocabulary. `memory.*` depends on `core.*`, never the reverse. Core v0 is closed — nothing here modifies it.

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
| 0 | `belief` | `BeliefProjection`, `belief_state()` | `core.identity`, `core.context`, `core.value`, `core.epistemic` |
| 0 | `codec` | `PersistedValue`, `as_persisted_value()`, Core-primitive encoders | `core.value`, `core.identity`, `core.time`, `core.context` |
| 1 | `store` | `MemoryStore` (protocol), `InMemoryStore` | `episode`, `recall`, `retention`, `belief`, `core.identity`, `core.time`, `core.context`, `core.epistemic`, `core.observation`, `core.event`, `core.effect` *(provisional — see below)* |
| 2 | `sqlite_store` | `SqliteMemoryStore` | `store`, `codec`, `episode`, `recall`, `retention`, `belief`, `core.identity`, `core.time`, `core.context`, `core.epistemic`, `core.observation`, `core.event`, `core.effect`, stdlib `sqlite3` *(provisional — see below)* |

Tier-0 modules (`episode`, `recall`, `retention`, `belief`, `codec`) do not depend on each other — each is independently meaningful and independently testable against Core alone, exactly like Core's own tier-4 modules (`event`, `observation`, `relation`, `effect`, `provenance`, `epistemic`).

**Provisional rows**: `store` and `sqlite_store`'s Core import lists are provisional until the persisted-record union is pinned at Pass 2 preregistration. The current list covers what's already fixed (`claims_for`, `conflicts_for`, `retrieve`, and persisting `Episode`); it is expected to grow — at minimum `core.error` (for indexing `Error.message` per `MEMORY_ADVERSARIAL_MATRIX.md` FT-01) and possibly `core.provenance`, `core.inference` are candidates, depending on the final union. The candidate persisted-record union under discussion:

```text
Observation, Claim, Inference, Contradiction, Resolution,
Event, Effect, Provenance, Error, Episode, RetentionMark
```

## Persistence boundary

`persist()` does not take `Entity` — persistence eligibility is a Memory-level decision, independent of Core's `Entity` protocol (`Resolution` and `RetentionMark` are admissible despite carrying no `Id`; nothing in Memory promises to persist every `Entity` Core can produce). The exact admissible-record union type is pinned at Pass 2 preregistration.

Two distinct key regimes:

```text
identified records (carry a Core Id)
    semantic primary key = Id (kind, value)

append-only non-Entity records (Resolution, RetentionMark)
    storage ordering key = storage-local sequence
    semantic identity = none — never promoted to Id, never Ref-targetable
```

**Identified-record collision policy**: persisting the same `Id` twice with a canonically identical record is idempotent success; persisting a different record under the same `Id` is an explicit collision error. This makes retry-after-failure safe without permitting mutation-by-upsert.

## Episode persistence

Episode is mutable and append-only in memory, so persistence must preserve exactly three one-way transitions rather than repeatedly replacing a serialized snapshot:

```text
create episode
append item
close episode
```

Guaranteed invariants: stored items may only gain a suffix; `closed_at` moves `None → WallInstant` exactly once; existing items never change or reorder; `closed_at`, once set, never changes again. A normalized `episodes` + `episode_items` representation fits this naturally — exact table shape is pass-level detail.

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
- `as_persisted_value()` returns a defensive recursive immutable snapshot of validated container data — never a reference to the caller's (possibly still-mutable) input.
- `list` is rejected even where JSON could encode it — `PersistedValue` uses `tuple`, matching Core's own immutability discipline; no structural auto-conversion from dataclasses or other containers.

**Lexical indexing is a separate concern from persistence.** `sqlite_store.py` indexes a field in FTS5 only when that field is already `str`-typed in Core's own schema (`Error.message`, `Resolution.rationale`, `RetentionMark.rationale`), or when an `object`-typed field's `PersistedValue` encoding happens to *be* a bare `str`. Never `str(x)` on anything else, and no recursive search-document extraction from nested structures in v0.

## Contradiction relevance

Core's `Contradiction` has `subject` and `statements: tuple[Ref, ...]`, but no `predicate` — so `conflicts_for(subject, predicate)` must derive relevance through the referenced `Claim`s:

> A `Contradiction` is relevant to a `(subject, predicate)` slot if its `subject` matches and at least one of its `statement` `Ref`s resolves to a `Claim` belonging to that slot.

This holds even when a Claim in the slot is contested by a statement using a *different* predicate — the slot's claim is still contested, and Memory does not pretend otherwise merely because the other statement used another predicate. SQL only locates the relevant records; `belief_state()` in `belief.py` remains the sole authority for the resulting status.

## Retrieval composes with retention

```text
backend search
      ↓
candidate matches
      ↓
retention accessibility (RetentionLog.current())
      ↓
ordered RecallCandidates
```

`RetentionLog.current()` remains the semantic authority for accessibility, regardless of which backend, or which stage of a query pipeline, physically applies the filter. A backend may optimize this internally later, but no implementation may interpret `ACTIVE`/`DEPRIORITIZED`/`ARCHIVED` differently from another. Frozen default retrieval meaning: `ACTIVE` normally eligible; `DEPRIORITIZED` eligible, ordered after `ACTIVE`; `ARCHIVED` excluded by default, recoverable only through an explicit archive-inclusive query.

## `store.py` owns a real implementer, not just a protocol

Following Core's own `EffectSink`/`MemoryEffectSink` precedent (a Capability-like protocol is introduced only alongside a real implementer — law 19), `store.py` owns both:

```python
class MemoryStore(Protocol):
    def persist(self, record: MemoryRecord) -> None: ...
    def retrieve(self, query: RetrievalQuery) -> tuple[RecallCandidate, ...]: ...
    def claims_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Claim, ...]: ...
    def conflicts_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Contradiction | Resolution, ...]: ...

class InMemoryStore:
    """A real MemoryStore implementer, and the reference behavior SQLite is checked against."""
```

`MemoryRecord` (the admissible persisted-record union) and the exact shape of `RetrievalQuery` are pinned at Pass 2 preregistration — the signatures above name the operations already fixed by the specification, not their final Python types.

`belief_state()` and `admit()` stay pure functions over data already in hand — a store's only job is fetching candidates (claims for a slot, relevant conflict records, retrieval matches). Neither is ever reimplemented in SQL.

## The SQLite backend is not Memory v0

`SqliteMemoryStore` is one implementation proving that Memory v0's semantics survive durability. It is not where those semantics are defined. Table structure, FTS5 ranking behavior, SQL query ordering, and any SQLite-specific limitation are implementation details — never treated as memory semantics. `InMemoryStore` and `SqliteMemoryStore` are required to be behaviorally equivalent for every operation both support (`MEMORY_ADVERSARIAL_MATRIX.md` section Q).

## Concurrency posture

`Episode`, `InMemoryStore`, and `RetentionLog` are mutable containers, exactly like Core's `Trace`/`History`/`ContradictionLog`. For v0, all are **single-writer and not thread-safe unless otherwise stated** — no locks yet (law 19: no unearned synchronization). `SqliteMemoryStore` makes no multi-process/concurrent-write guarantee in v0 beyond what SQLite itself provides; concurrent write support is explicitly unsupported rather than silently assumed.

## Implementation order — four checkpointed passes, green tests gate each transition

Each pass answers a specific question and is scoped to the `MEMORY_ADVERSARIAL_MATRIX.md` sections it must prove.

1. **Semantic constructions** — "What is memory?" `episode`, `recall`, `retention`, `belief`, `codec`. Pure, in-memory, no persistence boundary. Proves matrix sections A (Episode), B (RecallCandidate), C (WorkingSet), D (Retention), E–G (BeliefProjection ordinary/context/contradiction), H–J (codec/float/primitive encoding).
2. **Persistence boundary + in-memory reference store** — "What does it mean to persist/query memory?" `store.py`: `MemoryStore` protocol and `InMemoryStore`. Pins the exact persisted-record union. Proves K (admissibility), L (Id collision).
3. **SQLite backend** — "Can those semantics survive a real durable backend?" `sqlite_store.py`: codec wired end-to-end, Episode transitions, FTS5, retrieval+retention composition, crash/reopen. Proves M (Episode SQLite transitions), N (contradiction lookup), O (retrieval/retention), P (FTS), U (crash/reopen), and backend-equivalence against pass 2's reference (Q).
4. **Architectural closure** — "Did the theory survive implementation?" Full adversarial suite run together; import-dependency graph test; import-side-effects test; strict typing/lint; manual `MEMORY_SPECIFICATION.md`/`MEMORY_LAWS.md`-to-code audit. Proves T (import/dependency), V (Ref-closure), W (law-13 information preservation), and the cross-module integration scenarios in X.

Same tooling as Core: pytest, Ruff, Pyright. Same test-layer discipline: `tests/memory/architecture/` mirrors Core's `test_import_graph.py`/`test_import_side_effects.py`, extended to cover the `memory` package; `tests/memory/semantics/` covers each module plus the cross-module scenarios in `MEMORY_ADVERSARIAL_MATRIX.md` section X.

## Explicitly not built in v0

Consolidation, a vector similarity backend, a general codec registry/plugin mechanism, automatic search-document extraction beyond explicitly textual content, any adjudication of `Contradiction`s, any modification of Core, multi-process/concurrent-write support, and a schema-migration framework. The goal of Memory v0 is not to make Memory broadly useful yet — it's to make the semantic substrate real, importable, executable, durable, and difficult to misuse, exactly as Core's own v0 did for its own layer.
