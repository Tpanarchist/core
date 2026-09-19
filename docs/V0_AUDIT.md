# Core v0 — Closure Audit

This document is not a design document. It records proof that the design frozen in `SPECIFICATION.md`/`LAWS.md`/`ARCHITECTURE.md` reached code, and it is the deliverable of Pass 6 (`docs/passes/06-architectural-closure.md`). Every row below points at an actual file and, where applicable, an actual test — no row is invented for visual uniformity, and no concept is skipped because its representation is conceptual rather than a class.

As of this audit: **362 tests**, `ruff check` clean, `pyright` (strict) clean, all over the entire repository. Test count is recorded here as an observation, not a semantic invariant — it will grow.

---

## Table A — the 16 ontology concepts

| # | Concept | Question | Code home | Representation/mechanism | Test evidence | Status |
|---|---|---|---|---|---|---|
| 1 | Identity | Which thing? | `identity.py` | `Id = (kind: Kind, value: str)`, structural `Entity(Protocol)` | `test_identity.py` | Closed |
| 2 | Value | What information? | `value.py` | Conceptual role — no dedicated class for plain information; its epistemic-status refinement (`Known`/`Unknown`/`Maybe`) and its categorical-label refinement (`Kind`) are the earned runtime representations | `test_value.py` | Closed |
| 3 | Context | Under what frame? | `context.py` | `Context` (frozen structure); `with_`/`merge`/`override` | `test_context.py` | Closed |
| 4 | Time | When / in what order? | `time.py` | Five readings (`WallInstant`, `MonotonicInstant`, `LogicalTime`, `Sequence`, `Duration`) + three producer protocols (`Clock`, `MonotonicClock`, `LogicalClock`) | `test_time.py` | Closed |
| 5 | State | What holds, at this time/frame, for which thing? | `state.py` | `State[T]` (frozen, not Entity-bearing) | `test_state.py` | Closed |
| 6 | Event | What occurred? | `event.py` | `Event` (`eq=False`, custom by-Id `__eq__`/`__hash__`) | `test_event.py` | Closed |
| 7 | Observation | What was perceived/measured, about which thing? | `observation.py` | `Observation[T]` (subject/source/time/context all required) | `test_observation.py` | Closed |
| 8 | Result | What came out of an attempt? | `result.py` | `Ok[T]` / `Err[E]` (`type Result[T, E] = Ok[T] \| Err[E]`) | `test_result.py` | Closed |
| 9 | Error | How did an attempt fail? | `error.py` | `Error` (structured `cause` vs. foreign `exception`, `.chain()`, `.is_kind()`) | `test_error.py` | Closed |
| 10 | Transformation | How did one thing become another? | `transform.py` | `Transform[A, B]` (by-Id equality, `.apply()`, `.then()`) | `test_transform.py` | Closed |
| 11 | Effect | What changed outside the computation itself? | `effect.py` | `EffectSpec` (declarative) / `Effect` (record), kept permanently distinct | `test_effect.py` | Closed |
| 12 | Capability | What can this thing do? | Distributed: `identity.py` (`Entity`, `IdSource`), `time.py` (`Clock`, `MonotonicClock`, `LogicalClock`), `effect.py` (`EffectSink`) | Structural `Protocol`s, each with a real implementer (Table B) | `test_identity.py`, `test_time.py`, `test_effect.py` | Closed |
| 13 | Relation | How are things connected? | `relation.py` | `Relation` (not Entity-bearing) + `RelationSet` (adjacency, BFS) | `test_relation.py` | Closed |
| 14 | Constraint | What must or must not hold? | `constraint.py` | Plain functions `require`/`ensure`/`invariant` + `ContractError` control-flow carrier | `test_constraint.py` | Closed |
| 15 | Provenance | Where did this come from? | `provenance.py` | `Provenance` (lazy DAG via `parents`), `Traced[T]`, `AncestorReport` | `test_provenance.py` | Closed |
| 16 | Trace | What path did execution take? | `trace.py` | `Trace`/`TraceEntry` (low tier only — the high-tier `inspect/` layer, `Task`/`Checkpoint`/`Session`, is deliberately deferred beyond v0) | `test_trace.py` | Closed for the specified v0 scope |

