"""Exhaustive, fresh-process import-side-effect verification — law 20 made
executable across the whole package.

See docs/passes/06-architectural-closure.md, "2. Exhaustive import-side-
effect verification". Supersedes the pass-local subprocess checks in
tests/semantics/_side_effects.py as the authoritative version; those
remain as useful local regressions.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "core"
_HARNESS = pathlib.Path(__file__).resolve().parent / "_side_effect_harness.py"


def _discover_module_dotted_paths() -> list[str]:
    paths: list[str] = []
    for file in sorted(_SRC.glob("*.py")):
        paths.append("core" if file.stem == "__init__" else f"core.{file.stem}")
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
