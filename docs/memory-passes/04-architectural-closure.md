# Memory v0 — Pass 4 Preregistration

> Mirrors Core's own closure pass (`docs/passes/06-architectural-closure.md`,
> `docs/V0_AUDIT.md`) at the Memory layer. Read that pair first if anything
> here is under-specified — Pass 4 deliberately reuses its structure and,
> where the mechanism is generic, its actual code.

## Status

Pass 4 adds **no new semantic concepts, runtime capabilities, application
features, or public APIs**. Memory v0's implementation is complete after
Pass 3. Pass 4 exists only to prove the implementation as a whole obeys the
frozen `MEMORY_SPECIFICATION.md`, `MEMORY_LAWS.md`, `MEMORY_ARCHITECTURE.md`,
and `MEMORY_ADVERSARIAL_MATRIX.md`. `MEMORY_ADVERSARIAL_MATRIX.md` section Y
already records that Pass 3 resolved every open semantic decision and that
"Pass 4 is architectural closure/audit and is not expected to require new
open semantic decisions" — if this pass uncovers a mismatch with an
already-frozen invariant, correct that mismatch and add a regression test;
it is not license to enlarge the ontology or introduce a new subsystem.

Matrix sections this pass is specifically scoped to prove, on top of running
the full existing suite together as one gate:

- **T** (`IM-01`–`IM-10`) — import/dependency boundaries.
- **V** (`RF-01`–`RF-08`) — Ref-closure (which Memory constructions are, and
  are not, `Entity`-bearing / `Ref`-targetable).
- **W** (`IP-01`–`IP-10`) — Core's own information-preservation principle
  (the law behind Core's "failure preserves information" audit entry),
  applied at the Memory layer: nothing Memory does silently discards
  information a caller might need.
- **X** (`X-01`–`X-10`) — the cross-module integration scenarios already
  written out narratively in the matrix, turned into real tests.

Sections A–S and U (the per-module semantic matrix, and crash/reopen/
database-integrity) are already proven by Passes 1–3's own test suites —
Pass 4 does not re-derive them, only confirms via the full-suite gate (§3)
and the audit (§4) that they're still green and still accounted for.

## Deliverables

```text
tests/memory/architecture/test_import_graph.py
tests/memory/architecture/test_import_side_effects.py
docs/memory-passes/04-architectural-closure.md   (this file)
docs/MEMORY_V0_AUDIT.md
```

Plus: a regression test for any genuine gap the audit in §4 uncovers, added
to whichever existing test module the gap naturally belongs in (matching
Pass 3 Task 7's own precedent — new matrix-case coverage went into the
existing per-module test files, not a pile of new ones invented for
symmetry). No test file is preregistered for T/V/W/X beyond the two above;
§4's audit is what decides, case by case, whether existing coverage
already proves a case or a new test is needed.

## 1. Import graph verification

Turns `MEMORY_ARCHITECTURE.md`'s dependency table (the `| Tier | Module |
Owns | Depends on |` table) into an executable invariant, at the AST level,
exactly like Core's own `tests/architecture/test_import_graph.py` — a
forbidden import under `TYPE_CHECKING` must be caught exactly like a
runtime one, not mere cycle detection. The module inventory
(`src/memory/*.py`, keyed by filename stem, `__init__` for the package
root) must exactly equal the allowed-imports map's keys.

Unlike Core's own graph (whose modules only ever import each other), each
Memory module's allowed-edges set spans **two** namespaces: other `memory.*`
modules, and the specific `core.*` modules `MEMORY_ARCHITECTURE.md`
authorizes it to depend on. Both must be checked from the same AST walk —
an edge to an unauthorized `core.*` module is exactly as much a violation
as one to an unauthorized `memory.*` module. `core.*` itself is out of
scope for the *acyclicity*/tier check (Core is already closed and already
has its own import graph test); Memory's test only needs to confirm each
`core.*` edge is one `MEMORY_ARCHITECTURE.md` actually lists for that
module.

Checked, mirroring Core's own test exactly except for the two-namespace
allowance above: every `Import`/`ImportFrom` edge (including relative,
resolved to its absolute target, and literal-string
`importlib.import_module()`/`__import__()`); `from memory import X` and its
relative equivalent are rejected outright; intra-Memory wildcard imports
are rejected; the resulting `memory.*`-only subgraph is acyclic (tiers 0/1/2
per the dependency table); and the specific negative seams
`MEMORY_ARCHITECTURE.md` and the matrix's section T already name explicitly
hold:

| Case | Scenario | Required behavior |
|---|---|---|
| IM-01 | A tier-0 Memory module (`episode`/`recall`/`retention`/`belief`/`codec`) imports a `memory.*` sibling it doesn't need | Fails |
| IM-02 | Any `core.*` module imports `memory.*` | Fails (checked by grepping `src/core/*.py` for a `memory` import — Core's own closed import graph already can't have gained one, this is a regression guard) |
| IM-03 | `belief` imports `sqlite3` (or `sqlite_store`) | Fails |
| IM-04 | `recall` imports `store` | Fails |
| IM-05 | `codec` imports `sqlite3` | Fails |
| IM-06 | `store` imports `sqlite_store` (the inversion — tier 1 depending on tier 2) | Fails |
| IM-07 | `sqlite_store` imports only what `MEMORY_ARCHITECTURE.md`'s row for it explicitly lists | Pass |
| IM-08 | A module exists under `src/memory/` with no entry in the allowed-imports map | Module-inventory test fails |

Also confirm, as parametrized negative-seam cases the same way Core's own
`TestCriticalNegativeSeams` does: `sqlite_store` never imports
`memory.belief`, `core.state`, `core.trace`, or `core.transform` (the
specific forbidden edges `MEMORY_ARCHITECTURE.md`'s dependency-table note
already names), and no tier-0 module imports `store` or `sqlite_store`.

## 2. Exhaustive import-side-effect verification (IM-09, IM-10)

Turns Core's law 20 (nondeterminism control — every module import performs
no clock/randomness/filesystem/network/subprocess/env action) into a
package-wide property for `memory.*`, exactly as Core's Pass 6 did for
`core.*`. Reuses `tests/architecture/_side_effect_harness.py` as-is — it is
already fully generic (takes any dotted module path as its one CLI
argument, has no Core-specific logic anywhere in it) — rather than
duplicating roughly 140 lines of audit-hook/guard setup. The new
`tests/memory/architecture/test_import_side_effects.py` only needs its own
module-discovery function (`src/memory/*.py` → `memory.<stem>` /
`memory` for `__init__`) and a `subprocess.run` call per module against
that same harness script, parametrized one case per discovered module.

