# Pass 4 preregistration — `event`, `observation`, `relation`, `effect`, `provenance`, `epistemic`

Pass-local API decisions not already fixed by SPECIFICATION.md/ARCHITECTURE.md. Implement, test, commit; do not reopen the frozen ontology, dependency graph, or completed lower passes.

## Scope and dependency boundary

Six sibling Tier-4 modules, each depending only on the frozen substrate (`value`, `identity`, `time`, `context`), never on each other:

```text
event        → value, identity, time, context
observation  → identity, time, context
relation     → value, identity, time, context
effect       → value, identity, time, context
provenance   → identity, time, context
epistemic    → value, identity, time, context
```

`epistemic` does not import `Observation`/`Event` (evidence crosses that boundary only by `Ref`). `provenance`/`effect` do not import `Transform`. `relation` does not import `Provenance`. No Pass-4 module imports `trace`. No Pass-4 constructor allocates an Id or reads a clock.

## `Event`

Frozen, slotted, keyword-only, Entity-bearing, `eq=False` (custom equality): `id`, `kind`, `at`, `payload: object = None`, `context: Context | None = None`. Equality/hash are **by `id` only** — the frozen spec's "compare by Id" operation — so two Events sharing an Id but disagreeing on other fields still compare equal (a representation-consistency problem for a higher layer, not something `==` silently resolves). `summary() -> tuple[Id, Kind, WallInstant]` is the only projection — no payload/context formatting.

## `Observation[T]`

Frozen, slotted, keyword-only, Entity-bearing: `id`, `subject: Id | Ref`, `value: T`, `at`, `source: object`, `context: Context` (required, not optional), `observer: object | None = None`. Ordinary structural equality (unlike Event — Pass 4 does not universally redefine Entity equality; Event does so because its own frozen operation says so). `source`/`observer` stay plain Value roles — no new wrapper types. Refers to an Event only via that Event's `Id`/`Ref`; never imports `Event`.

## `Relation` / `RelationSet`

`Relation`: frozen, slotted, keyword-only, **not** Entity-bearing (nothing targets one by `Ref` in v0): `source: Id | Ref`, `kind: Kind`, `target: Id | Ref`, `at`, `context: Context | None = None`, `metadata` (same defensive-copy/read-only rule as Context/Error).

`RelationSet`: mutable, single-writer, append-preserving (no dedup) adjacency helper — `add`, `relations() -> tuple[...]` (snapshot), `query(*, kind=None, source=None, target=None)` (conjunctive filters, exact `Kind` equality, source/target matched via `identity_of()` not raw equality, insertion order), `path_exists(source, target, *, kind=None)` (directed plain BFS, `identity_of()` throughout, reflexive — same identity is always reachable in zero steps — terminates on cycles via a visited set).

## `EffectSpec` / `Effect` / `EffectSink` / `MemoryEffectSink`

`EffectSpec` (declarative, not Entity-bearing): `kind`, `target_shape: object`, `description: str` (non-empty). `Effect` (a record, Entity-bearing): `id`, `kind`, `description` (non-empty), `target: object` (opaque), `at`, `context`, `metadata` (defensive-copy rule). Declared-possible and actually-happened stay permanently separate types.

`EffectSink` is a narrow `@runtime_checkable Protocol`: `record(effect) -> None` only, no query API in the protocol itself. `MemoryEffectSink` is the concrete v0 implementer: insertion-ordered, `record`/`effects()` (snapshot), single-writer. Neither allocates identities, infers `EffectSpec`s, validates declaration, nor imports `Transform`.

## `Provenance` / `Traced[T]` / `AncestorReport`

`Provenance` (frozen, slotted, keyword-only, Entity-bearing): `id`, `transform_id`, `transform_name`/`transform_version` (both non-empty), `inputs: tuple[Ref, ...]`, `parents: tuple[Ref, ...]` (both normalized to tuples), `at`, `duration`, `context`. `inputs` cites identified inputs; `parents` cites prior Provenance nodes — different relationships, never conflated. `Traced[T]` stays minimal: `value: T`, `provenance: Provenance | None = None` — no proxying, no inspection.

`AncestorReport`: `found: tuple[Provenance, ...]` (excludes the traversal root), `unresolved: tuple[Ref, ...]`, `cycles: tuple[Ref, ...]`.

