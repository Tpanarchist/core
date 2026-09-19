"""Shared helper for "importing X has no side effects" propositions.

Uses a fresh subprocess rather than ``importlib.reload()``: reloading a
lower-tier module in the same process while other already-imported modules
still hold references to its pre-reload classes creates two incompatible
class "generations" — ``isinstance`` checks and dataclass-generated
``__eq__`` both compare ``__class__`` identity, which then silently
mismatches between an old-class instance and a post-reload class. That's a
test-infrastructure hazard, not a product bug (discovered when Pass 4's
full suite — but not test_relation.py alone — failed a query-by-identity
test purely because an earlier file's reload of core.identity had already
run). A fresh interpreter has no such shared state, and this is the same
"fresh Python process" mechanism ARCHITECTURE.md specifies for Pass 6's
exhaustive version of this check.
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