Supersedes `tests/memory/semantics/_memory_side_effects.py`'s per-module,
patch-based checks as the *authoritative* version (narrower: it only
guards `uuid.uuid4`/`time.time`/`time.monotonic`, the three patch targets
each existing call site passes explicitly) — those remain as useful local
regressions exactly as Core's own pass-local checks did, not replaced or
deleted.

## 3. Full gate

After the two architecture test files exist: `uv run pytest`, `uv run ruff
check`, `uv run pyright` over the entire repository, all clean — **except**
`tests/personal_finance`/`src/personal_finance`, which is unrelated,
independently-owned, in-progress work sharing this working tree and
outside Memory v0's scope entirely; exclude it from the gate run
(`--ignore=tests/personal_finance` for pytest; scope ruff/pyright
invocations to `src/core src/memory tests/architecture tests/memory` plus
whatever else already existed before this pass) rather than either
including it prematurely or letting its presence block Memory's own
closure. No pass-specific subset substitutes for the whole (scoped) suite;
no global suppressions — a targeted one is acceptable only where narrow,
documented, and consistent with existing precedent in this codebase.

## 4. Manual v0 audit (`docs/MEMORY_V0_AUDIT.md`)

Mirrors Core's `docs/V0_AUDIT.md` structure, scaled to what Memory actually
added — no row invented merely for visual uniformity, no case skipped
because its evidence is scattered across an existing test file rather than
a dedicated one:

- **Table A — the five derived constructions.** `Episode`, `RecallCandidate`,
  `WorkingSet`, `RetentionMark`/`RetentionLog` (counted as the one Retention
  construction per `MEMORY_SPECIFICATION.md`), `BeliefProjection` → built
  from → code home → enforced invariant → test evidence → status. Plus a
  separate subsection for the two supporting-machinery items
  `MEMORY_SPECIFICATION.md` explicitly distinguishes from the five
  (`PersistedValue`, `MemoryStore`) and their concrete v0 implementers
  (`InMemoryStore`, `SqliteMemoryStore`), so they're accounted for without
  being misdescribed as a sixth construction.
- **Table B — the 16 Memory laws** → concrete code enforcement (not
  "supported" — *where*, file and mechanism) → test evidence → status,
  cross-referenced against the adversarial-evidence citations
  `MEMORY_LAWS.md`'s own "Notes on the harder-to-enforce laws" section
  already provides for 13 of the 16 laws — confirm each citation still
  names real, passing tests, not just that it once did.
- **Ref-closure audit (matrix section V, `RF-01`–`RF-08`)** — for every
  Memory construction, confirm the correct side of "is it `Entity`-bearing
  and therefore legitimately `Ref`-targetable": `Episode` yes (carries its
  own caller-supplied `Id`, per `MEMORY_LAWS.md` law 12's own citation of
  this exact fact); `RecallCandidate`, `WorkingSet`, `RetentionMark`,
  `BeliefProjection` no — and confirm no API anywhere in `memory.*` lets a
  caller construct a `Ref` from one of these or from a SQLite storage-local
  sequence key (`RF-07`, `RF-08` — the storage-local key must never be
  promoted into something `Ref`-shaped).
