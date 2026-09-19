"""Pass-5 dependency and side-effect seam checks.

See docs/passes/05-change-and-execution.md. Pass 6 supplies the exhaustive,
whole-package AST/audit-hook version of this; this file enforces only the
seams this pass is directly responsible for.
"""

from __future__ import annotations

import ast
import pathlib

from _side_effects import assert_fresh_import_has_no_side_effects

import core.state as core_state

_SRC = pathlib.Path(core_state.__file__).parent


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
    def test_state_does_not_import_constraint_or_error_or_transform(self) -> None:
        imports = _imported_module_names("state.py")
        assert "core.constraint" not in imports
        assert "core.error" not in imports
        assert "core.transform" not in imports

    def test_constraint_imports_only_its_allowed_lower_modules(self) -> None:
        allowed = {"value", "context", "error", "identity", "time"}
        imports = _imported_module_names("constraint.py")
        core_imports = {name.removeprefix("core.") for name in imports if name.startswith("core.")}
        assert core_imports <= allowed, f"unexpected imports: {core_imports - allowed}"

    def test_transform_does_not_import_constraint_state_event_trace_epistemic(self) -> None:
        imports = _imported_module_names("transform.py")
        for forbidden in (
            "core.constraint",
            "core.state",
            "core.event",
            "core.trace",
            "core.epistemic",
        ):
            assert forbidden not in imports


def test_importing_pass5_modules_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects(
        ("core.state", "core.constraint", "core.transform"),
        ("uuid.uuid4",),
    )
