"""Pass-4 dependency and side-effect seam checks.

See docs/passes/04-facts-and-relationships.md, "Dependency boundary" and
"Import behavior". Pass 6 supplies the exhaustive, whole-package AST/audit-
hook version of this; this file enforces only the seams this pass is
directly responsible for.
"""

from __future__ import annotations

import ast
import pathlib

from _side_effects import assert_fresh_import_has_no_side_effects

import core.event as core_event

_SRC = pathlib.Path(core_event.__file__).parent

_PASS4_MODULES = (
    "event.py",
    "observation.py",
    "relation.py",
    "effect.py",
    "provenance.py",
    "epistemic.py",
)


def _imported_module_names(module_filename: str) -> set[str]:
    source = (_SRC / module_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestDependencySeams:
    def test_epistemic_does_not_import_observation_or_event(self) -> None:
        imports = _imported_module_names("epistemic.py")
        assert "core.observation" not in imports
        assert "core.event" not in imports

    def test_provenance_does_not_import_transform(self) -> None:
        assert "core.transform" not in _imported_module_names("provenance.py")

    def test_effect_does_not_import_transform(self) -> None:
        assert "core.transform" not in _imported_module_names("effect.py")

    def test_relation_does_not_import_provenance(self) -> None:
        assert "core.provenance" not in _imported_module_names("relation.py")

    def test_no_pass4_module_imports_trace(self) -> None:
        for filename in _PASS4_MODULES:
            assert "core.trace" not in _imported_module_names(filename), (
                f"{filename} must not import core.trace"
            )

    def test_no_pass4_module_imports_a_sibling_pass4_module(self) -> None:
        siblings = {f"core.{name.removesuffix('.py')}" for name in _PASS4_MODULES}
        for filename in _PASS4_MODULES:
            own_module = f"core.{filename.removesuffix('.py')}"
            imports = _imported_module_names(filename)
            forbidden = (siblings - {own_module}) & imports
            assert not forbidden, f"{filename} imports sibling Tier-4 module(s): {forbidden}"


def test_importing_pass4_modules_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects(
        (
            "core.event",
            "core.observation",
            "core.relation",
            "core.effect",
            "core.provenance",
            "core.epistemic",
        ),
        ("uuid.uuid4",),
    )