- **Import graph manual cross-check** — read `MEMORY_ARCHITECTURE.md`'s
  dependency table by eye against the automated test's allowed-imports map,
  confirming they agree entry for entry (the automated test proves the
  *code* matches the *map*; this step proves the *map* matches the
  *frozen doc* — the same two-step Core's own audit performs).
- **Public-surface audit** — `memory.__init__` stays intentionally minimal
  (currently just a docstring + `__version__`, matching `core.__init__`'s
  own shape); `src/memory/py.typed` present (it already is).
- **Frozen-document consistency audit** — narrow, factual corrections only
  (a dependency edge already corrected in code but not yet in
  `MEMORY_ARCHITECTURE.md`'s table, wording that claims different behavior
  than what's actually implemented) — never changing semantic meaning to
  match accidental code; if code violates frozen semantics, the code is
  fixed, not the document. Specifically re-check the `sqlite_store`
  dependency row against the fix wave landed in this repository's Pass 3
  closure — it was corrected once already (a stale `episode` entry
  removed) and this audit is the natural place to confirm no other row has
  drifted the same way.
- **Cross-module integration scenarios (matrix section X, `X-01`–`X-10`)**
  — for each of the ten scenarios already written out narratively in
  `MEMORY_ADVERSARIAL_MATRIX.md`, confirm either an existing test already
  proves it (name the test) or write one. These are integration-shaped by
  nature (each crosses `belief`/`recall`/`retention`/`store` or
  `sqlite_store` boundaries in one scenario), so where a new test is
  needed it belongs in a new `tests/memory/integration/` directory rather
  than being wedged into one module's existing semantics file — but check
  for existing coverage first; several of the ten (e.g. X-01's
  no-temporal-carry-forward, X-04's archive-is-not-deletion) are plausibly
  already proven by Pass 1/2's own `belief`/`retention` test suites under
  different names, and X-07 (Episode ordering survives a SQLite round-trip)
  and X-06 (`UnsupportedPersistedValue` at the SQLite boundary specifically)
  are plausibly already covered by Pass 3's own Episode-transition and
  backend-equivalence tests.

## 5. Correction policy

**Allowed**: fixing a forbidden dependency, a missing invariant check, an
import-time side effect, a Ref-target incorrectly reachable/unreachable, or
a stale doc describing a superseded preregistered choice — each with a
regression test. **Not allowed**: new ontology primitives, new
general-purpose frameworks, new protocol families, or speculative public
conveniences. A good idea outside v0 gets, at most, a one-line future
note — it is not part of closure.

## 6. Closure checklist

```text
[x] All Passes 1-3 tests still pass (778 at the start of this pass).
[x] Import graph exactly obeys MEMORY_ARCHITECTURE.md's dependency table
    (both memory.* and core.* edges); actual memory.*-only subgraph is
    acyclic; every IM-01..08 negative/inventory seam holds.
[x] Every Memory module passes fresh-process import-side-effect
    verification (IM-09, IM-10), reusing tests/architecture/_side_effect_harness.py.
[x] Ruff clean; Pyright strict clean — core + memory + their tests +
    tests/architecture (tests/personal_finance excluded, unrelated).
[x] All five derived constructions + PersistedValue/MemoryStore (+
    InMemoryStore/SqliteMemoryStore as their v0 implementers) audited to a
    code/test home.
[x] All 16 Memory laws have concrete code enforcement + test evidence.
[x] Every Ref-targetable Memory construction is Entity-bearing (Episode);
    every non-Ref-targetable one confirmed not constructible as a Ref
    target, including SQLite's own storage-local sequence key (RF-01..08).
[x] Core's information-preservation principle holds at the Memory layer
    for all ten IP-01..10 cases, with test evidence.
[x] All ten X-01..10 cross-module scenarios have test evidence, existing
    or newly written.
[x] memory.__init__ remains intentionally small; py.typed present.
[x] Frozen documents (MEMORY_ARCHITECTURE.md's dependency table
    specifically) agree factually with the finished implementation.
[x] Working tree clean after the checkpoint commit.
```

See `docs/MEMORY_V0_AUDIT.md`'s own "Closure checklist" section for the
evidence annotation behind each box above.

When every box is satisfied, Memory v0 is closed. No further implementation
pass follows; subsequent work (a fifth pass, or an application layer like
`personal_finance` gaining a Memory integration) builds through this
substrate rather than reopening it — exactly the posture Core's own v0
closure established for Memory itself.
