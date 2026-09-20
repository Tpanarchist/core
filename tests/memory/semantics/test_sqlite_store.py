"""Propositions for memory.sqlite_store.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections
ES (Episode SQLite transitions), CL-11 (contradiction lookup equivalence),
RR (retrieval/retention), FT (FTS indexing), BE (backend equivalence),
IM (import graph), DB (database integrity).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.effect import Effect
from core.epistemic import Claim, Contradiction, Inference, Resolution
from core.error import Error
from core.event import Event
from core.identity import Id, Namespace, Ref
from core.observation import Observation
from core.provenance import Provenance
from core.time import Duration, WallInstant
from core.value import Kind, Known, Unknown
from memory.retention import ACTIVE, RetentionMark
from memory.sqlite_store import (
    SqliteMemoryStore,
    SqliteStoreClosed,
    StoreCorruption,
    UnsupportedSchemaVersion,
    _compute_digest,  # pyright: ignore[reportPrivateUsage]
    _decode_record,  # pyright: ignore[reportPrivateUsage]
    _encode_record,  # pyright: ignore[reportPrivateUsage]
)
from memory.store import PersistRecord


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


AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
CTX = Context(as_of=AT)
SUBJECT = Id(Kind("t.subject"), "s1")
AGENT = Id(Kind("t.agent"), "a1")
ERROR_KIND = Kind("t.error")


class TestRecordCodecRoundTrip:
    def test_observation_round_trips(self) -> None:
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="hello", at=AT,
            source="sensor-1", context=CTX, observer=None,
        )
        assert _decode_record(_encode_record(obs)) == obs

    def test_observation_with_observer_and_namespaced_ref_subject_round_trips(self) -> None:
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"),
            subject=Ref(id=SUBJECT, namespace=Namespace(("finance",))),
            value=("a", "b", 3), at=AT, source=42, context=CTX, observer="watcher-1",
        )
        assert _decode_record(_encode_record(obs)) == obs

    def test_claim_known_round_trips(self) -> None:
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT,
            evidence_refs=(Ref(id=SUBJECT),), at=AT,
        )
        decoded = _decode_record(_encode_record(claim))
        assert isinstance(decoded, Claim)
        assert decoded == claim
        assert isinstance(decoded.value, Known)

    def test_claim_unknown_round_trips(self) -> None:
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Unknown(), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        decoded = _decode_record(_encode_record(claim))
        assert isinstance(decoded, Claim)
        assert decoded == claim
        assert isinstance(decoded.value, Unknown)

    def test_inference_with_embedded_claim_round_trips(self) -> None:
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference: Inference[object] = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(Ref(id=SUBJECT),),
            method=Kind("t.m"), conclusion=claim, at=AT,
        )
        decoded = _decode_record(_encode_record(inference))
        assert isinstance(decoded, Inference)
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
        decoded1 = _decode_record(_encode_record(e1))
        decoded2 = _decode_record(_encode_record(e2))
        assert isinstance(decoded1, Event)
        assert isinstance(decoded2, Event)
        assert decoded1.payload == "A"
        assert decoded2.payload == "B"
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
        assert isinstance(decoded, Error)
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

        def _tag_of(node: object) -> str:
            if not isinstance(node, tuple) or not node:
                raise AssertionError(f"expected a non-empty tuple, got {node!r}")
            # isinstance() on a bare `tuple` narrows to `tuple[Unknown, ...]`, not
            # `tuple[object, ...]` -- cast() after the real runtime check mirrors
            # _expect_tuple's identical pattern in src/memory/sqlite_store.py.
            tag = cast(tuple[object, ...], node)[0]
            if not isinstance(tag, str):
                raise AssertionError(f"expected a string tag, got {node!r}")
            return tag

        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT,
            source="s", context=CTX,
        )
        inference: Inference[object] = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"),
            conclusion=claim, at=AT,
        )
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=claim.id), Ref(id=Id(Kind("t.claim"), "x"))),
            detected_at=AT, context=CTX,
        )
        resolution = Resolution(
            contradiction=Ref(id=Id(Kind("t.contra"), "k1")), rationale="r",
            resolved_by=AGENT, at=AT,
        )
        event = Event(id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload="p")
        effect = Effect(
            id=Id(Kind("t.effect"), "f1"), kind=Kind("t.k"),
            description="d", target="t", at=AT,
        )
        provenance = Provenance(
            id=Id(Kind("t.prov"), "p1"), transform_id=Id(Kind("t.tx"), "t1"),
            transform_name="n", transform_version="1",
            inputs=(), parents=(), at=AT, duration=Duration(0),
        )
        error = Error(id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="m", at=AT)
        mark = RetentionMark(item=Ref(id=Id(Kind("t.item"), "x")), accessibility=ACTIVE, at=AT)

        records: list[PersistRecord] = [
            obs, claim, inference, contradiction, resolution,
            event, effect, provenance, error, mark,
        ]
        tags = [_tag_of(decode_persisted_value(_encode_record(r))) for r in records]
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
