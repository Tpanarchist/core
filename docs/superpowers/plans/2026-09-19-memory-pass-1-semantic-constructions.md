# Memory Pass 1: Semantic Constructions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Memory v0's pure semantic layer — `episode`, `recall`, `retention`, `belief`, `codec` — with no persistence, no SQLite, no clock reads, no hidden nondeterminism, proving adversarial-matrix sections A–J entirely in-process.

**Architecture:** Five independent tier-0 modules under `src/memory/`, each a thin, explicit set of dataclasses/functions built only on Core's already-closed v0 (`core.identity`, `core.time`, `core.context`, `core.value`, `core.epistemic`, `core.result`). No module in this pass imports another Memory module or any storage/database machinery. Every module mirrors Core's own style: `from __future__ import annotations`, frozen `slots=True, kw_only=True` dataclasses where a value type is called for, explicit validation in `__post_init__`, plain built-in exceptions for local invariants (this pass sits below any `memory.store`/`core.constraint`-equivalent tier).

**Tech Stack:** Python 3.14, pytest, Ruff, Pyright (strict) — same toolchain as Core, invoked via `uv run`.

**Spec:** `MEMORY_SPECIFICATION.md`, `MEMORY_LAWS.md`, `MEMORY_ARCHITECTURE.md`, `MEMORY_ADVERSARIAL_MATRIX.md` (repo root), and `docs/memory-passes/01-semantic-constructions.md` (the Pass 1 preregistration this plan implements). Executors should read the preregistration doc in full before starting — this plan is its task breakdown, not a replacement for it.

## Global Constraints

- Core v0 (`src/core/`) is closed — no file under `src/core/` is modified in this plan, ever.
- No Pass-1 module imports `memory.store`, `memory.sqlite_store`, `sqlite3`, `core.event`, `core.observation`, `core.effect`, `core.provenance`, `core.error`, or `core.trace`.
- Tier-0 Memory modules (`episode`, `recall`, `retention`, `belief`, `codec`) do not import each other.
- No operation in this pass reads a wall/monotonic clock, allocates a UUID, or uses `random` — every timestamp/identity a test needs is constructed literally in the test.
- Every dataclass matches Core's own style: `@dataclass(frozen=True, slots=True, kw_only=True)`.
- Test commands use `uv run pytest`; lint/type commands use `uv run ruff check` / `uv run pyright`, matching how this repo already runs Core's own suite.
- Commit messages end with the attribution trailer used throughout this session:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```

---

### Task 1: Package scaffolding

**Files:**
- Create: `src/memory/__init__.py`
- Create: `src/memory/py.typed`
- Modify: `pyproject.toml` (add `src/memory` to the wheel's package list)
- Create: `tests/memory/semantics/_memory_side_effects.py`
- Test: `tests/memory/semantics/test_package.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: an importable, empty `memory` package that every later task's modules live under. Later tasks assume `import memory.<module>` works and that `uv run pytest tests/memory/semantics/` is a valid invocation.

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/_memory_side_effects.py` (same helper as `tests/semantics/_side_effects.py`, deliberately **not** reusing that exact basename here: neither `tests/semantics/` nor `tests/memory/semantics/` has an `__init__.py`/`conftest.py`, so pytest's default "prepend" import mode caches whichever same-named module it imports first in `sys.modules` under that bare name — a second directory's same-named file would then be silently skipped for the rest of the run, not actually loaded, whenever both directories are collected together. Core's own real precedent (`tests/architecture/_side_effect_harness.py` vs. `tests/semantics/_side_effects.py`) already avoids this by using two different basenames — this file follows that actual pattern, not a same-name-in-both-dirs one):

```python
"""Shared helper for "importing X has no side effects" propositions, for
the memory package's own test tree.

Uses a fresh subprocess rather than ``importlib.reload()`` — see
tests/semantics/_side_effects.py in this same repo for the full rationale
(reload-based approaches corrupt isinstance/dataclass-equality semantics
across module "generations"; a fresh interpreter has no such shared state).
Named distinctly from that file (not just placed in a different directory)
because neither test directory has an __init__.py/conftest.py — two
same-named modules loaded under pytest's default import mode collide in
sys.modules, silently dropping whichever loads second.
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
```

Create `tests/memory/semantics/test_package.py`:

```python
"""Propositions for the memory package's own scaffolding."""

from _memory_side_effects import assert_fresh_import_has_no_side_effects


class TestPackageImport:
    def test_memory_package_imports(self) -> None:
        import memory

        assert memory.__version__ == "0.1.0"

    def test_memory_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory",
            patch_targets=(
                "uuid.uuid4",
                "time.time",
                "time.monotonic",
            ),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_package.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory'`

- [ ] **Step 3: Create the package and register it in the build**

Create `src/memory/__init__.py`:

```python
"""Memory: derived constructions over Core's closed v0 ontology.

See MEMORY_SPECIFICATION.md, MEMORY_LAWS.md, and MEMORY_ARCHITECTURE.md
at the repository root.
"""

__version__ = "0.1.0"
```

Create `src/memory/py.typed` (empty file — presence alone marks the package typed for Pyright/consumers):

```text
```

Modify `pyproject.toml` — change:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/core"]
```

to:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/core", "src/memory"]
```

This keeps the wheel manifest accurate for an actual build/distribution of this package later — it is not required for the test below to pass. Verified directly: this repo's editable install already puts `E:\core\src` itself on `sys.path` (confirmed via `uv run python -c "import sys; print(sys.path)"`), so any package under `src/` — including a brand-new `src/memory/`, before this edit even lands — imports immediately with no `uv sync` step needed. `uv sync` is therefore *not* part of this step; do not run it expecting it to be necessary, and do not expect it to touch `uv.lock` (the wheel `packages` list is a build-target detail, not a dependency declaration `uv.lock` tracks).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_package.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/memory/__init__.py src/memory/py.typed pyproject.toml \
        tests/memory/semantics/_memory_side_effects.py tests/memory/semantics/test_package.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: package scaffolding

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `codec.py` — the durable-value domain

**Files:**
- Create: `src/memory/codec.py`
- Test: `tests/memory/semantics/test_codec.py`

**Interfaces:**
- Consumes: nothing from other Memory modules.
- Produces: `PersistedValue` (type alias), `UnsupportedPersistedValue` (exception, carries `.path: tuple[str | int, ...]`, `.value: object`, `.reason: str`), `as_persisted_value(value: object, *, path: tuple[str | int, ...] = ()) -> PersistedValue`. Task 3 builds `encode_persisted_value`/`decode_persisted_value` on top of these in the same file; Task 4's Core-primitive codecs call `as_persisted_value()` for `Context`'s object-typed fields.

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_codec.py`:

```python
"""Propositions for memory.codec — the durable-value domain.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections H (codec domain)
and I (float handling).
"""

from __future__ import annotations

from types import MappingProxyType

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from memory.codec import UnsupportedPersistedValue, as_persisted_value


class TestScalars:
    def test_cd_01_none_round_trips(self) -> None:
        assert as_persisted_value(None) is None

    def test_cd_02_false_and_zero_stay_distinguishable(self) -> None:
        assert as_persisted_value(False) is False
        assert as_persisted_value(0) == 0
        assert type(as_persisted_value(False)) is bool
        assert type(as_persisted_value(0)) is int

    def test_cd_03_true_and_one_stay_distinguishable(self) -> None:
        assert as_persisted_value(True) is True
        assert type(as_persisted_value(1)) is int

    def test_cd_04_cd_05_arbitrary_precision_int_round_trips(self) -> None:
        huge_positive = 2**256 + 1
        huge_negative = -(2**256) - 1
        assert as_persisted_value(huge_positive) == huge_positive
        assert as_persisted_value(huge_negative) == huge_negative

    def test_cd_06_unicode_string_preserved_exactly(self) -> None:
        text = "café \U0001f600 ́"
        assert as_persisted_value(text) == text

    def test_cd_08_arbitrary_bytes_round_trip(self) -> None:
        data = b"\x00\x01\xff\xfe"
        assert as_persisted_value(data) == data


class TestContainers:
    def test_cd_09_nested_tuples_round_trip_structure(self) -> None:
        nested = (1, ("a", (b"x", None)), (True, 2.5))
        assert as_persisted_value(nested) == nested

    def test_cd_10_mapping_iteration_order_does_not_change_content(self) -> None:
        first = as_persisted_value({"a": 1, "b": 2})
        second = as_persisted_value({"b": 2, "a": 1})
        assert dict(first) == dict(second) == {"a": 1, "b": 2}

    def test_cd_16_defensive_snapshot_survives_source_mutation(self) -> None:
        source = {"nested": [1, 2]} if False else {"nested": (1, 2)}
        snapshot = as_persisted_value(source)
        source["nested"] = (99,)
        assert dict(snapshot)["nested"] == (1, 2)

    def test_validated_mapping_is_immutable(self) -> None:
        snapshot = as_persisted_value({"a": 1})
        assert isinstance(snapshot, MappingProxyType)
        with pytest.raises(TypeError):
            snapshot["a"] = 2  # type: ignore[index]


