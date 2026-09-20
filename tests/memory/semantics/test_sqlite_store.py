"""Propositions for memory.sqlite_store.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections
ES (Episode SQLite transitions), CL-11 (contradiction lookup equivalence),
RR (retrieval/retention), FT (FTS indexing), BE (backend equivalence),
IM (import graph), DB (database integrity).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from memory.sqlite_store import (
    SqliteMemoryStore,
    SqliteStoreClosed,
    StoreCorruption,
    UnsupportedSchemaVersion,
)


class TestNewDatabaseInitialization:
    def test_creates_expected_schema_objects(self, tmp_path: Path) -> None:
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

    def test_integrity_check_passes_on_fresh_database(self, tmp_path: Path) -> None:
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
    def test_reopening_valid_v1_database_succeeds(self, tmp_path: Path) -> None:
        path = tmp_path / "reopen.sqlite"
        first = SqliteMemoryStore(path)
        first.close()
        second = SqliteMemoryStore(path)  # must not raise
        second.close()

    def test_partial_schema_missing_ops_table_is_corruption(self, tmp_path: Path) -> None:
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

    def test_partial_schema_missing_meta_table_is_corruption(self, tmp_path: Path) -> None:
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

    def test_memory_fts_replaced_by_non_fts_table_is_corruption(self, tmp_path: Path) -> None:
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

    def test_missing_schema_version_key_is_corruption(self, tmp_path: Path) -> None:
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
    def test_wrong_schema_version_raises(self, tmp_path: Path) -> None:
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
    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "idempotent-close.sqlite")
        store.close()
        store.close()  # must not raise

    def test_no_temp_file_left_after_close(self, tmp_path: Path) -> None:
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
    def test_fresh_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.sqlite_store",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
