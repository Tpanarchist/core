# Memory Pass 3: SQLite Durable Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/memory/sqlite_store.py` — `SqliteMemoryStore`, a durable, replay-backed implementation of the already-frozen `MemoryStore` protocol, proving that `InMemoryStore`'s reference semantics survive an append-only SQLite operation journal, process restart, transactional failure, FTS5 indexing, and corrupted external state, without SQLite ever acquiring semantic authority.

**Architecture:** SQLite's authoritative state is an append-only journal of `MemoryStore` operations (`persist`/`create_episode`/`append_episode`/`close_episode`), each row SHA-256-checksummed and strictly sequenced. Opening a database replays every journal row, in order, through a fresh `InMemoryStore`'s **public API only** — never its private internals — producing the current semantic projection. Every write applies to that in-process reference first (proving semantic legality before durable state is appended), then commits one atomic SQLite transaction (journal row + full FTS5 rebuild). A rolled-back transaction reloads the reference from the last-committed journal via the same replay path used at open. FTS5 is a fully rebuildable derived index, never retrieval authority — all indexed text comes from the already-frozen `lexical_content()`, and query methods (`resolve`/`claims_for`/`conflicts_for`/`retention_for`/`retrieve`) all delegate to the replayed reference in this pass.

**Tech Stack:** Python 3.13, stdlib `sqlite3`/`hashlib`/`hmac`/`os`, pytest, Ruff, Pyright (strict). Builds directly on `src/memory/store.py` (Pass 2, closed) and `src/memory/codec.py` (Pass 1, closed) — no other new dependencies.

**Spec:** `docs/memory-passes/03-sqlite-backend.md` (Pass 3 preregistration — 117 sections, the binding authority this plan argues from), `MEMORY_ARCHITECTURE.md` ("The SQLite backend is not Memory v0" section, dependency graph), `MEMORY_ADVERSARIAL_MATRIX.md` (sections referenced by case-ID prefix: `ES-*` = Episode SQLite transitions, `CL-11` = contradiction-lookup equivalence, `RR-*` = retrieval/retention, `FT-*` = FTS indexing, `BE-*` = backend equivalence, `IM-*` = import graph, `DB-*` = database integrity).

## Global Constraints

- **Exact allowed import list for `sqlite_store.py`** (preregistration §79, `MEMORY_ARCHITECTURE.md` dependency graph): `memory.store`, `memory.codec`, `memory.recall`, `memory.retention`, `core.identity`, `core.time`, `core.context`, `core.value`, `core.epistemic`, `core.observation`, `core.event`, `core.effect`, `core.provenance`, `core.error`, plus stdlib `sqlite3`, `hashlib`, `hmac`, `os`. Never `memory.belief`, `core.state`, `core.trace`, `core.transform`, `core.relation`.
- **`sqlite3` exists only in `memory/sqlite_store.py`** (§80) — never add it to `episode.py`, `recall.py`, `retention.py`, `belief.py`, `codec.py`, or `store.py`.
- **No private `memory.store` imports** (§29): never import or call `_canonical_record`, `_snapshot_context`, `_check`, `_commit`, `_entities`, `_entity_order`, `_conflict_entries`, or any other underscore-prefixed `InMemoryStore`/module-level name. Build only on `MemoryStore`, `IdentityCollision`, `UnsupportedMemoryRecord`, `EntityMemoryRecord`, `NonEntityMemoryRecord`, `MemoryRecord`, `PersistRecord`, `InMemoryStore`, `lexical_content`, `RetrievalQuery`.
- **No operation reads a wall/monotonic clock, allocates a UUID, or uses `random`** (§57-58). `retrieve()` forwards the caller's explicit `retrieved_at`. Journal `seq` values are storage-local positions, never identity.
- **Schema version frozen at `1`** (§5) — no migration framework. Any other version raises `UnsupportedSchemaVersion` immediately; never guessed, upgraded, downgraded, or silently rewritten.
- **SQL parameterization only** (§89): every value bound into SQL uses `?` placeholders. Never interpolate `Id.value`, `Kind.value`, user lexical text, record content, or rationale into SQL text. Schema identifiers (table/column names) are fixed constants, never derived from data.
- **No public raw-SQL/journal API** (§82): never expose `operations()`, `journal()`, `sequence()`, `raw_sql()`, `.connection`, `.cursor`, or `fts_query()`. Tests needing direct inspection open their own separate `sqlite3` connection to the test database file.
- **No delete/compaction/migration/async/pooling/retry-backoff surface** (§84-88, §55): the backend never exposes `DELETE record`/`purge`, never compacts the journal, never migrates schemas, never uses `async def`/`aiosqlite`, never pools connections beyond the one the instance owns, never retries or backs off on SQLite contention.
- **Import-time side effects forbidden** (§78): importing `memory.sqlite_store` must never open a database, touch the filesystem, read the clock, allocate a UUID, read randomness, or open a socket. All I/O begins at `SqliteMemoryStore(path)` construction.
- **Exception boundary stays sharp** (§94): semantic failures (`IdentityCollision`, `UnsupportedMemoryRecord`, `UnsupportedPersistedValue`, `ValueError`/`KeyError`/`TypeError` as already raised by `MemoryStore` operations) propagate completely unchanged — never wrapped, never collapsed into a generic "database error." Corrupt durable state becomes `StoreCorruption`/`UnsupportedSchemaVersion`. Closed-resource misuse becomes `SqliteStoreClosed`.
- **Journal decode never trusts Python typing** (§96): every byte sequence read back from SQLite is untrusted external input. Every decoded node is `isinstance`-checked and length-checked before being indexed, unpacked, or used to construct a Core object — carrying forward the exact lesson Pass 1's codec review already established for `_decode_node`.
- **Test commands:** `uv run pytest tests/memory/semantics/test_sqlite_store.py -v` (or the split integrity file once it exists) for scoped runs, `uv run pytest -q` for the full suite. Lint/type: `uv run ruff check src/memory tests/memory`, `uv run pyright src/memory tests/memory` (strict, 0 errors).
- **Commits end with:** `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- **Baseline before Task 1:** 654 tests passing, Ruff clean, Pyright strict clean, working tree clean (Pass 2 fully closed, pushed).

---

### Task 1: Schema, lifecycle, and exception types

**Files:**
- Create: `src/memory/sqlite_store.py`
- Create: `tests/memory/semantics/test_sqlite_store.py`

**Interfaces:**
- Consumes: nothing new — this task lays the foundation.
- Produces: `StoreCorruption`, `UnsupportedSchemaVersion`, `SqliteStoreClosed` (exceptions); module-level schema constants `_SCHEMA_VERSION`, `_SCHEMA_SQL`, `_REQUIRED_TABLES`; `SqliteMemoryStore.__init__(self, path: str | os.PathLike[str]) -> None`; `SqliteMemoryStore.close(self) -> None`. Tasks 2-7 all build on this class and these exceptions. The constructor's existing-database branch is intentionally incomplete here (replay doesn't exist until Task 3) — marked with a `# NOTE(Task 3): ...` comment, exactly as Pass 2's Task 2→3 handoff worked.

Before writing code, skim `src/memory/store.py`'s own module docstring and import block for house style (module docstring citing the specs it implements, `from __future__ import annotations` first, then stdlib, then `core.*`, then `memory.*`).

- [ ] **Step 1: Write the failing test**

Create `tests/memory/semantics/test_sqlite_store.py`:

```python
"""Propositions for memory.sqlite_store.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections
ES (Episode SQLite transitions), CL-11 (contradiction lookup equivalence),
RR (retrieval/retention), FT (FTS indexing), BE (backend equivalence),
IM (import graph), DB (database integrity).
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from memory.sqlite_store import (
    SqliteMemoryStore,
    SqliteStoreClosed,
    StoreCorruption,
    UnsupportedSchemaVersion,
)


class TestNewDatabaseInitialization:
    def test_creates_expected_schema_objects(self, tmp_path) -> None:
        path = tmp_path / "new.sqlite"
        store = SqliteMemoryStore(path)
        conn = sqlite3.connect(str(path))
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name IN ('memory_meta','memory_ops','memory_fts')"
                ).fetchall()
            }
            assert names == {"memory_meta", "memory_ops", "memory_fts"}
            version = conn.execute(
                "SELECT value FROM memory_meta WHERE key = 'schema_version'"
            ).fetchone()
            assert version == ("1",)
            op_count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
            assert op_count == 0
        finally:
            conn.close()
            store.close()

    def test_memory_path_creates_in_memory_database(self) -> None:
        store = SqliteMemoryStore(":memory:")
        store.close()  # must not raise

    def test_integrity_check_passes_on_fresh_database(self, tmp_path) -> None:
        path = tmp_path / "fresh.sqlite"
        store = SqliteMemoryStore(path)
        conn = sqlite3.connect(str(path))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            assert result == ("ok",)
        finally:
            conn.close()
            store.close()


class TestExistingDatabaseDetection:
    def test_reopening_valid_v1_database_succeeds(self, tmp_path) -> None:
        path = tmp_path / "reopen.sqlite"
        first = SqliteMemoryStore(path)
        first.close()
        second = SqliteMemoryStore(path)  # must not raise
        second.close()

    def test_partial_schema_missing_ops_table_is_corruption(self, tmp_path) -> None:
        path = tmp_path / "partial.sqlite"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO memory_meta VALUES ('schema_version', '1');"
        )
        conn.commit()
        conn.close()
        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_partial_schema_missing_meta_table_is_corruption(self, tmp_path) -> None:
        path = tmp_path / "partial2.sqlite"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE memory_ops ("
            "seq INTEGER PRIMARY KEY, op_kind TEXT NOT NULL, "
            "payload BLOB NOT NULL, digest BLOB NOT NULL);"
            "CREATE VIRTUAL TABLE memory_fts USING fts5("
            "id_kind UNINDEXED, id_value UNINDEXED, field_index UNINDEXED, content);"
        )
        conn.commit()
        conn.close()
        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_memory_fts_replaced_by_non_fts_table_is_corruption(self, tmp_path) -> None:
        path = tmp_path / "fakefts.sqlite"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO memory_meta VALUES ('schema_version', '1');"
            "CREATE TABLE memory_ops ("
            "seq INTEGER PRIMARY KEY, op_kind TEXT NOT NULL, "
            "payload BLOB NOT NULL, digest BLOB NOT NULL);"
            "CREATE TABLE memory_fts (content TEXT);"
        )
        conn.commit()
        conn.close()
        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_missing_schema_version_key_is_corruption(self, tmp_path) -> None:
        path = tmp_path / "noversion.sqlite"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE memory_ops ("
            "seq INTEGER PRIMARY KEY, op_kind TEXT NOT NULL, "
            "payload BLOB NOT NULL, digest BLOB NOT NULL);"
            "CREATE VIRTUAL TABLE memory_fts USING fts5("
            "id_kind UNINDEXED, id_value UNINDEXED, field_index UNINDEXED, content);"
        )
        conn.commit()
        conn.close()
        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)


class TestUnsupportedSchemaVersion:
    def test_wrong_schema_version_raises(self, tmp_path) -> None:
        path = tmp_path / "wrongversion.sqlite"
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO memory_meta VALUES ('schema_version', '2');"
            "CREATE TABLE memory_ops ("
            "seq INTEGER PRIMARY KEY, op_kind TEXT NOT NULL, "
            "payload BLOB NOT NULL, digest BLOB NOT NULL);"
            "CREATE VIRTUAL TABLE memory_fts USING fts5("
            "id_kind UNINDEXED, id_value UNINDEXED, field_index UNINDEXED, content);"
        )
        conn.commit()
        conn.close()
        with pytest.raises(UnsupportedSchemaVersion) as excinfo:
            SqliteMemoryStore(path)
        assert excinfo.value.found == "2"
        assert excinfo.value.supported == 1


class TestStoreLifecycle:
    def test_close_then_persist_raises_closed(self, tmp_path) -> None:
        from core.context import Context
        from core.identity import Id
        from core.observation import Observation
        from core.time import WallInstant
        from core.value import Kind
        from datetime import UTC, datetime

        at = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
        store = SqliteMemoryStore(tmp_path / "closed.sqlite")
        store.close()
        obs = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=Id(Kind("t.subj"), "s1"), value="x",
            at=at, source="s", context=Context(as_of=at),
        )
        with pytest.raises(SqliteStoreClosed):
            store.persist(obs)  # type: ignore[arg-type]

    def test_close_is_idempotent(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "idempotent-close.sqlite")
        store.close()
        store.close()  # must not raise

    def test_no_temp_file_left_after_close(self, tmp_path) -> None:
        path = tmp_path / "cleanup.sqlite"
        store = SqliteMemoryStore(path)
        store.close()
        assert path.exists()  # the db file itself stays; only the connection closes


class TestExceptionShapes:
    def test_store_corruption_carries_sequence_and_reason(self) -> None:
        exc = StoreCorruption(sequence=3, reason="checksum mismatch")
        assert exc.sequence == 3
        assert exc.reason == "checksum mismatch"

    def test_store_corruption_sequence_may_be_none(self) -> None:
        exc = StoreCorruption(sequence=None, reason="malformed schema")
        assert exc.sequence is None

    def test_unsupported_schema_version_carries_found_and_supported(self) -> None:
        exc = UnsupportedSchemaVersion(found="7", supported=1)
        assert exc.found == "7"
        assert exc.supported == 1

    def test_all_three_are_runtime_errors(self) -> None:
        assert issubclass(StoreCorruption, RuntimeError)
        assert issubclass(UnsupportedSchemaVersion, RuntimeError)
        assert issubclass(SqliteStoreClosed, RuntimeError)


class TestImportHasNoSideEffects:
    def test_fresh_import_has_no_side_effects(self, tmp_path) -> None:
        from _memory_side_effects import assert_fresh_import_has_no_side_effects

        assert_fresh_import_has_no_side_effects("memory.sqlite_store")
```