class TestRejections:
    def test_cd_11_mapping_containing_list_is_rejected_with_path(self) -> None:
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value({"items": [1, 2]})
        assert exc_info.value.path == ("items",)

    def test_cd_12_unsupported_object_three_levels_deep_reports_exact_path(self) -> None:
        class Exotic:
            pass

        payload = {"a": {"b": (1, Exotic())}}
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value(payload)
        assert exc_info.value.path == ("a", "b", 1)

    def test_cd_13_tuple_containing_unsupported_object_reports_index(self) -> None:
        class Exotic:
            pass

        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value((1, 2, Exotic()))
        assert exc_info.value.path == (2,)

    def test_cd_14_non_string_mapping_key_is_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value({1: "a"})

    def test_cd_15_cycles_fail_explicitly(self) -> None:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(cyclic)

    def test_cd_17_id_is_rejected_as_a_bare_domain_payload(self) -> None:
        from core.identity import Id
        from core.value import Kind

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Id(Kind("test.subject"), "s1"))

    def test_cd_19_repr_able_object_is_rejected_not_stringified(self) -> None:
        class HasRepr:
            def __repr__(self) -> str:
                return "HasRepr()"

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(HasRepr())

    def test_cd_20_pickleable_object_is_rejected(self) -> None:
        class Pickleable:
            def __init__(self) -> None:
                self.x = 1

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Pickleable())

    def test_cd_21_decimal_is_rejected_in_v0(self) -> None:
        from decimal import Decimal

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Decimal("1.5"))

    def test_cd_22_dataclass_is_rejected_without_structural_auto_conversion(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class PersistedValueShaped:
            a: int
            b: str

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(PersistedValueShaped(a=1, b="x"))

    def test_cd_23_list_is_rejected_even_though_json_could_encode_it(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value([1, 2, 3])


class TestFloats:
    def test_fl_01_ordinary_finite_float_round_trips(self) -> None:
        assert as_persisted_value(1.5) == 1.5

    def test_fl_02_negative_zero_sign_preserved(self) -> None:
        import math

        result = as_persisted_value(-0.0)
        assert math.copysign(1.0, result) == -1.0

    def test_fl_03_positive_infinity_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(float("inf"))

    def test_fl_04_negative_infinity_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(float("-inf"))

    def test_fl_05_nan_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(float("nan"))


class TestNoStringificationFallback:
    def test_codec_never_calls_str_or_repr_as_a_fallback(self) -> None:
        class Loud:
            def __str__(self) -> str:
                raise AssertionError("str() must never be called during validation")

            def __repr__(self) -> str:
                raise AssertionError("repr() must never be called during validation")

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Loud())


class TestImportSideEffects:
    def test_codec_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.codec",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory.codec'`

- [ ] **Step 3: Write minimal implementation**

Create `src/memory/codec.py`:

```python
"""Codec: the durable-value domain crossing the persistence boundary.

See MEMORY_SPECIFICATION.md "Persistence and the codec boundary" and
MEMORY_ARCHITECTURE.md "The codec boundary" (codec.py, tier 0).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType

type PersistedValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | tuple[PersistedValue, ...]
    | Mapping[str, PersistedValue]
)


class UnsupportedPersistedValue(ValueError):
    """Raised by ``as_persisted_value()`` when a value cannot cross the
    durable-storage boundary. Carries the exact nested path and the
    offending value/reason — never silently stringified, pickled, or dropped.
    """

    def __init__(self, path: tuple[str | int, ...], value: object, reason: str) -> None:
        super().__init__(f"unsupported persisted value at {path!r}: {reason}")
        self.path = path
        self.value = value
        self.reason = reason


def as_persisted_value(
    value: object,
    *,
    path: tuple[str | int, ...] = (),
    _seen: frozenset[int] = frozenset(),
) -> PersistedValue:
    """Validate ``value`` against the closed PersistedValue domain and return
    a defensive recursive immutable snapshot. Never converts/coerces —
    validates only. Raises UnsupportedPersistedValue, naming the exact path,
    for anything outside the domain.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedPersistedValue(path, value, "float must be finite")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value
    if isinstance(value, tuple):
        if id(value) in _seen:
            raise UnsupportedPersistedValue(path, value, "cyclic container")
        next_seen = _seen | {id(value)}
        return tuple(
            as_persisted_value(item, path=(*path, index), _seen=next_seen)
            for index, item in enumerate(value)
        )
    if isinstance(value, list):
        raise UnsupportedPersistedValue(path, value, "list is not persistable; use tuple")
    if isinstance(value, Mapping):
        if id(value) in _seen:
            raise UnsupportedPersistedValue(path, value, "cyclic container")
        next_seen = _seen | {id(value)}
        result: dict[str, PersistedValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnsupportedPersistedValue(path, key, "mapping keys must be strings")
            result[key] = as_persisted_value(item, path=(*path, key), _seen=next_seen)
        return MappingProxyType(result)
    raise UnsupportedPersistedValue(path, value, f"unsupported type {type(value).__name__}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/codec.py tests/memory/semantics/test_codec.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement codec value domain

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `codec.py` — canonical byte encoding

**Files:**
- Modify: `src/memory/codec.py`
- Modify: `tests/memory/semantics/test_codec.py`

**Interfaces:**
- Consumes: `PersistedValue`, `as_persisted_value()` from Task 2 (same file).
- Produces: `encode_persisted_value(value: PersistedValue) -> bytes`, `decode_persisted_value(data: bytes) -> PersistedValue`, and the internal `_envelope`/`_unenvelope` helpers Task 4's Core-primitive codecs reuse.

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_codec.py`:

```python
from memory.codec import decode_persisted_value, encode_persisted_value


class TestCanonicalEncoding:
    def test_cd_07_unicode_spellings_not_normalized(self) -> None:
        composed = "é"  # é as one code point
        decomposed = "é"  # e + combining acute accent
        assert composed != decomposed
        assert decode_persisted_value(encode_persisted_value(composed)) == composed
        assert decode_persisted_value(encode_persisted_value(decomposed)) == decomposed
        assert encode_persisted_value(composed) != encode_persisted_value(decomposed)

    def test_cd_10_mapping_key_order_yields_identical_bytes(self) -> None:
        first = encode_persisted_value(MappingProxyType({"a": 1, "b": 2}))
        second = encode_persisted_value(MappingProxyType({"b": 2, "a": 1}))
        assert first == second

    def test_round_trips_every_scalar_kind(self) -> None:
        for value in (None, True, False, 0, -1, 2**300, 1.5, "s", b"\x00\x01"):
            assert decode_persisted_value(encode_persisted_value(value)) == value

    def test_round_trips_nested_containers(self) -> None:
        value = (1, MappingProxyType({"x": (True, None, "y")}), b"z")
        assert decode_persisted_value(encode_persisted_value(value)) == value

    def test_fl_02_negative_zero_round_trips_with_sign(self) -> None:
        import math

        decoded = decode_persisted_value(encode_persisted_value(-0.0))
        assert math.copysign(1.0, decoded) == -1.0

    def test_fl_07_repeated_encoding_is_byte_identical(self) -> None:
        value = (1, "x", 2.5, MappingProxyType({"k": True}))
        assert encode_persisted_value(value) == encode_persisted_value(value)

    def test_cd_04_large_int_round_trips_exactly(self) -> None:
        huge = 2**256 + 12345
        assert decode_persisted_value(encode_persisted_value(huge)) == huge

    def test_unknown_codec_version_fails_loudly(self) -> None:
        import json

        malformed = json.dumps(["memory.persisted_value", 999, ["none"]]).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

    def test_malformed_envelope_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            decode_persisted_value(b"not json at all")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'encode_persisted_value'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/memory/codec.py`:

```python
import base64
import json


def _envelope(tag: str, version: int, payload: object) -> bytes:
    return json.dumps(
        [tag, version, payload], ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _unenvelope(expected_tag: str, expected_version: int, data: bytes) -> object:
    try:
        envelope = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"malformed {expected_tag} envelope: {exc}") from exc
    if not (isinstance(envelope, list) and len(envelope) == 3):
        raise ValueError(f"malformed {expected_tag} envelope: {envelope!r}")
    tag, version, payload = envelope
    if tag != expected_tag or version != expected_version:
        raise ValueError(f"unknown codec version/tag: {tag!r} v{version!r}")
    return payload


def _encode_node(value: PersistedValue) -> object:
    if value is None:
        return ["none"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, tuple):
        return ["tuple", [_encode_node(item) for item in value]]
    if isinstance(value, Mapping):
        pairs = sorted(value.items(), key=lambda pair: pair[0])
        return ["map", [[key, _encode_node(item)] for key, item in pairs]]
    raise TypeError(f"not a PersistedValue: {type(value).__name__}")


def _decode_node(node: object) -> PersistedValue:
    """Decode one canonical node. ``node`` comes from parsed, but otherwise
    untrusted, external bytes — every branch validates its own payload shape
    and raises ``ValueError`` (never IndexError/TypeError/other) on anything
    malformed, exactly like _unenvelope() already does at the envelope level.
    """
    if not (isinstance(node, list) and node):
        raise ValueError(f"malformed persisted-value node: {node!r}")
    tag = node[0]
    rest = node[1:]
    if tag == "none":
        if rest:
            raise ValueError(f"malformed 'none' node: {node!r}")
        return None
    if tag == "bool":
        if len(rest) != 1 or not isinstance(rest[0], bool):
            raise ValueError(f"malformed 'bool' node: {node!r}")
        return rest[0]
    if tag == "int":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'int' node: {node!r}")
        try:
            return int(rest[0])
        except ValueError as exc:
            raise ValueError(f"malformed 'int' payload: {node!r}") from exc
    if tag == "float":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'float' node: {node!r}")
        try:
            return float.fromhex(rest[0])
        except ValueError as exc:
            raise ValueError(f"malformed 'float' payload: {node!r}") from exc
    if tag == "str":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'str' node: {node!r}")
        return rest[0]
    if tag == "bytes":
        if len(rest) != 1 or not isinstance(rest[0], str):
            raise ValueError(f"malformed 'bytes' node: {node!r}")
        try:
            return base64.b64decode(rest[0], validate=True)
        except ValueError as exc:
            raise ValueError(f"malformed 'bytes' payload: {node!r}") from exc
    if tag == "tuple":
        if len(rest) != 1 or not isinstance(rest[0], list):
            raise ValueError(f"malformed 'tuple' node: {node!r}")
        return tuple(_decode_node(item) for item in rest[0])
    if tag == "map":
        if len(rest) != 1 or not isinstance(rest[0], list):
            raise ValueError(f"malformed 'map' node: {node!r}")
        result: dict[str, PersistedValue] = {}
        for pair in rest[0]:
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)):
                raise ValueError(f"malformed 'map' entry: {pair!r}")
            key, item = pair
            result[key] = _decode_node(item)
        return MappingProxyType(result)
    raise ValueError(f"unknown persisted-value tag: {tag!r}")


def encode_persisted_value(value: PersistedValue) -> bytes:
    """Deterministic canonical byte encoding. Same input always produces
    byte-identical output — see MEMORY_ARCHITECTURE.md "The codec boundary".
    """
    return _envelope("memory.persisted_value", 1, _encode_node(value))


def decode_persisted_value(data: bytes) -> PersistedValue:
    return _decode_node(_unenvelope("memory.persisted_value", 1, data))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/codec.py tests/memory/semantics/test_codec.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement codec canonical byte encoding

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `codec.py` — Core primitive codecs

**Files:**
- Modify: `src/memory/codec.py`
- Modify: `tests/memory/semantics/test_codec.py`

**Interfaces:**
- Consumes: `_envelope`/`_unenvelope`/`_encode_node`/`_decode_node`/`as_persisted_value` from Tasks 2–3 (same file); `core.identity.{Id, Namespace, Ref}`, `core.time.{WallInstant, Duration}`, `core.value.Kind`, `core.context.Context`.
- Produces: `encode_kind`/`decode_kind`, `encode_id`/`decode_id`, `encode_namespace`/`decode_namespace`, `encode_ref`/`decode_ref`, `encode_wall_instant`/`decode_wall_instant`, `encode_duration`/`decode_duration`, `encode_context`/`decode_context`. This closes out `codec.py` for Pass 1; Task 8 (`belief.py`) does not need these, but Pass 2's store code will.

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_codec.py`:

```python
from datetime import UTC, datetime

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.time import Duration, WallInstant
from core.value import Kind

from memory.codec import (
    decode_context,
    decode_duration,
    decode_id,
    decode_kind,
    decode_namespace,
    decode_ref,
    decode_wall_instant,
    encode_context,
    encode_duration,
    encode_id,
    encode_kind,
    encode_namespace,
    encode_ref,
    encode_wall_instant,
)


class TestCorePrimitiveCodecs:
    def test_cp_01_kind_round_trips(self) -> None:
        kind = Kind("memory.test.subject")
        assert decode_kind(encode_kind(kind)) == kind

    def test_cp_02_id_round_trips(self) -> None:
        value = Id(Kind("memory.test.subject"), "s1")
        assert decode_id(encode_id(value)) == value

    def test_cp_03_namespace_round_trips(self) -> None:
        namespace = Namespace(("finance", "checking"))
        assert decode_namespace(encode_namespace(namespace)) == namespace

    def test_cp_04_ref_without_namespace_round_trips(self) -> None:
        ref = Ref(id=Id(Kind("memory.test.subject"), "s1"))
        decoded = decode_ref(encode_ref(ref))
        assert decoded == ref
        assert decoded.namespace is None

    def test_cp_05_ref_with_namespace_preserves_it(self) -> None:
        ref = Ref(
            id=Id(Kind("memory.test.subject"), "s1"),
            namespace=Namespace(("finance", "checking")),
        )
        decoded = decode_ref(encode_ref(ref))
        assert decoded == ref
        assert decoded.namespace == Namespace(("finance", "checking"))

    def test_cp_06_wall_instant_persists_canonical_utc(self) -> None:
        instant = WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))
        assert decode_wall_instant(encode_wall_instant(instant)) == instant

    def test_cp_07_duration_zero_round_trips(self) -> None:
        duration = Duration(0)
        assert decode_duration(encode_duration(duration)) == duration

    def test_cp_08_very_large_duration_round_trips(self) -> None:
        duration = Duration(2**200)
        assert decode_duration(encode_duration(duration)) == duration

    def test_cp_09_context_with_only_as_of_round_trips(self) -> None:
        context = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))
        assert decode_context(encode_context(context)) == context

    def test_cp_10_context_with_every_supported_field_round_trips(self) -> None:
        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)),
            namespace=Namespace(("finance",)),
            scope="checking",
            environment="prod",
            source="bank-api",
            authority="user",
            version="1",
            units="usd",
            metadata={"note": "test"},
        )
        assert decode_context(encode_context(context)) == context

    def test_cp_11_context_metadata_order_yields_identical_bytes(self) -> None:
        base = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
        first = Context(as_of=base, metadata={"a": 1, "b": 2})
        second = Context(as_of=base, metadata={"b": 2, "a": 1})
        assert encode_context(first) == encode_context(second)

    def test_cp_12_context_unsupported_object_fails_with_field_path(self) -> None:
        class Exotic:
            pass

        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)), scope=Exotic()
        )
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            encode_context(context)
        assert exc_info.value.path == ("scope",)

    def test_cd_18_context_scope_as_id_is_rejected_in_v0(self) -> None:
        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)),
            scope=Id(Kind("memory.test.subject"), "s1"),
        )
        with pytest.raises(UnsupportedPersistedValue):
            encode_context(context)

    def test_cp_13_decode_malformed_kind_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            decode_kind(b"not an envelope")

    def test_cp_14_decode_unknown_tag_fails_loudly(self) -> None:
        malformed = _envelope("memory.not_a_kind", 1, "x")
        with pytest.raises(ValueError):
            decode_kind(malformed)

    def test_cp_15_encode_decode_encode_is_stable(self) -> None:
        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)),
            metadata={"a": 1, "b": 2},
        )
        once = encode_context(context)
        twice = encode_context(decode_context(once))
        assert once == twice
```

Add the necessary imports at the top of `tests/memory/semantics/test_codec.py` (`_envelope` and `UnsupportedPersistedValue` — the latter is already imported from Task 2's test class; add `_envelope` to the `from memory.codec import (...)` block used by Task 4's tests, or import it directly: `from memory.codec import _envelope`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'encode_kind'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/memory/codec.py`:

```python
from datetime import UTC, datetime

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.time import Duration, WallInstant
from core.value import Kind

_CONTEXT_OBJECT_FIELDS = ("scope", "environment", "source", "authority", "version", "units")


def encode_kind(kind: Kind) -> bytes:
    return _envelope("memory.kind", 1, kind.value)


def decode_kind(data: bytes) -> Kind:
    value = _unenvelope("memory.kind", 1, data)
    return Kind(value)


def encode_id(value: Id) -> bytes:
    return _envelope("memory.id", 1, [value.kind.value, value.value])


def decode_id(data: bytes) -> Id:
    kind_value, id_value = _unenvelope("memory.id", 1, data)
    return Id(Kind(kind_value), id_value)


def encode_namespace(namespace: Namespace) -> bytes:
    return _envelope("memory.namespace", 1, list(namespace.segments))


def decode_namespace(data: bytes) -> Namespace:
    segments = _unenvelope("memory.namespace", 1, data)
    return Namespace(tuple(segments))


def encode_ref(ref: Ref) -> bytes:
    namespace_segments = list(ref.namespace.segments) if ref.namespace is not None else None
    payload = [[ref.id.kind.value, ref.id.value], namespace_segments]
    return _envelope("memory.ref", 1, payload)


def decode_ref(data: bytes) -> Ref:
    (kind_value, id_value), namespace_segments = _unenvelope("memory.ref", 1, data)
    namespace = Namespace(tuple(namespace_segments)) if namespace_segments is not None else None
    return Ref(id=Id(Kind(kind_value), id_value), namespace=namespace)


def encode_wall_instant(instant: WallInstant) -> bytes:
    return _envelope("memory.wall_instant", 1, instant.value.isoformat())


def decode_wall_instant(data: bytes) -> WallInstant:
    iso = _unenvelope("memory.wall_instant", 1, data)
    return WallInstant(datetime.fromisoformat(iso))


def encode_duration(duration: Duration) -> bytes:
    return _envelope("memory.duration", 1, str(duration.nanoseconds))


def decode_duration(data: bytes) -> Duration:
    nanoseconds = _unenvelope("memory.duration", 1, data)
    return Duration(int(nanoseconds))


def encode_context(context: Context) -> bytes:
    payload: dict[str, object] = {
        "as_of": context.as_of.value.isoformat(),
        "namespace": list(context.namespace.segments) if context.namespace is not None else None,
    }
    for field in _CONTEXT_OBJECT_FIELDS:
        raw = getattr(context, field)
        payload[field] = (
            None if raw is None else _encode_node(as_persisted_value(raw, path=(field,)))
        )
    payload["metadata"] = (
        None
        if context.metadata is None
        else _encode_node(as_persisted_value(context.metadata, path=("metadata",)))
    )
    return _envelope("memory.context", 1, payload)


def decode_context(data: bytes) -> Context:
    payload = _unenvelope("memory.context", 1, data)
    as_of = WallInstant(datetime.fromisoformat(payload["as_of"]))
    namespace = (
        Namespace(tuple(payload["namespace"])) if payload["namespace"] is not None else None
    )
    fields: dict[str, object] = {}
    for field in _CONTEXT_OBJECT_FIELDS:
        node = payload[field]
        fields[field] = None if node is None else _decode_node(node)
    metadata_node = payload["metadata"]
    metadata = None if metadata_node is None else _decode_node(metadata_node)
    return Context(as_of=as_of, namespace=namespace, metadata=metadata, **fields)
```

Note: `datetime`/`UTC` and `Context`/`Id`/`Namespace`/`Ref`/`Duration`/`WallInstant`/`Kind` are imported once at the top of `codec.py` in this step — if Ruff flags a duplicate/unused import against anything pulled in incidentally by Task 2/3 code, consolidate all imports into the single top-of-file import block rather than leaving scattered duplicates.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_codec.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/codec.py tests/memory/semantics/test_codec.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement codec Core-primitive encoders

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `episode.py`

**Files:**
- Create: `src/memory/episode.py`
- Test: `tests/memory/semantics/test_episode.py`

**Interfaces:**
- Consumes: `core.identity.{Id, Ref, Entity}`, `core.context.Context`, `core.time.WallInstant`. No dependency on Tasks 2–4.
- Produces: `Episode` — constructed via `Episode(*, id, subject, context, opened_at)`, with `.id`, `.subject`, `.context`, `.opened_at`, `.closed_at`, `.append(ref) -> Ref`, `.items() -> tuple[Ref, ...]`, `.close(at) -> WallInstant`.

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_episode.py`:

```python
"""Propositions for memory.episode.

Matrix reference: MEMORY_ADVERSARIAL_MATRIX.md section A (Episode).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.identity import Entity, Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind

from memory.episode import Episode

EPISODE_KIND = Kind("memory.test.episode")
SUBJECT_KIND = Kind("memory.test.subject")
OBS_KIND = Kind("memory.test.observation")
CTX = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))


def make_episode(*, opened_at: WallInstant | None = None) -> Episode:
    return Episode(
        id=Id(EPISODE_KIND, "e1"),
        subject=Id(SUBJECT_KIND, "s1"),
        context=CTX,
        opened_at=opened_at or WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
    )


def ref(value: str) -> Ref:
    return Ref(id=Id(OBS_KIND, value))


class TestConstruction:
    def test_episode_satisfies_entity(self) -> None:
        assert isinstance(make_episode(), Entity)

    def test_episode_requires_caller_supplied_id(self) -> None:
        episode = make_episode()
        assert episode.id == Id(EPISODE_KIND, "e1")

    def test_starts_empty_and_open(self) -> None:
        episode = make_episode()
        assert episode.items() == ()
        assert episode.closed_at is None


class TestAppendOrdering:
    def test_ep_03_duplicate_refs_preserved_as_separate_positions(self) -> None:
        episode = make_episode()
        episode.append(ref("a"))
        episode.append(ref("a"))
        assert episode.items() == (ref("a"), ref("a"))

    def test_ep_01_ep_02_member_subject_never_rewritten(self) -> None:
        # Episode groups by asserting its own subject; it never inspects or
        # rewrites a member's own subject (Event has no subject field at
        # all, and an Observation's subject may legitimately differ).
        episode = make_episode()
        member = ref("event-with-no-subject")
        episode.append(member)
        assert episode.items() == (member,)
        assert episode.subject == Id(SUBJECT_KIND, "s1")

    def test_ep_04_insertion_order_beats_wall_time(self) -> None:
        # Members carry no wall-time field on Ref itself — insertion order
        # is the only order Episode tracks, and it must not be reconstructed
        # from any external timestamp a caller might associate with a Ref.
        episode = make_episode()
        episode.append(ref("later-in-reality"))
        episode.append(ref("earlier-in-reality"))
        assert episode.items() == (ref("later-in-reality"), ref("earlier-in-reality"))

    def test_ep_11_distinct_namespaces_on_same_id_preserved_exactly(self) -> None:
        episode = make_episode()
        plain = Ref(id=Id(OBS_KIND, "shared"))
        namespaced = Ref(id=Id(OBS_KIND, "shared"), namespace=Namespace(("finance",)))
        episode.append(plain)
        episode.append(namespaced)
        assert episode.items() == (plain, namespaced)
        assert episode.items()[0].namespace is None
        assert episode.items()[1].namespace == Namespace(("finance",))

    def test_ep_12_ref_to_another_episode_preserved_opaquely(self) -> None:
        episode = make_episode()
        other_episode_ref = Ref(id=Id(EPISODE_KIND, "e2"))
        episode.append(other_episode_ref)
        assert episode.items() == (other_episode_ref,)

    def test_ep_13_self_reference_preserved_opaquely(self) -> None:
        episode = make_episode()
        self_ref = Ref(id=episode.id)
        episode.append(self_ref)
        assert episode.items() == (self_ref,)

    def test_ep_14_old_snapshot_unaffected_by_later_append(self) -> None:
        episode = make_episode()
        episode.append(ref("a"))
        snapshot = episode.items()
        episode.append(ref("b"))
        assert snapshot == (ref("a"),)
        assert episode.items() == (ref("a"), ref("b"))


class TestClose:
    def test_ep_06_append_after_close_raises(self) -> None:
        episode = make_episode()
        episode.close(episode.opened_at)
        with pytest.raises(ValueError):
            episode.append(ref("a"))

    def test_ep_07_close_twice_raises(self) -> None:
        episode = make_episode()
        episode.close(episode.opened_at)
        with pytest.raises(ValueError):
            episode.close(episode.opened_at)

    def test_ep_08_close_before_opened_at_raises(self) -> None:
        episode = make_episode(
            opened_at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        )
        with pytest.raises(ValueError):
            episode.close(WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))

    def test_ep_09_equal_open_and_close_instants_permitted(self) -> None:
        episode = make_episode()
        episode.close(episode.opened_at)
        assert episode.closed_at == episode.opened_at

    def test_close_returns_the_accepted_instant(self) -> None:
        episode = make_episode()
        closing = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        assert episode.close(closing) == closing


class TestImportSideEffects:
    def test_episode_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.episode",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_episode.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory.episode'`

- [ ] **Step 3: Write minimal implementation**

Create `src/memory/episode.py`:

```python
"""Episode: an asserted, ordered grouping of references to preserved Core
facts under one subject and context.

See MEMORY_SPECIFICATION.md #1 and MEMORY_ARCHITECTURE.md (episode.py, tier 0).
"""

from __future__ import annotations

from core.context import Context
from core.identity import Id, Ref
from core.time import WallInstant


class Episode:
    """Mutable, single-writer, append-only grouping — same concurrency
    family as Core's Trace/History/ContradictionLog. Entity-bearing via
    ``id``. ``id`` is always caller-supplied; Episode never allocates its
    own identity.
    """

    def __init__(
        self,
        *,
        id: Id,
        subject: Id | Ref,
        context: Context,
        opened_at: WallInstant,
    ) -> None:
        self._id = id
        self._subject = subject
        self._context = context
        self._opened_at = opened_at
        self._closed_at: WallInstant | None = None
        self._items: list[Ref] = []

    @property
    def id(self) -> Id:
        return self._id

    @property
    def subject(self) -> Id | Ref:
        return self._subject

    @property
    def context(self) -> Context:
        return self._context

    @property
    def opened_at(self) -> WallInstant:
        return self._opened_at

    @property
    def closed_at(self) -> WallInstant | None:
        return self._closed_at

    def append(self, ref: Ref) -> Ref:
        if self._closed_at is not None:
            raise ValueError("cannot append to a closed Episode")
        self._items.append(ref)
        return ref

    def items(self) -> tuple[Ref, ...]:
        return tuple(self._items)

    def close(self, at: WallInstant) -> WallInstant:
        if self._closed_at is not None:
            raise ValueError("Episode is already closed")
        if at < self._opened_at:
            raise ValueError("closed_at must not precede opened_at")
        self._closed_at = at
        return at
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_episode.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/episode.py tests/memory/semantics/test_episode.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement episode

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `recall.py`

**Files:**
- Create: `src/memory/recall.py`
- Test: `tests/memory/semantics/test_recall.py`

**Interfaces:**
- Consumes: `core.identity.Ref`, `core.context.Context`, `core.time.WallInstant`, `core.value.Kind`. No dependency on Tasks 2–5.
- Produces: `IDENTITY_MATCH`, `LEXICAL_MATCH`, `CONTEXTUAL_MATCH` (well-known `Kind` constants), `RecallCandidate`, `WorkingSet`, `admit(candidates, capacity) -> tuple[WorkingSet, tuple[RecallCandidate, ...]]`.

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_recall.py`:

```python
"""Propositions for memory.recall.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections B (RecallCandidate)
and C (WorkingSet).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.identity import Id, Ref
from core.time import WallInstant
from core.value import Kind

from memory.recall import (
    CONTEXTUAL_MATCH,
    IDENTITY_MATCH,
    LEXICAL_MATCH,
    RecallCandidate,
    WorkingSet,
    admit,
)

ITEM_KIND = Kind("memory.test.item")
CTX = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))
AT = WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))


def candidate(value: str, relevance: tuple[Kind, ...] = (IDENTITY_MATCH,)) -> RecallCandidate:
    return RecallCandidate(
        item=Ref(id=Id(ITEM_KIND, value)),
        query_context=CTX,
        relevance=relevance,
        retrieved_at=AT,
    )


class TestRecallCandidate:
    def test_rc_01_identity_match_kind_distinct_from_lexical(self) -> None:
        c = candidate("a", relevance=(IDENTITY_MATCH,))
        assert IDENTITY_MATCH in c.relevance
        assert LEXICAL_MATCH not in c.relevance

    def test_rc_03_multiple_evidence_kinds_may_coexist(self) -> None:
        c = candidate("a", relevance=(IDENTITY_MATCH, LEXICAL_MATCH))
        assert c.relevance == (IDENTITY_MATCH, LEXICAL_MATCH)

    def test_rc_09_empty_relevance_rejected(self) -> None:
        with pytest.raises(ValueError):
            candidate("a", relevance=())

    def test_rc_10_duplicate_relevance_kinds_rejected(self) -> None:
        with pytest.raises(ValueError):
            candidate("a", relevance=(IDENTITY_MATCH, IDENTITY_MATCH))

    def test_relevance_order_preserved(self) -> None:
        c = candidate("a", relevance=(LEXICAL_MATCH, IDENTITY_MATCH, CONTEXTUAL_MATCH))
        assert c.relevance == (LEXICAL_MATCH, IDENTITY_MATCH, CONTEXTUAL_MATCH)

    def test_relevance_is_defensively_tuple_normalized(self) -> None:
        c = RecallCandidate(
            item=Ref(id=Id(ITEM_KIND, "a")),
            query_context=CTX,
            relevance=[IDENTITY_MATCH],  # type: ignore[arg-type]
            retrieved_at=AT,
        )
        assert c.relevance == (IDENTITY_MATCH,)


class TestWorkingSetAdmission:
    def test_ws_01_capacity_smaller_than_candidates_splits_exactly(self) -> None:
        candidates = (candidate("a"), candidate("b"), candidate("c"))
        working_set, excluded = admit(candidates, capacity=2)
        assert working_set.admitted == candidates[:2]
        assert excluded == candidates[2:]

    def test_ws_02_capacity_equals_candidate_count(self) -> None:
        candidates = (candidate("a"), candidate("b"))
        working_set, excluded = admit(candidates, capacity=2)
        assert working_set.admitted == candidates
        assert excluded == ()

    def test_ws_03_capacity_exceeds_candidate_count(self) -> None:
        candidates = (candidate("a"),)
        working_set, excluded = admit(candidates, capacity=5)
        assert working_set.admitted == candidates
        assert excluded == ()

    def test_ws_04_capacity_zero_is_a_valid_empty_working_set(self) -> None:
        candidates = (candidate("a"),)
        working_set, excluded = admit(candidates, capacity=0)
        assert working_set.admitted == ()
        assert excluded == candidates

    def test_ws_05_negative_capacity_rejected(self) -> None:
        with pytest.raises(ValueError):
            admit((candidate("a"),), capacity=-1)

    def test_ws_06_admit_preserves_caller_order_regardless_of_relevance(self) -> None:
        poorly_ordered = (
            candidate("lexical-first", relevance=(LEXICAL_MATCH,)),
            candidate("identity-second", relevance=(IDENTITY_MATCH,)),
        )
        working_set, _ = admit(poorly_ordered, capacity=2)
        assert working_set.admitted == poorly_ordered

    def test_ws_07_duplicate_items_in_input_are_not_deduplicated(self) -> None:
        same = candidate("a")
        working_set, excluded = admit((same, same), capacity=2)
        assert working_set.admitted == (same, same)

    def test_ws_09_mutating_input_after_admission_does_not_affect_working_set(self) -> None:
        candidates = [candidate("a"), candidate("b")]
        working_set, _ = admit(tuple(candidates), capacity=2)
        candidates.clear()
        assert working_set.admitted == (candidate("a"), candidate("b"))


class TestWorkingSetConstruction:
    def test_ws_capacity_must_not_be_negative(self) -> None:
        with pytest.raises(ValueError):
            WorkingSet(capacity=-1, admitted=())

    def test_ws_admitted_must_not_exceed_capacity(self) -> None:
        with pytest.raises(ValueError):
            WorkingSet(capacity=1, admitted=(candidate("a"), candidate("b")))


class TestImportSideEffects:
    def test_recall_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.recall",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_recall.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory.recall'`

- [ ] **Step 3: Write minimal implementation**

Create `src/memory/recall.py`:

```python
"""RecallCandidate / WorkingSet: retrieval evidence and bounded attention.

See MEMORY_SPECIFICATION.md #2-3 and MEMORY_ARCHITECTURE.md (recall.py, tier 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.identity import Ref
from core.time import WallInstant
from core.value import Kind

IDENTITY_MATCH = Kind("memory.relevance.identity")
LEXICAL_MATCH = Kind("memory.relevance.lexical")
CONTEXTUAL_MATCH = Kind("memory.relevance.contextual")


@dataclass(frozen=True, slots=True, kw_only=True)
class RecallCandidate:
    """A proposed memory item surfaced by retrieval, with structured evidence
    for why — never a truth claim. Not Entity-bearing: nothing targets a
    RecallCandidate by Ref.
    """

    item: Ref
    query_context: Context
    relevance: tuple[Kind, ...]
    retrieved_at: WallInstant

    def __post_init__(self) -> None:
        relevance = tuple(self.relevance)
        object.__setattr__(self, "relevance", relevance)
        if not relevance:
            raise ValueError("RecallCandidate.relevance must not be empty")
        if len(set(relevance)) != len(relevance):
            raise ValueError("RecallCandidate.relevance must not contain duplicate Kinds")


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkingSet:
    """The bounded subset of RecallCandidates currently admitted to active
    reasoning — the concrete realization of "attention."
    """

    capacity: int
    admitted: tuple[RecallCandidate, ...]

    def __post_init__(self) -> None:
        admitted = tuple(self.admitted)
        object.__setattr__(self, "admitted", admitted)
        if self.capacity < 0:
            raise ValueError("WorkingSet.capacity must not be negative")
        if len(admitted) > self.capacity:
            raise ValueError("WorkingSet.admitted must not exceed capacity")


def admit(
    candidates: tuple[RecallCandidate, ...], capacity: int
) -> tuple[WorkingSet, tuple[RecallCandidate, ...]]:
    """Admit the first ``capacity`` candidates in caller order. No
    reranking, scoring, deduplication, or retention lookup — admit() bounds
    attention, it does not decide relevance.
    """
    if capacity < 0:
        raise ValueError("capacity must not be negative")
    candidates = tuple(candidates)
    admitted = candidates[:capacity]
    excluded = candidates[capacity:]
    return WorkingSet(capacity=capacity, admitted=admitted), excluded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_recall.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/recall.py tests/memory/semantics/test_recall.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement recall

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `retention.py`

**Files:**
- Create: `src/memory/retention.py`
- Test: `tests/memory/semantics/test_retention.py`

**Interfaces:**
- Consumes: `core.identity.{Id, Ref, identity_of}`, `core.time.WallInstant`, `core.value.Kind`. No dependency on Tasks 2–6.
- Produces: `ACTIVE`, `DEPRIORITIZED`, `ARCHIVED` (well-known `Kind` constants), `RetentionMark`, `RetentionLog` (`.record(mark)`, `.current(item) -> Kind`, `.history(item) -> tuple[RetentionMark, ...]`).

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_retention.py`:

```python
"""Propositions for memory.retention.

Matrix reference: MEMORY_ADVERSARIAL_MATRIX.md section D (Retention).
"""

from __future__ import annotations

from datetime import UTC, datetime

from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind

from memory.retention import ACTIVE, ARCHIVED, DEPRIORITIZED, RetentionLog, RetentionMark

ITEM_KIND = Kind("memory.test.item")
T1 = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
T2 = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
T3 = WallInstant(datetime(2024, 1, 3, tzinfo=UTC))


def mark(item: Ref, accessibility: Kind, at: WallInstant, rationale: str | None = None) -> RetentionMark:
    return RetentionMark(item=item, accessibility=accessibility, at=at, rationale=rationale)


class TestDefaultAndBasicTransitions:
    def test_rt_01_no_marks_defaults_to_active(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        assert log.current(item) == ACTIVE

    def test_rt_02_active_to_deprioritized(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, DEPRIORITIZED, T1))
        assert log.current(item) == DEPRIORITIZED

    def test_rt_03_archived_then_active_all_marks_preserved(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ARCHIVED, T1))
        log.record(mark(item, ACTIVE, T2))
        assert log.current(item) == ACTIVE
        assert log.history(item) == (
            mark(item, ARCHIVED, T1),
            mark(item, ACTIVE, T2),
        )

    def test_rt_04_append_order_wins_over_earlier_at_timestamp(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ACTIVE, T3))
        log.record(mark(item, ARCHIVED, T1))  # appended later, earlier `at`
        assert log.current(item) == ARCHIVED

    def test_rt_07_duplicate_marks_both_preserved(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ARCHIVED, T1))
        log.record(mark(item, ARCHIVED, T1))
        assert len(log.history(item)) == 2

    def test_rt_08_rt_09_custom_kind_preserved_never_guessed(self) -> None:
        custom = Kind("memory.retention.legal_hold")
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, custom, T1))
        assert log.current(item) == custom
        assert log.current(item) not in (ACTIVE, DEPRIORITIZED, ARCHIVED)

    def test_rt_11_rationale_may_be_absent(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        recorded = log.record(mark(item, ACTIVE, T1, rationale=None))
        assert recorded.rationale is None


class TestIdentityAcrossNamespaces:
    def test_rt_05_mark_through_namespaced_ref_read_by_bare_id(self) -> None:
        target_id = Id(ITEM_KIND, "x")
        namespaced = Ref(id=target_id, namespace=Namespace(("finance",)))
        log = RetentionLog()
        log.record(mark(namespaced, ARCHIVED, T1))
        assert log.current(target_id) == ARCHIVED

    def test_rt_06_mark_through_one_namespace_read_through_another(self) -> None:
        target_id = Id(ITEM_KIND, "x")
        marked_via = Ref(id=target_id, namespace=Namespace(("finance",)))
        queried_via = Ref(id=target_id, namespace=Namespace(("ledger",)))
        log = RetentionLog()
        log.record(mark(marked_via, ARCHIVED, T1))
        assert log.current(queried_via) == ARCHIVED


class TestHistorySnapshot:
    def test_rt_12_earlier_snapshot_unaffected_by_later_marks(self) -> None:
        log = RetentionLog()
        item = Ref(id=Id(ITEM_KIND, "x"))
        log.record(mark(item, ACTIVE, T1))
        snapshot = log.history(item)
        log.record(mark(item, ARCHIVED, T2))
        assert snapshot == (mark(item, ACTIVE, T1),)
        assert len(log.history(item)) == 2


class TestImportSideEffects:
    def test_retention_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.retention",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_retention.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory.retention'`

- [ ] **Step 3: Write minimal implementation**

Create `src/memory/retention.py`:

```python
"""RetentionMark / RetentionLog: append-only accessibility history — the
concrete realization of "forgetting" as a projection over history, never
deletion. Direct sibling of Core's ContradictionLog.

See MEMORY_SPECIFICATION.md #4 and MEMORY_ARCHITECTURE.md (retention.py, tier 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.identity import Id, Ref, identity_of
from core.time import WallInstant
from core.value import Kind

ACTIVE = Kind("memory.retention.active")
DEPRIORITIZED = Kind("memory.retention.deprioritized")
ARCHIVED = Kind("memory.retention.archived")


@dataclass(frozen=True, slots=True, kw_only=True)
class RetentionMark:
    item: Ref
    accessibility: Kind
    at: WallInstant
    rationale: str | None = None


class RetentionLog:
    """Append-only, single-writer log — same concurrency family as Core's
    ContradictionLog. "Current" is always a projection over append order,
    never a stored flag and never sorted by ``at``.
    """

    def __init__(self) -> None:
        self._marks: list[RetentionMark] = []

    def record(self, mark: RetentionMark) -> RetentionMark:
        self._marks.append(mark)
        return mark

    def current(self, item: Id | Ref) -> Kind:
        target = identity_of(item)
        for mark in reversed(self._marks):
            if identity_of(mark.item) == target:
                return mark.accessibility
        return ACTIVE

    def history(self, item: Id | Ref) -> tuple[RetentionMark, ...]:
        target = identity_of(item)
        return tuple(mark for mark in self._marks if identity_of(mark.item) == target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_retention.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/retention.py tests/memory/semantics/test_retention.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement retention

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `belief.py`

**Files:**
- Create: `src/memory/belief.py`
- Test: `tests/memory/semantics/test_belief.py`

**Interfaces:**
- Consumes: `core.identity.{Id, Ref, identity_of}`, `core.context.Context`, `core.value.Kind`, `core.epistemic.{Claim, Contradiction, ContradictionLog, Resolution}`, `core.result.{Ok, Err}`. No dependency on Tasks 2–7 (independently testable, per the Pass 1 tier-0 graph).
- Produces: `DETERMINED`, `AMBIGUOUS`, `UNRESOLVED_CONFLICT`, `RESOLVED_OPAQUE_CONFLICT`, `UNKNOWN` (the closed status `Kind` vocabulary), `BeliefProjection`, `belief_state(*, subject, predicate, query_context, claims, conflict_entries) -> BeliefProjection`.

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_belief.py`:

```python
"""Propositions for memory.belief.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections E (ordinary),
F (Context/time), G (contradiction).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.epistemic import Claim, Contradiction, Resolution
from core.identity import Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind, Known

from memory.belief import (
    AMBIGUOUS,
    DETERMINED,
    RESOLVED_OPAQUE_CONFLICT,
    UNKNOWN,
    UNRESOLVED_CONFLICT,
    BeliefProjection,
    belief_state,
)

CLAIM_KIND = Kind("memory.test.claim")
CONTRA_KIND = Kind("memory.test.contradiction")
SUBJECT_KIND = Kind("memory.test.subject")
AGENT_KIND = Kind("memory.test.agent")
BALANCE = Kind("memory.test.balance")
BIRTH_DATE = Kind("memory.test.birth_date")
OWNER = Kind("memory.test.owner")
LEGAL_CONTROL = Kind("memory.test.legal_control")

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
SUBJECT = Id(SUBJECT_KIND, "checking")
AGENT = Id(AGENT_KIND, "agent-1")


def make_claim(
    claim_id: str,
    predicate: Kind,
    *,
    subject: Id | Ref = SUBJECT,
    context: Context,
    value: object = True,
) -> Claim[object]:
    return Claim(
        id=Id(CLAIM_KIND, claim_id),
        subject=subject,
        predicate=predicate,
        value=Known(value),
        context=context,
        asserted_by=AGENT,
        evidence_refs=(),
        at=AT,
    )


def ctx(as_of: WallInstant) -> Context:
    return Context(as_of=as_of)


MONDAY = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
FRIDAY = WallInstant(datetime(2024, 1, 5, tzinfo=UTC))
SATURDAY = WallInstant(datetime(2024, 1, 6, tzinfo=UTC))


class TestOrdinaryProjection:
    def test_bp_01_no_matching_claim_is_unknown(self) -> None:
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN
        assert result.candidates == ()

    def test_bp_02_exactly_one_compatible_claim_is_determined(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (claim,)

    def test_bp_03_two_compatible_claims_no_contradiction_is_ambiguous(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        result = belief_state(
            subject=SUBJECT,
            predicate=BIRTH_DATE,
            query_context=ctx(MONDAY),
            claims=(c1, c2),
            conflict_entries=(),
        )
        assert result.status == AMBIGUOUS
        assert set(result.candidates) == {c1, c2}

    def test_bp_04_different_predicate_ignored(self) -> None:
        other = make_claim("c1", Kind("memory.test.other"), context=ctx(MONDAY))
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(other,),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_05_different_subject_ignored(self) -> None:
        other = make_claim(
            "c1", BALANCE, subject=Id(SUBJECT_KIND, "savings"), context=ctx(MONDAY)
        )
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(other,),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_06_bp_07_subject_matches_across_id_and_namespaced_ref(self) -> None:
        claim = make_claim("c1", BALANCE, subject=SUBJECT, context=ctx(MONDAY), value=900)
        query_subject = Ref(id=SUBJECT, namespace=Namespace(("finance",)))
        result = belief_state(
            subject=query_subject,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == DETERMINED

    def test_bp_10_unknown_projection_distinct_from_claim_value_unknown(self) -> None:
        from core.value import UNKNOWN as CORE_UNKNOWN

        claim = Claim(
            id=Id(CLAIM_KIND, "c1"),
            subject=SUBJECT,
            predicate=BALANCE,
            value=CORE_UNKNOWN,
            context=ctx(MONDAY),
            asserted_by=AGENT,
            evidence_refs=(),
            at=AT,
        )
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates[0].value is CORE_UNKNOWN

    def test_bp_11_no_claim_survives_context_filtering_is_unknown(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(FRIDAY),
            claims=(claim,),
            conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_13_duplicate_identical_claim_does_not_fabricate_ambiguity(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT,
            predicate=BALANCE,
            query_context=ctx(MONDAY),
            claims=(claim, claim),
            conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (claim,)

    def test_bp_13_different_claims_sharing_an_id_raise(self) -> None:
        shared_id = Id(CLAIM_KIND, "c1")
        first = Claim(
            id=shared_id, subject=SUBJECT, predicate=BALANCE, value=Known(900),
            context=ctx(MONDAY), asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        second = Claim(
            id=shared_id, subject=SUBJECT, predicate=BALANCE, value=Known(901),
            context=ctx(MONDAY), asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        with pytest.raises(ValueError):
            belief_state(
                subject=SUBJECT,
                predicate=BALANCE,
                query_context=ctx(MONDAY),
                claims=(first, second),
                conflict_entries=(),
            )


class TestContextAndTime:
    def test_x_01_changing_balance_without_contradiction(self) -> None:
        monday_claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        friday_claim = make_claim("c2", BALANCE, context=ctx(FRIDAY), value=1200)
        claims = (monday_claim, friday_claim)

        monday_result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=claims, conflict_entries=(),
        )
        assert monday_result.status == DETERMINED
        assert monday_result.candidates == (monday_claim,)

        friday_result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(FRIDAY),
            claims=claims, conflict_entries=(),
        )
        assert friday_result.status == DETERMINED
        assert friday_result.candidates == (friday_claim,)

        saturday_result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(SATURDAY),
            claims=claims, conflict_entries=(),
        )
        assert saturday_result.status == UNKNOWN

    def test_ct_01_differing_as_of_is_a_context_conflict(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(FRIDAY),
            claims=(claim,), conflict_entries=(),
        )
        assert claim not in result.candidates

    def test_ct_03_optional_context_field_populated_one_side_fills_in(self) -> None:
        # Claim's context has `source` populated; query leaves it None —
        # Context.merge() fills in from the non-None side, so they're compatible.
        claim = make_claim("c1", BALANCE, context=Context(as_of=MONDAY, source="bank-api"), value=900)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(claim,), conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (claim,)

    def test_ct_04_incompatible_non_none_context_field_excludes_claim(self) -> None:
        claim = make_claim(
            "c1", BALANCE, context=Context(as_of=MONDAY, source="bank-api"), value=900
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE,
            query_context=Context(as_of=MONDAY, source="user-entered"),
            claims=(claim,), conflict_entries=(),
        )
        assert result.status == UNKNOWN
        assert claim not in result.candidates

    def test_ct_05_metadata_only_difference_uses_core_merge_not_custom_logic(self) -> None:
        claim = make_claim(
            "c1", BALANCE, context=Context(as_of=MONDAY, metadata={"note": "a"}), value=900
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE,
            query_context=Context(as_of=MONDAY, metadata={"note": "b"}),
            claims=(claim,), conflict_entries=(),
        )
        assert result.status == UNKNOWN

    def test_bp_08_ct_08_recency_never_breaks_a_tie(self) -> None:
        # Both claims share the exact same Context (same as_of), so neither
        # is excluded by Context.merge() — recency must not be used to pick
        # a winner; the result stays AMBIGUOUS.
        earlier = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        later = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(earlier, later), conflict_entries=(),
        )
        assert result.status == AMBIGUOUS

    def test_bp_09_only_context_compatible_claim_may_be_determined(self) -> None:
        old_compatible = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        newer_incompatible = make_claim("c2", BALANCE, context=ctx(FRIDAY), value=1200)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(old_compatible, newer_incompatible), conflict_entries=(),
        )
        assert result.status == DETERMINED
        assert result.candidates == (old_compatible,)

    def test_belief_state_never_inspects_claim_at(self) -> None:
        # Both claims are asserted (`.at`) in the opposite order from their
        # Context.as_of — if belief_state() ever consulted `.at`, this would
        # produce a different (wrong) result than context-only filtering.
        earlier_context_later_assertion = Claim(
            id=Id(CLAIM_KIND, "c1"), subject=SUBJECT, predicate=BALANCE,
            value=Known(900), context=ctx(MONDAY), asserted_by=AGENT,
            evidence_refs=(), at=WallInstant(datetime(2024, 6, 1, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(earlier_context_later_assertion,), conflict_entries=(),
        )
        assert result.status == DETERMINED


class TestContradiction:
    def _contradiction(self, statements: tuple[Ref, ...], contra_id: str = "k1") -> Contradiction:
        return Contradiction(
            id=Id(CONTRA_KIND, contra_id),
            subject=SUBJECT,
            statements=statements,
            detected_at=AT,
            context=ctx(MONDAY),
        )

    def test_cf_01_relevant_unresolved_contradiction(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert contradiction in result.conflict_entries
        assert result.candidates == (c1, c2)

    def test_cf_relevance_survives_context_filtering(self) -> None:
        # The single most subtle property in this module: relevant-Contradiction
        # membership is computed from the FULL (subject, predicate) slot, gathered
        # BEFORE context filtering — not from `candidates` (post-filter). A claim
        # that is in the slot but context-incompatible with the query must still
        # establish relevance, even though it does not appear in `candidates`.
        filtered_out = make_claim("c1", BIRTH_DATE, context=ctx(FRIDAY), value="1990-04-12")
        contradiction = self._contradiction(
            (Ref(id=filtered_out.id), Ref(id=Id(CLAIM_KIND, "other")))
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(filtered_out,), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert result.candidates == ()

    def test_cf_02_cf_03_cf_04_resolution_never_selects_a_winner(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        resolution = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale='{"accepted_claim": "c1"}',  # machine-looking text — still opaque
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction, resolution),
        )
        assert result.status == RESOLVED_OPAQUE_CONFLICT
        assert result.conflict_entries == (contradiction, resolution)

    def test_cf_07_irrelevant_contradiction_does_not_affect_slot(self) -> None:
        c1 = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        other_subject_claim = make_claim(
            "c2", BALANCE, subject=Id(SUBJECT_KIND, "savings"), context=ctx(MONDAY), value=50
        )
        contradiction = Contradiction(
            id=Id(CONTRA_KIND, "k1"),
            subject=Id(SUBJECT_KIND, "savings"),
            statements=(Ref(id=c1.id), Ref(id=other_subject_claim.id)),
            detected_at=AT,
            context=ctx(MONDAY),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(c1, other_subject_claim), conflict_entries=(contradiction,),
        )
        assert result.status == DETERMINED

    def test_x_08_cross_predicate_conflict_still_relevant(self) -> None:
        owner_claim = make_claim("c1", OWNER, context=ctx(MONDAY), value="alice")
        legal_control_claim = make_claim("c2", LEGAL_CONTROL, context=ctx(MONDAY), value="bob")
        contradiction = self._contradiction(
            (Ref(id=owner_claim.id), Ref(id=legal_control_claim.id))
        )
        result = belief_state(
            subject=SUBJECT, predicate=OWNER, query_context=ctx(MONDAY),
            claims=(owner_claim, legal_control_claim), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT

    def test_cf_10_single_candidate_with_relevant_conflict_is_not_determined(self) -> None:
        c1 = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        # The other conflicting statement isn't itself in the supplied
        # claims (CL-05/CL-06): relevance still holds via c1.
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=Id(CLAIM_KIND, "missing"))))
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(c1,), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert result.candidates == (c1,)

    def test_cf_11_empty_candidates_with_relevant_conflict_still_surfaces(self) -> None:
        contradiction = self._contradiction(
            (Ref(id=Id(CLAIM_KIND, "gone-1")), Ref(id=Id(CLAIM_KIND, "gone-2")))
        )
        # Neither statement resolves to a supplied claim, so this
        # contradiction is *not* relevant by the frozen rule — included here
        # to prove absence of a false positive, paired with cf_10 above for
        # the true-positive case.
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(), conflict_entries=(contradiction,),
        )
        assert result.status == UNKNOWN

    def test_x_09_resolved_and_unresolved_conflicts_coexist(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        c3 = make_claim("c3", BIRTH_DATE, context=ctx(MONDAY), value="1992-04-12")
        resolved = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)), contra_id="k1")
        unresolved = self._contradiction((Ref(id=c1.id), Ref(id=c3.id)), contra_id="k2")
        resolution = Resolution(
            contradiction=Ref(id=resolved.id),
            rationale="addressed",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2, c3), conflict_entries=(resolved, resolution, unresolved),
        )
        assert result.status == UNRESOLVED_CONFLICT
        assert resolved in result.conflict_entries
        assert resolution in result.conflict_entries
        assert unresolved in result.conflict_entries

    def test_cf_09_statement_ref_matches_via_identity_not_bare_equality(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        namespaced_statement = Ref(id=c1.id, namespace=Namespace(("some", "ns")))
        contradiction = self._contradiction((namespaced_statement, Ref(id=c2.id)))
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction,),
        )
        assert result.status == UNRESOLVED_CONFLICT

    def test_cf_12_multiple_resolutions_for_same_contradiction_all_preserved(self) -> None:
        c1 = make_claim("c1", BIRTH_DATE, context=ctx(MONDAY), value="1990-04-12")
        c2 = make_claim("c2", BIRTH_DATE, context=ctx(MONDAY), value="1991-04-12")
        contradiction = self._contradiction((Ref(id=c1.id), Ref(id=c2.id)))
        resolution1 = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale="first pass",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC)),
        )
        resolution2 = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale="revisited",
            resolved_by=AGENT,
            at=WallInstant(datetime(2024, 1, 3, tzinfo=UTC)),
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=ctx(MONDAY),
            claims=(c1, c2), conflict_entries=(contradiction, resolution1, resolution2),
        )
        assert result.status == RESOLVED_OPAQUE_CONFLICT
        assert result.conflict_entries == (contradiction, resolution1, resolution2)

    def test_cf_14_differing_authority_values_never_compared_to_each_other(self) -> None:
        # Each claim's Context.authority is only ever merged against the query's
        # (which leaves it None), never against the other claim's — so two claims
        # with different "authority-looking" values both remain independently
        # compatible, and neither is preferred. Proves Memory doesn't judge authority.
        query_context = ctx(MONDAY)
        verified = Claim(
            id=Id(CLAIM_KIND, "cv"), subject=SUBJECT, predicate=BIRTH_DATE,
            value=Known("1990-04-12"), context=Context(as_of=MONDAY, authority="verified"),
            asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        unverified = Claim(
            id=Id(CLAIM_KIND, "cu"), subject=SUBJECT, predicate=BIRTH_DATE,
            value=Known("1991-04-12"), context=Context(as_of=MONDAY, authority="unverified"),
            asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        result = belief_state(
            subject=SUBJECT, predicate=BIRTH_DATE, query_context=query_context,
            claims=(verified, unverified), conflict_entries=(),
        )
        assert result.status == AMBIGUOUS
        assert set(result.candidates) == {verified, unverified}


class TestBeliefProjectionConstructorInvariants:
    def test_determined_requires_exactly_one_candidate_and_no_conflicts(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=DETERMINED, candidates=(claim, claim), conflict_entries=(),
            )

    def test_unknown_requires_zero_candidates(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=UNKNOWN, candidates=(claim,), conflict_entries=(),
            )

    def test_unresolved_conflict_requires_at_least_one_conflict_entry(self) -> None:
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=UNRESOLVED_CONFLICT, candidates=(), conflict_entries=(),
            )

    def test_unknown_status_kind_rejected(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        with pytest.raises(ValueError):
            BeliefProjection(
                subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
                status=Kind("memory.belief.not_a_real_status"),
                candidates=(claim,), conflict_entries=(),
            )


class TestQueryContextRetained:
    def test_belief_projection_preserves_query_context(self) -> None:
        claim = make_claim("c1", BALANCE, context=ctx(MONDAY), value=900)
        result = belief_state(
            subject=SUBJECT, predicate=BALANCE, query_context=ctx(MONDAY),
            claims=(claim,), conflict_entries=(),
        )
        assert result.query_context == ctx(MONDAY)


class TestImportSideEffects:
    def test_belief_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.belief",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_belief.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'memory.belief'`

- [ ] **Step 3: Write minimal implementation**

Create `src/memory/belief.py`:

```python
"""BeliefProjection: the mechanically-derived result of asking "what is
currently believed" for a (subject, predicate) slot under a query Context.
Memory selects; Memory does not judge.

See MEMORY_SPECIFICATION.md #5 and MEMORY_ARCHITECTURE.md (belief.py, tier 0).
"""

from __future__ import annotations

from dataclasses import dataclass

from core.context import Context
from core.epistemic import Claim, Contradiction, ContradictionLog, Resolution
from core.identity import Id, Ref, identity_of
from core.result import Err, Ok
from core.value import Kind

DETERMINED = Kind("memory.belief.determined")
AMBIGUOUS = Kind("memory.belief.ambiguous")
UNRESOLVED_CONFLICT = Kind("memory.belief.unresolved_conflict")
RESOLVED_OPAQUE_CONFLICT = Kind("memory.belief.resolved_opaque_conflict")
UNKNOWN = Kind("memory.belief.unknown")

_STATUS_KINDS = frozenset(
    {DETERMINED, AMBIGUOUS, UNRESOLVED_CONFLICT, RESOLVED_OPAQUE_CONFLICT, UNKNOWN}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class BeliefProjection:
    """An operation's structured result, not a persisted record — same
    category as Core's own AncestorReport.
    """

    subject: Id | Ref
    predicate: Kind
    query_context: Context
    status: Kind
    candidates: tuple[Claim[object], ...]
    conflict_entries: tuple[Contradiction | Resolution, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "conflict_entries", tuple(self.conflict_entries))
        if self.status not in _STATUS_KINDS:
            raise ValueError(f"unknown BeliefProjection status: {self.status!r}")

        has_conflict = len(self.conflict_entries) > 0
        candidate_count = len(self.candidates)

        if self.status == DETERMINED:
            if candidate_count != 1 or has_conflict:
                raise ValueError("DETERMINED requires exactly 1 candidate and no conflict entries")
        elif self.status == AMBIGUOUS:
            if candidate_count < 2 or has_conflict:
                raise ValueError("AMBIGUOUS requires 2+ candidates and no conflict entries")
        elif self.status == UNKNOWN:
            if candidate_count != 0 or has_conflict:
                raise ValueError("UNKNOWN requires 0 candidates and no conflict entries")
        elif self.status in (UNRESOLVED_CONFLICT, RESOLVED_OPAQUE_CONFLICT) and not has_conflict:
            raise ValueError(f"{self.status} requires at least one conflict entry")


def belief_state(
    *,
    subject: Id | Ref,
    predicate: Kind,
    query_context: Context,
    claims: tuple[Claim[object], ...],
    conflict_entries: tuple[Contradiction | Resolution, ...],
) -> BeliefProjection:
    """Mechanical-only projection: never reads Claim.at, never parses
    Resolution.rationale, never performs temporal carry-forward.
    """
    subject_id = identity_of(subject)

    normalized: dict[Id, Claim[object]] = {}
    order: list[Id] = []
    for claim in claims:
        existing = normalized.get(claim.id)
        if existing is None:
            normalized[claim.id] = claim
            order.append(claim.id)
        elif existing != claim:
            raise ValueError(f"conflicting Claim records supplied for the same Id: {claim.id!r}")

    slot_claim_ids: set[Id] = set()
    slot_claims: list[Claim[object]] = []
    for claim_id in order:
        claim = normalized[claim_id]
        if identity_of(claim.subject) == subject_id and claim.predicate == predicate:
            slot_claim_ids.add(claim_id)
            slot_claims.append(claim)

    compatible: list[Claim[object]] = []
    for claim in slot_claims:
        merged = claim.context.merge(query_context)
        if isinstance(merged, Ok):
            compatible.append(claim)
        elif not isinstance(merged, Err):
            raise TypeError(f"Context.merge() returned neither Ok nor Err: {merged!r}")

    log = ContradictionLog()
    for entry in conflict_entries:
        log.record(entry)

    relevant_entries: list[Contradiction | Resolution] = []
    relevant_contradiction_ids: set[Id] = set()
    for entry in log.entries():
        if isinstance(entry, Contradiction):
            if identity_of(entry.subject) != subject_id:
                continue
            if not any(identity_of(statement) in slot_claim_ids for statement in entry.statements):
                continue
            relevant_contradiction_ids.add(entry.id)
            relevant_entries.append(entry)
        elif entry.contradiction.id in relevant_contradiction_ids:
            relevant_entries.append(entry)

    resolved_ids = {
        entry.contradiction.id for entry in relevant_entries if isinstance(entry, Resolution)
    }
    unresolved_relevant = relevant_contradiction_ids - resolved_ids

    if unresolved_relevant:
        status = UNRESOLVED_CONFLICT
    elif relevant_entries:
        status = RESOLVED_OPAQUE_CONFLICT
    elif len(compatible) >= 2:
        status = AMBIGUOUS
    elif len(compatible) == 1:
        status = DETERMINED
    else:
        status = UNKNOWN

    return BeliefProjection(
        subject=subject,
        predicate=predicate,
        query_context=query_context,
        status=status,
        candidates=tuple(compatible),
        conflict_entries=tuple(relevant_entries),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_belief.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/belief.py tests/memory/semantics/test_belief.py
git commit -m "$(cat <<'EOF'
Memory Pass 1: implement belief

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Pass 1 closing gate

**Files:** none created; this task only runs verification across everything Tasks 1–8 produced, per `docs/memory-passes/01-semantic-constructions.md` section 11 ("Quality gates").

**Interfaces:**
- Consumes: the full `src/memory/` tree and `tests/memory/semantics/` suite from Tasks 1–8.
- Produces: nothing new — this is Pass 1's checkpoint, gating the transition to Pass 2.

- [ ] **Step 1: Run the full test suite (Core + Memory)**

Run: `uv run pytest -v`
Expected: PASS — every existing Core test plus every Task 1–8 Memory test green, none skipped/xfail.

- [ ] **Step 2: Run Ruff**

Run: `uv run ruff check src/memory tests/memory`
Expected: no findings. Fix any and re-run before proceeding.

- [ ] **Step 3: Run Pyright**

Run: `uv run pyright src/memory tests/memory`
Expected: 0 errors in strict mode. Fix any and re-run before proceeding.

- [ ] **Step 4: Manually confirm no forbidden import exists**

Run:
```bash
grep -rn "^import sqlite3\|^from sqlite3\|memory\.store\|memory\.sqlite_store" src/memory
```
Expected: no output. (This is a manual pre-check standing in for Pass 4's formal import-graph test, which is out of scope for Pass 1 per the preregistration.)

- [ ] **Step 5: Manually confirm every A–J matrix ID has a test home**

Cross-check `MEMORY_ADVERSARIAL_MATRIX.md` sections A–J against the test files written in Tasks 2–8. Every row must be traceable to at least one test (by docstring/comment reference, as written throughout Tasks 2–8) or to a documented, deliberate non-applicability (e.g. FL-06 is explicitly N/A once non-finite floats are rejected outright — noted in the matrix itself). Record any gap found and close it with an additional test before continuing — do not defer a genuine gap to Pass 2.

- [ ] **Step 6: Commit the closing state (only if Steps 1–5 required fixes)**

If every prior task's commit already left the tree green, this step is a no-op — nothing to commit. If Steps 1–5 required corrections, stage exactly those corrections:

```bash
git add -A
git commit -m "$(cat <<'EOF'
Memory Pass 1: closing gate corrections

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

Pass 1 is closed once this task's steps pass clean. Pass 2 (persistence boundary + in-memory reference store) starts a new preregistration, per `MEMORY_ARCHITECTURE.md`'s implementation order.