**`Provenance.ancestors(resolve: Callable[[Ref], Provenance | None]) -> AncestorReport`** — no registry, no ambient resolver. The resolver's contract is validated: if it returns a node whose `.id` doesn't match the requested `Ref.id`, that's false identity information, not merely "unresolved," and `ancestors()` raises `ValueError` rather than silently trusting it.

Traversal is deterministic DFS preorder over each node's `parents` in stored order, using the standard three-state distinction — **unseen** (resolve and descend), **active** (currently on the DFS path — reaching it again is a genuine cycle, recorded and not descended into), **done** (fully expanded — reaching it again is ordinary convergence, recorded once in `found` via the `done`-check itself, never re-traversed). The root's own Id starts active, so a path looping back to the root is caught. `unresolved`/`cycles` are deduplicated by **exact `Ref` equality** (id *and* namespace), not by `Id` alone — two differently-namespaced Refs to the same missing/cyclic Id are two distinct reportable facts, per the frozen `Ref` semantics; `found`, by contrast, is naturally deduplicated by the `active`/`done` state machine (a node's `Id`, not the specific `Ref` that reached it, is what convergence is about). No mutation of any traversed node; nothing is flattened back onto `Provenance`.

## `Claim[T]` / `Inference[T]` / `Contradiction` / `Resolution` / `ContradictionLog`

`Claim[T]` (Entity-bearing): `id`, `subject: Id | Ref`, `predicate: Kind` (an open categorical label, not a second string vocabulary), `value: Maybe[T]`, `context: Context`, `asserted_by: Id | Ref`, `evidence_refs: tuple[Ref, ...]` (normalized, empty allowed), `at`. Never imports Observation/Event/Inference — evidence is opaque by `Ref`.

`Inference[T]` (Entity-bearing): `id`, `premises: tuple[Ref, ...]` (normalized, empty allowed), `method: Kind`, `conclusion: Claim[T]`, `at`. Never rewrites its conclusion's `evidence_refs`.

`Contradiction` (Entity-bearing): `id`, `subject: Id | Ref`, `statements: tuple[Ref, ...]`, `detected_at`, `context`. Requires **at least two distinct entities after `identity_of()`** among `statements` — two differently-namespaced Refs to the same Claim don't count as two conflicting statements. Never inferred automatically from Claim values; construction means a higher process already made the semantic judgment.

`Resolution` (frozen, slotted, keyword-only, **not** Entity-bearing): `contradiction: Ref`, `rationale: str` (non-empty), `resolved_by: Id | Ref`, `at`. Records a later fact; never mutates the Contradiction; no `resolved: bool`, no canonical-winner field.

`ContradictionLog` (mutable, append-only, single-writer): `record(entry) -> entry`, `entries() -> tuple[...]` (snapshot), `unresolved() -> tuple[Contradiction, ...]`, `for_subject(subject) -> tuple[...]`. A `Contradiction` may be recorded at most once per `id` (`ValueError` on duplicate). A `Resolution` may only be recorded if its `contradiction.id` already appears earlier in the same log (`ValueError` otherwise) — this makes "later" exactly append order, never inferred from `WallInstant`; multiple later Resolutions for one Contradiction are permitted. `unresolved()` is recomputed from the append-only facts every call — a Contradiction with no Resolution anywhere in the log referencing it. `for_subject()` matches via `identity_of()` and returns **both** the matching Contradictions and their later Resolutions, in original log order — not Contradictions alone.

## Local failures

Still below `core.constraint`: `ValueError`/`TypeError` locally, never the structured-contract layer. No failure path allocates an identity or reads a clock.

## Equality and identity rules

No universal equality rule across all Entity-bearing Pass-4 records. `Event` is by-Id (per its own frozen operation); `Observation`/`Effect`/`Provenance`/`Claim`/`Inference`/`Contradiction` keep ordinary structural equality (their `.id` is still how you ask the identity question explicitly). `Relation` source/target and epistemic `subject` comparisons go through `identity_of()`. Mutable containers (`RelationSet`, `MemoryEffectSink`, `ContradictionLog`) have no structural equality over their changing contents.

## Import behavior

Importing any Pass-4 module allocates no Id, reads no clock, records no Effect, creates no global container, and performs no filesystem/network/subprocess/environment/random operation. Pass 6 supplies the exhaustive architecture-wide mechanical audit; Pass 4's own tests enforce only the seams this pass is directly responsible for (no sibling Tier-4 imports where the spec forbids them, no `trace` import, no side effects on import).
