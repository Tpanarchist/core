"""Finance remains a consumer of the closed Core and Memory layers."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _imports(path: Path) -> tuple[str, ...]:
    nodes = ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    result: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return tuple(result)


def test_dependency_direction() -> None:
    for package in ("core", "memory"):
        for path in (SRC / package).rglob("*.py"):
            assert not any(name.startswith("personal_finance") for name in _imports(path)), path
    forbidden_domain = (
        "sqlite3",
        "textual",
        "personal_finance.app",
        "personal_finance.ui",
        "personal_finance.adapters",
        "personal_finance.application",
        "httpx",
        "requests",
    )
    for path in (SRC / "personal_finance" / "domain").rglob("*.py"):
        assert not any(name.startswith(forbidden_domain) for name in _imports(path)), path
    for path in (SRC / "personal_finance" / "application").rglob("*.py"):
        forbidden = ("sqlite3", "textual", "personal_finance.adapters", "personal_finance.ui")
        assert not any(name.startswith(forbidden) for name in _imports(path)), path


def _modules() -> list[str]:
    return [
        ".".join(path.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
        for path in sorted((SRC / "personal_finance").rglob("*.py"))
        if path.stem not in ("app", "__main__") and "ui" not in path.parts
    ]


@pytest.mark.parametrize("module", _modules())
def test_finance_imports_do_no_external_work(module: str) -> None:
    harness = ROOT / "tests" / "architecture" / "_side_effect_harness.py"
    process = subprocess.run(
        [sys.executable, str(harness), module], capture_output=True, text=True, timeout=30
    )
    assert process.returncode == 0, process.stderr


def test_runtime_composition_is_explicit(tmp_path: Path) -> None:
    from personal_finance.adapters.sqlite import FinanceStore

    destination = tmp_path / "not-created.sqlite3"
    FinanceStore(destination)
    assert not destination.exists()