Check `tests/memory/_memory_side_effects.py` (used by Pass 1/2's own `test_store_import_has_no_side_effects` tests) for `assert_fresh_import_has_no_side_effects`'s exact signature before using it — reuse it verbatim, don't reimplement it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'memory.sqlite_store'`

- [ ] **Step 3: Write minimal implementation**

Create `src/memory/sqlite_store.py`:

```python
"""SqliteMemoryStore: the durable, replay-backed MemoryStore implementer.

SQLite's authoritative state is an append-only journal of MemoryStore
operations, never a record-per-table semantic model. Opening a database
replays every journal row, in order, through a fresh InMemoryStore's public
API only. FTS5 is a rebuildable derived index, never retrieval authority.

See MEMORY_ARCHITECTURE.md ("The SQLite backend is not Memory v0") and
docs/memory-passes/03-sqlite-backend.md (sqlite_store.py, tier 2).
"""

from __future__ import annotations

import os
import sqlite3

from memory.store import InMemoryStore

_SCHEMA_VERSION = "1"
_SUPPORTED_SCHEMA_VERSION = 1

_REQUIRED_TABLES = ("memory_meta", "memory_ops", "memory_fts")

# Individual CREATE statements, executed one at a time inside a manual
# BEGIN IMMEDIATE/COMMIT/ROLLBACK in _initialize_new_database below.
# VERIFIED (see plan notes after this code block): sqlite3.Connection.
# executescript() ALWAYS issues an implicit COMMIT before running, which
# breaks any manually-opened transaction and leaves no real atomicity on
# partial failure. _SCHEMA_SQL (the combined script, composed from these
# same statements) stays available for tests, which call executescript()
# directly on a connection with no pending manual transaction — safe there.
_CREATE_MEMORY_META = """
CREATE TABLE memory_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_CREATE_MEMORY_OPS = """
CREATE TABLE memory_ops (
    seq       INTEGER PRIMARY KEY,
    op_kind   TEXT NOT NULL,
    payload   BLOB NOT NULL,
    digest    BLOB NOT NULL
)
"""

_CREATE_MEMORY_FTS = """
CREATE VIRTUAL TABLE memory_fts USING fts5(
    id_kind     UNINDEXED,
    id_value    UNINDEXED,
    field_index UNINDEXED,
    content
)
"""

_SCHEMA_STATEMENTS = (_CREATE_MEMORY_META, _CREATE_MEMORY_OPS, _CREATE_MEMORY_FTS)
_SCHEMA_SQL = ";\n".join(_SCHEMA_STATEMENTS) + ";"


class StoreCorruption(RuntimeError):
    """Raised when durable SQLite state cannot be trusted as a valid Memory
    history — a failed integrity check, a broken sequence/checksum, an
    unknown operation/record tag, a malformed payload, or a well-formed
    journal that reference semantics reject on replay. ``sequence`` names
    the offending journal position when one is known.
    """

    def __init__(self, sequence: int | None, reason: str) -> None:
        location = f"at seq={sequence}" if sequence is not None else "in schema/metadata"
        super().__init__(f"store corruption {location}: {reason}")
        self.sequence = sequence
        self.reason = reason


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when an existing database's schema_version is not the one
    this Pass-3 implementation supports. Never guessed, migrated, upgraded,
    or downgraded.
    """

    def __init__(self, found: str, supported: int) -> None:
        super().__init__(
            f"unsupported schema version {found!r}: this implementation supports {supported}"
        )
        self.found = found
        self.supported = supported


class SqliteStoreClosed(RuntimeError):
    """Raised by any public operation on a SqliteMemoryStore after close()."""


class SqliteMemoryStore:
    """Durable MemoryStore implementer backed by an append-only SQLite
    operation journal. Single-writer, not thread-safe, no multi-process
    write guarantee — matches InMemoryStore's own concurrency posture.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._closed = False
        self._conn = sqlite3.connect(str(path))
        self._conn.isolation_level = None  # manual BEGIN/COMMIT/ROLLBACK control
        self._conn.execute("PRAGMA foreign_keys = OFF")

        present = self._present_required_tables()
        if not present:
            self._initialize_new_database()
            self._reference = InMemoryStore()
        elif present == set(_REQUIRED_TABLES):
            self._validate_existing_schema()
            # NOTE(Task 3): replace this with real journal replay — the
            # constructor must reconstruct self._reference (and rebuild FTS)
            # from the committed journal instead of starting empty.
            self._reference = InMemoryStore()
        else:
            self._conn.close()
            raise StoreCorruption(
                None,
                f"partial Memory schema: found {sorted(present)}, "
                f"expected all of {sorted(_REQUIRED_TABLES)} or none",
            )

    def _present_required_tables(self) -> set[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name IN (?,?,?)",
            _REQUIRED_TABLES,
        ).fetchall()
        return {row[0] for row in rows}

    def _initialize_new_database(self) -> None:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT INTO memory_meta(key, value) VALUES ('schema_version', ?)",
                (_SCHEMA_VERSION,),
            )
            self._conn.execute("COMMIT")
        except Exception:
            # BEGIN IMMEDIATE itself can be the failing statement (e.g. lock
            # contention from another writer) — ROLLBACK then has nothing to
            # roll back and would raise its own error, masking the real one.
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            self._conn.close()
            raise

    def _validate_existing_schema(self) -> None:
        integrity = self._conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            self._conn.close()
            raise StoreCorruption(None, f"PRAGMA integrity_check failed: {integrity!r}")

        fts_sql_row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'memory_fts'"
        ).fetchone()
        if fts_sql_row is None or "fts5" not in fts_sql_row[0].lower():
            self._conn.close()
            raise StoreCorruption(None, "memory_fts is not a valid FTS5 table")

        version_row = self._conn.execute(
            "SELECT value FROM memory_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version_row is None:
            self._conn.close()
            raise StoreCorruption(None, "memory_meta missing required 'schema_version' key")
        found = version_row[0]
        if found != _SCHEMA_VERSION:
            self._conn.close()
            raise UnsupportedSchemaVersion(found=found, supported=_SUPPORTED_SCHEMA_VERSION)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._conn.close()

    def _require_open(self) -> None:
        if self._closed:
            raise SqliteStoreClosed("this SqliteMemoryStore has been closed")
```

**Two things verified empirically before this plan was written, both already reflected in the code above — do not "simplify" them back to the more obvious-looking version:**

1. `sqlite3.Connection.executescript()` always issues an implicit `COMMIT` before running, even when a manual `BEGIN IMMEDIATE` transaction is already open — confirmed by direct reproduction (wrapping `executescript()` in manual `BEGIN`/`COMMIT` raises `sqlite3.OperationalError: cannot commit - no transaction is active`, because the script's own implicit commit already closed the transaction). This is why `_initialize_new_database` executes each `CREATE` statement individually via `self._conn.execute(...)` in a loop over `_SCHEMA_STATEMENTS`, never via `executescript()`, inside its own manual transaction — confirmed by direct reproduction that this DOES give real atomicity (a forced mid-script failure, e.g. a duplicate `CREATE TABLE`, correctly leaves zero tables after `ROLLBACK`, where `executescript()` alone left a partial schema behind). `_SCHEMA_SQL` (the combined script string) is still provided for tests that call `executescript()` directly on a fresh connection with no pending manual transaction — that usage is safe; it's only unsafe interleaved with manual `BEGIN`/`COMMIT`.

2. `BEGIN IMMEDIATE` can itself be the statement that fails (e.g. another writer already holds the database lock — this is exactly what Matrix case DB-09 exercises). If the `except` handler unconditionally calls `ROLLBACK` in that case, `ROLLBACK` itself raises `sqlite3.OperationalError: cannot rollback - no transaction is active`, which masks the original error and leaves the connection non-functional (confirmed by direct reproduction: this exact bug crashed a DB-09 verification script during this plan's own pre-flight testing). The fix, already in the code above, is to guard every `ROLLBACK` with `if self._conn.in_transaction:` — `Connection.in_transaction` accurately reflects whether a transaction is actually open, verified directly. This guard appears three times in this file: `_initialize_new_database` (above), the existing-database branch's FTS-rebuild-after-replay block (Task 6), and `_write_operation` (Task 4) — apply it in all three places when you reach them, not just here.

Note: `SqliteMemoryStore.persist()` and the rest of `MemoryStore`'s methods don't exist yet — Task 1 only needs `_require_open()` wired into `close()`'s guard shape for `test_close_then_persist_raises_closed` to eventually work once Task 4/5 add those methods. For THIS task, that test will fail with `AttributeError: 'SqliteMemoryStore' object has no attribute 'persist'` rather than `SqliteStoreClosed` — **move `test_close_then_persist_raises_closed` out of this task's test class into a skip-until-later state is wrong; instead, delete that one test from Step 1 for this task** (it belongs in Task 4, once `persist()` exists) — replace it in Step 1 above with nothing (already omitted from the final list your tests run), and re-add it verbatim as part of Task 4's own test additions. Concretely: **do not include `test_close_then_persist_raises_closed` in this task's committed test file** — every other test in the Step 1 block does not depend on `persist()` existing and should pass.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: PASS (all tests except the one moved to Task 4, which should not be present in this commit)

- [ ] **Step 5: Commit**

```bash
git add src/memory/sqlite_store.py tests/memory/semantics/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: schema, lifecycle, and exception types

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Private journal operation and record codec

**Files:**
- Modify: `src/memory/sqlite_store.py`
- Modify: `tests/memory/semantics/test_sqlite_store.py`

**Interfaces:**
- Consumes: Task 1's exceptions (`StoreCorruption`). `memory.codec`'s existing `as_persisted_value`, `encode_persisted_value`, `decode_persisted_value`, `encode_kind`/`decode_kind`, `encode_id`/`decode_id`, `encode_ref`/`decode_ref`, `encode_wall_instant`/`decode_wall_instant`, `encode_duration`/`decode_duration`, `encode_context`/`decode_context`.
- Produces: `_encode_id_or_ref`/`_decode_id_or_ref`; ten private record encoders/decoders (`_encode_observation`/`_decode_observation`, ... one pair per `PersistRecord` concrete type); `_encode_record`/`_decode_record` (isinstance/tag dispatchers); four operation-payload encoders/decoders (`_encode_persist_op`, `_encode_create_episode_op`, `_encode_append_episode_op`, `_encode_close_episode_op`, and matching decoders); `_compute_digest(seq: int, op_kind: str, payload: bytes) -> bytes`; `_track_entity_ids(known_ids: set[Id], record: PersistRecord) -> None`. All pure functions/private methods — nothing wired into `persist()` yet (that's Task 4). Tasks 3-7 all build on this codec.

Before writing code, read `src/memory/store.py`'s canonicalization functions (`_canonical_observation` through `_canonical_error`, lines ~133-225) for the exact, already-verified field lists and field order per record type — this task's encoders must preserve the same fields (not necessarily the same order, but every field, with nothing added or silently dropped). Also read `src/core/effect.py`'s and `src/core/provenance.py`'s dataclass definitions directly for their exact field types (`Effect.target: object`, `Effect.metadata: Mapping[str, object] | None`; `Provenance.transform_name: str`, `Provenance.duration: Duration`).

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_sqlite_store.py` (add imports as needed — `from datetime import UTC, datetime`, `from core.context import Context`, `from core.effect import Effect`, `from core.epistemic import Claim, Contradiction, Inference, Resolution`, `from core.error import Error`, `from core.event import Event`, `from core.identity import Id, Namespace, Ref`, `from core.observation import Observation`, `from core.provenance import Provenance`, `from core.time import Duration, WallInstant`, `from core.value import Kind, Known, Unknown`, `from memory.retention import RetentionMark, ACTIVE`, and reach into the module under test for its private codec: `from memory.sqlite_store import (_compute_digest, _decode_record, _encode_record)`):

```python
AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
CTX = Context(as_of=AT)
SUBJECT = Id(Kind("t.subject"), "s1")
AGENT = Id(Kind("t.agent"), "a1")
ERROR_KIND = Kind("t.error")


class TestRecordCodecRoundTrip:
    def test_observation_round_trips(self) -> None:
        obs = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="hello", at=AT,
            source="sensor-1", context=CTX, observer=None,
        )
        assert _decode_record(_encode_record(obs)) == obs

    def test_observation_with_observer_and_namespaced_ref_subject_round_trips(self) -> None:
        obs = Observation(
            id=Id(Kind("t.obs"), "o2"),
            subject=Ref(id=SUBJECT, namespace=Namespace(("finance",))),
            value=("a", "b", 3), at=AT, source=42, context=CTX, observer="watcher-1",
        )
        assert _decode_record(_encode_record(obs)) == obs

    def test_claim_known_round_trips(self) -> None:
        claim = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT,
            evidence_refs=(Ref(id=SUBJECT),), at=AT,
        )
        decoded = _decode_record(_encode_record(claim))
        assert decoded == claim
        assert isinstance(decoded.value, Known)

    def test_claim_unknown_round_trips(self) -> None:
        claim = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Unknown(), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        decoded = _decode_record(_encode_record(claim))
        assert decoded == claim
        assert isinstance(decoded.value, Unknown)

    def test_inference_with_embedded_claim_round_trips(self) -> None:
        claim = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(Ref(id=SUBJECT),),
            method=Kind("t.m"), conclusion=claim, at=AT,
        )
        decoded = _decode_record(_encode_record(inference))
        assert decoded == inference
        assert decoded.conclusion == claim

    def test_contradiction_round_trips(self) -> None:
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=Id(Kind("t.claim"), "a")), Ref(id=Id(Kind("t.claim"), "b"))),
            detected_at=AT, context=CTX,
        )
        assert _decode_record(_encode_record(contradiction)) == contradiction

    def test_resolution_round_trips(self) -> None:
        resolution = Resolution(
            contradiction=Ref(id=Id(Kind("t.contra"), "k1")), rationale="because",
            resolved_by=AGENT, at=AT,
        )
        assert _decode_record(_encode_record(resolution)) == resolution

    def test_event_round_trips_and_preserves_payload_distinctness(self) -> None:
        e1 = Event(id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload="A")
        e2 = Event(id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload="B")
        assert e1 == e2, "sanity: Core Event equality really is Id-only"
        assert _decode_record(_encode_record(e1)).payload == "A"
        assert _decode_record(_encode_record(e2)).payload == "B"
        assert _encode_record(e1) != _encode_record(e2)

    def test_event_with_bytes_payload_and_no_context_round_trips(self) -> None:
        event = Event(id=Id(Kind("t.event"), "e2"), kind=Kind("t.event"), at=AT, payload=b"raw")
        assert _decode_record(_encode_record(event)) == event

    def test_effect_round_trips(self) -> None:
        effect = Effect(
            id=Id(Kind("t.effect"), "f1"), kind=Kind("t.k"), description="did a thing",
            target="str target", at=AT, context=CTX, metadata={"a": 1},
        )
        assert _decode_record(_encode_record(effect)) == effect

    def test_effect_with_no_context_no_metadata_round_trips(self) -> None:
        effect = Effect(
            id=Id(Kind("t.effect"), "f2"), kind=Kind("t.k"), description="thing",
            target=123, at=AT,
        )
        assert _decode_record(_encode_record(effect)) == effect

    def test_provenance_round_trips(self) -> None:
        prov = Provenance(
            id=Id(Kind("t.prov"), "p1"), transform_id=Id(Kind("t.tx"), "t1"),
            transform_name="normalize", transform_version="1.0",
            inputs=(Ref(id=SUBJECT),), parents=(), at=AT, duration=Duration(500), context=CTX,
        )
        assert _decode_record(_encode_record(prov)) == prov

    def test_error_with_recursive_cause_chain_round_trips(self) -> None:
        root = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="root failure", at=AT)
        wrap = Error(
            id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrapped", at=AT, cause=root,
            operation="op1", recoverable=True, metadata={"k": "v"},
        )
        decoded = _decode_record(_encode_record(wrap))
        assert decoded == wrap
        assert decoded.cause == root

    def test_retention_mark_round_trips(self) -> None:
        mark = RetentionMark(
            item=Ref(id=Id(Kind("t.item"), "x")), accessibility=ACTIVE, at=AT, rationale="why",
        )
        assert _decode_record(_encode_record(mark)) == mark

    def test_retention_mark_with_no_rationale_round_trips(self) -> None:
        mark = RetentionMark(item=Ref(id=Id(Kind("t.item"), "x")), accessibility=ACTIVE, at=AT)
        assert _decode_record(_encode_record(mark)) == mark

    def test_every_persist_record_type_has_a_distinct_tag(self) -> None:
        from memory.codec import decode_persisted_value

        claim = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        records = [
            Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX),
            claim,
            Inference(id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim, at=AT),
            Contradiction(id=Id(Kind("t.contra"), "k1"), subject=SUBJECT, statements=(Ref(id=claim.id), Ref(id=Id(Kind("t.claim"), "x"))), detected_at=AT, context=CTX),
            Resolution(contradiction=Ref(id=Id(Kind("t.contra"), "k1")), rationale="r", resolved_by=AGENT, at=AT),
            Event(id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload="p"),
            Effect(id=Id(Kind("t.effect"), "f1"), kind=Kind("t.k"), description="d", target="t", at=AT),
            Provenance(id=Id(Kind("t.prov"), "p1"), transform_id=Id(Kind("t.tx"), "t1"), transform_name="n", transform_version="1", inputs=(), parents=(), at=AT, duration=Duration(0)),
            Error(id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="m", at=AT),
            RetentionMark(item=Ref(id=Id(Kind("t.item"), "x")), accessibility=ACTIVE, at=AT),
        ]
        tags = [decode_persisted_value(_encode_record(r))[0] for r in records]  # type: ignore[arg-type]
        assert len(set(tags)) == len(tags), f"duplicate tags: {tags}"

    def test_unknown_record_tag_raises_store_corruption_on_decode(self) -> None:
        from memory.codec import as_persisted_value, encode_persisted_value

        garbage = encode_persisted_value(as_persisted_value(("not_a_real_tag", 1, 2, 3)))
        with pytest.raises(StoreCorruption):
            _decode_record(garbage)

    def test_malformed_payload_raises_store_corruption_not_a_raw_python_exception(self) -> None:
        from memory.codec import as_persisted_value, encode_persisted_value

        malformed = encode_persisted_value(as_persisted_value(("observation",)))  # too few fields
        with pytest.raises(StoreCorruption):
            _decode_record(malformed)


class TestOperationChecksum:
    def test_deterministic(self) -> None:
        assert _compute_digest(1, "persist", b"abc") == _compute_digest(1, "persist", b"abc")

    def test_sensitive_to_seq(self) -> None:
        assert _compute_digest(1, "persist", b"abc") != _compute_digest(2, "persist", b"abc")

    def test_sensitive_to_op_kind(self) -> None:
        assert _compute_digest(1, "persist", b"abc") != _compute_digest(1, "create_episode", b"abc")

    def test_sensitive_to_payload(self) -> None:
        assert _compute_digest(1, "persist", b"abc") != _compute_digest(1, "persist", b"abd")

    def test_no_framing_ambiguity_across_field_boundary(self) -> None:
        assert _compute_digest(1, "ab", b"c") != _compute_digest(1, "a", b"bc")

    def test_digest_is_32_bytes(self) -> None:
        assert len(_compute_digest(1, "persist", b"abc")) == 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: FAIL/ERROR — `ImportError: cannot import name '_compute_digest'`

- [ ] **Step 3: Write minimal implementation**

Add to the top-of-file import block in `src/memory/sqlite_store.py`:

```python
import hashlib
import hmac
import struct
from collections.abc import Mapping
from typing import cast

from core.context import Context
from core.effect import Effect
from core.epistemic import Claim, Contradiction, Inference, Resolution
from core.error import Error
from core.event import Event
from core.identity import Id, Ref
from core.observation import Observation
from core.provenance import Provenance
from core.time import Duration, WallInstant
from core.value import Kind, Known, Unknown
from memory.codec import (
    as_persisted_value,
    decode_context,
    decode_duration,
    decode_id,
    decode_kind,
    decode_persisted_value,
    decode_ref,
    decode_wall_instant,
    encode_context,
    encode_duration,
    encode_id,
    encode_kind,
    encode_persisted_value,
    encode_ref,
    encode_wall_instant,
)
from memory.retention import RetentionMark
from memory.store import PersistRecord
```

Append to `src/memory/sqlite_store.py`:

```python
_JOURNAL_FORMAT_VERSION = b"memory.sqlite.operation.v1"


def _compute_digest(seq: int, op_kind: str, payload: bytes) -> bytes:
    """SHA-256 over unambiguous, length-prefixed fields — never naive
    concatenation, which would let e.g. op_kind="ab"+payload="c" collide
    with op_kind="a"+payload="bc".
    """
    op_kind_bytes = op_kind.encode("utf-8")
    h = hashlib.sha256()
    h.update(struct.pack(">I", len(_JOURNAL_FORMAT_VERSION)) + _JOURNAL_FORMAT_VERSION)
    h.update(struct.pack(">Q", seq))
    h.update(struct.pack(">I", len(op_kind_bytes)) + op_kind_bytes)
    h.update(struct.pack(">Q", len(payload)) + payload)
    return h.digest()


def _digests_match(expected: bytes, actual: bytes) -> bool:
    return hmac.compare_digest(expected, actual)


# ---- Decode-time validation helpers. Every node read back from SQLite is
# untrusted external input (§96) -- these each do one isinstance check and
# either raise StoreCorruption or return a properly narrowed type, so every
# call site gets real Pyright narrowing from the return annotation instead
# of relying on control-flow narrowing through an aggregated `all(...)`
# check. VERIFIED before this plan was written: Pyright strict does NOT
# propagate isinstance narrowing back through `all(isinstance(x, T) for x in
# (a, b, c))` to the individual names `a`/`b`/`c` — using that pattern here
# (it looks natural, don't reach for it) produced 62 real Pyright errors
# during this plan's own pre-flight verification. Each helper below fixes
# that by returning a properly-typed value instead of relying on narrowing.


def _expect_bytes(value: object, seq: int | None, message: str) -> bytes:
    if not isinstance(value, bytes):
        raise StoreCorruption(seq, message)
    return value


def _expect_str(value: object, seq: int | None, message: str) -> str:
    if not isinstance(value, str):
        raise StoreCorruption(seq, message)
    return value


def _expect_optional_str(value: object, seq: int | None, message: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise StoreCorruption(seq, message)
    return value


def _expect_bool(value: object, seq: int | None, message: str) -> bool:
    if not isinstance(value, bool):
        raise StoreCorruption(seq, message)
    return value


def _expect_tuple(value: object, seq: int | None, message: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise StoreCorruption(seq, message)
    # isinstance() on a bare `tuple` narrows to `tuple[Unknown, ...]`, not
    # `tuple[object, ...]` — verified during pre-flight; cast() after the
    # real runtime check (not instead of it) matches memory/codec.py's own
    # established pattern (cast(list[object], node) after isinstance).
    return cast(tuple[object, ...], value)


def _encode_id_or_ref(value: Id | Ref) -> tuple[str, bytes]:
    if isinstance(value, Ref):
        return ("ref", encode_ref(value))
    return ("id", encode_id(value))


def _decode_id_or_ref(node: object, *, seq: int | None = None) -> Id | Ref:
    node = _expect_tuple(node, seq, "malformed Id/Ref node")
    if len(node) != 2:
        raise StoreCorruption(seq, "malformed Id/Ref node")
    tag = _expect_str(node[0], seq, "malformed Id/Ref tag")
    data = _expect_bytes(node[1], seq, "malformed Id/Ref data")
    if tag == "id":
        return decode_id(data)
    if tag == "ref":
        return decode_ref(data)
    raise StoreCorruption(seq, f"unknown id/ref tag: {tag!r}")


def _encode_optional_payload(value: object | None) -> bytes | None:
    return encode_persisted_value(as_persisted_value(value)) if value is not None else None


def _decode_optional_payload(data: object, *, seq: int | None = None) -> object | None:
    if data is None:
        return None
    if not isinstance(data, bytes):
        raise StoreCorruption(seq, "malformed optional payload node")
    return decode_persisted_value(data)


def _decode_optional_metadata(
    data: object, *, seq: int | None = None
) -> Mapping[str, object] | None:
    """Like _decode_optional_payload, but for fields typed
    Mapping[str, object] | None (Effect.metadata, Error.metadata) --
    decode_persisted_value's static return type is the full PersistedValue
    union, so callers need this extra isinstance(..., Mapping) narrowing
    (plus cast(), same generic-narrowing reason as _expect_tuple above) to
    satisfy Pyright at the Effect(...)/Error(...) constructor call.
    """
    if data is None:
        return None
    raw = _expect_bytes(data, seq, "malformed metadata node")
    decoded = decode_persisted_value(raw)
    if not isinstance(decoded, Mapping):
        raise StoreCorruption(seq, "malformed metadata: decoded value is not a mapping")
    return cast(Mapping[str, object], decoded)


def _encode_optional_context(context: Context | None) -> bytes | None:
    return encode_context(context) if context is not None else None


def _decode_optional_context(data: object, *, seq: int | None = None) -> Context | None:
    if data is None:
        return None
    if not isinstance(data, bytes):
        raise StoreCorruption(seq, "malformed context node")
    return decode_context(data)


# ---- Per-record-type codecs. Each _encode_X returns a tagged tuple (itself
# a valid PersistedValue, since every element is str/bytes/None/tuple); the
# caller wraps it with encode_persisted_value(as_persisted_value(...)) via
# _encode_record. Each _decode_X takes the ALREADY-DECODED node (not raw
# bytes) so the generic dispatcher only calls decode_persisted_value once. ----


def _encode_observation(obs: Observation[object]) -> tuple[object, ...]:
    return (
        "observation",
        encode_id(obs.id),
        _encode_id_or_ref(obs.subject),
        encode_persisted_value(as_persisted_value(obs.value)),
        encode_wall_instant(obs.at),
        encode_persisted_value(as_persisted_value(obs.source)),
        encode_context(obs.context),
        _encode_optional_payload(obs.observer),
    )


def _decode_observation(node: tuple[object, ...], *, seq: int | None = None) -> Observation[object]:
    if len(node) != 8:
        raise StoreCorruption(seq, "malformed observation record")
    _, id_n, subj_n, value_n, at_n, source_n, ctx_n, observer_n = node
    return Observation(
        id=decode_id(_expect_bytes(id_n, seq, "malformed observation id")),
        subject=_decode_id_or_ref(subj_n, seq=seq),
        value=decode_persisted_value(_expect_bytes(value_n, seq, "malformed observation value")),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed observation at")),
        source=decode_persisted_value(_expect_bytes(source_n, seq, "malformed observation source")),
        context=decode_context(_expect_bytes(ctx_n, seq, "malformed observation context")),
        observer=_decode_optional_payload(observer_n, seq=seq),
    )


def _encode_claim(claim: Claim[object]) -> tuple[object, ...]:
    if isinstance(claim.value, Known):
        value_node: tuple[object, ...] = (
            "known", encode_persisted_value(as_persisted_value(claim.value.value)),
        )
    else:
        value_node = ("unknown",)
    return (
        "claim",
        encode_id(claim.id),
        _encode_id_or_ref(claim.subject),
        encode_kind(claim.predicate),
        value_node,
        encode_context(claim.context),
        _encode_id_or_ref(claim.asserted_by),
        tuple(encode_ref(r) for r in claim.evidence_refs),
        encode_wall_instant(claim.at),
    )


def _decode_claim(node: tuple[object, ...], *, seq: int | None = None) -> Claim[object]:
    if len(node) != 9:
        raise StoreCorruption(seq, "malformed claim record")
    _, id_n, subj_n, pred_n, value_n_raw, ctx_n, asserted_n, evid_n_raw, at_n = node
    value_n = _expect_tuple(value_n_raw, seq, "malformed claim value node")
    if not value_n:
        raise StoreCorruption(seq, "malformed claim value node")
    value_tag = _expect_str(value_n[0], seq, "malformed claim value tag")
    if value_tag == "known":
        if len(value_n) != 2:
            raise StoreCorruption(seq, "malformed known-claim-value node")
        inner = _expect_bytes(value_n[1], seq, "malformed known-claim-value payload")
        value: Known[object] | Unknown = Known(decode_persisted_value(inner))
    elif value_tag == "unknown":
        value = Unknown()
    else:
        raise StoreCorruption(seq, f"unknown claim value tag: {value_tag!r}")
    evid_n = _expect_tuple(evid_n_raw, seq, "malformed evidence_refs node")
    return Claim(
        id=decode_id(_expect_bytes(id_n, seq, "malformed claim id")),
        subject=_decode_id_or_ref(subj_n, seq=seq),
        predicate=decode_kind(_expect_bytes(pred_n, seq, "malformed claim predicate")),
        value=value,
        context=decode_context(_expect_bytes(ctx_n, seq, "malformed claim context")),
        asserted_by=_decode_id_or_ref(asserted_n, seq=seq),
        evidence_refs=tuple(
            decode_ref(_expect_bytes(r, seq, "malformed evidence ref")) for r in evid_n
        ),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed claim at")),
    )


def _encode_inference(inference: Inference[object]) -> tuple[object, ...]:
    # Inference.premises is tuple[Ref, ...] (not Id | Ref, unlike most other
    # subject-shaped fields) — verified against core/epistemic.py directly
    # during this plan's pre-flight; using _encode_id_or_ref here (an easy
    # mistake, since every other subject-shaped field uses it) fails
    # Pyright because Inference(premises=...) requires tuple[Ref, ...].
    return (
        "inference",
        encode_id(inference.id),
        tuple(encode_ref(p) for p in inference.premises),
        encode_kind(inference.method),
        _encode_claim(inference.conclusion),
        encode_wall_instant(inference.at),
    )


def _decode_inference(node: tuple[object, ...], *, seq: int | None = None) -> Inference[object]:
    if len(node) != 6:
        raise StoreCorruption(seq, "malformed inference record")
    _, id_n, premises_n_raw, method_n, conclusion_n_raw, at_n = node
    premises_n = _expect_tuple(premises_n_raw, seq, "malformed premises node")
    conclusion_n = _expect_tuple(conclusion_n_raw, seq, "malformed embedded claim in inference")
    if not conclusion_n or conclusion_n[0] != "claim":
        raise StoreCorruption(seq, "malformed embedded claim in inference")
    return Inference(
        id=decode_id(_expect_bytes(id_n, seq, "malformed inference id")),
        premises=tuple(
            decode_ref(_expect_bytes(p, seq, "malformed inference premise")) for p in premises_n
        ),
        method=decode_kind(_expect_bytes(method_n, seq, "malformed inference method")),
        conclusion=_decode_claim(conclusion_n, seq=seq),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed inference at")),
    )


def _encode_contradiction(contradiction: Contradiction) -> tuple[object, ...]:
    return (
        "contradiction",
        encode_id(contradiction.id),
        _encode_id_or_ref(contradiction.subject),
        tuple(encode_ref(r) for r in contradiction.statements),
        encode_wall_instant(contradiction.detected_at),
        encode_context(contradiction.context),
    )


def _decode_contradiction(node: tuple[object, ...], *, seq: int | None = None) -> Contradiction:
    if len(node) != 6:
        raise StoreCorruption(seq, "malformed contradiction record")
    _, id_n, subj_n, statements_n_raw, detected_n, ctx_n = node
    statements_n = _expect_tuple(statements_n_raw, seq, "malformed contradiction statements node")
    return Contradiction(
        id=decode_id(_expect_bytes(id_n, seq, "malformed contradiction id")),
        subject=_decode_id_or_ref(subj_n, seq=seq),
        statements=tuple(
            decode_ref(_expect_bytes(s, seq, "malformed contradiction statement"))
            for s in statements_n
        ),
        detected_at=decode_wall_instant(
            _expect_bytes(detected_n, seq, "malformed contradiction detected_at")
        ),
        context=decode_context(_expect_bytes(ctx_n, seq, "malformed contradiction context")),
    )


def _encode_resolution(resolution: Resolution) -> tuple[object, ...]:
    return (
        "resolution",
        encode_ref(resolution.contradiction),
        resolution.rationale,
        _encode_id_or_ref(resolution.resolved_by),
        encode_wall_instant(resolution.at),
    )


def _decode_resolution(node: tuple[object, ...], *, seq: int | None = None) -> Resolution:
    if len(node) != 5:
        raise StoreCorruption(seq, "malformed resolution record")
    _, contra_n, rationale_n, resolved_n, at_n = node
    return Resolution(
        contradiction=decode_ref(
            _expect_bytes(contra_n, seq, "malformed resolution contradiction")
        ),
        rationale=_expect_str(rationale_n, seq, "malformed resolution rationale"),
        resolved_by=_decode_id_or_ref(resolved_n, seq=seq),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed resolution at")),
    )


def _encode_event(event: Event) -> tuple[object, ...]:
    return (
        "event",
        encode_id(event.id),
        encode_kind(event.kind),
        encode_wall_instant(event.at),
        encode_persisted_value(as_persisted_value(event.payload)),
        _encode_optional_context(event.context),
    )


def _decode_event(node: tuple[object, ...], *, seq: int | None = None) -> Event:
    if len(node) != 6:
        raise StoreCorruption(seq, "malformed event record")
    _, id_n, kind_n, at_n, payload_n, ctx_n = node
    return Event(
        id=decode_id(_expect_bytes(id_n, seq, "malformed event id")),
        kind=decode_kind(_expect_bytes(kind_n, seq, "malformed event kind")),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed event at")),
        payload=decode_persisted_value(_expect_bytes(payload_n, seq, "malformed event payload")),
        context=_decode_optional_context(ctx_n, seq=seq),
    )


def _encode_effect(effect: Effect) -> tuple[object, ...]:
    return (
        "effect",
        encode_id(effect.id),
        encode_kind(effect.kind),
        effect.description,
        encode_persisted_value(as_persisted_value(effect.target)),
        encode_wall_instant(effect.at),
        _encode_optional_context(effect.context),
        _encode_optional_payload(effect.metadata),
    )


def _decode_effect(node: tuple[object, ...], *, seq: int | None = None) -> Effect:
    if len(node) != 8:
        raise StoreCorruption(seq, "malformed effect record")
    _, id_n, kind_n, description_n, target_n, at_n, ctx_n, metadata_n = node
    return Effect(
        id=decode_id(_expect_bytes(id_n, seq, "malformed effect id")),
        kind=decode_kind(_expect_bytes(kind_n, seq, "malformed effect kind")),
        description=_expect_str(description_n, seq, "malformed effect description"),
        target=decode_persisted_value(_expect_bytes(target_n, seq, "malformed effect target")),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed effect at")),
        context=_decode_optional_context(ctx_n, seq=seq),
        metadata=_decode_optional_metadata(metadata_n, seq=seq),
    )


def _encode_provenance(provenance: Provenance) -> tuple[object, ...]:
    return (
        "provenance",
        encode_id(provenance.id),
        encode_id(provenance.transform_id),
        provenance.transform_name,
        provenance.transform_version,
        tuple(encode_ref(r) for r in provenance.inputs),
        tuple(encode_ref(r) for r in provenance.parents),
        encode_wall_instant(provenance.at),
        encode_duration(provenance.duration),
        _encode_optional_context(provenance.context),
    )


def _decode_provenance(node: tuple[object, ...], *, seq: int | None = None) -> Provenance:
    if len(node) != 10:
        raise StoreCorruption(seq, "malformed provenance record")
    (
        _, id_n, tx_id_n, tx_name_n, tx_version_n,
        inputs_n_raw, parents_n_raw, at_n, duration_n, ctx_n,
    ) = node
    inputs_n = _expect_tuple(inputs_n_raw, seq, "malformed provenance inputs")
    parents_n = _expect_tuple(parents_n_raw, seq, "malformed provenance parents")
    return Provenance(
        id=decode_id(_expect_bytes(id_n, seq, "malformed provenance id")),
        transform_id=decode_id(_expect_bytes(tx_id_n, seq, "malformed provenance transform_id")),
        transform_name=_expect_str(tx_name_n, seq, "malformed provenance transform_name"),
        transform_version=_expect_str(tx_version_n, seq, "malformed provenance transform_version"),
        inputs=tuple(
            decode_ref(_expect_bytes(r, seq, "malformed provenance input ref")) for r in inputs_n
        ),
        parents=tuple(
            decode_ref(_expect_bytes(r, seq, "malformed provenance parent ref")) for r in parents_n
        ),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed provenance at")),
        duration=decode_duration(_expect_bytes(duration_n, seq, "malformed provenance duration")),
        context=_decode_optional_context(ctx_n, seq=seq),
    )


def _encode_error(error: Error) -> tuple[object, ...]:
    if error.exception is not None:
        raise ValueError("foreign exceptions are not persistable")  # unreachable via persist()
    cause_node = (
        (encode_id(error.cause.id), _encode_error_bytes(error.cause))
        if error.cause is not None
        else None
    )
    return (
        "error",
        encode_id(error.id),
        encode_kind(error.kind),
        error.message,
        encode_wall_instant(error.at),
        cause_node,
        _encode_optional_context(error.context),
        error.operation,
        error.recoverable,
        _encode_optional_payload(error.metadata),
    )


def _encode_error_bytes(error: Error) -> bytes:
    return encode_persisted_value(as_persisted_value(_encode_error(error)))


def _decode_error(node: tuple[object, ...], *, seq: int | None = None) -> Error:
    if len(node) != 10:
        raise StoreCorruption(seq, "malformed error record")
    _, id_n, kind_n, message_n, at_n, cause_n, ctx_n, operation_n, recoverable_n, metadata_n = node
    cause: Error | None = None
    if cause_n is not None:
        cause_pair = _expect_tuple(cause_n, seq, "malformed error cause node")
        if len(cause_pair) != 2:
            raise StoreCorruption(seq, "malformed error cause node")
        nested_bytes = _expect_bytes(cause_pair[1], seq, "malformed nested error cause bytes")
        nested_node_raw = decode_persisted_value(nested_bytes)
        nested_node = _expect_tuple(nested_node_raw, seq, "malformed nested error tag")
        if not nested_node or nested_node[0] != "error":
            raise StoreCorruption(seq, "malformed nested error tag")
        cause = _decode_error(nested_node, seq=seq)
    return Error(
        id=decode_id(_expect_bytes(id_n, seq, "malformed error id")),
        kind=decode_kind(_expect_bytes(kind_n, seq, "malformed error kind")),
        message=_expect_str(message_n, seq, "malformed error message"),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed error at")),
        cause=cause,
        context=_decode_optional_context(ctx_n, seq=seq),
        operation=_expect_optional_str(operation_n, seq, "malformed error operation"),
        recoverable=_expect_bool(recoverable_n, seq, "malformed error recoverable flag"),
        metadata=_decode_optional_metadata(metadata_n, seq=seq),
    )


def _encode_retention_mark(mark: RetentionMark) -> tuple[object, ...]:
    return (
        "retention_mark",
        encode_ref(mark.item),
        encode_kind(mark.accessibility),
        encode_wall_instant(mark.at),
        mark.rationale,
    )


def _decode_retention_mark(node: tuple[object, ...], *, seq: int | None = None) -> RetentionMark:
    if len(node) != 5:
        raise StoreCorruption(seq, "malformed retention_mark record")
    _, item_n, accessibility_n, at_n, rationale_n = node
    return RetentionMark(
        item=decode_ref(_expect_bytes(item_n, seq, "malformed retention_mark item")),
        accessibility=decode_kind(
            _expect_bytes(accessibility_n, seq, "malformed retention_mark accessibility")
        ),
        at=decode_wall_instant(_expect_bytes(at_n, seq, "malformed retention_mark at")),
        rationale=_expect_optional_str(rationale_n, seq, "malformed retention_mark rationale"),
    )


def _encode_record(record: PersistRecord) -> bytes:
    if isinstance(record, Observation):
        tree = _encode_observation(record)
    elif isinstance(record, Claim):
        tree = _encode_claim(record)
    elif isinstance(record, Inference):
        tree = _encode_inference(record)
    elif isinstance(record, Contradiction):
        tree = _encode_contradiction(record)
    elif isinstance(record, Resolution):
        tree = _encode_resolution(record)
    elif isinstance(record, Event):
        tree = _encode_event(record)
    elif isinstance(record, Effect):
        tree = _encode_effect(record)
    elif isinstance(record, Provenance):
        tree = _encode_provenance(record)
    elif isinstance(record, Error):
        tree = _encode_error(record)
    # Runtime boundary guard: PersistRecord is closed to exactly 10 types and
    # this is provably the last one once the 9 branches above are exhausted
    # — but nothing stops a caller who ignores static typing from passing
    # something else, and that failure needs to be loud, not assumed away
    # (matches core/context.py's identical `# pyright: ignore` precedent).
    elif isinstance(record, RetentionMark):  # pyright: ignore[reportUnnecessaryIsInstance]
        tree = _encode_retention_mark(record)
    else:
        # Unreachable via persist() — PersistRecord already excludes anything else statically.
        raise TypeError(f"not a PersistRecord: {type(record).__name__}")
    return encode_persisted_value(as_persisted_value(tree))


_RECORD_DECODERS = {
    "observation": _decode_observation,
    "claim": _decode_claim,
    "inference": _decode_inference,
    "contradiction": _decode_contradiction,
    "resolution": _decode_resolution,
    "event": _decode_event,
    "effect": _decode_effect,
    "provenance": _decode_provenance,
    "error": _decode_error,
    "retention_mark": _decode_retention_mark,
}


def _decode_record(data: bytes, *, seq: int | None = None) -> PersistRecord:
    node = _expect_tuple(
        decode_persisted_value(data), seq, "malformed record payload: expected a tagged tuple"
    )
    if not node:
        raise StoreCorruption(seq, "malformed record payload: expected a tagged tuple")
    tag = _expect_str(node[0], seq, "malformed record payload: expected a string tag")
    decoder = _RECORD_DECODERS.get(tag)
    if decoder is None:
        raise StoreCorruption(seq, f"unknown record tag: {tag!r}")
    return decoder(node, seq=seq)
```

Then append the four operation-payload codecs and the entity-id tracker:

```python
def _encode_persist_op(record: PersistRecord) -> bytes:
    return _encode_record(record)


def _decode_persist_op(data: bytes, *, seq: int | None = None) -> PersistRecord:
    return _decode_record(data, seq=seq)


def _encode_create_episode_op(
    *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant
) -> bytes:
    tree = (
        "create_episode",
        encode_id(id),
        _encode_id_or_ref(subject),
        encode_context(context),
        encode_wall_instant(opened_at),
    )
    return encode_persisted_value(as_persisted_value(tree))


def _decode_create_episode_op(
    data: bytes, *, seq: int | None = None
) -> tuple[Id, Id | Ref, Context, WallInstant]:
    node = _expect_tuple(decode_persisted_value(data), seq, "malformed create_episode operation")
    if len(node) != 5 or node[0] != "create_episode":
        raise StoreCorruption(seq, "malformed create_episode operation")
    _, id_n, subj_n, ctx_n, opened_n = node
    return (
        decode_id(_expect_bytes(id_n, seq, "malformed create_episode id")),
        _decode_id_or_ref(subj_n, seq=seq),
        decode_context(_expect_bytes(ctx_n, seq, "malformed create_episode context")),
        decode_wall_instant(_expect_bytes(opened_n, seq, "malformed create_episode opened_at")),
    )


def _encode_append_episode_op(episode: Id | Ref, item: Ref) -> bytes:
    tree = ("append_episode", _encode_id_or_ref(episode), encode_ref(item))
    return encode_persisted_value(as_persisted_value(tree))


def _decode_append_episode_op(data: bytes, *, seq: int | None = None) -> tuple[Id | Ref, Ref]:
    node = _expect_tuple(decode_persisted_value(data), seq, "malformed append_episode operation")
    if len(node) != 3 or node[0] != "append_episode":
        raise StoreCorruption(seq, "malformed append_episode operation")
    _, episode_n, item_n = node
    return (
        _decode_id_or_ref(episode_n, seq=seq),
        decode_ref(_expect_bytes(item_n, seq, "malformed append_episode item")),
    )


def _encode_close_episode_op(episode: Id | Ref, at: WallInstant) -> bytes:
    tree = ("close_episode", _encode_id_or_ref(episode), encode_wall_instant(at))
    return encode_persisted_value(as_persisted_value(tree))


def _decode_close_episode_op(
    data: bytes, *, seq: int | None = None
) -> tuple[Id | Ref, WallInstant]:
    node = _expect_tuple(decode_persisted_value(data), seq, "malformed close_episode operation")
    if len(node) != 3 or node[0] != "close_episode":
        raise StoreCorruption(seq, "malformed close_episode operation")
    _, episode_n, at_n = node
    return (
        _decode_id_or_ref(episode_n, seq=seq),
        decode_wall_instant(_expect_bytes(at_n, seq, "malformed close_episode at")),
    )


def _track_entity_ids(known_ids: set[Id], record: PersistRecord) -> None:
    """Extend `known_ids` with every Id that becomes independently
    resolvable by persisting `record` -- including embedded entities
    (Inference.conclusion, Error.cause chain). Resolution/RetentionMark are
    not Entity-bearing and contribute nothing.
    """
    if isinstance(record, (Resolution, RetentionMark)):
        return
    if isinstance(record, Inference):
        known_ids.add(record.id)
        known_ids.add(record.conclusion.id)
        return
    if isinstance(record, Error):
        current: Error | None = record
        while current is not None:
            known_ids.add(current.id)
            current = current.cause
        return
    known_ids.add(record.id)
```

Note: this task does not produce a standalone `_record_tag()` helper — `_every_persist_record_type_has_a_distinct_tag`'s test above gets each record's tag by decoding `_encode_record(r)` and reading `node[0]` directly, since a separate `_record_tag()` function would have no production caller (nothing in this module ever needs the tag string in isolation from the full encoded payload) and would trip Pyright's `reportUnusedFunction` for a function that exists purely to make one test's job marginally more convenient.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/sqlite_store.py tests/memory/semantics/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: private journal operation and record codec

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Replay and corruption validation

**Files:**
- Modify: `src/memory/sqlite_store.py`
- Modify: `tests/memory/semantics/test_sqlite_store.py`

**Interfaces:**
- Consumes: Task 1's `SqliteMemoryStore.__init__`/exceptions; Task 2's `_compute_digest`, `_digests_match`, `_decode_persist_op`, `_decode_create_episode_op`, `_decode_append_episode_op`, `_decode_close_episode_op`, `_track_entity_ids`. `InMemoryStore`'s public `persist`/`create_episode`/`append_episode`/`close_episode`.
- Produces: `SqliteMemoryStore._replay_journal(self) -> InMemoryStore` (reads all `memory_ops` rows in seq order, verifies contiguity+checksum, decodes, applies through the public API, wraps failures as `StoreCorruption`); wires this into `__init__`'s existing-database branch (replacing the `# NOTE(Task 3): ...` placeholder). Also introduces `self._known_entity_ids: set[Id]`, populated during replay. Task 4 reuses `_replay_journal` verbatim as its failure-recovery mechanism — do not write a second implementation of replay.

Before writing code, read `src/memory/sqlite_store.py` as Task 2 left it — you are wiring `_replay_journal` into the constructor's existing `elif present == set(_REQUIRED_TABLES):` branch, replacing only the `# NOTE(Task 3): ...` comment and the line below it (`self._reference = InMemoryStore()`).

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_sqlite_store.py`:

```python
def _insert_op_row(conn: sqlite3.Connection, seq: int, op_kind: str, payload: bytes) -> None:
    from memory.sqlite_store import _compute_digest

    digest = _compute_digest(seq, op_kind, payload)
    conn.execute(
        "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (?, ?, ?, ?)",
        (seq, op_kind, payload, digest),
    )


def _fresh_v1_schema(path) -> sqlite3.Connection:
    from memory.sqlite_store import _SCHEMA_SQL, _SCHEMA_VERSION

    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA_SQL)
    conn.execute("INSERT INTO memory_meta VALUES ('schema_version', ?)", (_SCHEMA_VERSION,))
    conn.commit()
    return conn


class TestJournalReplay:
    def test_replays_a_simple_observation(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "replay1.sqlite"
        obs = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="hello", at=AT,
            source="s", context=CTX,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(obs))
        conn.commit()
        conn.close()

        store = SqliteMemoryStore(path)
        assert store.resolve(obs.id) == obs
        store.close()

    def test_replays_episode_create_append_close_in_order(self, tmp_path) -> None:
        from memory.sqlite_store import (
            _encode_append_episode_op,
            _encode_close_episode_op,
            _encode_create_episode_op,
        )
        from memory.episode import Episode

        path = tmp_path / "replay2.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        item = Ref(id=Id(Kind("t.item"), "x"))
        conn = _fresh_v1_schema(path)
        _insert_op_row(
            conn, 1, "create_episode",
            _encode_create_episode_op(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT),
        )
        _insert_op_row(conn, 2, "append_episode", _encode_append_episode_op(episode_id, item))
        _insert_op_row(conn, 3, "close_episode", _encode_close_episode_op(episode_id, AT))
        conn.commit()
        conn.close()

        store = SqliteMemoryStore(path)
        resolved = store.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == (item,)
        assert resolved.closed_at == AT
        store.close()

    def test_resolution_before_contradiction_is_corruption(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "orphan-resolution.sqlite"
        resolution = Resolution(
            contradiction=Ref(id=Id(Kind("t.contra"), "never-persisted")),
            rationale="r", resolved_by=AGENT, at=AT,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(resolution))
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption) as excinfo:
            SqliteMemoryStore(path)
        assert excinfo.value.sequence == 1

    def test_episode_append_after_close_is_corruption(self, tmp_path) -> None:
        from memory.sqlite_store import (
            _encode_append_episode_op,
            _encode_close_episode_op,
            _encode_create_episode_op,
        )

        path = tmp_path / "bad-episode-order.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        conn = _fresh_v1_schema(path)
        _insert_op_row(
            conn, 1, "create_episode",
            _encode_create_episode_op(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT),
        )
        _insert_op_row(conn, 2, "close_episode", _encode_close_episode_op(episode_id, AT))
        _insert_op_row(
            conn, 3, "append_episode",
            _encode_append_episode_op(episode_id, Ref(id=Id(Kind("t.item"), "x"))),
        )
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_unknown_op_kind_is_corruption(self, tmp_path) -> None:
        path = tmp_path / "unknown-opkind.sqlite"
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "teleport", b"whatever")
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_checksum_mismatch_is_corruption(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "badchecksum.sqlite"
        obs = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        conn = _fresh_v1_schema(path)
        conn.execute(
            "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (1, 'persist', ?, ?)",
            (_encode_persist_op(obs), b"\x00" * 32),  # deliberately wrong digest
        )
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption) as excinfo:
            SqliteMemoryStore(path)
        assert excinfo.value.sequence == 1

    def test_payload_tamper_is_corruption(self, tmp_path) -> None:
        from memory.sqlite_store import _compute_digest, _encode_persist_op

        path = tmp_path / "tamperedpayload.sqlite"
        obs = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        original_payload = _encode_persist_op(obs)
        original_digest = _compute_digest(1, "persist", original_payload)
        tampered_payload = original_payload + b"\x00"  # append a byte after digesting

        conn = _fresh_v1_schema(path)
        conn.execute(
            "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (1, 'persist', ?, ?)",
            (tampered_payload, original_digest),
        )
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_sequence_gap_is_corruption(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "seqgap.sqlite"
        obs1 = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="a", at=AT, source="s", context=CTX)
        obs2 = Observation(id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="b", at=AT, source="s", context=CTX)
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(obs1))
        _insert_op_row(conn, 3, "persist", _encode_persist_op(obs2))  # gap: 2 is missing
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_sequence_not_starting_at_one_is_corruption(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "seqstart.sqlite"
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="a", at=AT, source="s", context=CTX)
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 2, "persist", _encode_persist_op(obs))  # starts at 2, not 1
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_special_event_regression_survives_replay(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "event-trap.sqlite"
        e1 = Event(id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload="A")
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(e1))
        conn.commit()
        conn.close()

        store = SqliteMemoryStore(path)
        e2 = Event(id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload="B")
        assert e1 == e2, "sanity: Core Event equality really is Id-only"
        with pytest.raises(IdentityCollision):
            store.persist(e2)
        store.close()

    def test_special_embedded_identity_regression_survives_replay(self, tmp_path) -> None:
        from memory.sqlite_store import _encode_persist_op

        path = tmp_path / "embedded-identity.sqlite"
        claim1 = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim1, at=AT,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(inference))
        conn.commit()
        conn.close()

        store = SqliteMemoryStore(path)
        claim2 = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference_swapped = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim2, at=AT,
        )
        with pytest.raises(IdentityCollision):
            store.persist(inference_swapped)
        store.close()

    from core.identity import Id as _Id  # noqa: F401  (import already present above; keep for clarity)
