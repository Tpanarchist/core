# Core — Laws

These are the twenty laws the ontology in `SPECIFICATION.md` is built to obey. They are not aspirations; where a law has a specific enforcement mechanism, that mechanism is named in `SPECIFICATION.md` and implemented in code, not left as a comment.

1. Identity is explicit.
2. Context-dependent information carries its context.
3. Observation is distinct from interpretation.
4. State is distinct from change.
5. Effects are distinguishable from computation.
6. Unknown is distinct from false.
7. Failure preserves information.
8. Transformations preserve provenance when provenance matters.
9. Nondeterminism must be controllable or observable.
10. Meaningful state changes should be inspectable.
11. Components compose through narrow capabilities.
12. Mechanism and policy remain separable.
13. Information is not silently discarded.
14. Contradictions remain visible until explicitly resolved.
15. Invariants are executable.
16. Long operations should permit interruption and inspection.
17. Concepts have predictable locations and names.
18. Simple operations should be simple; depth remains accessible.
19. Abstraction must reduce complexity rather than merely move it.
20. Nothing happens merely because a module was imported.

## Notes on the harder-to-enforce laws

**Law 5 — Effects are distinguishable from computation.** Realized as two separate types, not one: `EffectSpec` (declarative — what an operation *may* do) and `Effect` (a record that something *actually* happened), recorded through an explicit `EffectSink` passed at the execution boundary. `Transform.apply()` does not take a sink itself and does not claim to know which declared effect fired — an effectful callable receives whatever sink it needs through its own explicit API. No ambient `contextvars`-backed global log; declared-possible and actually-happened are never conflated into one record.

**Law 13 — Information is not silently discarded.** Enforced at several points rather than once: `Context.merge()` refuses on incompatible fields (returns `Result[Context, ContextConflict]`) instead of letting one side silently win; `Context.override()` produces an audited `ContextOverride` record of exactly what was replaced; `Error.cause` wraps rather than replaces a prior `Error`; `Provenance.ancestors()` reports `unresolved` references explicitly in its `AncestorReport` rather than letting a missing parent quietly vanish from the traversal.

**Law 14 — Contradictions remain visible until explicitly resolved.** `Contradiction` and `Resolution` are both immutable facts appended to a `ContradictionLog`, never a `Contradiction` mutated into a "resolved" state. `unresolved()` is a projection computed fresh over the log's append order every time it's asked, not a stored flag — so the full history of what was found and what was later decided about it is preserved rather than overwritten. `ContextConflict` (a structural Context-merge failure) is kept deliberately distinct from `Contradiction` (a semantic judgment about conflicting claims) — a failed merge doesn't automatically mean two assertions about reality contradict each other.

**Law 20 — Nothing happens merely because a module was imported.** Verified, not just asserted: `tests/architecture/test_import_side_effects.py` imports every `core.*` module in a fresh process and rejects filesystem writes, directory creation, subprocess launches, socket/network activity, environment mutation, UUID generation, wall-clock acquisition, and random-number generation during import — excluding Python's own import machinery's read-only operations.
