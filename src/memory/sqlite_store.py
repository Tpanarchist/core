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