```

Import `from memory.store import IdentityCollision` at the top of the test file if not already present from Task 1.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: FAIL — replayed stores come back empty (constructor still uses the Task 1 placeholder `InMemoryStore()`)

- [ ] **Step 3: Write minimal implementation**

In `src/memory/sqlite_store.py`, replace the constructor's placeholder branch:

```python
        elif present == set(_REQUIRED_TABLES):
            self._validate_existing_schema()
            # NOTE(Task 3): replace this with real journal replay — ...
            self._reference = InMemoryStore()
```

with:

```python
        elif present == set(_REQUIRED_TABLES):
            self._validate_existing_schema()
            self._known_entity_ids: set[Id] = set()
            self._reference = self._replay_journal()
```

(Also add `self._known_entity_ids: set[Id] = set()` to the new-database branch, right before `self._reference = InMemoryStore()`, so the attribute always exists regardless of which branch ran.)

Add `_replay_journal` as a method on `SqliteMemoryStore`:

```python
    def _replay_journal(self) -> InMemoryStore:
        reference = InMemoryStore()
        rows = self._conn.execute(
            "SELECT seq, op_kind, payload, digest FROM memory_ops ORDER BY seq ASC"
        ).fetchall()

        expected_seq = 1
        for seq, op_kind, payload, digest in rows:
            if not isinstance(seq, int) or seq != expected_seq:
                raise StoreCorruption(
                    seq if isinstance(seq, int) else None,
                    f"non-contiguous operation sequence: expected {expected_seq}, found {seq!r}",
                )
            if (
                not isinstance(op_kind, str)
                or not isinstance(payload, bytes)
                or not isinstance(digest, bytes)
            ):
                raise StoreCorruption(seq, "malformed journal row types")
            recomputed = _compute_digest(seq, op_kind, payload)
            if not _digests_match(recomputed, digest):
                raise StoreCorruption(seq, "operation checksum mismatch")

            try:
                self._apply_decoded_operation(reference, seq, op_kind, payload)
            except StoreCorruption:
                raise
            except Exception as exc:
                raise StoreCorruption(
                    seq, f"replay rejected by reference semantics: {exc}"
                ) from exc

            expected_seq += 1

        return reference

    def _apply_decoded_operation(
        self, reference: InMemoryStore, seq: int, op_kind: str, payload: bytes
    ) -> None:
        if op_kind == "persist":
            record = _decode_persist_op(payload, seq=seq)
            reference.persist(record)
            self._track_entity_ids_safe(record)
        elif op_kind == "create_episode":
            episode_id, subject, context, opened_at = _decode_create_episode_op(payload, seq=seq)
            reference.create_episode(
                id=episode_id, subject=subject, context=context, opened_at=opened_at
            )
            self._known_entity_ids.add(episode_id)
        elif op_kind == "append_episode":
            episode, item = _decode_append_episode_op(payload, seq=seq)
            reference.append_episode(episode, item)
        elif op_kind == "close_episode":
            episode, at = _decode_close_episode_op(payload, seq=seq)
            reference.close_episode(episode, at)
        else:
            raise StoreCorruption(seq, f"unknown operation kind: {op_kind!r}")

    def _track_entity_ids_safe(self, record: PersistRecord) -> None:
        _track_entity_ids(self._known_entity_ids, record)
