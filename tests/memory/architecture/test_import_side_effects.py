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
_HARNESS = pathlib.Path(__file__).resolve().parent.parent.parent / "architecture" / "_side_effect_harness.py"  # noqa: E501


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
