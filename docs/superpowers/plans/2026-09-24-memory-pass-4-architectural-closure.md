# Memory Pass 4: Architectural Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Memory v0 by proving the finished Pass 1-3 implementation obeys the frozen `MEMORY_SPECIFICATION.md`/`MEMORY_LAWS.md`/`MEMORY_ARCHITECTURE.md`, closing matrix sections T (import/dependency), V (Ref-closure), W (information preservation), and X (cross-module integration) — no new semantic concepts or public APIs.

**Architecture:** Two new AST/subprocess-based architecture tests mirroring Core's own closure pass exactly (one reusing Core's existing generic harness unmodified); a handful of small gap-filling tests for the two genuine Ref-closure/information-preservation gaps a research pass found; two new cross-module integration tests for the two genuine gaps in section X; a written audit document citing test evidence for every law/construction/matrix case; a final scoped full-suite gate.

**Tech Stack:** Python 3.14, pytest, ruff, pyright (strict) — same as every prior pass. No new dependencies.

**Spec:** `docs/memory-passes/04-architectural-closure.md` (this plan's preregistration — itself built directly from the already-frozen `MEMORY_ARCHITECTURE.md` and `MEMORY_ADVERSARIAL_MATRIX.md`, which this pass does not modify).

## Global Constraints

- No new semantic concepts, runtime capabilities, or public APIs — this pass proves the existing theory, never extends it (prereg §"Status").
- `tests/personal_finance/` and `src/personal_finance/` are unrelated, independently-owned, in-progress work sharing this working tree. Never read, edit, `git add`, or include them in any command's scope. Every ruff/pyright/pytest invocation in this plan is explicitly scoped to exclude them — confirmed empirically that the bare (unscoped) commands currently fail on that code, not on anything in `core`/`memory` (prereg §3).
- Every new/modified test file must pass `uv run ruff check` and `uv run pyright` (strict) with zero findings before being committed.
- Follow each existing test file's established style (imports, module-level constants, class-per-scenario-group) rather than introducing a new convention.
- Every code block below was verified directly against the real repository before this plan was written (see each task's "Verified" note) — do not deviate from provided code without a documented reason.

---

### Task 1: Import graph verification

**Files:**
- Create: `tests/memory/architecture/test_import_graph.py`
- Test: (same file — the deliverable IS the test file)

**Interfaces:**
- Consumes: nothing from other tasks. Reads `src/memory/*.py` and `src/core/*.py` directly via `ast`.
- Produces: nothing later tasks depend on directly, but Task 6's audit doc cites this file by name as V0_AUDIT.md's "import graph manual cross-check" evidence.

**Verified:** the full allowed-imports map below was checked via a standalone script run directly against the real `src/memory/*.py` and `src/core/*.py` — every check (module inventory, forbidden edges, root-package imports, wildcards, acyclicity, Core-never-imports-Memory, all seven negative seams) passed with zero violations found. No implementation changes were needed; the code is already compliant.

- [ ] **Step 1: Create the test file**

```python
"""Architectural closure: the frozen MEMORY_ARCHITECTURE.md dependency
table is an executable property of the actual source, not merely
documentation. Mirrors tests/architecture/test_import_graph.py (Core's own
closure pass) exactly, except Memory modules span TWO namespaces
(memory.* and the specific core.* modules MEMORY_ARCHITECTURE.md
authorizes) instead of one.

See docs/memory-passes/04-architectural-closure.md, "1. Import graph
verification".
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC_MEMORY = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "src" / "memory"
_SRC_CORE = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "src" / "core"

# The frozen dependency table (MEMORY_ARCHITECTURE.md's "| Tier | Module |
# Owns | Depends on |" table). Each value holds fully-dotted targets
# spanning both namespaces ("core.identity", "memory.episode", ...) --
# unlike Core's own single-namespace map, a Memory module's allowed edges
# are never bare module-key strings.
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "__init__": set(),
    "episode": {"core.identity", "core.time", "core.context"},
    "recall": {"core.identity", "core.time", "core.context", "core.value"},
    "retention": {"core.identity", "core.time", "core.value"},
    "belief": {"core.identity", "core.context", "core.value", "core.epistemic", "core.result"},
    "codec": {"core.value", "core.identity", "core.time", "core.context"},
    "store": {
        "memory.episode", "memory.recall", "memory.retention", "memory.codec",
        "core.identity", "core.time", "core.context", "core.value", "core.epistemic",
        "core.observation", "core.event", "core.effect", "core.provenance", "core.error",
    },
    "sqlite_store": {
        "memory.store", "memory.codec", "memory.recall", "memory.retention",
        "core.identity", "core.time", "core.context", "core.value", "core.epistemic",
        "core.observation", "core.event", "core.effect", "core.provenance", "core.error",
    },
}


def _module_key_to_dotted(key: str) -> str:
    return "memory" if key == "__init__" else f"memory.{key}"


def _discover_module_keys() -> set[str]:
    return {path.stem for path in _SRC_MEMORY.glob("*.py")}


def _parse(module_key: str) -> ast.Module:
    path = _SRC_MEMORY / f"{module_key}.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _all_trees() -> dict[str, ast.Module]:
    return {key: _parse(key) for key in _discover_module_keys()}


def _resolve_from_import(node: ast.ImportFrom, current_dotted: str) -> str | None:
    """Resolve an ImportFrom's target to an absolute dotted path.

    Handles the flat ``memory/*.py`` layout: every module's containing
    package is exactly ``memory`` (level 1). Levels >= 2 would escape
    above ``memory`` and aren't meaningful in this layout. current_dotted
    is unused for level-0 (absolute) imports but kept for signature
    symmetry with Core's own equivalent function.
    """
    if node.level == 0:
        return node.module
    if node.level == 1:
        return f"memory.{node.module}" if node.module else "memory"
    return None


def _add_target(edges: set[str], dotted: str | None) -> None:
    if dotted is None:
        return
    if dotted in ("core", "memory"):
        edges.add(dotted)
    elif dotted.startswith("core.") or dotted.startswith("memory."):
        parts = dotted.split(".")
        edges.add(f"{parts[0]}.{parts[1]}")


def _dynamic_import_targets(tree: ast.AST) -> list[str]:
    """Literal-string ``importlib.import_module(...)``/``__import__(...)`` targets."""
    targets: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        is_import_module = (isinstance(func, ast.Attribute) and func.attr == "import_module") or (
            isinstance(func, ast.Name) and func.id == "import_module"
        )
        is_dunder_import = isinstance(func, ast.Name) and func.id == "__import__"
        if is_import_module or is_dunder_import:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                targets.append(first.value)
    return targets


def _dependency_edges(tree: ast.AST, current_dotted: str) -> set[str]:
    """Every distinct memory.X/core.X target this file's imports (static +
    literal-string dynamic) resolve to, as full two-segment dotted strings.
    """
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name in ("core", "memory")
                    or alias.name.startswith("core.")
                    or alias.name.startswith("memory.")
                ):
                    _add_target(edges, alias.name)
        elif isinstance(node, ast.ImportFrom):
            _add_target(edges, _resolve_from_import(node, current_dotted))
    for target in _dynamic_import_targets(tree):
        if target in ("core", "memory") or target.startswith("core.") or target.startswith("memory."):
            _add_target(edges, target)
    edges.discard(current_dotted)
    return edges


def _root_package_name_imports(tree: ast.AST) -> list[str]:
    """``from memory import X``/``from core import X`` (or the relative
    equivalent ``from . import X``) -- importing a name through a package
    root instead of its canonical module.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        is_absolute_root = node.level == 0 and node.module in ("core", "memory")
        is_relative_root = node.level == 1 and node.module is None
        if is_absolute_root or is_relative_root:
            found.extend(alias.name for alias in node.names)
    return found


def _wildcard_imports(tree: ast.AST, current_dotted: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        resolved = _resolve_from_import(node, current_dotted)
        if resolved and (
            resolved in ("core", "memory")
            or resolved.startswith("core.")
            or resolved.startswith("memory.")
        ):
            if any(alias.name == "*" for alias in node.names):
                found.append(resolved)
    return found


class TestModuleInventory:
    def test_discovered_modules_exactly_match_the_architecture_map(self) -> None:
        assert _discover_module_keys() == set(ALLOWED_IMPORTS.keys())


class TestImportEdgesAreAllowed:
    def test_every_import_edge_is_allowed(self) -> None:
        violations: dict[str, set[str]] = {}
        for key, tree in _all_trees().items():
            edges = _dependency_edges(tree, _module_key_to_dotted(key))
            forbidden = edges - ALLOWED_IMPORTS[key]
            if forbidden:
                violations[key] = forbidden
        assert not violations, f"forbidden import edge(s): {violations}"


class TestRootPackageAndWildcardRules:
    def test_no_module_imports_a_name_from_a_package_root(self) -> None:
        violations = {
            key: names
            for key, tree in _all_trees().items()
            if (names := _root_package_name_imports(tree))
        }
        assert not violations, (
            f"`from memory import X` / `from core import X` (or relative "
            f"equivalent) found in: {violations}"
        )

    def test_no_wildcard_imports(self) -> None:
        violations = {
            key: found
            for key, tree in _all_trees().items()
            if (found := _wildcard_imports(tree, _module_key_to_dotted(key)))
        }
        assert not violations, f"wildcard imports found in: {violations}"


class TestGraphAcyclicity:
    def test_actual_memory_only_subgraph_is_acyclic(self) -> None:
        # Only memory.* edges are graphed -- core.* is Core's own already-
        # closed, already-acyclic subgraph (proven by Core's own test), and
        # Core never imports Memory (TestCoreNeverImportsMemory below), so
        # mixing the two namespaces into one graph could never introduce a
        # real cycle, only noise.
        graph: dict[str, set[str]] = {}
        for key, tree in _all_trees().items():
            edges = _dependency_edges(tree, _module_key_to_dotted(key))
            graph[key] = {e.removeprefix("memory.") for e in edges if e.startswith("memory.")}

        white, gray, black = 0, 1, 2
        color = dict.fromkeys(graph, white)
        path: list[str] = []

        def visit(node: str) -> list[str] | None:
            color[node] = gray
            path.append(node)
            for neighbor in graph.get(node, ()):
                if color.get(neighbor) == gray:
                    return [*path[path.index(neighbor) :], neighbor]
                if color.get(neighbor, white) == white:
                    cycle = visit(neighbor)
                    if cycle:
                        return cycle
            path.pop()
            color[node] = black
            return None

        for node in graph:
            if color[node] == white:
                cycle = visit(node)
                assert cycle is None, f"cycle detected: {' -> '.join(cycle)}"


class TestCoreNeverImportsMemory:
    def test_no_core_module_imports_memory(self) -> None:
        violators: list[str] = []
        for path in sorted(_SRC_CORE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("memory"):
                    violators.append(path.name)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("memory"):
                            violators.append(path.name)
            for target in _dynamic_import_targets(tree):
                if target == "memory" or target.startswith("memory."):
                    violators.append(path.name)
        assert not violators, f"core module(s) importing memory: {violators}"


class TestCriticalNegativeSeams:
    @pytest.mark.parametrize(
        ("module", "forbidden"),
        [
            ("sqlite_store", "memory.belief"),
            ("sqlite_store", "core.state"),
            ("sqlite_store", "core.trace"),
            ("sqlite_store", "core.transform"),
            ("episode", "memory.store"),
            ("episode", "memory.sqlite_store"),
            ("recall", "memory.store"),
            ("recall", "memory.sqlite_store"),
            ("retention", "memory.store"),
            ("retention", "memory.sqlite_store"),
            ("belief", "memory.store"),
            ("belief", "memory.sqlite_store"),
            ("codec", "memory.store"),
            ("codec", "memory.sqlite_store"),
        ],
    )
    def test_negative_seam(self, module: str, forbidden: str) -> None:
        edges = _dependency_edges(_parse(module), _module_key_to_dotted(module))
        assert forbidden not in edges


class TestDynamicAndRelativeImportMechanism:
    """Unit tests for the extraction mechanism itself, against synthetic
    source -- none of these patterns exist in the real src/memory files
    today, but the mechanism must still be proven correct. Mirrors Core's
    own equivalent test class.
    """

    def test_import_module_literal_is_detected(self) -> None:
        tree = ast.parse("import importlib\nimportlib.import_module('core.identity')\n")
        assert _dynamic_import_targets(tree) == ["core.identity"]

    def test_dunder_import_literal_is_detected(self) -> None:
        tree = ast.parse("__import__('memory.episode')\n")
        assert _dynamic_import_targets(tree) == ["memory.episode"]

    def test_non_literal_dynamic_import_argument_is_not_matched(self) -> None:
        source = "import importlib\nname = 'core.identity'\nimportlib.import_module(name)\n"
        tree = ast.parse(source)
        assert _dynamic_import_targets(tree) == []

    def test_type_checking_guarded_import_is_detected(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from core.identity import Id\n"
        )
        tree = ast.parse(source)
        assert "core.identity" in _dependency_edges(tree, "memory.episode")

    def test_relative_import_resolves_to_its_memory_target(self) -> None:
        tree = ast.parse("from .episode import Episode\n")
        assert "memory.episode" in _dependency_edges(tree, "memory.store")

    def test_bare_relative_import_is_flagged_as_root_package_import(self) -> None:
        tree = ast.parse("from . import episode\n")
        assert _root_package_name_imports(tree) == ["episode"]

    def test_absolute_memory_root_package_import_is_flagged(self) -> None:
        tree = ast.parse("from memory import Episode\n")
        assert _root_package_name_imports(tree) == ["Episode"]

    def test_absolute_core_root_package_import_is_flagged(self) -> None:
        tree = ast.parse("from core import Id\n")
        assert _root_package_name_imports(tree) == ["Id"]

    def test_wildcard_memory_import_is_detected(self) -> None:
        tree = ast.parse("from memory.episode import *\n")
        assert _wildcard_imports(tree, "memory.store") == ["memory.episode"]

    def test_wildcard_core_import_is_detected(self) -> None:
        tree = ast.parse("from core.identity import *\n")
        assert _wildcard_imports(tree, "memory.store") == ["core.identity"]
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/memory/architecture/test_import_graph.py -v`
Expected: all tests PASS (verified directly against the real repo before this plan was written — no production code changes are expected).

- [ ] **Step 3: Ruff/Pyright**

Run: `uv run ruff check tests/memory/architecture/test_import_graph.py`
Run: `uv run pyright tests/memory/architecture/test_import_graph.py`
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add tests/memory/architecture/test_import_graph.py
git commit -m "Memory Pass 4: import graph verification (matrix T, IM-01..08)"
```

---

### Task 2: Import side-effects verification

**Files:**
- Create: `tests/memory/architecture/test_import_side_effects.py`
- Test: (same file)

**Interfaces:**
- Consumes: `tests/architecture/_side_effect_harness.py` (Core's own existing, already-committed, fully generic harness — takes one CLI argument, a dotted module path, and imports it with every law-20-prohibited action guarded; has zero Core-specific logic). Do not copy or modify this file — reference it by relative path from the new test file.
- Produces: nothing later tasks depend on.

**Verified:** ran the existing harness directly against all 8 real `memory`/`memory.*` modules (`memory`, `memory.episode`, `memory.recall`, `memory.retention`, `memory.belief`, `memory.codec`, `memory.store`, `memory.sqlite_store`) — every one exits 0 (no import-time side effect) with zero changes to the harness or to any Memory source file.

- [ ] **Step 1: Create the test file**

```python
"""Exhaustive, fresh-process import-side-effect verification for the
memory package -- Core's own law 20 (nondeterminism control) made
executable at the Memory layer, exactly as Core's Pass 6 did for core.*.

Reuses tests/architecture/_side_effect_harness.py UNMODIFIED (it is
already fully generic: one CLI argument, a dotted module path, no
Core-specific logic anywhere in it) rather than duplicating the ~140
lines of audit-hook/guard setup it contains.

Supersedes tests/memory/semantics/_memory_side_effects.py's per-module,
patch-based checks as the *authoritative* version (that helper only
guards uuid.uuid4/time.time/time.monotonic, the three targets each
existing call site names explicitly) -- those remain as useful local
regressions, not replaced or deleted.

See docs/memory-passes/04-architectural-closure.md, "2. Exhaustive
import-side-effect verification".
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "src" / "memory"
_HARNESS = pathlib.Path(__file__).resolve().parent.parent.parent / "architecture" / "_side_effect_harness.py"


def _discover_module_dotted_paths() -> list[str]:
    paths: list[str] = []
    for file in sorted(_SRC.glob("*.py")):
        paths.append("memory" if file.stem == "__init__" else f"memory.{file.stem}")
    return paths


@pytest.mark.parametrize("module", _discover_module_dotted_paths())
def test_importing_module_has_no_side_effects(module: str) -> None:
    result = subprocess.run(
        [sys.executable, str(_HARNESS), module],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"importing {module} triggered a prohibited side effect:\n{result.stderr}"
    )
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/memory/architecture/test_import_side_effects.py -v`
Expected: 8 tests PASS (one per discovered `memory`/`memory.*` module).

- [ ] **Step 3: Ruff/Pyright**

Run: `uv run ruff check tests/memory/architecture/test_import_side_effects.py`
Run: `uv run pyright tests/memory/architecture/test_import_side_effects.py`
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add tests/memory/architecture/test_import_side_effects.py
git commit -m "Memory Pass 4: import side-effect verification (matrix T, IM-09/10)"
```

---

### Task 3: Ref-closure gap-filling (matrix section V, RF-01..08)

**Files:**
- Modify: `tests/memory/semantics/test_recall.py` (add one test to `TestWorkingSetConstruction`, RF-03)
- Modify: `tests/memory/semantics/test_retention.py` (add `Entity` import + one new small test class, RF-04)
- Modify: `tests/memory/semantics/test_belief.py` (add `Entity` import + one test to `TestBeliefProjectionConstructorInvariants`, RF-05)
- Modify: `tests/memory/semantics/test_sqlite_store.py` (add one new test class, RF-07 + RF-08)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: four new/extended tests Task 6's audit doc cites by name for matrix section V.

A research pass (read `MEMORY_ADVERSARIAL_MATRIX.md` section V, lines ~554-566, for the full RF-01..08 case list) found RF-01, RF-02, and RF-06 already proven by existing tests (`test_episode.py::TestConstruction`, `test_recall.py::TestRecallCandidate::test_rc_not_entity_bearing_in_v0`, and `test_store.py::test_pa_09_10_11_public_surface_matches_protocol_exactly` respectively — Task 6 cites these, no new test needed for them). RF-03, RF-04, RF-05, RF-07, and RF-08 are genuine gaps; this task closes all five.

**Verified:** every assertion below was run directly against the real `memory.recall`/`memory.retention`/`memory.belief`/`memory.sqlite_store` — all pass as written. The RF-08 expected-surface set specifically needed one correction versus a naive copy of `InMemoryStore`'s own lockdown test: `SqliteMemoryStore` legitimately adds `close` to its public surface (resource lifecycle `InMemoryStore` doesn't need) — confirmed by direct comparison, not assumed.

- [ ] **Step 1: RF-03 — add to `tests/memory/semantics/test_recall.py`'s `TestWorkingSetConstruction` class**

`Entity` is already imported at the top of this file (`from core.identity import Entity, Id, Ref`). Add this method to the existing `TestWorkingSetConstruction` class (do not create a new class):

```python
    def test_rf_03_working_set_not_entity_bearing(self) -> None:
        # MEMORY_ADVERSARIAL_MATRIX.md RF-03: a WorkingSet Ref is impossible
        # -- WorkingSet carries no id field, exactly like RecallCandidate
        # (see test_rc_not_entity_bearing_in_v0 above).
        ws = WorkingSet(capacity=1, admitted=())
        assert not isinstance(ws, Entity)
```

- [ ] **Step 2: RF-04 — add to `tests/memory/semantics/test_retention.py`**

This file does not yet import `Entity`. Change:

```python
from core.identity import Id, Namespace, Ref
```

to:

```python
from core.identity import Entity, Id, Namespace, Ref
```

Then add a new class (place it after `TestHistorySnapshot`, before `TestImportSideEffects`):

```python
class TestRefClosure:
    def test_rf_04_retention_mark_not_entity_bearing(self) -> None:
        # MEMORY_ADVERSARIAL_MATRIX.md RF-04: a RetentionMark Ref is
        # impossible -- RetentionMark carries no id field.
        mark = RetentionMark(item=Ref(id=Id(ITEM_KIND, "x")), accessibility=ACTIVE, at=T1)
        assert not isinstance(mark, Entity)
```

- [ ] **Step 3: RF-05 — add to `tests/memory/semantics/test_belief.py`**

This file does not yet import `Entity`. Change:

```python
from core.identity import Id, Namespace, Ref
```

to:

```python
from core.identity import Entity, Id, Namespace, Ref
```

Then add this method to the existing `TestBeliefProjectionConstructorInvariants` class:

```python
    def test_rf_05_belief_projection_not_entity_bearing(self) -> None:
        # MEMORY_ADVERSARIAL_MATRIX.md RF-05: a BeliefProjection Ref is
        # impossible -- BeliefProjection carries no id field.
        projection = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(), conflict_entries=(),
        )
        assert not isinstance(projection, Entity)
```

(`BALANCE` at line 33, `SUBJECT` at line 39, `ctx()` at line 63, and `MONDAY` at line 67 are all already defined at module level in this file — confirmed present under these exact names, use them as-is.)

- [ ] **Step 4: RF-07 + RF-08 — add to `tests/memory/semantics/test_sqlite_store.py`**

Add a new class (place it near `TestProtocolConformance`, since RF-08 extends that same public-surface-lockdown idea to the SQLite backend):

```python
class TestRefClosure:
    def test_rf_08_sqlite_memory_store_public_surface_matches_protocol_plus_close(
        self, tmp_path: Path
    ) -> None:
        # MEMORY_ADVERSARIAL_MATRIX.md RF-08: an application wanting to
        # reference a non-Entity storage row must reference some real
        # Entity instead -- storage position is never promoted. Proven the
        # same way test_pa_09_10_11_public_surface_matches_protocol_exactly
        # proves it for InMemoryStore: there is no OTHER public method at
        # all through which a storage-local position could leak out.
        # Unlike InMemoryStore (no lifecycle to manage), SqliteMemoryStore
        # legitimately adds `close` to its own public surface -- confirmed
        # by direct comparison, not assumed.
        store = SqliteMemoryStore(tmp_path / "rf08.sqlite")
        try:
            public_attrs = {name for name in dir(store) if not name.startswith("_")}
            protocol_methods = {
                "persist", "resolve", "retrieve", "claims_for", "conflicts_for",
                "retention_for", "create_episode", "append_episode", "close_episode",
                "close",
            }
            assert public_attrs == protocol_methods
        finally:
            store.close()

    def test_rf_07_sqlite_local_sequence_key_is_a_plain_int_never_entity(
        self, tmp_path: Path
    ) -> None:
        # MEMORY_ADVERSARIAL_MATRIX.md RF-07: the SQLite local sequence key
        # (memory_ops.seq) does not satisfy Entity -- it is a bare int,
        # never wrapped in an Id/Ref-shaped value anywhere in this module.
        store = SqliteMemoryStore(tmp_path / "rf07.sqlite")
        try:
            obs: Observation[object] = Observation(
                id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x",
                at=AT, source="s", context=CTX,
            )
            store.persist(obs)
            conn = sqlite3.connect(str(tmp_path / "rf07.sqlite"))
            seq_value = conn.execute("SELECT seq FROM memory_ops LIMIT 1").fetchone()[0]
            conn.close()
            assert type(seq_value) is int
            assert not isinstance(seq_value, Entity)
        finally:
            store.close()
```

This needs `Entity` imported. Line 24 of `test_sqlite_store.py` currently reads `from core.identity import Id, Namespace, Ref` — confirmed exact; change it to `from core.identity import Entity, Id, Namespace, Ref`. `SUBJECT`, `AT`, `CTX`, `Kind`, `Observation`, `sqlite3`, and `Path` are all already available in this file (used throughout).

- [ ] **Step 5: Run all four files**

Run: `uv run pytest tests/memory/semantics/test_recall.py tests/memory/semantics/test_retention.py tests/memory/semantics/test_belief.py tests/memory/semantics/test_sqlite_store.py -v -k "rf_0"`
Expected: 5 new tests PASS (rf_03, rf_04, rf_05, rf_07, rf_08).

Run the full four files without the `-k` filter too, to confirm nothing else broke:
Run: `uv run pytest tests/memory/semantics/test_recall.py tests/memory/semantics/test_retention.py tests/memory/semantics/test_belief.py tests/memory/semantics/test_sqlite_store.py`
Expected: all PASS, same total-minus-5 as before this task plus the 5 new ones.

- [ ] **Step 6: Ruff/Pyright**

Run: `uv run ruff check tests/memory/semantics/test_recall.py tests/memory/semantics/test_retention.py tests/memory/semantics/test_belief.py tests/memory/semantics/test_sqlite_store.py`
Run: `uv run pyright tests/memory/semantics/test_recall.py tests/memory/semantics/test_retention.py tests/memory/semantics/test_belief.py tests/memory/semantics/test_sqlite_store.py`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add tests/memory/semantics/test_recall.py tests/memory/semantics/test_retention.py tests/memory/semantics/test_belief.py tests/memory/semantics/test_sqlite_store.py
git commit -m "Memory Pass 4: Ref-closure gap-filling (matrix V, RF-03/04/05/07/08)"
```

---

### Task 4: Information-preservation gap-filling (matrix section W, IP-06)

**Files:**
- Modify: `tests/memory/semantics/test_codec.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: one new test Task 6's audit doc cites for IP-06.

A research pass (read `MEMORY_ADVERSARIAL_MATRIX.md` section W, lines ~569-582, for the full IP-01..10 case list) found nine of the ten cases already proven — several via a combination of existing tests rather than one dedicated test, which is fine (Task 6 cites the combination). IP-06 ("Codec encounters unsupported path → Path + offending type/value information retained in error safely") is the one genuine gap: existing tests (`test_cd_11`, `test_cd_12`, `test_cd_13` in this file) check `UnsupportedPersistedValue.path` precisely but none of them check `.value` (the offending object itself) is actually retained and accessible, even though `UnsupportedPersistedValue.__init__` does store it (`src/memory/codec.py:43`, `self.value = value`).

**Verified:** ran directly against the real `memory.codec` — `exc.value is offender` (identity-equal to the exact object passed in, not a copy/repr/stringification) and `exc.path == ("a",)` both hold as written below.

- [ ] **Step 1: Add to `tests/memory/semantics/test_codec.py`**

Place this immediately after the existing `test_cd_11_mapping_containing_list_is_rejected_with_path` test (same class):

```python
    def test_ip_06_offending_value_itself_is_retained_not_just_its_path(self) -> None:
        # MEMORY_ADVERSARIAL_MATRIX.md IP-06: path + offending type/value
        # information must be retained in the error safely -- test_cd_11/
        # test_cd_12/test_cd_13 above all check .path precisely but none
        # checks .value is the actual offending object, not a copy, repr,
        # or string fallback.
        class Exotic:
            pass

        offender = Exotic()
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value({"a": offender})
        assert exc_info.value.value is offender
        assert exc_info.value.path == ("a",)
```

(`pytest`, `UnsupportedPersistedValue`, and `as_persisted_value` are already imported at the top of this file — used throughout the existing `test_cd_*` tests.)

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v -k "ip_06"`
Expected: PASS.

Run: `uv run pytest tests/memory/semantics/test_codec.py`
Expected: all PASS.

- [ ] **Step 3: Ruff/Pyright**

Run: `uv run ruff check tests/memory/semantics/test_codec.py`
Run: `uv run pyright tests/memory/semantics/test_codec.py`
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add tests/memory/semantics/test_codec.py
git commit -m "Memory Pass 4: information-preservation gap-filling (matrix W, IP-06)"
```

---

### Task 5: Cross-module integration scenarios (matrix section X, X-03 + X-10)

**Files:**
- Create: `tests/memory/integration/test_cross_module_scenarios.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: two new tests Task 6's audit doc cites for X-03 and X-10.

A research pass (read `MEMORY_ADVERSARIAL_MATRIX.md` section X, lines ~586-845, for the full narrative scenario of all ten X cases) found X-01, X-02, X-05, X-07, X-08, X-09 already proven, several with literal matching test names (`test_x_01_changing_balance_without_contradiction`, `test_x_08_cross_predicate_conflict_still_relevant`, `test_x_09_resolved_and_unresolved_conflicts_coexist` in `test_belief.py`), and X-04/X-06 proven via a combination of existing tests across `test_store.py`/`test_sqlite_store.py` (Task 6 cites all of these by name — no new test needed for any of them). X-03 ("Retrieval is not belief — attention ≠ truth") and X-10 ("Forgetting vs failure-to-recall") are genuine gaps: no existing test combines `admit()`/`WorkingSet` with `belief_state()` (X-03), and no existing test combines a persisted, ACTIVE-retention item with a failed lexical query in one place while also confirming retention still reports ACTIVE (X-10).

This is the first file in a new `tests/memory/integration/` directory (no `__init__.py` needed — this repo's `tests/` tree has none anywhere, pytest's rootless import mode discovers every directory under `testpaths = ["tests"]` in `pyproject.toml` without one).

**Verified:** both scenarios below were run directly against the real `memory.store`/`memory.recall`/`memory.belief` — every assertion holds exactly as written, including the specific claim ordering X-03 depends on (the false, lexically-matching claim genuinely ranks first in `retrieve()`'s output, and `belief_state()` genuinely sees both claims regardless of what `admit()` narrowed attention to).

- [ ] **Step 1: Create the directory and file**

```python
"""Cross-module integration scenarios: MEMORY_ADVERSARIAL_MATRIX.md
section X (X-01..10), the minimum cross-module adversarial scenarios --
each crosses several semantic boundaries (belief/recall/retention/store),
so these live in their own integration-shaped test module rather than
being wedged into any one module's own semantics file.

Only X-03 and X-10 are new here -- the other eight of the ten X cases
already have real test coverage elsewhere (belief/store/sqlite_store's own
semantics test files); see docs/MEMORY_V0_AUDIT.md for the full citation
list, one row per case.

See docs/memory-passes/04-architectural-closure.md, "4. Manual v0 audit",
the "Cross-module integration scenarios" subsection.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.context import Context
from core.epistemic import Claim
from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind, Known
from memory.belief import belief_state
from memory.recall import admit
from memory.retention import ACTIVE
from memory.store import InMemoryStore, RetentionMark, RetrievalQuery

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
CTX = Context(as_of=AT)
SUBJECT = Id(Kind("t.subject"), "s1")
AGENT = Id(Kind("t.agent"), "a1")
PREDICATE = Kind("t.predicate")


class TestX03RetrievalIsNotBelief:
    def test_false_lexically_perfect_match_ranks_first_but_belief_state_unaffected(
        self,
    ) -> None:
        store = InMemoryStore()
        false_claim = Claim(
            id=Id(Kind("t.claim"), "false1"), subject=SUBJECT, predicate=PREDICATE,
            value=Known("findme wrong"), context=CTX, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        true_claim = Claim(
            id=Id(Kind("t.claim"), "true1"), subject=SUBJECT, predicate=PREDICATE,
            value=Known("correct"), context=CTX, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        store.persist(false_claim)
        store.persist(true_claim)

        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert len(candidates) >= 1
        assert candidates[0].item == Ref(id=false_claim.id)

        # attention ≠ truth: WorkingSet(capacity=1) may therefore contain
        # only the false claim.
        working_set, _excluded = admit(candidates, capacity=1)
        assert working_set.admitted == (candidates[0],)

        # This must NOT alter persisted belief/conflict state -- belief_state
        # is computed from claims_for(), never from what retrieval/attention
        # happened to surface.
        claims_for_subject = store.claims_for(SUBJECT, PREDICATE)
        projection = belief_state(
            subject=SUBJECT, predicate=PREDICATE, query_context=CTX,
            claims=claims_for_subject, conflict_entries=(),
        )
        assert len(projection.candidates) == 2


class TestX10ForgettingVsFailureToRecall:
    def test_active_retention_survives_a_failed_lexical_query(self) -> None:
        store = InMemoryStore()
        claim = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=PREDICATE,
            value=Known("stored value"), context=CTX, asserted_by=AGENT,
            evidence_refs=(), at=AT,
        )
        store.persist(claim)
        store.persist(RetentionMark(item=Ref(id=claim.id), accessibility=ACTIVE, at=AT))

        no_match = store.retrieve(
            RetrievalQuery(context=CTX, text="nonexistent-query-text"), retrieved_at=AT
        )
        assert no_match == ()

        # A failed retrieval says only "this query didn't surface a match"
        # -- it must never be conflated with forgotten/archived/nonexistent.
        retention = store.retention_for(claim.id)
        assert len(retention) == 1
        assert retention[0].accessibility == ACTIVE
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/memory/integration/test_cross_module_scenarios.py -v`
Expected: both tests PASS.

- [ ] **Step 3: Ruff/Pyright**

Run: `uv run ruff check tests/memory/integration/test_cross_module_scenarios.py`
Run: `uv run pyright tests/memory/integration/test_cross_module_scenarios.py`
Expected: both clean.

- [ ] **Step 4: Commit**

```bash
git add tests/memory/integration/test_cross_module_scenarios.py
git commit -m "Memory Pass 4: cross-module integration scenarios (matrix X, X-03/X-10)"
```

---

### Task 6: Manual v0 audit (`docs/MEMORY_V0_AUDIT.md`)

**Files:**
- Create: `docs/MEMORY_V0_AUDIT.md`

**Interfaces:**
- Consumes: the test files created/modified by Tasks 1-5 (cited by exact name — verify each citation is real by actually running/grepping for it, don't transcribe blindly), plus `MEMORY_SPECIFICATION.md`, `MEMORY_LAWS.md`, `MEMORY_ARCHITECTURE.md`, `MEMORY_ADVERSARIAL_MATRIX.md`.
- Produces: the closure evidence Task 7's checklist verification reads.

This task must run AFTER Tasks 1-5 are committed (it cites their exact file/test names). Mirrors Core's own `docs/V0_AUDIT.md` structure, scaled to what Memory actually added (5 derived constructions rather than 16 ontology concepts, since Memory adds no new ontology — see `MEMORY_SPECIFICATION.md`'s own "Derived constructions" section).

A draft of every table row is given below, pre-researched against the real repository. **Verify, don't just transcribe**: for every "Test evidence" cell, actually confirm that test exists and currently passes (`uv run pytest <file>::<class>::<method> -v` or a grep for the method name) before writing it into the table — if a citation given below turns out to be stale or wrong, fix it; this document's whole purpose is to be factually accurate evidence, not a restatement of this plan's own beliefs.

- [ ] **Step 1: Write Table A — the five derived constructions**

```markdown
# Memory v0 — Closure Audit

Mirrors Core's own `docs/V0_AUDIT.md` at the Memory layer. See
`docs/memory-passes/04-architectural-closure.md` for what this pass
covers and why.

## Table A — the five derived constructions

| Construction | Built from | Code home | Enforced invariant | Test evidence | Status |
|---|---|---|---|---|---|
| `Episode` | `Id` + `subject: Id \| Ref` + `Context` + ordered `Ref` items + `WallInstant` open/close | `episode.py` | Caller-supplied `Id` required (never self-allocated); append-only until closed; item order is append-call order, never sorted by wall time (law 7) | `test_episode.py`; SQLite round-trip specifically: `test_sqlite_store.py::TestEpisodeSqliteTransitions::test_es04_append_call_order_survives_reversed_member_timestamps` | Closed |
| `RecallCandidate` | `Ref` + `Context` + `relevance: tuple[Kind, ...]` + `WallInstant` | `recall.py` | `relevance` non-empty, no duplicate Kinds; not `Entity`-bearing (RF-02) | `test_recall.py::TestRecallCandidate` | Closed |
| `WorkingSet` | `capacity: int` + `admitted: tuple[RecallCandidate, ...]` | `recall.py` (`admit()`) | `admitted` never exceeds `capacity`; overflow returned as `excluded`, never silently dropped (law 6); no reranking/scoring — caller order preserved (law 7); not `Entity`-bearing (RF-03) | `test_recall.py::TestWorkingSetAdmission`, `TestWorkingSetConstruction` (incl. `test_rf_03_working_set_not_entity_bearing`, Task 3) | Closed |
| `RetentionMark` / `RetentionLog` | `Ref` + `accessibility: Kind` + `WallInstant` + optional `rationale`, append-only sequence with last-mark-wins projection | `retention.py` | Append-only; `current()` is a projection over append order, never sorted by `at` and never a stored flag (law 5); not `Entity`-bearing (RF-04) | `test_retention.py` (incl. `TestRefClosure::test_rf_04_retention_mark_not_entity_bearing`, Task 3) | Closed |
| `BeliefProjection` | `Id \| Ref` + `Kind` (predicate) + `Context` (query) + `Kind` (status) + `tuple[Claim, ...]` + `tuple[Contradiction \| Resolution, ...]` | `belief.py` (`belief_state()`) | Mechanical-only: never reads `Claim.at` (law 4), never parses `Resolution.rationale` (law 8); keyed by subject *and* predicate (law 10); not `Entity`-bearing (RF-05) | `test_belief.py` (incl. `TestBeliefProjectionConstructorInvariants::test_rf_05_belief_projection_not_entity_bearing`, Task 3) | Closed |

### Supporting machinery (not counted among the five)

| Item | Category | Code home | Test evidence | Status |
|---|---|---|---|---|
| `PersistedValue` | semantic value-domain refinement (same category as Core's own `Kind`/`Maybe`) | `codec.py` | `test_codec.py` | Closed |
| `MemoryStore` (protocol) | Capability-like protocol (same category as Core's own `EffectSink`) | `store.py` | `test_store.py::TestProtocolConformance`-equivalent coverage (`isinstance` checks), `test_sqlite_store.py::TestProtocolConformance` | Closed |
| `InMemoryStore` | concrete v0 implementer of `MemoryStore` | `store.py` | `test_store.py` (whole file) | Closed |
| `SqliteMemoryStore` | concrete, durable v0 implementer of `MemoryStore` | `sqlite_store.py` | `test_sqlite_store.py` (whole file) | Closed |
```

- [ ] **Step 2: Write Table B — the 16 Memory laws**

Confirm each "Code enforcement" and "Test evidence" cell against the real files before writing it (several are drawn directly from `MEMORY_LAWS.md`'s own "Notes on the harder-to-enforce laws" section, which already cites adversarial evidence for 13 of the 16 — verify those citations still name real, passing tests; laws 6, 7, and 10 aren't in that notes section and need their code/test homes determined fresh, drafted below):

```markdown
## Table B — the 16 Memory laws

| # | Law | Code enforcement | Test evidence | Status |
|---|---|---|---|---|
| 1 | Memory never rewrites or deletes a Core fact | No delete operation anywhere in Memory's surface; `RetentionLog` is itself append-only | `test_retention.py`, `test_store.py::TestRetrievalRetention` | Closed |
| 2 | Retrieval establishes candidacy, not truth | `RecallCandidate` carries no truth claim; `BeliefProjection` computed independently from stored state | `test_recall.py`, `test_belief.py`; X-03 (`tests/memory/integration/test_cross_module_scenarios.py`, Task 5) | Closed |
| 3 | Similarity is not identity | `RecallCandidate.relevance: tuple[Kind, ...]` names match *kind*, never a bare score | `test_recall.py::TestRecallCandidate` | Closed |
| 4 | Recency alone never establishes epistemic precedence | `belief_state()` filters by subject/predicate/Context only; `Claim.at` never read as a tiebreaker | `test_belief.py::TestContextAndTime`; X-01 | Closed |
| 5 | Accessibility is not existence | `RetentionLog.current()` changes only default retrieval visibility; `ARCHIVED` item remains fully persisted | `test_store.py::TestRetrievalRetention`; X-04 | Closed |
| 6 | Attention (`WorkingSet`) is bounded, overflow always reported, never silently dropped | `admit()` returns `(WorkingSet, excluded)` — the excluded tuple IS the report | `test_recall.py::TestWorkingSetAdmission::test_ws_01_capacity_smaller_than_candidates_splits_exactly` | Closed |
| 7 | Ordering belongs to the construction that owns it | `Episode` preserves append-call order, never sorted by wall time; `admit()` performs no reranking | X-07 (`test_es04_append_call_order_survives_reversed_member_timestamps`); `admit()`'s own docstring/tests in `test_recall.py` | Closed |
| 8 | Conflict is surfaced, never adjudicated | `BeliefProjection` never reads `Resolution.rationale` as structured data, ever | `test_belief.py::TestContradiction`; X-02, X-09 | Closed |
| 9 | A resolved `Contradiction` is not the same as a structured adjudication | `RESOLVED_OPAQUE_CONFLICT` distinguishes from both unresolved conflict and mechanical determination | `test_belief.py::TestContradiction` | Closed |
| 10 | Belief projection is keyed by subject *and* predicate, granted only where Core's `Context`/time make the answer mechanical | `belief_state()`'s required `subject`/`predicate` params + `Context.merge()`-based compatibility check | `test_belief.py::TestContextAndTime` (e.g. `test_ct_01_differing_as_of_is_a_context_conflict`) | Closed |
| 11 | Memory performs no temporal carry-forward | Direct consequence of `Context.merge()`: differing `as_of` is always a conflict, never a fill-in | `test_belief.py::TestContextAndTime`; X-01 | Closed |
| 12 | Persistence eligibility ≠ Core `Entity`; persistence never manufactures identity | `Resolution`/`RetentionMark` persisted with a storage-local sequence key, never promoted to `Id`/`Ref` | `test_store.py::PA-01..11`, `ID-01..06`; RF-01..08 (matrix V, Task 3) | Closed |
| 13 | Unsupported durable values fail explicitly | `as_persisted_value()` validates against the closed `PersistedValue` domain, raises with exact path | `test_codec.py::CD-01..24`, `FL-01..07`, incl. `test_ip_06_offending_value_itself_is_retained_not_just_its_path` (Task 4) | Closed |
| 14 | Lexical indexing never invents searchable text | Only already-`str`-typed fields (or `object` fields whose encoding happens to be `str`) are indexed | `test_sqlite_store.py::TestFtsIndexing` (`FT-01..14`) | Closed |
| 15 | Storage backends do not own semantic policy | `belief_state()`/`admit()` are pure functions over already-fetched data; `InMemoryStore`/`SqliteMemoryStore` required semantically identical | `test_sqlite_store.py::TestBackendEquivalence` (`BE-01..08`), `CL-01..11`, `test_store.py` (`MS-01..06`) | Closed |
| 16 | Failure to retrieve is not evidence of absence, deletion, or falsehood | A no-candidate retrieval result is never conflated with forgotten/archived/nonexistent/false | `test_store.py::TestRetrievalRetention` (`RR-10`); X-10 (`tests/memory/integration/test_cross_module_scenarios.py`, Task 5) | Closed |
```

- [ ] **Step 3: Write the Ref-closure audit subsection**

```markdown
## Ref-closure audit (matrix section V, RF-01..08)

| Case | Scenario | Result | Evidence |
|---|---|---|---|
| RF-01 | Episode Ref | Valid — Episode carries `Id` | `test_episode.py::TestConstruction` |
| RF-02 | RecallCandidate Ref attempted | Impossible — not `Entity`-bearing | `test_recall.py::TestRecallCandidate::test_rc_not_entity_bearing_in_v0` |
| RF-03 | WorkingSet Ref attempted | Impossible — not `Entity`-bearing | `test_recall.py::TestWorkingSetConstruction::test_rf_03_working_set_not_entity_bearing` (Task 3) |
| RF-04 | RetentionMark Ref attempted | Impossible — not `Entity`-bearing | `test_retention.py::TestRefClosure::test_rf_04_retention_mark_not_entity_bearing` (Task 3) |
| RF-05 | BeliefProjection Ref attempted | Impossible — not `Entity`-bearing | `test_belief.py::TestBeliefProjectionConstructorInvariants::test_rf_05_belief_projection_not_entity_bearing` (Task 3) |
| RF-06 | Resolution persisted | Persistence does not make it Ref-targetable — no `id` field on Core's own `Resolution` | `test_store.py::test_pa_09_10_11_public_surface_matches_protocol_exactly` |
| RF-07 | SQLite local sequence key exists | Does not satisfy Entity — plain `int`, never wrapped | `test_sqlite_store.py::TestRefClosure::test_rf_07_sqlite_local_sequence_key_is_a_plain_int_never_entity` (Task 3) |
| RF-08 | Application wants to reference a non-Entity storage row | Must reference a real Entity — storage position never promoted; confirmed no other public API path exists on either backend | `test_store.py::test_pa_09_10_11_public_surface_matches_protocol_exactly`, `test_sqlite_store.py::TestRefClosure::test_rf_08_sqlite_memory_store_public_surface_matches_protocol_plus_close` (Task 3) |
```

- [ ] **Step 4: Write the information-preservation audit subsection**

```markdown
## Information-preservation audit (matrix section W, IP-01..10)

Core's own "failure preserves information" principle, applied at the
Memory layer — nothing Memory does silently discards information a caller
might need.

| Case | Scenario | Result | Evidence |
|---|---|---|---|
| IP-01 | WorkingSet over capacity | Excluded returned, never dropped | `test_recall.py::TestWorkingSetAdmission` |
| IP-02 | Contradiction resolved | Original Contradiction preserved | `test_belief.py::TestContradiction` |
| IP-03 | Retention changes | Prior marks preserved (append-only) | `test_retention.py::TestDefaultAndBasicTransitions` |
| IP-04 | Episode closed | Items preserved | `test_store.py::TestEpisodeStoreSurface` |
| IP-05 | Retrieval cannot resolve a referenced Claim | Missing Ref stays explicit (`None`, never fabricated) | `test_store.py::test_resolve_missing_id_returns_none`, `test_sqlite_store.py::TestQueryDelegation::test_resolve_missing_returns_none` |
| IP-06 | Codec encounters unsupported path | Path + offending value retained in the error, not just the path | `test_codec.py::test_ip_06_offending_value_itself_is_retained_not_just_its_path` (Task 4) |
| IP-07 | Identity collision | Existing/attempted identity exposed structurally | `test_store.py::TestExceptions::test_identity_collision_carries_structured_fields_not_stringified_payload` |
| IP-08 | FTS cannot index non-string payload | Payload persists; merely not lexically indexed | `test_sqlite_store.py::TestFtsIndexing::test_ft08_event_bytes_payload_not_indexed`, `TestRecordCodecRoundTrip::test_event_with_bytes_payload_and_no_context_round_trips` |
| IP-09 | Claim incompatible with query Context | Exclusion mechanical; Claim itself unchanged (immutable) | `test_belief.py::TestContextAndTime::test_ct_04_incompatible_non_none_context_field_excludes_claim` |
| IP-10 | Belief conflict opaque after Resolution | Conflict history surfaced, no fabricated winner | `test_belief.py::TestContradiction::test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner` |
```

- [ ] **Step 5: Write the cross-module scenarios subsection**

```markdown
## Cross-module integration scenarios (matrix section X, X-01..10)

| Case | Scenario | Evidence |
|---|---|---|
| X-01 | Changing balance without contradiction | `test_belief.py::TestContextAndTime::test_x_01_changing_balance_without_contradiction` |
| X-02 | Conflicting birth date (AMBIGUOUS → UNRESOLVED_CONFLICT → RESOLVED_OPAQUE_CONFLICT) | `test_belief.py::test_bp_03_two_compatible_claims_no_contradiction_is_ambiguous`, `test_cf_01_relevant_unresolved_contradiction`, `test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner` |
| X-03 | Retrieval is not belief | `tests/memory/integration/test_cross_module_scenarios.py::TestX03RetrievalIsNotBelief` (Task 5) |
| X-04 | Archive is not deletion | `test_store.py::TestRetrievalRetention::test_rr_02_rr_03_archived_excluded_by_default_included_when_requested`, `test_sqlite_store.py::TestFtsIndexing::test_archived_item_remains_physically_indexed`, `TestBackendEquivalence::test_archive_then_reactivate_retrieval_equivalent` |
| X-05 | Identity vs namespace | `test_belief.py::test_bp_06_bp_07_subject_matches_across_id_and_namespaced_ref`, `test_retention.py::TestIdentityAcrossNamespaces` |
| X-06 | Unsupported persistence payload | `test_sqlite_store.py::TestBackendEquivalence::test_unsupported_value_fails_identically_on_both_backends` |
| X-07 | Episode ordering defeats wall time | `test_sqlite_store.py::TestEpisodeSqliteTransitions::test_es04_append_call_order_survives_reversed_member_timestamps` |
| X-08 | Cross-predicate conflict | `test_belief.py::TestContradiction::test_x_08_cross_predicate_conflict_still_relevant`, `test_store.py::test_cl_04_cross_predicate_contradiction_still_relevant` |
| X-09 | Resolved and unresolved conflict coexist | `test_belief.py::TestContradiction::test_x_09_resolved_and_unresolved_conflicts_coexist` |
| X-10 | Forgetting vs failure-to-recall | `tests/memory/integration/test_cross_module_scenarios.py::TestX10ForgettingVsFailureToRecall` (Task 5) |
```

- [ ] **Step 6: Write the remaining subsections**

```markdown
## Import graph manual cross-check

`MEMORY_ARCHITECTURE.md`'s dependency table (the `| Tier | Module | Owns |
Depends on |` table) was read by eye against
`tests/memory/architecture/test_import_graph.py`'s `ALLOWED_IMPORTS` map
(Task 1) — confirm entry-for-entry they agree, and record here that they
do (or note and fix any drift found).

## Public-surface audit

`memory/__init__.py` is a module docstring pointing at the three frozen
spec documents plus `__version__ = "0.1.0"` — nothing else. `src/memory/py.typed`
is present. Both match `core/__init__.py`'s own shape exactly.

## Frozen-document consistency audit

Narrow, factual corrections only — never a semantic change. As of this
pass: `MEMORY_ARCHITECTURE.md`'s `sqlite_store` dependency row was already
corrected once during Pass 3's closure (a stale `episode` entry removed,
since `sqlite_store.py` reaches Episode transitions entirely through
`InMemoryStore`'s own public API and never imports `memory.episode`
directly) — confirm no other row has drifted the same way; Task 1's own
passing `test_every_import_edge_is_allowed` is the mechanical proof this
holds for every row simultaneously.
```

- [ ] **Step 7: Run the full doc through a final read**

Re-read the complete `docs/MEMORY_V0_AUDIT.md` once, end to end, checking: no row says "Closed" without a real test citation that actually exists and passes; no placeholder text; the two subsections in Step 6 are filled in with real findings, not left as instructions.

- [ ] **Step 8: Commit**

```bash
git add docs/MEMORY_V0_AUDIT.md
git commit -m "Memory Pass 4: manual v0 closure audit"
```

---

### Task 7: Closing gate

**Files:**
- Modify: `README.md` (status line)
- Modify: `MEMORY_ADVERSARIAL_MATRIX.md` (if the audit in Task 6 found anything to correct — otherwise no change)

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: Memory v0's closure.

- [ ] **Step 1: Run the full scoped gate**

Run: `uv run pytest --ignore=tests/personal_finance -q`
Expected: all tests pass (778 before this plan's tasks, plus this plan's new tests — confirm the exact final count and record it).

Run: `uv run ruff check src/core src/memory tests/architecture tests/memory`
Expected: clean.

Run: `uv run pyright src/core src/memory tests/architecture tests/memory`
Expected: clean. (Do not run bare `uv run ruff check`/`uv run pyright` with no path — confirmed in this plan's preregistration that both currently fail on the unrelated, uncommitted `personal_finance` work; scope every invocation as shown here or in `pyproject.toml`'s own `[tool.pytest.ini_options]`/`[tool.pyright]` sections.)

- [ ] **Step 2: Verify the closure checklist**

Go through `docs/memory-passes/04-architectural-closure.md`'s "6. Closure checklist" line by line. Every box should now be checkable given Tasks 1-6. If any box cannot honestly be checked, stop and determine whether it's a genuine gap this plan missed (fix it, following the correction policy in the prereg's §5) or a checklist item that needs a one-line factual correction (e.g., wording that no longer matches what was actually built).

- [ ] **Step 3: Update README.md's status line**

Find the `Status:` line (currently ends with `...Pass 3 (SQLite durable backend) closed (...). Pass 4 (architectural closure) preregistered, implementation starting (...).`). Change it to:

```text
...Pass 3 (SQLite durable backend) closed (`docs/memory-passes/03-sqlite-backend.md`). Pass 4 (architectural closure) closed (`docs/memory-passes/04-architectural-closure.md`). Memory v0 is closed.
```

**Note:** this repository's working tree has unrelated, uncommitted `personal_finance` content already mixed into `README.md` (a whole extra section, uncommitted from before this plan started). Do not `git add README.md` directly — that would stage the unrelated content too. Instead isolate just this one-line change: `git show HEAD:README.md > /tmp/readme-clean.md`, make the same one-line edit to that clean copy, then stage it with `git hash-object -w /tmp/readme-clean.md` followed by `git update-index --cacheinfo 100644,<the printed hash>,README.md` — this stages exactly the isolated diff without touching the working tree's unrelated content. Verify with `git diff --cached -- README.md` before committing: it must show only the one status-line change.

- [ ] **Step 4: Commit**

```bash
git commit -m "Memory Pass 4: closing gate — Memory v0 closed"
```

- [ ] **Step 5: Report the final test count, and flag anything Task 6's audit found that needed a correction beyond what this plan anticipated, to the controller for the final whole-branch review.**