```

Note: `_apply_decoded_operation` and `_track_entity_ids_safe` are written now as methods on `SqliteMemoryStore` so Task 4 can call them directly during live writes (§28's requirement that replay is *the* semantic decoder — live writes reuse the exact same decode-application path conceptually, by reusing `reference.persist(...)` etc. directly, not a second implementation).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/sqlite_store.py tests/memory/semantics/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: journal replay and corruption validation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Transactional mutation methods

**Files:**
- Modify: `src/memory/sqlite_store.py`
- Modify: `tests/memory/semantics/test_sqlite_store.py`

**Interfaces:**
- Consumes: Task 2's operation encoders + `_track_entity_ids`; Task 3's `_replay_journal` (reused verbatim as the failure-recovery mechanism), `_apply_decoded_operation`.
- Produces: `SqliteMemoryStore.persist(self, record: PersistRecord) -> None`, `create_episode(self, *, id, subject, context, opened_at) -> None`, `append_episode(self, episode, item) -> None`, `close_episode(self, episode, at) -> None`; private `_rebuild_fts(self) -> None` (full rebuild, called inside every write transaction and once after replay); private `_next_seq(self) -> int`; private `_write_operation(self, op_kind: str, payload: bytes) -> None` (the shared transaction wrapper all four public methods use). Task 5 adds the read-only query methods alongside these. Task 6 hardens FTS-specific test coverage on top of `_rebuild_fts`.

Before writing code, read `src/memory/sqlite_store.py` as Task 3 left it, in particular `_replay_journal`/`_apply_decoded_operation`/`_track_entity_ids_safe` — this task's failure-recovery path calls `_replay_journal()` again (rebuilding the reference from whatever is durably committed) rather than reimplementing recovery logic.

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_sqlite_store.py`:

