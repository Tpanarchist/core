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
        if (
            target in ("core", "memory")
            or target.startswith("core.")
            or target.startswith("memory.")
        ):
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
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith("memory")
                ):
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
