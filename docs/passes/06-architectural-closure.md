# Pass 6 preregistration — architectural closure

Pass 6 adds **no new semantic concepts, runtime capabilities, application features, or public APIs**. The v0 implementation is complete after Pass 5 (plus its Pipeline corrective, landed separately). Pass 6 exists only to prove the implementation as a whole obeys the frozen `SPECIFICATION.md`, `LAWS.md`, and `ARCHITECTURE.md`. If this pass uncovers a mismatch with an already-frozen invariant, correct that mismatch and add a regression test — it is not license to enlarge the ontology or introduce a new subsystem.

## Deliverables

```text
tests/architecture/test_import_graph.py
tests/architecture/test_import_side_effects.py
tests/architecture/_side_effect_harness.py
docs/passes/06-architectural-closure.md   (this file)
docs/V0_AUDIT.md
```

## 1. Import graph verification

Turns the `ARCHITECTURE.md` dependency table into an executable invariant: a module may have only the conceptual dependencies explicitly granted to it — checked via full AST walk (so a forbidden import under `TYPE_CHECKING` is caught exactly like a runtime one), not mere cycle detection.

The allowed map includes the Pass-5 `value` correction for `constraint`/`transform`. The actual module inventory (`src/core/*.py`, keyed by filename stem, `__init__` for the package root) must exactly equal the map's keys — a future `core/foo.py` can't silently escape enforcement, and the map can't silently drift stale.

Checked: every `Import`/`ImportFrom` edge (including relative, resolved to its absolute `core.<module>` target, and literal-string `importlib.import_module()`/`__import__()`); `from core import X` and its relative equivalent `from . import X` are rejected outright (importing a name through the package root, not a module's own canonical home); intra-Core wildcard imports (`from core.foo import *`) are rejected; the resulting graph is acyclic; and a fixed list of critical negative seams named in `ARCHITECTURE.md` (Trace/Provenance/Epistemic/Effect/Relation/State/Transform not importing what they're specifically forbidden from importing) hold — derived from the same edge-extraction the general check uses, not a separately hand-maintained assertion list.

## 2. Exhaustive import-side-effect verification

Turns law 20 into a package-wide property: nothing happens merely because a Core module was imported. Supersedes the pass-local `tests/semantics/_side_effects.py` checks as the *authoritative* version (those remain as useful local regressions); process isolation via a fresh subprocess per module is now an architectural testing rule, not a convenience — `importlib.reload()` is never used here, per the class-generation bug fixed in `5140cd3`.

A harness script (`_side_effect_harness.py`), run fresh per module via `subprocess.run`, guards every law-20-prohibited action before importing its target: `uuid.uuid1`/`uuid.uuid4`; `time.time`/`time.time_ns`; `time.monotonic`/`time.monotonic_ns`/`time.perf_counter`/`time.perf_counter_ns`; `random.random`/`randrange`/`randint`/`choice`/`choices`/`getrandbits` and `os.urandom`; `datetime.datetime.now`/`.utcnow` via a guarded subclass substituted into the `datetime` module (its C-level immutability means `.now` can't be patched directly — see the Pass 2/3 notes on this same limitation); and, via `sys.addaudithook`, `subprocess.Popen`, socket creation/connect/bind, filesystem-mutation events (`os.mkdir`/`rmdir`/`rename`/`replace`/`remove`/`unlink`), environment mutation (`os.putenv`/`unsetenv`), and `open` calls whose mode/flags permit a write (read-only opens — what Python's own import machinery legitimately does — are allowed). Each Core module gets its own fresh process so a failure identifies the offending module directly.

## 3. Full gate

After the architecture tests exist: `uv run pytest`, `uv run ruff check`, `uv run pyright` over the *entire* repository, all clean. No pass-specific subset substitutes for the whole suite; no global suppressions — a targeted one is acceptable only where narrow, documented, and consistent with the precedent already set (the `reportUnnecessaryIsInstance` boundary-check comments in `context.py`/`error.py`/`effect.py`).