```python
class TestTransactionalPersist:
    def test_persist_then_resolve(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "persist1.sqlite")
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store.persist(obs)
        assert store.resolve(obs.id) == obs
        store.close()

    def test_persist_survives_close_and_reopen(self, tmp_path) -> None:
        path = tmp_path / "persist2.sqlite"
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store = SqliteMemoryStore(path)
        store.persist(obs)
        store.close()

        reopened = SqliteMemoryStore(path)
        assert reopened.resolve(obs.id) == obs
        reopened.close()

    def test_identity_collision_leaves_no_journal_row(self, tmp_path) -> None:
        path = tmp_path / "collision.sqlite"
        store = SqliteMemoryStore(path)
        c1 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("first"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        c2 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("second"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        store.persist(c1)
        with pytest.raises(IdentityCollision):
            store.persist(c2)
        store.close()

        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 1  # only c1's persist op, never c2's rejected attempt

    def test_idempotent_retry_does_not_duplicate_journal_row(self, tmp_path) -> None:
        path = tmp_path / "idempotent.sqlite"
        store = SqliteMemoryStore(path)
        claim = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        store.persist(claim)
        store.persist(claim)  # idempotent retry
        store.close()

        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 2  # journal records BOTH successful calls (§100) -- it is an
        # operation log, not a state-delta log; idempotency is a reference-semantics
        # property (resolve() still returns one Claim), not a journal-compaction rule.

    def test_episode_create_append_close_persist_across_reopen(self, tmp_path) -> None:
        from memory.episode import Episode

        path = tmp_path / "episode-lifecycle.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        item = Ref(id=Id(Kind("t.item"), "x"))
        store = SqliteMemoryStore(path)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.append_episode(episode_id, item)
        store.close_episode(episode_id, AT)
        store.close()

        reopened = SqliteMemoryStore(path)
        resolved = reopened.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == (item,)
        assert resolved.closed_at == AT
        reopened.close()

    def test_close_then_persist_raises_closed(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "closed.sqlite")
        store.close()
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        with pytest.raises(SqliteStoreClosed):
            store.persist(obs)

    def test_close_then_create_episode_raises_closed(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "closed2.sqlite")
        store.close()
        with pytest.raises(SqliteStoreClosed):
            store.create_episode(id=Id(Kind("t.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT)


class _BlockNewOps:
    """Test-only failure injection: blocks INSERT into memory_ops via a
    trigger, while SELECT keeps working. VERIFIED before this plan was
    written that renaming memory_ops away instead (an earlier draft of
    this test used that) is WRONG — it also breaks the store's own
    in-process recovery, since `_write_operation`'s except-handler calls
    `_replay_journal()`, which itself needs to SELECT from memory_ops to
    rebuild `self._reference`. Renaming the table away makes recovery
    itself fail too, silently leaving `self._reference` un-reverted (the
    bug reproduced during this plan's pre-flight testing: `resolve()`
    after the "failed" write still returned the supposedly-reverted
    record). A trigger blocks writes without blocking reads, which is
    also a more realistic failure shape (a real disk-full or constraint
    failure blocks the specific write, not all access to the table).
    Also note: SQLite forbids triggers directly on FTS5 virtual tables —
    this technique only works on ordinary tables like memory_ops.
    """

    def __init__(self, store: SqliteMemoryStore) -> None:
        self._conn = store._conn

    def __enter__(self) -> "_BlockNewOps":
        self._conn.execute(
            "CREATE TRIGGER block_new_ops BEFORE INSERT ON memory_ops "
            "BEGIN SELECT RAISE(ABORT, 'forced failure for testing'); END;"
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._conn.execute("DROP TRIGGER block_new_ops")


class TestTransactionFailureRollback:
    def test_forced_sqlite_failure_leaves_reference_at_last_committed_state(self, tmp_path) -> None:
        path = tmp_path / "forced-failure.sqlite"
        store = SqliteMemoryStore(path)
        obs1 = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="first", at=AT, source="s", context=CTX)
        store.persist(obs1)

        obs2 = Observation(id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="second", at=AT, source="s", context=CTX)
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(obs2)

            # Confirm the IN-PROCESS reference already reflects only the
            # committed state, without any external reconstruction, WHILE
            # the trigger is still armed (recovery must not need repair
            # first — only the specific write was blocked, not reads).
            assert store.resolve(obs1.id) == obs1
            assert store.resolve(obs2.id) is None
        store.close()

    def test_database_reopen_after_forced_failure_matches_pre_failure_state(self, tmp_path) -> None:
        path = tmp_path / "reopen-after-failure.sqlite"
        store = SqliteMemoryStore(path)
        obs1 = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="first", at=AT, source="s", context=CTX)
        store.persist(obs1)

        obs2 = Observation(id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="second", at=AT, source="s", context=CTX)
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(obs2)
        store.close()

        reopened = SqliteMemoryStore(path)
        assert reopened.resolve(obs1.id) == obs1
        assert reopened.resolve(obs2.id) is None
        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 1
        reopened.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: FAIL/ERROR — `AttributeError: 'SqliteMemoryStore' object has no attribute 'persist'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/memory/sqlite_store.py`'s imports: `from memory.episode import Episode` is NOT needed (methods only take/return protocol-shaped values); no new imports required beyond what Tasks 1-3 already added.

Append these methods to `SqliteMemoryStore`:

```python
    def _next_seq(self) -> int:
        row = self._conn.execute("SELECT MAX(seq) FROM memory_ops").fetchone()
        current_max = row[0] if row is not None and row[0] is not None else 0
        return current_max + 1

    def _rebuild_fts(self) -> None:
        """Full rebuild: clear the derived index, then reinsert one row per
        string lexical_content() returns for every known Entity, resolved
        fresh from the reference projection. Correctness-first for v0 — see
        MEMORY_ARCHITECTURE.md on optimizing this only after backend-
        equivalence tests prove a replacement.
        """
        self._conn.execute("DELETE FROM memory_fts")
        for entity_id in self._known_entity_ids:
            entity = self._reference.resolve(entity_id)
            if entity is None:
                continue
            for field_index, text in enumerate(lexical_content(entity)):
                self._conn.execute(
                    "INSERT INTO memory_fts(id_kind, id_value, field_index, content) "
                    "VALUES (?, ?, ?, ?)",
                    (entity_id.kind.value, entity_id.value, field_index, text),
                )

    def _write_operation(self, op_kind: str, payload: bytes) -> None:
        """Shared transaction wrapper for all four mutating methods. The
        caller has ALREADY applied the operation to self._reference before
        calling this — that proved semantic legality. This method only
        durably records it; on any SQLite failure it rolls back and
        reloads self._reference from the last-committed journal via the
        same replay path used at open, then re-raises.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            seq = self._next_seq()
            digest = _compute_digest(seq, op_kind, payload)
            self._conn.execute(
                "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (?, ?, ?, ?)",
                (seq, op_kind, payload, digest),
            )
            self._rebuild_fts()
            self._conn.execute("COMMIT")
        except Exception:
            # BEGIN IMMEDIATE itself can be the failing statement (e.g. lock
            # contention from another writer — this is exactly Matrix case
            # DB-09). ROLLBACK then has nothing to roll back and would raise
            # its own "cannot rollback - no transaction is active" error,
            # masking the real one — verified by direct reproduction during
            # this plan's pre-flight testing. Guard it with in_transaction.
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            self._known_entity_ids = set()
            self._reference = self._replay_journal()
            raise

    def persist(self, record: PersistRecord) -> None:
        self._require_open()
        self._reference.persist(record)
        self._track_entity_ids_safe(record)
        self._write_operation("persist", _encode_persist_op(record))

    def create_episode(
        self, *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant
    ) -> None:
        self._require_open()
        self._reference.create_episode(id=id, subject=subject, context=context, opened_at=opened_at)
        self._known_entity_ids.add(id)
        self._write_operation(
            "create_episode",
            _encode_create_episode_op(id=id, subject=subject, context=context, opened_at=opened_at),
        )

    def append_episode(self, episode: Id | Ref, item: Ref) -> None:
        self._require_open()
        self._reference.append_episode(episode, item)
        self._write_operation("append_episode", _encode_append_episode_op(episode, item))

    def close_episode(self, episode: Id | Ref, at: WallInstant) -> None:
        self._require_open()
        self._reference.close_episode(episode, at)
        self._write_operation("close_episode", _encode_close_episode_op(episode, at))
```

Add `from memory.store import lexical_content` to the top-of-file import block (consolidate with the existing `from memory.store import PersistRecord` line — change it to `from memory.store import PersistRecord, lexical_content`).

**Important ordering note, verified against a real forced-failure scenario before this plan was written:** `persist()` calls `self._reference.persist(record)` (and `_track_entity_ids_safe`) BEFORE `_write_operation`. If `self._reference.persist(record)` itself raises (a semantic rejection — `IdentityCollision`, etc.), `_write_operation` is never called, so no journal row and no reference mutation occurs (the reference's own `persist()` doesn't mutate state on a rejected call — this was already true of `InMemoryStore` before Pass 3). If `self._reference.persist(record)` succeeds but `_write_operation` then fails at the SQLite layer, the reference has ALREADY been mutated in-process — this is why `_write_operation`'s failure path discards and reloads `self._reference` from the journal rather than trying to "undo" the in-process mutation directly. Confirm this by reading `test_forced_sqlite_failure_leaves_reference_at_last_committed_state` above: it asserts `store.resolve(obs1.id) == obs1` (survives) and `store.resolve(obs2.id) is None` (correctly reverted) using only `store.resolve(...)` — never reaching into `store._reference` directly — proving the recovery is real, not merely something the test orchestrates externally.

**DB-09 implication, verified before this plan was written and directly relevant to Task 7's `test_db09_second_writer_on_locked_database_fails_explicitly`:** when a second `SqliteMemoryStore` is *constructed* against a database another connection already holds a write lock on, the failure happens inside `__init__` itself (Task 6's FTS-rebuild-after-replay step, which also opens `BEGIN IMMEDIATE`), not inside a later call to `persist()`. Confirmed by direct reproduction: `store2 = SqliteMemoryStore(path)` raises `sqlite3.OperationalError: database is locked` directly from the constructor while `store1` holds an open, uncommitted transaction — `store2` never finishes constructing, so there is no `store2` object to call `.persist()` on afterward. Task 7's DB-09 test must wrap the `SqliteMemoryStore(path)` constructor call itself in `pytest.raises(sqlite3.OperationalError)`, not a subsequent `.persist()` call — see that task for the corrected test.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/sqlite_store.py tests/memory/semantics/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: transactional mutation methods

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Query delegation and protocol conformance

**Files:**
- Modify: `src/memory/sqlite_store.py`
- Modify: `tests/memory/semantics/test_sqlite_store.py`

**Interfaces:**
- Consumes: Task 1-4's `SqliteMemoryStore`; `InMemoryStore`'s public `resolve`/`claims_for`/`conflicts_for`/`retention_for`/`retrieve`; `memory.store.MemoryStore` (the frozen Protocol).
- Produces: `SqliteMemoryStore.resolve`, `claims_for`, `conflicts_for`, `retention_for`, `retrieve` — all delegating to `self._reference`, all guarded by `_require_open()`. Confirms `isinstance(SqliteMemoryStore(path), MemoryStore)` while open. This completes `SqliteMemoryStore`'s `MemoryStore` surface — Task 6/7 add no new public methods, only tests and (Task 6) FTS-specific hardening of `_rebuild_fts`.

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_sqlite_store.py`:

```python
class TestQueryDelegation:
    def test_resolve_missing_returns_none(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "q1.sqlite")
        assert store.resolve(Id(Kind("t.missing"), "x")) is None
        store.close()

    def test_claims_for_matches_persisted_claim(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "q2.sqlite")
        claim = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        store.persist(claim)
        assert store.claims_for(SUBJECT, Kind("t.p")) == (claim,)
        store.close()

    def test_conflicts_for_matches_persisted_contradiction(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "q3.sqlite")
        claim = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        store.persist(claim)
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=claim.id), Ref(id=Id(Kind("t.claim"), "other"))),
            detected_at=AT, context=CTX,
        )
        store.persist(contradiction)
        assert contradiction in store.conflicts_for(SUBJECT, Kind("t.p"))
        store.close()

    def test_retention_for_matches_persisted_mark(self, tmp_path) -> None:
        store = SqliteMemoryStore(tmp_path / "q4.sqlite")
        target = Ref(id=Id(Kind("t.item"), "x"))
        mark = RetentionMark(item=target, accessibility=ACTIVE, at=AT)
        store.persist(mark)
        assert store.retention_for(Id(Kind("t.item"), "x")) == (mark,)
        store.close()

    def test_retrieve_identity_match(self, tmp_path) -> None:
        from memory.store import RetrievalQuery

        store = SqliteMemoryStore(tmp_path / "q5.sqlite")
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, identity=obs.id), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].item.id == obs.id
        store.close()

    def test_retrieve_text_match(self, tmp_path) -> None:
        from memory.store import RetrievalQuery

        store = SqliteMemoryStore(tmp_path / "q6.sqlite")
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findable text", at=AT, source="s", context=CTX)
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findable"), retrieved_at=AT)
        assert len(candidates) == 1
        store.close()

    def test_all_query_methods_raise_closed_after_close(self, tmp_path) -> None:
        from memory.store import RetrievalQuery

        store = SqliteMemoryStore(tmp_path / "q7.sqlite")
        store.close()
        with pytest.raises(SqliteStoreClosed):
            store.resolve(Id(Kind("t.x"), "x"))
        with pytest.raises(SqliteStoreClosed):
            store.claims_for(SUBJECT, Kind("t.p"))
        with pytest.raises(SqliteStoreClosed):
            store.conflicts_for(SUBJECT, Kind("t.p"))
        with pytest.raises(SqliteStoreClosed):
            store.retention_for(Id(Kind("t.x"), "x"))
        with pytest.raises(SqliteStoreClosed):
            store.retrieve(RetrievalQuery(context=CTX, identity=Id(Kind("t.x"), "x")), retrieved_at=AT)


class TestProtocolConformance:
    def test_sqlite_memory_store_satisfies_memory_store_protocol(self, tmp_path) -> None:
        from memory.store import MemoryStore

        store = SqliteMemoryStore(tmp_path / "protocol.sqlite")
        assert isinstance(store, MemoryStore)
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: FAIL/ERROR — `AttributeError: 'SqliteMemoryStore' object has no attribute 'resolve'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/memory/sqlite_store.py`'s imports: add `RecallCandidate` and `RetrievalQuery` — change `from memory.store import PersistRecord, lexical_content` to also import `RetrievalQuery`: `from memory.store import PersistRecord, RetrievalQuery, lexical_content`; add `from memory.recall import RecallCandidate`; add `from memory.retention import RetentionMark` is already present from Task 2 (no change needed there).

Append these methods to `SqliteMemoryStore`:

```python
    def resolve(self, item: Id | Ref) -> object:
        self._require_open()
        return self._reference.resolve(item)

    def claims_for(self, subject: Id | Ref, predicate: Kind) -> tuple[Claim[object], ...]:
        self._require_open()
        return self._reference.claims_for(subject, predicate)

    def conflicts_for(
        self, subject: Id | Ref, predicate: Kind
    ) -> tuple[Contradiction | Resolution, ...]:
        self._require_open()
        return self._reference.conflicts_for(subject, predicate)

    def retention_for(self, item: Id | Ref) -> tuple[RetentionMark, ...]:
        self._require_open()
        return self._reference.retention_for(item)

    def retrieve(
        self, query: RetrievalQuery, *, retrieved_at: WallInstant
    ) -> tuple[RecallCandidate, ...]:
        self._require_open()
        return self._reference.retrieve(query, retrieved_at=retrieved_at)
```

`resolve`'s return type here is intentionally `object` rather than `EntityMemoryRecord | None` to avoid importing `EntityMemoryRecord` merely for an annotation — Pyright will still check callers correctly through `InMemoryStore.resolve`'s own precise signature. If Pyright strict flags this as insufficiently precise for `MemoryStore` protocol conformance, import `EntityMemoryRecord` from `memory.store` instead (it's already an allowed import — `memory.store`'s public surface) and annotate `-> EntityMemoryRecord | None` precisely; either is acceptable, but match whatever the rest of the file's style already does once Pyright's actual verdict is known.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/sqlite_store.py tests/memory/semantics/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: query delegation and MemoryStore protocol conformance

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: FTS5 rebuild and index verification

**Files:**
- Modify: `src/memory/sqlite_store.py` (only if a genuine gap is found — see below; expect this task to be test-only)
- Modify: `tests/memory/semantics/test_sqlite_store.py`

**Interfaces:**
- Consumes: Task 4's `_rebuild_fts` (already wired into every write transaction and into `_replay_journal`'s caller — verify this last part: does `__init__`'s existing-database branch call `_rebuild_fts()` once after `_replay_journal()` returns? If not, that's a real gap this task must close — see Step 3.)
- Produces: full FT-01..14 matrix coverage; the FTS exactness test (§67); reactivation/archived-record FTS-vs-retention distinctness (§102-103); collision-leaves-FTS-unchanged (§104); failed-transaction-leaves-FTS-unchanged (§105); reopen-reproduces-same-indexable-content (§38, FT-14); FTS5-availability confirmation (§108). No public production API is added for direct FTS inspection — tests open their own separate `sqlite3` connection to the test database file (the Global Constraints' "no public raw-SQL/journal API" rule applies here too).

Before writing tests, read `src/memory/sqlite_store.py`'s current `__init__` (as Task 3-4 left it) and confirm: does the `elif present == set(_REQUIRED_TABLES):` branch call `self._rebuild_fts()` after `self._reference = self._replay_journal()`? **It currently does not** — Task 3 wrote `_replay_journal` before `_rebuild_fts` existed (Task 4 added it), so the constructor never rebuilds the FTS index after replaying an existing database. This is a real gap: fix it as this task's first step, verified below by `test_reopen_rebuilds_fts_from_replayed_state`, which would fail without the fix.

- [ ] **Step 1: Write the failing test**

Append to `tests/memory/semantics/test_sqlite_store.py`:

```python
def _fts_rows(path) -> list[tuple[str, str, int, str]]:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT id_kind, id_value, field_index, content FROM memory_fts ORDER BY id_kind, id_value, field_index"
        ).fetchall()
    finally:
        conn.close()


class TestFtsIndexing:
    def test_reopen_rebuilds_fts_from_replayed_state(self, tmp_path) -> None:
        path = tmp_path / "fts-reopen.sqlite"
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findable text", at=AT, source="s", context=CTX)
        store = SqliteMemoryStore(path)
        store.persist(obs)
        store.close()

        # Simulate a database whose FTS table was empty on disk for some
        # reason (e.g. an older writer that never populated it) -- reopening
        # must rebuild it from the journal-replayed reference, not trust
        # whatever was (or wasn't) already in memory_fts.
        conn = sqlite3.connect(str(path))
        conn.execute("DELETE FROM memory_fts")
        conn.commit()
        conn.close()
        assert _fts_rows(path) == []

        reopened = SqliteMemoryStore(path)
        rows = _fts_rows(path)
        assert rows == [("t.obs", "o1", 0, "findable text")]
        reopened.close()

    def test_fts_exactness_matches_lexical_content_exactly(self, tmp_path) -> None:
        from memory.store import lexical_content

        path = tmp_path / "fts-exact.sqlite"
        store = SqliteMemoryStore(path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="hello", at=AT, source="src-text", context=CTX, observer="obs-text")
        error = Error(id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="failed hard", at=AT, operation="do-thing")
        store.persist(obs)
        store.persist(error)
        store.close()

        rows = _fts_rows(path)
        obs_texts = sorted(r[3] for r in rows if r[0] == "t.obs" and r[1] == "o1")
        err_texts = sorted(r[3] for r in rows if r[0] == ERROR_KIND.value and r[1] == "err1")
        assert obs_texts == sorted(lexical_content(obs))
        assert err_texts == sorted(lexical_content(error))

    def test_non_searchable_fields_never_indexed(self, tmp_path) -> None:
        path = tmp_path / "fts-nonsearchable.sqlite"
        store = SqliteMemoryStore(path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value=42, at=AT, source="s", context=CTX)
        store.persist(obs)
        contradiction = Contradiction(id=Id(Kind("t.contra"), "k1"), subject=SUBJECT, statements=(Ref(id=Id(Kind("t.claim"), "a")), Ref(id=Id(Kind("t.claim"), "b"))), detected_at=AT, context=CTX)
        store.persist(contradiction)
        resolution_target = Resolution(contradiction=Ref(id=Id(Kind("t.contra"), "never-persisted-alone")), rationale="secret rationale text", resolved_by=AGENT, at=AT)
        store.close()

        rows = _fts_rows(path)
        contents = [r[3] for r in rows]
        assert "secret rationale text" not in contents  # never even persisted, but double-checks the principle
        assert not any(r[0] == "t.obs" and r[1] == "o1" for r in rows)  # int value: not indexed
        assert not any(r[0] == "t.contra" for r in rows)  # Contradiction: not Ref-targetable text source

    def test_archived_item_remains_physically_indexed(self, tmp_path) -> None:
        from memory.retention import ARCHIVED

        path = tmp_path / "fts-archived.sqlite"
        store = SqliteMemoryStore(path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        store.close()

        rows = _fts_rows(path)
        assert any(r[0] == "t.obs" and r[1] == "o1" and r[3] == "findme" for r in rows)

    def test_default_retrieve_still_excludes_archived_despite_fts_row_present(self, tmp_path) -> None:
        from memory.retention import ARCHIVED
        from memory.store import RetrievalQuery

        path = tmp_path / "fts-archived-retrieve.sqlite"
        store = SqliteMemoryStore(path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert candidates == ()
        store.close()

    def test_collision_rejection_leaves_fts_unchanged(self, tmp_path) -> None:
        path = tmp_path / "fts-collision.sqlite"
        store = SqliteMemoryStore(path)
        c1 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("original"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        store.persist(c1)
        rows_before = _fts_rows(path)

        c2 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("conflicting"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        with pytest.raises(IdentityCollision):
            store.persist(c2)
        store.close()

        rows_after = _fts_rows(path)
        assert rows_after == rows_before
        assert any(r[3] == "original" for r in rows_after)
        assert not any(r[3] == "conflicting" for r in rows_after)

    def test_failed_transaction_leaves_fts_unchanged(self, tmp_path) -> None:
        path = tmp_path / "fts-failed-txn.sqlite"
        store = SqliteMemoryStore(path)
        obs1 = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="first", at=AT, source="s", context=CTX)
        store.persist(obs1)
        rows_before = _fts_rows(path)

        obs2 = Observation(id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="second", at=AT, source="s", context=CTX)
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(obs2)
        store.close()

        rows_after = _fts_rows(path)
        assert rows_after == rows_before

    def test_idempotent_retry_does_not_duplicate_fts_content(self, tmp_path) -> None:
        path = tmp_path / "fts-idempotent.sqlite"
        store = SqliteMemoryStore(path)
        claim = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        store.persist(claim)
        store.persist(claim)
        store.close()

        rows = [r for r in _fts_rows(path) if r[3] == "v"]
        assert len(rows) == 1


def test_fts5_is_available_in_this_environment() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: FAIL specifically on `test_reopen_rebuilds_fts_from_replayed_state` (the real gap this task exists to close); everything else should already pass given Task 4's `_rebuild_fts` runs on every write.

- [ ] **Step 3: Write minimal implementation**

In `src/memory/sqlite_store.py`'s `__init__`, find the existing-database branch:

```python
        elif present == set(_REQUIRED_TABLES):
            self._validate_existing_schema()
            self._known_entity_ids: set[Id] = set()
            self._reference = self._replay_journal()
```

and change it to rebuild FTS once, in its own transaction, after a successful replay:

```python
        elif present == set(_REQUIRED_TABLES):
            self._validate_existing_schema()
            self._known_entity_ids: set[Id] = set()
            self._reference = self._replay_journal()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._rebuild_fts()
                self._conn.execute("COMMIT")
            except Exception:
                # BEGIN IMMEDIATE itself can be the failing statement (e.g.
                # lock contention from another writer — this is exactly
                # Matrix case DB-09, and is exercised by
                # test_db09_second_writer_on_locked_database_fails_explicitly
                # in Task 7). ROLLBACK then has nothing to roll back and
                # would raise its own error, masking the real one — same
                # fix as _write_operation and _initialize_new_database.
                # This failure is operational, not a corruption finding —
                # the journal itself already replayed successfully — so
                # the original exception propagates as-is rather than
                # being wrapped in StoreCorruption.
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                self._conn.close()
                raise
```

This matches §75's intent: "If this rebuild cannot complete, opening the backend fails. It does not return a store with a known-broken index. Journal state itself remains untouched" (the journal was never written to in this branch — only `memory_fts` rows, which roll back cleanly on failure). It does NOT wrap the failure as `StoreCorruption`, unlike most other failure paths in this file — verified directly against a real lock-contention scenario (Matrix case DB-09) during this plan's pre-flight testing: constructing a second store while another connection holds an open write transaction fails right here, and the failure is a transient `sqlite3.OperationalError` about the lock, not a finding about the data's trustworthiness — the journal replay that already completed successfully is not in question. Wrapping it as `StoreCorruption` would misrepresent an operational/contention failure as a data-integrity one.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/memory/semantics/test_sqlite_store.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/memory/sqlite_store.py tests/memory/semantics/test_sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: FTS5 rebuild-on-open and index verification

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Backend-equivalence and crash/corruption closing gate

**Files:** none created; may modify `src/memory/sqlite_store.py` only if this task's audit finds a genuine gap (see closing-gate discipline below — mirrors Pass 2 Task 7's precedent, where a genuine production bug surfaced during the audit itself).
- Modify: `tests/memory/semantics/test_sqlite_store.py`, or split into `tests/memory/semantics/test_sqlite_integrity.py` if the file has grown unwieldy by this point (§110) — implementer's judgment, not required.

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: nothing new in production code unless a genuine gap is found. This is Pass 3's checkpoint, structured exactly like Pass 2's Task 7 closing-gate audit — treat the matrix cross-check as real work, not a formality; Pass 2's equivalent audit found a real production bug.

- [ ] **Step 1: Backend-equivalence tests (BE-01..08)**

Add a test-only helper (not production code) that runs the same operation trace against both `InMemoryStore` and `SqliteMemoryStore`, comparing outcomes at each checkpoint:

```python
class TestBackendEquivalence:
    def _paired_stores(self, tmp_path):
        from memory.store import InMemoryStore

        reference = InMemoryStore()
        durable = SqliteMemoryStore(tmp_path / "equivalence.sqlite")
        return reference, durable

    def test_ordinary_records_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        reference.persist(obs)
        durable.persist(obs)
        assert reference.resolve(obs.id) == durable.resolve(obs.id)
        durable.close()

    def test_idempotent_retries_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        claim = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        for store in (reference, durable):
            store.persist(claim)
            store.persist(claim)
        assert reference.claims_for(SUBJECT, Kind("t.p")) == durable.claims_for(SUBJECT, Kind("t.p"))
        durable.close()

    def test_identity_collisions_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        c1 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("first"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        c2 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("second"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        for store in (reference, durable):
            store.persist(c1)
            with pytest.raises(IdentityCollision):
                store.persist(c2)
        durable.close()

    def test_inference_with_embedded_claim_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        claim = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        inference = Inference(id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim, at=AT)
        for store in (reference, durable):
            store.persist(inference)
        assert reference.resolve(claim.id) == durable.resolve(claim.id)
        assert reference.resolve(inference.id) == durable.resolve(inference.id)
        durable.close()

    def test_error_cause_chain_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        root = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="root", at=AT)
        wrap = Error(id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrap", at=AT, cause=root)
        for store in (reference, durable):
            store.persist(wrap)
        assert reference.resolve(root.id) == durable.resolve(root.id)
        assert reference.resolve(wrap.id) == durable.resolve(wrap.id)
        durable.close()

    def test_contradiction_and_resolutions_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        c1 = Claim(id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("a"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        c2 = Claim(id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("b"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT)
        contradiction = Contradiction(id=Id(Kind("t.contra"), "k1"), subject=SUBJECT, statements=(Ref(id=c1.id), Ref(id=c2.id)), detected_at=AT, context=CTX)
        r1 = Resolution(contradiction=Ref(id=contradiction.id), rationale="first", resolved_by=AGENT, at=AT)
        r2 = Resolution(contradiction=Ref(id=contradiction.id), rationale="second", resolved_by=AGENT, at=AT)
        for store in (reference, durable):
            store.persist(c1)
            store.persist(c2)
            store.persist(contradiction)
            store.persist(r1)
            store.persist(r2)
        assert reference.conflicts_for(SUBJECT, Kind("t.p")) == durable.conflicts_for(SUBJECT, Kind("t.p"))
        durable.close()

    def test_retention_history_equivalent(self, tmp_path) -> None:
        from memory.retention import ARCHIVED

        reference, durable = self._paired_stores(tmp_path)
        target = Id(Kind("t.item"), "x")
        mark1 = RetentionMark(item=Ref(id=target), accessibility=ACTIVE, at=AT)
        mark2 = RetentionMark(item=Ref(id=target), accessibility=ARCHIVED, at=AT)
        for store in (reference, durable):
            store.persist(mark1)
            store.persist(mark2)
        assert reference.retention_for(target) == durable.retention_for(target)
        durable.close()

    def test_episode_lifecycle_equivalent(self, tmp_path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        episode_id = Id(Kind("t.episode"), "ep1")
        item = Ref(id=Id(Kind("t.item"), "x"))
        for store in (reference, durable):
            store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
            store.append_episode(episode_id, item)
            store.close_episode(episode_id, AT)
        ref_episode = reference.resolve(episode_id)
        dur_episode = durable.resolve(episode_id)
        assert ref_episode.items() == dur_episode.items()  # type: ignore[union-attr]
        assert ref_episode.closed_at == dur_episode.closed_at  # type: ignore[union-attr]
        durable.close()

    def test_archived_deprioritized_retrieval_equivalent(self, tmp_path) -> None:
        from memory.retention import ACTIVE, DEPRIORITIZED
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        deprioritized_obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme first", at=AT, source="s", context=CTX)
        active_obs = Observation(id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="findme second", at=AT, source="s", context=CTX)
        for store in (reference, durable):
            store.persist(deprioritized_obs)
            store.persist(active_obs)
            store.persist(RetentionMark(item=Ref(id=deprioritized_obs.id), accessibility=DEPRIORITIZED, at=AT))
        ref_candidates = reference.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        dur_candidates = durable.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert [c.item.id for c in ref_candidates] == [c.item.id for c in dur_candidates]
        durable.close()

    def test_combined_identity_and_lexical_retrieval_equivalent(self, tmp_path) -> None:
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findable text", at=AT, source="s", context=CTX)
        for store in (reference, durable):
            store.persist(obs)
        ref_candidates = reference.retrieve(RetrievalQuery(context=CTX, identity=obs.id, text="findable"), retrieved_at=AT)
        dur_candidates = durable.retrieve(RetrievalQuery(context=CTX, identity=obs.id, text="findable"), retrieved_at=AT)
        assert [c.relevance for c in ref_candidates] == [c.relevance for c in dur_candidates]
        durable.close()

    def test_custom_retention_kind_error_equivalent(self, tmp_path) -> None:
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        for store in (reference, durable):
            store.persist(obs)
            store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=Kind("t.custom"), at=AT))
            with pytest.raises(ValueError):
                store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        durable.close()
```

- [ ] **Step 2: Database integrity tests (DB-01..10)**

Add tests for every DB case not already covered by Tasks 3/4/6 (`DB-01` reopen, `DB-02` interrupted Episode append, `DB-03` record+FTS atomicity, `DB-05`/`DB-06`/`DB-07` schema/payload/sequence corruption are already covered by Task 1/3/6 — audit which of `DB-04`, `DB-08`, `DB-09`, `DB-10` still need dedicated tests and add them):

```python
class TestDatabaseIntegrity:
    def test_db02_interrupted_episode_append_leaves_no_partial_append(self, tmp_path) -> None:
        path = tmp_path / "db02.sqlite"
        store = SqliteMemoryStore(path)
        episode_id = Id(Kind("t.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)

        item = Ref(id=Id(Kind("t.item"), "x"))
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.append_episode(episode_id, item)

            from memory.episode import Episode
            resolved = store.resolve(episode_id)
            assert isinstance(resolved, Episode)
            assert resolved.items() == ()
        store.close()

    def test_db04_retention_mark_and_derived_index_atomic(self, tmp_path) -> None:
        path = tmp_path / "db04.sqlite"
        store = SqliteMemoryStore(path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme", at=AT, source="s", context=CTX)
        store.persist(obs)

        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ACTIVE, at=AT))
            assert store.retention_for(obs.id) == ()
        store.close()

    def test_db08_two_readers_of_committed_data_agree(self, tmp_path) -> None:
        path = tmp_path / "db08.sqlite"
        store = SqliteMemoryStore(path)
        obs = Observation(id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX)
        store.persist(obs)
        store.close()

        reader1 = SqliteMemoryStore(path)
        reader2 = SqliteMemoryStore(path)
        assert reader1.resolve(obs.id) == reader2.resolve(obs.id)
        reader1.close()
        reader2.close()

    def test_db09_second_writer_on_locked_database_fails_explicitly(self, tmp_path) -> None:
        # VERIFIED before this plan was written: the failure happens inside
        # the SECOND store's CONSTRUCTOR, not a later persist() call — the
        # constructor's own FTS-rebuild-after-replay step (Task 6) needs
        # BEGIN IMMEDIATE too, and that's what collides with store1's held
        # lock. store2 never finishes constructing, so there is no store2
        # object afterward — direct reproduction confirmed this exact
        # sequence, see the "DB-09 implication" note in Task 4.
        path = tmp_path / "db09.sqlite"
        store1 = SqliteMemoryStore(path)
        store1._conn.execute("BEGIN IMMEDIATE")  # hold a write lock without committing
        store1._conn.execute(
            "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (1, 'persist', ?, ?)",
            (b"x", b"y"),
        )

        with pytest.raises(sqlite3.OperationalError):
            SqliteMemoryStore(path)  # lock contention -- explicit failure, no silent retry/wait

        store1._conn.execute("ROLLBACK")
        store1.close()

        # After store1 releases the lock, a fresh construction succeeds cleanly.
        store3 = SqliteMemoryStore(path)
        store3.close()

    def test_db10_sql_injection_like_text_cannot_mutate_database(self, tmp_path) -> None:
        path = tmp_path / "db10.sqlite"
        store = SqliteMemoryStore(path)
        malicious = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT,
            value='\'; DROP TABLE memory_ops; --', at=AT, source="s", context=CTX,
        )
        store.persist(malicious)
        from memory.store import RetrievalQuery
        candidates = store.retrieve(
            RetrievalQuery(context=CTX, text='\'; DROP TABLE memory_ops; --'), retrieved_at=AT
        )
        assert len(candidates) == 1
        store.close()

        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 1  # table intact, row survived
```

- [ ] **Step 3: Episode SQLite transition audit (ES-01..12)**

Cross-check every `ES-01` through `ES-12` case from `docs/memory-passes/03-sqlite-backend.md` §63 against the test suite built across Tasks 1-6 plus this task's own additions. Most are already covered (Task 3's replay tests, Task 4's episode-lifecycle test, this task's `test_db02_interrupted_episode_append_leaves_no_partial_append`). Confirm `ES-12` ("snapshot persist is not an Episode mutation mechanism") has a test — if not, add:

```python
class TestEpisodeSnapshotIsNotAMutationMechanism:
    def test_persist_does_not_accept_an_episode(self, tmp_path) -> None:
        from memory.episode import Episode
        from memory.store import UnsupportedMemoryRecord

        store = SqliteMemoryStore(tmp_path / "es12.sqlite")
        episode = Episode(id=Id(Kind("t.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT)
        with pytest.raises(UnsupportedMemoryRecord):
            store.persist(episode)  # type: ignore[arg-type]
        store.close()
```

Record the full case-ID-to-test checklist in your task report (the same discipline Pass 2's Task 7 used) — every `ES-*`/`CL-11`/`RR-*`/`FT-*`/`BE-*`/`DB-*` case, which test covers it, confirmed by reading the test body.

- [ ] **Step 4: Run the full closing-gate quality checks**

```bash
uv run pytest -v
uv run ruff check src/memory tests/memory
uv run pyright src/memory tests/memory
grep -rn "^import sqlite3\|^from sqlite3" src/memory/episode.py src/memory/recall.py src/memory/retention.py src/memory/belief.py src/memory/codec.py src/memory/store.py
```

Expected: every test green (no skipped/xfail on any M/N/O/P/Q/U requirement), Ruff clean, Pyright strict clean (0 errors), the grep produces no output (confirming §80 — `sqlite3` exists only in `sqlite_store.py`). Also confirm `src/core/` has zero touched files across all of Pass 3 (`git diff --stat <pass-3-start-commit>..HEAD -- src/core/` produces no output), and that Pass 1/Pass 2 production files (`episode.py`, `recall.py`, `retention.py`, `belief.py`, `codec.py`, `store.py`) are unchanged across Pass 3 unless a real counterexample forced a documented, ledgered exception (per §113 — "Any required semantic change pauses the pass for review").

- [ ] **Step 5: Commit (only if Steps 1-4 required production fixes)**

If everything from Tasks 1-6 was already correct and this task only added tests, commit the tests alone. If the closing-gate audit found a genuine gap (mirroring Pass 2's Task 7, which found a real bug), fix it, verify no regressions, and commit both together:

```bash
git add tests/memory/semantics/test_sqlite_store.py src/memory/sqlite_store.py
git commit -m "$(cat <<'EOF'
Memory Pass 3: closing gate — backend equivalence, database integrity, Episode transition audit

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

Pass 3 is closed once this task's steps pass clean. Pass 4 (architectural closure) starts a new preregistration — no new Memory ontology, no Core changes; it audits, it does not decide new semantics.