No concept lacks a home; no class was invented merely to make this table visually uniform (Value, Capability, and Constraint are honestly recorded as conceptual roles, not classes).

---

## Table B — derived constructions

| Construction | Built from | Code home | Enforced invariant/behavior | Test evidence | Status |
|---|---|---|---|---|---|
| `Namespace` | tuple of string segments | `identity.py` | Non-empty tuple, non-empty segments | `test_identity.py::TestNamespace` | Closed |
| `Ref` | `Id` + optional `Namespace` | `identity.py` | Equality includes namespace; `identity_of()` strips it for entity comparison | `test_identity.py::TestRef` | Closed |
| `Key` | `Namespace` + name | `identity.py` | Non-empty name | `test_identity.py::TestKey` | Closed |
| `Deadline` | reading + compatible producer, same family/space | `time.py` (`WallDeadline` \| `MonotonicDeadline`) | `MonotonicDeadline` validates matching space at construction | `test_time.py::TestWallDeadline`, `TestMonotonicDeadline` | Closed |
| `Transition` | `id` + before-`State` + `Event` + operation + after-`State` | `state.py` | Same-subject check, non-backward time, non-empty operation | `test_state.py::TestTransition` | Closed |
| `History` | subject-scoped ordered sequence of `Transition` | `state.py` | Subject match, no duplicate Transition id, exact before/after continuity | `test_state.py::TestHistory` | Closed |
| `Claim` | subject + predicate(`Kind`) + `Maybe[T]` + context + asserted_by + evidence_refs | `epistemic.py` | Evidence normalized to tuple; zero evidence allowed | `test_epistemic.py::TestClaim` | Closed |
| `Inference` | premises + method(`Kind`) + conclusion(`Claim`) | `epistemic.py` | Premises normalized to tuple; zero premises allowed; conclusion never rewritten | `test_epistemic.py::TestInference` | Closed |
| `Pipeline` | ordered sequence of `Transform` | `transform.py` | **>= 2 stages enforced at construction** (Pass-5 corrective); no Id/Provenance of its own | `test_transform.py::TestPipelineConstruction`, `TestPipeline` | Closed |
| `ContextConflict` | incompatible values for one Context field | `context.py` | Non-empty `conflicts` tuple | `test_context.py::TestMerge` | Closed |
| `ContextOverride` | `(field, old, new)` triples from `override()` | `context.py` | Records only effective changes | `test_context.py::TestOverride` | Closed |
| `Contradiction` | subject + statements(`Ref`) + detected_at + context | `epistemic.py` | >= 2 *distinct* entities among statements (via `identity_of`) | `test_epistemic.py::TestContradiction` | Closed |
| `Resolution` | `contradiction: Ref` + rationale + resolved_by + at | `epistemic.py` | Non-empty rationale; not Entity-bearing | `test_epistemic.py::TestResolution` | Closed |
| `ContradictionLog` | append-only `Contradiction \| Resolution` | `epistemic.py` | Contradiction recorded once per id; Resolution only after its Contradiction; `unresolved()` derived, not stored | `test_epistemic.py::TestContradictionLog` | Closed |
| `Traced` | value + optional `Provenance` | `provenance.py` | Minimal — no proxying/inspection | `test_provenance.py::TestTraced` | Closed |
| `RelationSet` | collection of `Relation` + adjacency/BFS | `relation.py` | Duplicates preserved; `identity_of()`-based query matching; reflexive `path_exists` | `test_relation.py` | Closed |
| `EffectSink` | narrow protocol consuming `Effect` | `effect.py` | `record()` only, no query API in the protocol itself | `test_effect.py::TestEffectSink` | Closed |
| `AncestorReport` | `found`/`unresolved`/`cycles` from `Provenance.ancestors()` | `provenance.py` | `found` excludes the root; `unresolved`/`cycles` deduplicated by exact `Ref` equality, not bare `Id` | `test_provenance.py::TestAncestors` | Closed |

### Concrete v0 capability implementations

Not additional derived-ontology constructions — the concrete implementers that let each `Capability` protocol satisfy law 19 (no protocol without a real user):