## 4. Manual v0 audit (`docs/V0_AUDIT.md`)

Three tables, each row pointing at real code and real tests — no row invented merely for visual uniformity, and no concept skipped because its representation is conceptual rather than a class:

- **Table A** — all 16 ontology concepts → question → code home → representation/mechanism → test evidence → status.
- **Table B** — every derived construction named in `SPECIFICATION.md` → built from → code home → enforced invariant → test evidence → status, plus a separate subsection for concrete capability implementations (`UuidIdSource`, `SystemClock`, `SystemMonotonicClock`, `LamportClock`, `MemoryEffectSink`) so they're accounted for without being misdescribed as ontology.
- **Table C** — all 20 laws → concrete code enforcement (not "supported" — *where*) → test evidence → status, with particular attention to the laws easiest to satisfy superficially: identity explicitness (every `Ref` target is actually `Entity`-bearing), effect/computation separation (`EffectSpec` vs. `Effect`, never auto-recorded), unknown-vs-false (no later code reintroduced `None` as epistemic Unknown), failure-preserves-information (structured `cause` vs. foreign `exception`, capability failures propagate rather than being redescribed), nondeterminism control (every `uuid`/`time`/`random` use in `src/core` sits behind an explicit capability), information-not-discarded (Context conflict reporting, override audits, Provenance's `unresolved`, Pipeline lineage), contradiction visibility (append-only, `unresolved()` derived not stored), executable invariants (lower tiers validate locally, not only via `core.constraint`), and earned abstraction (every protocol has a real implementer).

Plus dedicated subsections: a **Ref-closure audit** (every `Ref`-targetable type confirmed `Entity`-bearing, `Transform` confirmed as the one-directional counterexample — carries an `Id`, targeted by nothing, and that's fine); a **time-family audit** (every temporal field in the implementation matches its frozen family — `WallInstant`/`Duration`/`Sequence`/etc., no generic "time reading" slipped through); a manual cross-check of the import graph against the automated one; a **public-surface audit** (`core.__init__` stays minimal, `py.typed` present); and a **frozen-document consistency audit** — narrow, factual corrections only (a delegated signature now known, a dependency edge already corrected, `wording` that claims different behavior from what's actually implemented) — never changing semantic meaning to match accidental code; if code violates frozen semantics, the code is fixed, not the document.

## 5. Correction policy

**Allowed**: fixing a forbidden dependency, a missing invariant check, an import-time side effect, a wrong Time family, a Ref-target lacking `Entity`, or a stale doc describing a superseded preregistered choice — each with a regression test. **Not allowed**: new ontology primitives, new general-purpose frameworks, filesystem/config/logging/network/scheduler/DI systems, new protocol families, or speculative public conveniences. A good idea outside v0 gets, at most, a one-line future note — it is not part of closure.

## 6. Closure checklist

```text
[ ] Pipeline Pass-5 corrective landed before Pass 6.
[ ] All semantic tests pass.
[ ] Import graph exactly obeys ARCHITECTURE.md; actual graph is acyclic.
[ ] Every Core module passes fresh-process import-side-effect verification.
[ ] Ruff clean; Pyright strict clean — whole repo.
[ ] All 16 concepts, every derived construction, every concrete v0 capability
    audited to a code/test home.
[ ] All 20 laws have concrete enforcement + test evidence.
[ ] Every Ref target is Entity-bearing; time-family assignments correct.
[ ] No specified concept orphaned; no public abstraction unearned/orphaned.
[ ] Frozen documents agree factually with the finished implementation.
[ ] core.__init__ remains intentionally small.
[ ] Working tree clean after the checkpoint commit.
```

When every box is satisfied, Core v0 is closed. No further implementation pass follows; subsequent work builds through this substrate rather than reopening it.
