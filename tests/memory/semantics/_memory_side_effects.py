"""Shared helper for "importing X has no side effects" propositions.

Uses a fresh subprocess rather than ``importlib.reload()`` — see
tests/semantics/_side_effects.py in this same repo for the full rationale
(reload-based approaches corrupt isinstance/dataclass-equality semantics
across module "generations"; a fresh interpreter has no such shared state).
"""

from __future__ import annotations

import subprocess
import sys


def assert_fresh_import_has_no_side_effects(
    module_names: str | tuple[str, ...], patch_targets: tuple[str, ...]
) -> None:
    """Import ``module_names`` in a fresh subprocess with each dotted target in
    ``patch_targets`` patched to raise if called before/during that import.
    """
    modules = (module_names,) if isinstance(module_names, str) else module_names
    patches = "\n".join(
        f"_m.patch({target!r}, side_effect=AssertionError("
        f"'called during import: {target}')).start()"
        for target in patch_targets
    )
    imports = "\n".join(f"import {name}" for name in modules)
    script = f"import unittest.mock as _m\n{patches}\n{imports}\n"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"importing {modules} triggered a side effect:\n{result.stderr}"
    )