| Implementation | Implements | Code home | Test evidence |
|---|---|---|---|
| `UuidIdSource` | `IdSource` | `identity.py` | `test_identity.py::TestUuidIdSource` |
| `SystemClock` | `Clock` | `time.py` | `test_time.py::TestSystemClock` |
| `SystemMonotonicClock` | `MonotonicClock` | `time.py` | `test_time.py::TestSystemMonotonicClock` |
| `LamportClock` | `LogicalClock` | `time.py` | `test_time.py::TestLamportClock` |
| `MemoryEffectSink` | `EffectSink` | `effect.py` | `test_effect.py::TestEffectSink` |

---

## Table C — the 20 laws

| # | Law | Code enforcement | Test evidence | Status |
|---|---|---|---|---|
| 1 | Identity is explicit | `Id = (kind, value)`; every `Ref` target confirmed `Entity`-bearing (§ Ref-closure audit below) | `test_identity.py`, plus per-type `isinstance(x, Entity)` tests across `test_event.py`, `test_error.py`, `test_observation.py`, `test_effect.py`, `test_state.py`, `test_epistemic.py`, `test_provenance.py`, `test_trace.py`, `test_transform.py` | Closed |
| 2 | Context-dependent information carries its context | `Context` is a field on every primitive whose meaning depends on it (Observation, Claim, Contradiction require it; Error, Event, Effect, Relation, Provenance, State, Transform's `apply()` carry it optionally) | `test_context.py`, plus context-preservation assertions in `test_error.py`, `test_constraint.py`, `test_transform.py` | Closed |
| 3 | Observation is distinct from interpretation | Four non-interchangeable types: `Observation` (act) / `Claim` (assertion) / `Inference` (derivation) / plain `Value` role — evidence crosses by opaque `Ref`, `epistemic.py` never imports `observation.py` | `test_observation.py`, `test_epistemic.py`, `test_pass4_seams.py::test_epistemic_does_not_import_observation_or_event` | Closed |
| 4 | State is distinct from change | `State` frozen/immutable; `Transition` is the only thing that connects two States, and does so via an `Event` + named `operation` | `test_state.py::TestState`, `TestTransition` | Closed |
| 5 | Effects are distinguishable from computation | `EffectSpec` (declared-possible) and `Effect` (actually-happened) are permanently separate types; `Transform.apply()` takes no sink and never auto-records | `test_effect.py`, `test_transform.py::test_effect_specs_do_not_cause_effect_recording` | Closed |
| 6 | Unknown is distinct from false | `Unknown` is a true singleton, `Unknown() != Known(False)` and `!= None`; `Claim.value: Maybe[T]` — no later pass reintroduced `None` as an epistemic marker (optional `None` fields elsewhere mean "not supplied," never "unknown") | `test_value.py::TestUnknown` | Closed |
| 7 | Failure preserves information | `Result`'s failure branch always structured; `Error.cause` (structured) vs. `Error.exception` (foreign) kept distinct; `ContractError` carries its `Error` unaltered; `Transform.apply()` preserves the original exception verbatim in `Error.exception`; capability failures (`IdSource`/`Clock`) propagate rather than being redescribed as a fabricated Error | `test_result.py`, `test_error.py::TestChain`, `test_constraint.py::TestCapabilityFailurePropagates`, `test_transform.py::TestFunctionExecution`, `TestCapabilityFailurePropagates` | Closed |
| 8 | Transformations preserve provenance when provenance matters | `Transform.apply()` stamps exactly one `Provenance` on success, `None` on failure; `Traced[T]` makes attachment opt-in elsewhere | `test_transform.py::TestSuccessAndProvenance` | Closed |
| 9 | Nondeterminism must be controllable or observable | Every identity allocation goes through `IdSource`; every wall/monotonic/logical reading goes through `Clock`/`MonotonicClock`/`LogicalClock`; `Transform`/`constraint` receive these as explicit parameters; verified absent at import time | `test_identity.py`, `test_time.py`, `test_constraint.py`, `test_transform.py`, `tests/architecture/test_import_side_effects.py` | Closed |
| 10 | Meaningful state changes should be inspectable | `History` records every `Transition` with exact continuity; `Trace`/`TraceEntry` record ordered, structured evidence | `test_state.py::TestHistory`, `test_trace.py` | Closed |
| 11 | Components compose through narrow capabilities | `Entity`, `IdSource`, `Clock`, `MonotonicClock`, `LogicalClock`, `EffectSink` are all minimal structural `Protocol`s satisfied without inheritance | `test_identity.py::TestEntity`, `test_effect.py::TestEffectSink` | Closed |
| 12 | Mechanism and policy remain separable | `Transform`/`constraint` are the mechanism; *which* concrete `Clock`/`IdSource` to use is a call-site policy decision never hardcoded in `src/core` | `test_transform.py`, `test_constraint.py` (all use injected test doubles, never a concrete default) | Closed |
| 13 | Information is not silently discarded | `Context.merge()` reports every conflicting field; `Context.override()` audits every replacement; `Error.cause` wraps rather than replaces; `Provenance.ancestors()` reports `unresolved` explicitly; `ContradictionLog` never deletes a Resolution | `test_context.py::TestMerge`, `TestOverride`; `test_provenance.py::TestAncestors`; `test_epistemic.py::TestContradictionLog` | Closed |
| 14 | Contradictions remain visible until explicitly resolved | `Contradiction`/`Resolution` are immutable, append-only facts; `unresolved()` is a fresh projection over log order, never a stored/mutated flag; `ContextConflict` (structural) kept distinct from `Contradiction` (semantic) | `test_epistemic.py::TestContradictionLog`, `test_context.py` (no `Contradiction` import: `test_context_has_no_knowledge_of_contradiction`) | Closed |
| 15 | Invariants are executable | `require`/`ensure`/`invariant` for the structured contract layer; Tier 0–4 modules (which cannot depend on `constraint`) enforce their own invariants locally via `ValueError`/`TypeError` | `test_constraint.py`; local-failure tests throughout `test_identity.py`, `test_time.py`, `test_context.py`, `test_error.py`, `test_state.py`, etc. | Closed |
| 16 | Long operations should permit interruption and inspection | `Trace`'s append-only, sequenced evidence log is the v0 foundation; full interruption/resumption (`Task`/`Checkpoint`/`Session`) is the deliberately-deferred `inspect/` layer | `test_trace.py` | **Partial by design** — foundation laid, full realization out of v0 scope (see `SPECIFICATION.md`, "Deferred from this specification") |
| 17 | Concepts have predictable locations and names | One concept, one module (flat `src/core/*.py`, no re-export surface); the module inventory is itself a checked architectural property | `tests/architecture/test_import_graph.py::TestModuleInventory` | Closed |
| 18 | Simple operations should be simple; depth remains accessible | `Transform(id, name, version, fn)` is the minimal case (`requires`/`effect_specs` default empty); `Context.with_()` supports a single-field change; the full capability-injection signature is available but never mandatory beyond its three required capabilities | `test_transform.py::TestTransformConstruction`, `test_context.py::TestWith` | **Design-level** — evidenced by API shape, not a single behavioral test (this law is qualitative) |
| 19 | Abstraction must reduce complexity rather than merely move it | Every `Protocol` in v0 has a real implementer (Table B); no speculative `Readable`/`Writable`/`Serializable` was introduced | Table B above; absence confirmed by reading `effect.py`/`time.py`/`identity.py` — no unused protocol exists | Closed |
| 20 | Nothing happens merely because a module was imported | Every `core.*` module verified in a fresh subprocess against UUID/wall-clock/monotonic-clock/random/filesystem-write/subprocess/socket/env-mutation guards | `tests/architecture/test_import_side_effects.py` (17 modules), plus local `tests/semantics/_side_effects.py` regressions | Closed |

Laws 16 and 18 are marked precisely rather than rounded up to "Closed" — law 16 is honestly partial because its full realization (`Task`/`Session`) is out of v0 scope by the frozen specification's own design, not an oversight; law 18 is a qualitative API-shape property with no single pass/fail test, evidenced by construction ergonomics instead.

---

## Ref-closure audit

The frozen rule is one-directional: `Ref`-target ⇒ `Entity` ⇒ carries an `Id`. Never the converse.

| Type | Why referenced | Carries `Id`? | Satisfies `Entity`? | Evidence |
|---|---|---|---|---|
| `Event` | Cited from Trace entries, Observation subjects | Yes | Yes | `test_event.py::test_satisfies_entity` |
| `Error` | Cited from Trace entries | Yes | Yes | `test_error.py::test_satisfies_entity` |
| `Observation` | Cited from `Claim.evidence_refs` / `Inference.premises` | Yes | Yes | `test_observation.py::test_satisfies_entity` |
| `Claim` | Cited from `Inference.premises`, `Contradiction.statements` | Yes | Yes | `test_epistemic.py::TestClaim::test_satisfies_entity` |
| `Inference` | Cited from `Claim.evidence_refs` (potentially) | Yes | Yes | `test_epistemic.py::TestInference::test_satisfies_entity` |
| `Effect` | Cited from Trace entries | Yes | Yes | `test_effect.py::TestEffect::test_satisfies_entity` |
| `Transition` | Cited from History/Trace | Yes | Yes | `test_state.py::TestTransition::test_satisfies_entity` |
| `Contradiction` | Cited from `Resolution.contradiction` | Yes | Yes | `test_epistemic.py::TestContradiction::test_satisfies_entity` |
| `Provenance` | Cited from `parents`/Trace entries | Yes | Yes | `test_provenance.py::TestConstruction::test_satisfies_entity` |
| `Trace` | Its own `id` anchors its entries' `Sequence` space; a plausible Ref target | Yes | Yes | `test_trace.py::TestTraceIdentity::test_satisfies_entity` |

And separately, the one-directional counterexample:

| Type | Carries `Id`? | Currently a `Ref` target? | Why this is valid |
|---|---|---|---|
| `Transform` | Yes | No | `Ref`-target ⇒ `Entity` ⇒ `Id` is one-directional; a type may earn an `Id` (for `Provenance.transform_id` to cite) without anything holding a `Ref` to it. Verified structurally satisfying `Entity` anyway. | `test_transform.py::TestTransformConstruction::test_satisfies_entity` |

Not Entity-bearing, and correctly so (nothing targets them by `Ref`): `State` (identified by subject + time, not its own id), `EffectSpec` (declarative only), `Relation` (nothing cites one), `ContextConflict`/`ContextOverride` (structural, ephemeral), `Resolution` (records a fact about a Contradiction, isn't itself cited).

**Correction made during this audit**: `Error` and `Trace` were structurally `Entity`-bearing since Pass 3, but had no explicit `isinstance(x, Entity)` regression test — a real gap this audit exists to catch. `Transform`'s claimed `Entity` satisfaction (frozen in `SPECIFICATION.md` round 5) also had no direct test. All three were added (`test_error.py`, `test_trace.py`, `test_transform.py`) as part of this closure pass; no production code changed.

---

## Time-family audit

Every temporal field in the implementation, confirmed against `src/core/*.py`:

```text
Context.as_of                → WallInstant
Event.at                     → WallInstant
Observation.at               → WallInstant
Error.at                     → WallInstant
Relation.at                  → WallInstant
Effect.at                    → WallInstant
Provenance.at                → WallInstant
Provenance.duration          → Duration
Claim.at                     → WallInstant
Inference.at                 → WallInstant
Contradiction.detected_at    → WallInstant
Resolution.at                → WallInstant
State.at                     → WallInstant
WallDeadline.at               → WallInstant
MonotonicDeadline.at          → MonotonicInstant
TraceEntry.sequence          → Sequence
TraceEntry.observed_at       → WallInstant | None
```

No field uses a generic "time reading" — every one names its specific family. `MonotonicInstant`, `LogicalTime`, and `Sequence` remain space-scoped (`space: Id` on each, checked before any ordering/subtraction/`precedes` operation); no later pass introduced a cross-space comparison shortcut — confirmed by reading `time.py`'s `_check_space` guards, unchanged since Pass 2, and re-exercised by `test_time.py`.

---

## Import/dependency audit (manual cross-check)

Manual tier diagram, matching `ARCHITECTURE.md` and the automated `tests/architecture/test_import_graph.py::ALLOWED_CORE_IMPORTS` exactly:

```text
Tier 0: value, result                                    (stdlib only)
Tier 1: identity                                          → value
Tier 2: time                                              → identity
Tier 3: context                                           → identity, time, result
Tier 4: error, trace, event, relation, effect, provenance → value, identity, time, context
        observation                                       → identity, time, context
        epistemic                                          → value, identity, time, context
Tier 5: state                                             → identity, time, context, event
        constraint                                        → value, identity, time, context, error
Tier 6: transform                                         → value, identity, time, context, result, error, effect, provenance
```

No sibling Tier-4 module imports another (verified: `tests/semantics/test_pass4_seams.py`, and generally by `tests/architecture/test_import_graph.py`). No hidden reliance on re-exported names exists — `core/__init__.py` exports nothing, and `tests/architecture/test_import_graph.py::TestRootPackageAndWildcardRules` confirms no module imports through the package root or via wildcard. The manual and executable graphs agree exactly; the automated test is authoritative going forward.

---

## Public-package surface audit

`src/core/__init__.py` contains only a module docstring and `__version__` — no re-exports. Canonical usage is, and remains:

```python
from core.identity import Id, Ref
from core.time import WallInstant
from core.result import Ok, Err
```

never `from core import *`. `src/core/py.typed` is present (PEP 561 marker). No giant convenience surface was introduced at any pass.

---

## Frozen-document consistency audit

`SPECIFICATION.md`, `LAWS.md`, and `ARCHITECTURE.md` were read against the final source. Finding: **already consistent**, with one substantive correction already applied and reflected — the `constraint`/`transform` → `value` dependency edge, discovered during Pass 5 preregistration and landed in commit `4545f4d` before any Pass 5 code was written. `ARCHITECTURE.md`'s dependency table and prose already state this correctly; no further edit was needed there.

`SPECIFICATION.md`'s "Deferred from this specification" section was checked against the actual implementation and found accurate as written: it already correctly lists `SystemClock`/`SystemMonotonicClock`/`LamportClock`/`ContractError`/`MemoryEffectSink`/`IdSource`/`UuidIdSource`/`identity_of`/`UnwrapError`/`ContradictionLog` as promoted into v0, and `Traceable`/`Inspectable`/`Task`/`Checkpoint`/`Progress`/`Session`/`SeededRandom` as deferred beyond v0 — matching exactly what was and wasn't built.

No documentation wording was changed by this audit. No code was found to violate a frozen semantic requirement.

---

## No-orphan audit

**Specified thing → code/test home**: every row in Tables A, B, and C above resolves to a real file and (with laws 16/18 honestly flagged as partial/qualitative) real test evidence. Nothing in `SPECIFICATION.md`'s 16 concepts, 18 derived constructions, or `LAWS.md`'s 20 laws is unimplemented or untested in a way this audit found and left unrecorded.

**Public abstraction → specified/earned reason to exist**: every `Protocol`/class exported from a `core.*` module traces back to a Table A/B row or a Table B capability-implementation row. No public abstraction (module-level class, protocol, or function) was found with no ontology or concrete-use justification. Private helpers (`_check` in `constraint.py`, `_validate_and_freeze_metadata` in `context.py`/`relation.py`, `_add_target`/`_resolve_from_import` in the architecture tests) exist strictly to realize an already-justified mechanism and are not separately audited as ontology.

---

## Closure checklist

```text
[x] Pipeline Pass-5 corrective landed before Pass 6.        (24f6985)
[x] All semantic tests pass.                                 (362 passed)
[x] Import graph exactly obeys ARCHITECTURE.md.               (tests/architecture/test_import_graph.py)
[x] Actual Core import graph is acyclic.
[x] Every Core module passes fresh-process import-side-effect verification.
[x] Ruff is clean.
[x] Pyright strict is clean.
[x] All 16 concepts have an audited implementation/mechanism home.
[x] Every derived construction has an audited home.
[x] Every concrete v0 capability implementation is accounted for.
[x] All 20 laws have concrete enforcement/test evidence (16 and 18 flagged honestly).
[x] Every Ref target is Entity-bearing.                       (2 test gaps found and closed this pass)
[x] Time-family assignments remain correct.
[x] No specified concept is orphaned.
[x] No public abstraction is unearned/orphaned.
[x] Frozen documents agree factually with the finished implementation.
[x] core.__init__ remains intentionally small.
[x] Working tree is clean after the checkpoint commit.
```

**Core v0 is closed.** No further implementation pass follows. Subsequent work builds through these sixteen concepts, eighteen derived constructions, and twenty laws — it does not reopen this substrate by default.
