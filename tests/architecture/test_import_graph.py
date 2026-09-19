"""Architectural closure: the frozen ARCHITECTURE.md dependency graph is an
executable property of the actual source, not merely documentation.

See docs/passes/06-architectural-closure.md, "1. Import graph verification".
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "core"

# The frozen dependency table (ARCHITECTURE.md), including the Pass-5
# correction adding `value` to constraint/transform.
ALLOWED_CORE_IMPORTS: dict[str, set[str]] = {
    "__init__": set(),
    "value": set(),
    "result": set(),
    "identity": {"value"},
    "time": {"identity"},
    "context": {"identity", "time", "result"},
    "error": {"value", "identity", "time", "context"},
    "trace": {"value", "identity", "time", "context"},
    "event": {"value", "identity", "time", "context"},
    "observation": {"identity", "time", "context"},
    "relation": {"value", "identity", "time", "context"},
    "effect": {"value", "identity", "time", "context"},
    "provenance": {"identity", "time", "context"},
    "epistemic": {"value", "identity", "time", "context"},
    "state": {"identity", "time", "context", "event"},
    "constraint": {"value", "identity", "time", "context", "error"},
    "transform": {
        "value",
        "identity",
        "time",
        "context",
        "result",
        "error",
        "effect",
        "provenance",
    },
}


def _module_key_to_dotted(key: str) -> str:
    return "core" if key == "__init__" else f"core.{key}"


def _discover_module_keys() -> set[str]:
    return {path.stem for path in _SRC.glob("*.py")}


def _parse(module_key: str) -> ast.Module:
    path = _SRC / f"{module_key}.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _all_trees() -> dict[str, ast.Module]:
    return {key: _parse(key) for key in _discover_module_keys()}


def _resolve_from_import(node: ast.ImportFrom, current_dotted: str) -> str | None:
    """Resolve an ImportFrom's target to an absolute dotted module path.

    Handles the flat ``core/*.py`` layout: every module's containing
    package is exactly ``core`` (level 1). Levels >= 2 would escape above
    ``core`` and aren't meaningful in this layout.
    """
    if node.level == 0:
        return node.module
    if node.level == 1:
        return f"core.{node.module}" if node.module else "core"
    return None


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


def _root_package_name_imports(tree: ast.AST) -> list[str]:
    """``from core import X`` or its relative equivalent ``from . import X`` —
    importing a name through the package root instead of its canonical module.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        is_absolute_root = node.level == 0 and node.module == "core"
        is_relative_root = node.level == 1 and node.module is None
        if is_absolute_root or is_relative_root:
            found.extend(alias.name for alias in node.names)
    return found


def _wildcard_core_imports(tree: ast.AST, current_dotted: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        resolved = _resolve_from_import(node, current_dotted)
        if resolved and (resolved == "core" or resolved.startswith("core.")):
            if any(alias.name == "*" for alias in node.names):
                found.append(resolved)
    return found


def _add_target(edges: set[str], dotted: str | None) -> None:
    if dotted is None:
        return
    if dotted == "core":
        edges.add("__init__")
    elif dotted.startswith("core."):
        edges.add(dotted.removeprefix("core.").split(".")[0])


def _core_dependency_edges(tree: ast.AST, current_dotted: str) -> set[str]:
    """Every distinct module-key this file's imports (static + literal-string
    dynamic) resolve to under ``core``.
    """
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "core" or alias.name.startswith("core."):
                    _add_target(edges, alias.name)
        elif isinstance(node, ast.ImportFrom):
            _add_target(edges, _resolve_from_import(node, current_dotted))
    for target in _dynamic_import_targets(tree):
        if target == "core" or target.startswith("core."):
            _add_target(edges, target)

    current_key = "__init__" if current_dotted == "core" else current_dotted.removeprefix("core.")
    edges.discard(current_key)
    return edges


class TestModuleInventory:
    def test_discovered_modules_exactly_match_the_architecture_map(self) -> None:
        assert _discover_module_keys() == set(ALLOWED_CORE_IMPORTS.keys())


class TestImportEdgesAreAllowed:
    def test_every_import_edge_is_allowed(self) -> None:
        violations: dict[str, set[str]] = {}
        for key, tree in _all_trees().items():
            edges = _core_dependency_edges(tree, _module_key_to_dotted(key))
            forbidden = edges - ALLOWED_CORE_IMPORTS[key]
            if forbidden:
                violations[key] = forbidden
        assert not violations, f"forbidden import edge(s): {violations}"


class TestRootPackageAndWildcardRules:
    def test_no_module_imports_a_name_from_the_package_root(self) -> None:
        violations = {
            key: names
            for key, tree in _all_trees().items()
            if (names := _root_package_name_imports(tree))
        }
        assert not violations, (
            f"`from core import X` (or relative equivalent) found in: {violations}"
        )

    def test_no_intra_core_wildcard_imports(self) -> None:
        violations = {
            key: found
            for key, tree in _all_trees().items()
            if (found := _wildcard_core_imports(tree, _module_key_to_dotted(key)))
        }
        assert not violations, f"wildcard intra-Core imports found in: {violations}"


class TestGraphAcyclicity:
    def test_actual_dependency_graph_is_acyclic(self) -> None:
        graph = {
            key: _core_dependency_edges(tree, _module_key_to_dotted(key))
            & set(ALLOWED_CORE_IMPORTS)
            for key, tree in _all_trees().items()
        }
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


class TestCriticalNegativeSeams:
    @pytest.mark.parametrize(
        ("module", "forbidden"),
        [
            ("trace", "event"),
            ("trace", "effect"),
            ("trace", "provenance"),
            ("trace", "state"),
            ("provenance", "transform"),
            ("epistemic", "observation"),
            ("epistemic", "event"),
            ("effect", "transform"),
            ("relation", "provenance"),
            ("state", "constraint"),
            ("state", "transform"),
            ("state", "error"),
            ("transform", "constraint"),
            ("transform", "state"),
            ("transform", "event"),
            ("transform", "trace"),
            ("transform", "epistemic"),
        ],
    )
    def test_negative_seam(self, module: str, forbidden: str) -> None:
        edges = _core_dependency_edges(_parse(module), _module_key_to_dotted(module))
        assert forbidden not in edges


class TestDynamicAndRelativeImportMechanism:
    """Unit tests for the extraction mechanism itself, against synthetic
    source — none of these patterns exist in the real src/core files today,
    but the mechanism must still be proven correct.
    """

    def test_import_module_literal_is_detected(self) -> None:
        tree = ast.parse("import importlib\nimportlib.import_module('core.identity')\n")
        assert _dynamic_import_targets(tree) == ["core.identity"]

    def test_from_import_import_module_literal_is_detected(self) -> None:
        tree = ast.parse("from importlib import import_module\nimport_module('core.time')\n")
        assert _dynamic_import_targets(tree) == ["core.time"]

    def test_dunder_import_literal_is_detected(self) -> None:
        tree = ast.parse("__import__('core.time')\n")
        assert _dynamic_import_targets(tree) == ["core.time"]

    def test_non_literal_dynamic_import_argument_is_not_matched(self) -> None:
        source = "import importlib\nname = 'core.identity'\nimportlib.import_module(name)\n"
        tree = ast.parse(source)
        # Only literal-string arguments are recognized — a documented
        # limitation (arbitrary data-flow analysis is out of scope), not a
        # false positive.
        assert _dynamic_import_targets(tree) == []

    def test_type_checking_guarded_import_is_detected(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from core.identity import Id\n"
        )
        tree = ast.parse(source)
        assert "identity" in _core_dependency_edges(tree, "core.value")

    def test_relative_import_resolves_to_its_target_module(self) -> None:
        tree = ast.parse("from .identity import Id\n")
        assert "identity" in _core_dependency_edges(tree, "core.value")

    def test_bare_relative_import_is_flagged_as_root_package_import(self) -> None:
        tree = ast.parse("from . import identity\n")
        assert _root_package_name_imports(tree) == ["identity"]

    def test_absolute_root_package_import_is_flagged(self) -> None:
        tree = ast.parse("from core import Id\n")
        assert _root_package_name_imports(tree) == ["Id"]

    def test_intra_core_wildcard_is_detected(self) -> None:
        tree = ast.parse("from core.identity import *\n")
        assert _wildcard_core_imports(tree, "core.value") == ["core.identity"]
