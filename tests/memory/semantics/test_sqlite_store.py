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
from memory.store import IdentityCollision, InMemoryStore, PersistRecord


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


def _insert_op_row(conn: sqlite3.Connection, seq: int, op_kind: str, payload: bytes) -> None:
    from memory.sqlite_store import _compute_digest  # pyright: ignore[reportPrivateUsage]

    digest = _compute_digest(seq, op_kind, payload)
    conn.execute(
        "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (?, ?, ?, ?)",
        (seq, op_kind, payload, digest),
    )


def _journal_op_kinds(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT op_kind FROM memory_ops ORDER BY seq").fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def _fresh_v1_schema(path: Path) -> sqlite3.Connection:
    from memory.sqlite_store import (
        _SCHEMA_SQL,  # pyright: ignore[reportPrivateUsage]
        _SCHEMA_VERSION,  # pyright: ignore[reportPrivateUsage]
    )

    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA_SQL)
    conn.execute("INSERT INTO memory_meta VALUES ('schema_version', ?)", (_SCHEMA_VERSION,))
    conn.commit()
    return conn


class TestJournalReplay:
    def test_replays_a_simple_observation(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

        path = tmp_path / "replay1.sqlite"
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="hello", at=AT,
            source="s", context=CTX,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(obs))
        conn.commit()
        conn.close()

        store = SqliteMemoryStore(path)
        # store.resolve() is Task 5's query-delegation surface, not Task 3's --
        # what Task 3 guarantees is that replay reconstructs self._reference.
        assert store._reference.resolve(obs.id) == obs  # pyright: ignore[reportPrivateUsage]
        store.close()

    def test_replays_episode_create_append_close_in_order(self, tmp_path: Path) -> None:
        from memory.episode import Episode
        from memory.sqlite_store import (
            _encode_append_episode_op,  # pyright: ignore[reportPrivateUsage]
            _encode_close_episode_op,  # pyright: ignore[reportPrivateUsage]
            _encode_create_episode_op,  # pyright: ignore[reportPrivateUsage]
        )

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
        resolved = store._reference.resolve(episode_id)  # pyright: ignore[reportPrivateUsage]
        assert isinstance(resolved, Episode)
        assert resolved.items() == (item,)
        assert resolved.closed_at == AT
        store.close()

    def test_resolution_before_contradiction_is_corruption(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

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

    def test_episode_append_after_close_is_corruption(self, tmp_path: Path) -> None:
        from memory.sqlite_store import (
            _encode_append_episode_op,  # pyright: ignore[reportPrivateUsage]
            _encode_close_episode_op,  # pyright: ignore[reportPrivateUsage]
            _encode_create_episode_op,  # pyright: ignore[reportPrivateUsage]
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

    def test_unknown_op_kind_is_corruption(self, tmp_path: Path) -> None:
        path = tmp_path / "unknown-opkind.sqlite"
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "teleport", b"whatever")
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_checksum_mismatch_is_corruption(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

        path = tmp_path / "badchecksum.sqlite"
        obs: Observation[object] = Observation(
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

    def test_payload_tamper_is_corruption(self, tmp_path: Path) -> None:
        from memory.sqlite_store import (
            _compute_digest,  # pyright: ignore[reportPrivateUsage]
            _encode_persist_op,  # pyright: ignore[reportPrivateUsage]
        )

        path = tmp_path / "tamperedpayload.sqlite"
        obs: Observation[object] = Observation(
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

    def test_sequence_gap_is_corruption(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

        path = tmp_path / "seqgap.sqlite"
        obs1: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="a", at=AT, source="s", context=CTX,
        )
        obs2: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="b", at=AT, source="s", context=CTX,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(obs1))
        _insert_op_row(conn, 3, "persist", _encode_persist_op(obs2))  # gap: 2 is missing
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_sequence_not_starting_at_one_is_corruption(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

        path = tmp_path / "seqstart.sqlite"
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="a", at=AT, source="s", context=CTX,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 2, "persist", _encode_persist_op(obs))  # starts at 2, not 1
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption):
            SqliteMemoryStore(path)

    def test_special_event_regression_survives_replay(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

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
            # Task 3 produces only replay, not the public write path (Task 4) --
            # exercising this regression through the private reference directly
            # is exactly what replay is responsible for guaranteeing.
            store._reference.persist(e2)  # pyright: ignore[reportPrivateUsage]
        store.close()

    def test_special_embedded_identity_regression_survives_replay(self, tmp_path: Path) -> None:
        from memory.sqlite_store import _encode_persist_op  # pyright: ignore[reportPrivateUsage]

        path = tmp_path / "embedded-identity.sqlite"
        claim1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference: Inference[object] = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim1, at=AT,
        )
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "persist", _encode_persist_op(inference))
        conn.commit()
        conn.close()

        store = SqliteMemoryStore(path)
        claim2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference_swapped: Inference[object] = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim2, at=AT,
        )
        with pytest.raises(IdentityCollision):
            # See note in test_special_event_regression_survives_replay above:
            # store.persist() is Task 4's public write path, not Task 3's.
            store._reference.persist(inference_swapped)  # pyright: ignore[reportPrivateUsage]
        store.close()

    from core.identity import (
        Id as _Id,  # noqa: F401  (import already present above; keep for clarity)
    )


class TestTransactionalPersist:
    def test_persist_then_resolve(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "persist1.sqlite")
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        assert store._reference.resolve(obs.id) == obs  # pyright: ignore[reportPrivateUsage]
        store.close()

    def test_persist_survives_close_and_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "persist2.sqlite"
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        store = SqliteMemoryStore(path)
        store.persist(obs)
        store.close()

        reopened = SqliteMemoryStore(path)
        assert reopened._reference.resolve(obs.id) == obs  # pyright: ignore[reportPrivateUsage]
        reopened.close()

    def test_identity_collision_leaves_no_journal_row(self, tmp_path: Path) -> None:
        path = tmp_path / "collision.sqlite"
        store = SqliteMemoryStore(path)
        c1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("first"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        c2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("second"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(c1)
        with pytest.raises(IdentityCollision):
            store.persist(c2)
        store.close()

        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 1  # only c1's persist op, never c2's rejected attempt

    def test_idempotent_retry_does_not_duplicate_journal_row(self, tmp_path: Path) -> None:
        path = tmp_path / "idempotent.sqlite"
        store = SqliteMemoryStore(path)
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(claim)
        store.persist(claim)  # idempotent retry
        store.close()

        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 2  # journal records BOTH successful calls (§100) -- it is an
        # operation log, not a state-delta log; idempotency is a reference-semantics
        # property (resolve() still returns one Claim), not a journal-compaction rule.

    def test_episode_create_append_close_persist_across_reopen(self, tmp_path: Path) -> None:
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
        resolved = reopened._reference.resolve(episode_id)  # pyright: ignore[reportPrivateUsage]
        assert isinstance(resolved, Episode)
        assert resolved.items() == (item,)
        assert resolved.closed_at == AT
        reopened.close()

    def test_close_then_persist_raises_closed(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "closed.sqlite")
        store.close()
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        with pytest.raises(SqliteStoreClosed):
            store.persist(obs)

    def test_close_then_create_episode_raises_closed(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "closed2.sqlite")
        store.close()
        with pytest.raises(SqliteStoreClosed):
            store.create_episode(
                id=Id(Kind("t.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT
            )


class _BlockNewOps:
    """Test-only failure injection: blocks INSERT into memory_ops via a
    trigger, while SELECT keeps working. VERIFIED before this plan was
    written that renaming memory_ops away instead (an earlier draft of
    this test used that) is WRONG -- it also breaks the store's own
    in-process recovery, since `_write_operation`'s except-handler calls
    `_replay_journal()`, which itself needs to SELECT from memory_ops to
    rebuild `self._reference`. Renaming the table away makes recovery
    itself fail too, silently leaving `self._reference` un-reverted (the
    bug reproduced during this plan's pre-flight testing: `resolve()`
    after the "failed" write still returned the supposedly-reverted
    record). A trigger blocks writes without blocking reads, which is
    also a more realistic failure shape (a real disk-full or constraint
    failure blocks the specific write, not all access to the table).
    Also note: SQLite forbids triggers directly on FTS5 virtual tables --
    this technique only works on ordinary tables like memory_ops.
    """

    def __init__(self, store: SqliteMemoryStore) -> None:
        self._conn = store._conn  # pyright: ignore[reportPrivateUsage]

    def __enter__(self) -> _BlockNewOps:
        self._conn.execute(
            "CREATE TRIGGER block_new_ops BEFORE INSERT ON memory_ops "
            "BEGIN SELECT RAISE(ABORT, 'forced failure for testing'); END;"
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._conn.execute("DROP TRIGGER block_new_ops")


class TestTransactionFailureRollback:
    def test_forced_sqlite_failure_leaves_reference_at_last_committed_state(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "forced-failure.sqlite"
        store = SqliteMemoryStore(path)
        obs1: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="first",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs1)

        obs2: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="second",
            at=AT, source="s", context=CTX,
        )
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(obs2)

            # Confirm the IN-PROCESS reference already reflects only the
            # committed state, without any external reconstruction, WHILE
            # the trigger is still armed (recovery must not need repair
            # first -- only the specific write was blocked, not reads).
            assert store._reference.resolve(obs1.id) == obs1  # pyright: ignore[reportPrivateUsage]
            assert store._reference.resolve(obs2.id) is None  # pyright: ignore[reportPrivateUsage]
        store.close()

    def test_database_reopen_after_forced_failure_matches_pre_failure_state(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "reopen-after-failure.sqlite"
        store = SqliteMemoryStore(path)
        obs1: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="first",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs1)

        obs2: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="second",
            at=AT, source="s", context=CTX,
        )
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(obs2)
        store.close()

        reopened = SqliteMemoryStore(path)
        assert reopened._reference.resolve(obs1.id) == obs1  # pyright: ignore[reportPrivateUsage]
        assert reopened._reference.resolve(obs2.id) is None  # pyright: ignore[reportPrivateUsage]
        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 1
        reopened.close()


class TestQueryDelegation:
    def test_resolve_missing_returns_none(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "q1.sqlite")
        assert store.resolve(Id(Kind("t.missing"), "x")) is None
        store.close()

    def test_claims_for_matches_persisted_claim(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "q2.sqlite")
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(claim)
        assert store.claims_for(SUBJECT, Kind("t.p")) == (claim,)
        store.close()

    def test_conflicts_for_matches_persisted_contradiction(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "q3.sqlite")
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(claim)
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=claim.id), Ref(id=Id(Kind("t.claim"), "other"))),
            detected_at=AT, context=CTX,
        )
        store.persist(contradiction)
        assert contradiction in store.conflicts_for(SUBJECT, Kind("t.p"))
        store.close()

    def test_retention_for_matches_persisted_mark(self, tmp_path: Path) -> None:
        store = SqliteMemoryStore(tmp_path / "q4.sqlite")
        target = Ref(id=Id(Kind("t.item"), "x"))
        mark = RetentionMark(item=target, accessibility=ACTIVE, at=AT)
        store.persist(mark)
        assert store.retention_for(Id(Kind("t.item"), "x")) == (mark,)
        store.close()

    def test_retrieve_identity_match(self, tmp_path: Path) -> None:
        from memory.store import RetrievalQuery

        store = SqliteMemoryStore(tmp_path / "q5.sqlite")
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, identity=obs.id), retrieved_at=AT)
        assert len(candidates) == 1
        assert candidates[0].item.id == obs.id
        store.close()

    def test_retrieve_text_match(self, tmp_path: Path) -> None:
        from memory.store import RetrievalQuery

        store = SqliteMemoryStore(tmp_path / "q6.sqlite")
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findable text",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findable"), retrieved_at=AT)
        assert len(candidates) == 1
        store.close()

    def test_all_query_methods_raise_closed_after_close(self, tmp_path: Path) -> None:
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
            store.retrieve(
                RetrievalQuery(context=CTX, identity=Id(Kind("t.x"), "x")), retrieved_at=AT
            )


class TestProtocolConformance:
    def test_sqlite_memory_store_satisfies_memory_store_protocol(self, tmp_path: Path) -> None:
        from memory.store import MemoryStore

        store = SqliteMemoryStore(tmp_path / "protocol.sqlite")
        assert isinstance(store, MemoryStore)
        store.close()


def _fts_rows(path: Path) -> list[tuple[str, str, int, str]]:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT id_kind, id_value, field_index, content FROM memory_fts "
            "ORDER BY id_kind, id_value, field_index"
        ).fetchall()
    finally:
        conn.close()


class TestFtsIndexing:
    def test_reopen_rebuilds_fts_from_replayed_state(self, tmp_path: Path) -> None:
        path = tmp_path / "fts-reopen.sqlite"
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findable text",
            at=AT, source="s", context=CTX,
        )
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
        # lexical_content(obs) indexes both the str value and the str
        # source field (field_index 0 and 1 respectively) -- see
        # lexical_content's Observation branch in memory/store.py.
        assert rows == [("t.obs", "o1", 0, "findable text"), ("t.obs", "o1", 1, "s")]
        reopened.close()

    def test_fts_exactness_matches_lexical_content_exactly(self, tmp_path: Path) -> None:
        from memory.store import lexical_content

        path = tmp_path / "fts-exact.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="hello",
            at=AT, source="src-text", context=CTX, observer="obs-text",
        )
        error = Error(
            id=Id(ERROR_KIND, "err1"), kind=ERROR_KIND, message="failed hard",
            at=AT, operation="do-thing",
        )
        store.persist(obs)
        store.persist(error)
        store.close()

        rows = _fts_rows(path)
        obs_texts = sorted(r[3] for r in rows if r[0] == "t.obs" and r[1] == "o1")
        err_texts = sorted(r[3] for r in rows if r[0] == ERROR_KIND.value and r[1] == "err1")
        assert obs_texts == sorted(lexical_content(obs))
        assert err_texts == sorted(lexical_content(error))

    def test_non_searchable_fields_never_indexed(self, tmp_path: Path) -> None:
        path = tmp_path / "fts-nonsearchable.sqlite"
        store = SqliteMemoryStore(path)
        # source/observer must also be non-str here, or this would not
        # actually isolate whether the int `value` is excluded (same
        # pitfall test_store.py's test_observation_int_value_not_searchable
        # documents: source="s" would itself produce an indexed row via
        # lexical_content's source field, unrelated to the int value).
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value=42, at=AT, source=7, context=CTX,
        )
        store.persist(obs)
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=Id(Kind("t.claim"), "a")), Ref(id=Id(Kind("t.claim"), "b"))),
            detected_at=AT, context=CTX,
        )
        store.persist(contradiction)
        resolution_target = Resolution(
            contradiction=Ref(id=Id(Kind("t.contra"), "never-persisted-alone")),
            rationale="secret rationale text", resolved_by=AGENT, at=AT,
        )
        store.close()

        rows = _fts_rows(path)
        contents = [r[3] for r in rows]
        # resolution_target is never persisted at all -- its rationale
        # documents what a Resolution would carry (matrix section FT-02),
        # confirmed absent below even though it was never given the chance.
        assert resolution_target.rationale == "secret rationale text"
        assert "secret rationale text" not in contents
        assert not any(r[0] == "t.obs" and r[1] == "o1" for r in rows)  # int value: not indexed
        assert not any(r[0] == "t.contra" for r in rows)  # Contradiction: not indexed

    def test_archived_item_remains_physically_indexed(self, tmp_path: Path) -> None:
        from memory.retention import ARCHIVED

        path = tmp_path / "fts-archived.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        store.close()

        rows = _fts_rows(path)
        assert any(r[0] == "t.obs" and r[1] == "o1" and r[3] == "findme" for r in rows)

    def test_default_retrieve_still_excludes_archived_despite_fts_row_present(
        self, tmp_path: Path
    ) -> None:
        from memory.retention import ARCHIVED
        from memory.store import RetrievalQuery

        path = tmp_path / "fts-archived-retrieve.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        candidates = store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        assert candidates == ()
        store.close()

    def test_collision_rejection_leaves_fts_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "fts-collision.sqlite"
        store = SqliteMemoryStore(path)
        c1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("original"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(c1)
        rows_before = _fts_rows(path)

        c2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("conflicting"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        with pytest.raises(IdentityCollision):
            store.persist(c2)
        store.close()

        rows_after = _fts_rows(path)
        assert rows_after == rows_before
        assert any(r[3] == "original" for r in rows_after)
        assert not any(r[3] == "conflicting" for r in rows_after)

    def test_failed_transaction_leaves_fts_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "fts-failed-txn.sqlite"
        store = SqliteMemoryStore(path)
        obs1: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="first",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs1)
        rows_before = _fts_rows(path)

        obs2: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="second",
            at=AT, source="s", context=CTX,
        )
        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(obs2)
        store.close()

        rows_after = _fts_rows(path)
        assert rows_after == rows_before

    def test_idempotent_retry_does_not_duplicate_fts_content(self, tmp_path: Path) -> None:
        path = tmp_path / "fts-idempotent.sqlite"
        store = SqliteMemoryStore(path)
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        store.persist(claim)
        store.persist(claim)
        store.close()

        rows = [r for r in _fts_rows(path) if r[3] == "v"]
        assert len(rows) == 1

    # ---- Gaps closed by this task's own audit: the two existing tests that
    # gestured at FT-02/FT-03 (test_non_searchable_fields_never_indexed)
    # never actually persisted a Resolution or a RetentionMark with a
    # rationale through the store -- its own comment said as much ("confirmed
    # absent below even though it was never given the chance"). These
    # actually persist one and check its rationale text is absent from FTS. ----

    def test_ft02_resolution_rationale_never_indexed(self, tmp_path: Path) -> None:
        path = tmp_path / "ft02.sqlite"
        store = SqliteMemoryStore(path)
        c1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v1"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        c2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("v2"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=c1.id), Ref(id=c2.id)), detected_at=AT, context=CTX,
        )
        resolution = Resolution(
            contradiction=Ref(id=contradiction.id),
            rationale="unmistakable resolution rationale text",
            resolved_by=AGENT, at=AT,
        )
        store.persist(c1)
        store.persist(c2)
        store.persist(contradiction)
        store.persist(resolution)  # actually persisted, unlike the earlier weaker test
        store.close()

        contents = [r[3] for r in _fts_rows(path)]
        assert "unmistakable resolution rationale text" not in contents

    def test_ft03_retention_mark_rationale_never_indexed(self, tmp_path: Path) -> None:
        path = tmp_path / "ft03.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.persist(RetentionMark(
            item=Ref(id=obs.id), accessibility=ACTIVE, at=AT,
            rationale="unmistakable retention rationale text",
        ))
        store.close()

        contents = [r[3] for r in _fts_rows(path)]
        assert "unmistakable retention rationale text" not in contents
        assert "findme" in contents  # sanity: the Observation itself WAS indexed

    def test_ft06_nested_mapping_strings_not_recursively_indexed(self, tmp_path: Path) -> None:
        path = tmp_path / "ft06.sqlite"
        store = SqliteMemoryStore(path)
        effect = Effect(
            id=Id(Kind("t.effect"), "f1"), kind=Kind("t.k"), description="the description",
            target="the target", at=AT, context=CTX,
            metadata={"nested": "unmistakable nested metadata string"},
        )
        store.persist(effect)
        store.close()

        contents = [r[3] for r in _fts_rows(path)]
        assert "unmistakable nested metadata string" not in contents
        assert "the description" in contents
        assert "the target" in contents

    def test_ft07_event_bare_string_payload_indexed(self, tmp_path: Path) -> None:
        path = tmp_path / "ft07.sqlite"
        store = SqliteMemoryStore(path)
        event = Event(
            id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT,
            payload="findable event payload",
        )
        store.persist(event)
        store.close()

        rows = _fts_rows(path)
        assert any(
            r[0] == "t.event" and r[1] == "e1" and r[3] == "findable event payload" for r in rows
        )

    def test_ft08_event_bytes_payload_not_indexed(self, tmp_path: Path) -> None:
        path = tmp_path / "ft08.sqlite"
        store = SqliteMemoryStore(path)
        event = Event(
            id=Id(Kind("t.event"), "e1"), kind=Kind("t.event"), at=AT, payload=b"raw bytes payload",
        )
        store.persist(event)
        store.close()

        rows = _fts_rows(path)
        assert not any(r[0] == "t.event" and r[1] == "e1" for r in rows)

    def test_ft10_fts_operator_like_query_text_remains_literal_substring(
        self, tmp_path: Path
    ) -> None:
        from memory.store import RetrievalQuery

        path = tmp_path / "ft10.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT,
            value='has "quotes" and a * star and NEAR() text', at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        literal_hit = store.retrieve(
            RetrievalQuery(context=CTX, text='"quotes" and a * star'), retrieved_at=AT
        )
        assert len(literal_hit) == 1
        no_hit = store.retrieve(
            RetrievalQuery(context=CTX, text="unrelated wildcard*query"), retrieved_at=AT
        )
        assert no_hit == ()
        store.close()


class TestBackendEquivalence:
    """Matrix section Q (BE-01..08): the same operation trace against
    InMemoryStore and SqliteMemoryStore must produce identical observable
    results. Matrix section O (RR-01..12) and N (CL-11) are proven the same
    way -- SqliteMemoryStore.retrieve()/conflicts_for() delegate wholesale
    to the replayed reference (prereg §30-31), so equivalence here IS the
    proof that SQLite reproduces InMemoryStore's already-frozen retrieval/
    retention/conflict policy, not a second independent policy check.
    """

    def _paired_stores(self, tmp_path: Path) -> tuple[InMemoryStore, SqliteMemoryStore]:
        reference = InMemoryStore()
        durable = SqliteMemoryStore(tmp_path / "equivalence.sqlite")
        return reference, durable

    def test_ordinary_records_equivalent(self, tmp_path: Path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        reference.persist(obs)
        durable.persist(obs)
        assert reference.resolve(obs.id) == durable.resolve(obs.id)
        durable.close()

    def test_idempotent_retries_equivalent(self, tmp_path: Path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        for store in (reference, durable):
            store.persist(claim)
            store.persist(claim)
        ref_claims = reference.claims_for(SUBJECT, Kind("t.p"))
        dur_claims = durable.claims_for(SUBJECT, Kind("t.p"))
        assert ref_claims == dur_claims
        durable.close()

    def test_identity_collisions_equivalent(self, tmp_path: Path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        c1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("first"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        c2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"),
            value=Known("second"), context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        for store in (reference, durable):
            store.persist(c1)
            with pytest.raises(IdentityCollision):
                store.persist(c2)
        durable.close()

    def test_inference_with_embedded_claim_equivalent(self, tmp_path: Path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        claim: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("v"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        inference: Inference[object] = Inference(
            id=Id(Kind("t.inf"), "i1"), premises=(), method=Kind("t.m"), conclusion=claim, at=AT,
        )
        for store in (reference, durable):
            store.persist(inference)
        assert reference.resolve(claim.id) == durable.resolve(claim.id)
        assert reference.resolve(inference.id) == durable.resolve(inference.id)
        durable.close()

    def test_error_cause_chain_equivalent(self, tmp_path: Path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        root = Error(id=Id(ERROR_KIND, "root"), kind=ERROR_KIND, message="root", at=AT)
        wrap = Error(id=Id(ERROR_KIND, "wrap"), kind=ERROR_KIND, message="wrap", at=AT, cause=root)
        for store in (reference, durable):
            store.persist(wrap)
        assert reference.resolve(root.id) == durable.resolve(root.id)
        assert reference.resolve(wrap.id) == durable.resolve(wrap.id)
        durable.close()

    def test_contradiction_and_resolutions_equivalent(self, tmp_path: Path) -> None:
        reference, durable = self._paired_stores(tmp_path)
        c1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("a"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        c2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("b"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=c1.id), Ref(id=c2.id)), detected_at=AT, context=CTX,
        )
        r1 = Resolution(
            contradiction=Ref(id=contradiction.id), rationale="first", resolved_by=AGENT, at=AT
        )
        r2 = Resolution(
            contradiction=Ref(id=contradiction.id), rationale="second", resolved_by=AGENT, at=AT
        )
        for store in (reference, durable):
            store.persist(c1)
            store.persist(c2)
            store.persist(contradiction)
            store.persist(r1)
            store.persist(r2)
        ref_conflicts = reference.conflicts_for(SUBJECT, Kind("t.p"))
        dur_conflicts = durable.conflicts_for(SUBJECT, Kind("t.p"))
        assert ref_conflicts == dur_conflicts
        durable.close()

    def test_retention_history_equivalent(self, tmp_path: Path) -> None:
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

    def test_episode_lifecycle_equivalent(self, tmp_path: Path) -> None:
        from memory.episode import Episode

        reference, durable = self._paired_stores(tmp_path)
        episode_id = Id(Kind("t.episode"), "ep1")
        item = Ref(id=Id(Kind("t.item"), "x"))
        for store in (reference, durable):
            store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
            store.append_episode(episode_id, item)
            store.close_episode(episode_id, AT)
        ref_episode = reference.resolve(episode_id)
        dur_episode = durable.resolve(episode_id)
        assert isinstance(ref_episode, Episode)
        assert isinstance(dur_episode, Episode)
        assert ref_episode.items() == dur_episode.items()
        assert ref_episode.closed_at == dur_episode.closed_at
        durable.close()

    def test_archived_deprioritized_retrieval_equivalent(self, tmp_path: Path) -> None:
        from memory.retention import DEPRIORITIZED
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        deprioritized_obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme first",
            at=AT, source="s", context=CTX,
        )
        active_obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="findme second",
            at=AT, source="s", context=CTX,
        )
        for store in (reference, durable):
            store.persist(deprioritized_obs)
            store.persist(active_obs)
            store.persist(RetentionMark(
                item=Ref(id=deprioritized_obs.id), accessibility=DEPRIORITIZED, at=AT
            ))
        query = RetrievalQuery(context=CTX, text="findme")
        ref_candidates = reference.retrieve(query, retrieved_at=AT)
        dur_candidates = durable.retrieve(query, retrieved_at=AT)
        assert [c.item.id for c in ref_candidates] == [c.item.id for c in dur_candidates]
        durable.close()

    def test_combined_identity_and_lexical_retrieval_equivalent(self, tmp_path: Path) -> None:
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findable text",
            at=AT, source="s", context=CTX,
        )
        for store in (reference, durable):
            store.persist(obs)
        ref_candidates = reference.retrieve(
            RetrievalQuery(context=CTX, identity=obs.id, text="findable"), retrieved_at=AT
        )
        dur_candidates = durable.retrieve(
            RetrievalQuery(context=CTX, identity=obs.id, text="findable"), retrieved_at=AT
        )
        assert [c.relevance for c in ref_candidates] == [c.relevance for c in dur_candidates]
        durable.close()

    def test_custom_retention_kind_error_equivalent(self, tmp_path: Path) -> None:
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        for store in (reference, durable):
            store.persist(obs)
            store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=Kind("t.custom"), at=AT))
            with pytest.raises(ValueError):
                store.retrieve(RetrievalQuery(context=CTX, text="findme"), retrieved_at=AT)
        durable.close()

    # ---- Gaps closed by this task's own audit, beyond the brief's template ----

    def test_explicit_archive_inclusive_retrieval_equivalent(self, tmp_path: Path) -> None:
        """RR-03: an explicit archive-inclusive query may retrieve an
        archived item; default queries on both backends still exclude it.
        """
        from memory.retention import ARCHIVED
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        for store in (reference, durable):
            store.persist(obs)
            store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
        default_query = RetrievalQuery(context=CTX, text="findme")
        ref_default = reference.retrieve(default_query, retrieved_at=AT)
        dur_default = durable.retrieve(default_query, retrieved_at=AT)
        assert ref_default == () and dur_default == ()
        inclusive_query = RetrievalQuery(context=CTX, text="findme", include_archived=True)
        ref_inclusive = reference.retrieve(inclusive_query, retrieved_at=AT)
        dur_inclusive = durable.retrieve(inclusive_query, retrieved_at=AT)
        assert [c.item.id for c in ref_inclusive] == [c.item.id for c in dur_inclusive] == [obs.id]
        durable.close()

    def test_archive_then_reactivate_retrieval_equivalent(self, tmp_path: Path) -> None:
        """RR-04: ARCHIVED then ACTIVE makes an item eligible again on both
        backends.
        """
        from memory.retention import ARCHIVED
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        later = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        for store in (reference, durable):
            store.persist(obs)
            store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ARCHIVED, at=AT))
            store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ACTIVE, at=later))
        query = RetrievalQuery(context=CTX, text="findme")
        ref_candidates = reference.retrieve(query, retrieved_at=later)
        dur_candidates = durable.retrieve(query, retrieved_at=later)
        ref_ids = [c.item.id for c in ref_candidates]
        dur_ids = [c.item.id for c in dur_candidates]
        assert ref_ids == dur_ids == [obs.id]
        durable.close()

    def test_retention_append_order_beats_timestamp_order_equivalent(self, tmp_path: Path) -> None:
        """RR-08: RetentionLog.current() is a projection over append order,
        never sorted by RetentionMark.at -- prove this holds identically on
        both backends when the marks' timestamps are reversed relative to
        the order they were persisted in.
        """
        from memory.retention import ARCHIVED

        reference, durable = self._paired_stores(tmp_path)
        target = Id(Kind("t.item"), "x")
        later = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        mark_active_later = RetentionMark(item=Ref(id=target), accessibility=ACTIVE, at=later)
        mark_archived_earlier = RetentionMark(item=Ref(id=target), accessibility=ARCHIVED, at=AT)
        for store in (reference, durable):
            store.persist(mark_active_later)  # appended first, later timestamp
            store.persist(mark_archived_earlier)  # appended second, earlier timestamp
        assert reference.retention_for(target) == durable.retention_for(target)
        # The LAST-APPENDED mark (ARCHIVED, despite its earlier timestamp) wins on both.
        assert reference.retention_for(target)[-1].accessibility == ARCHIVED
        assert durable.retention_for(target)[-1].accessibility == ARCHIVED
        durable.close()

    def test_belief_state_from_either_backends_query_results_is_identical(
        self, tmp_path: Path
    ) -> None:
        """BE-05: belief_state() fed claims_for()/conflicts_for() results
        from either backend produces an identical BeliefProjection.
        """
        from memory.belief import belief_state

        reference, durable = self._paired_stores(tmp_path)
        c1: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c1"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("a"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        c2: Claim[object] = Claim(
            id=Id(Kind("t.claim"), "c2"), subject=SUBJECT, predicate=Kind("t.p"), value=Known("b"),
            context=CTX, asserted_by=AGENT, evidence_refs=(), at=AT,
        )
        contradiction = Contradiction(
            id=Id(Kind("t.contra"), "k1"), subject=SUBJECT,
            statements=(Ref(id=c1.id), Ref(id=c2.id)), detected_at=AT, context=CTX,
        )
        for store in (reference, durable):
            store.persist(c1)
            store.persist(c2)
            store.persist(contradiction)

        ref_projection = belief_state(
            subject=SUBJECT, predicate=Kind("t.p"), query_context=CTX,
            claims=reference.claims_for(SUBJECT, Kind("t.p")),
            conflict_entries=reference.conflicts_for(SUBJECT, Kind("t.p")),
        )
        dur_projection = belief_state(
            subject=SUBJECT, predicate=Kind("t.p"), query_context=CTX,
            claims=durable.claims_for(SUBJECT, Kind("t.p")),
            conflict_entries=durable.conflicts_for(SUBJECT, Kind("t.p")),
        )
        assert ref_projection == dur_projection
        durable.close()

    def test_admit_on_either_backends_retrieve_output_is_identical(self, tmp_path: Path) -> None:
        """BE-06/RR-12: admit() given retrieve() output from either backend
        produces the same WorkingSet/excluded split, and a capacity-excluded
        candidate remains independently resolvable on the durable backend
        (excluded is not the same thing as forgotten/archived).
        """
        from memory.recall import admit
        from memory.store import RetrievalQuery

        reference, durable = self._paired_stores(tmp_path)
        obs1: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme one",
            at=AT, source="s", context=CTX,
        )
        obs2: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o2"), subject=SUBJECT, value="findme two",
            at=AT, source="s", context=CTX,
        )
        for store in (reference, durable):
            store.persist(obs1)
            store.persist(obs2)
        query = RetrievalQuery(context=CTX, text="findme")
        ref_candidates = reference.retrieve(query, retrieved_at=AT)
        dur_candidates = durable.retrieve(query, retrieved_at=AT)

        ref_working, ref_excluded = admit(ref_candidates, 1)
        dur_working, dur_excluded = admit(dur_candidates, 1)
        assert ref_working.admitted == dur_working.admitted
        assert ref_excluded == dur_excluded
        assert len(dur_excluded) == 1
        assert durable.resolve(dur_excluded[0].item.id) is not None  # excluded != forgotten
        durable.close()

    def test_unsupported_value_fails_identically_on_both_backends(self, tmp_path: Path) -> None:
        """BE-08: an unsupported persisted value fails at both the semantic
        boundary (InMemoryStore, via UnsupportedPersistedValue) and the
        SQLite boundary (SqliteMemoryStore, which applies to its internal
        reference FIRST -- so it raises the identical exception, before any
        journal row is written), never silently diverging.
        """
        from memory.codec import UnsupportedPersistedValue

        class NotPersistable:
            pass

        reference, durable = self._paired_stores(tmp_path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value=NotPersistable(),
            at=AT, source="s", context=CTX,
        )
        for store in (reference, durable):
            with pytest.raises(UnsupportedPersistedValue):
                store.persist(obs)
        assert reference.resolve(obs.id) is None
        assert durable.resolve(obs.id) is None
        durable_path = tmp_path / "equivalence.sqlite"
        durable.close()

        conn = sqlite3.connect(str(durable_path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 0  # the rejected persist() never reached the journal


class TestEpisodeSqliteTransitions:
    """Dedicated live-SqliteMemoryStore proofs for Episode matrix cases not
    already exercised by TestJournalReplay (replay-time corruption), the
    lifecycle tests in TestTransactionalPersist, or TestBackendEquivalence's
    test_episode_lifecycle_equivalent (single-item create/append/close).
    """

    def test_es01_create_only_leaves_empty_header_no_items_no_close(self, tmp_path: Path) -> None:
        from memory.episode import Episode

        path = tmp_path / "es01.sqlite"
        store = SqliteMemoryStore(path)
        episode_id = Id(Kind("t.episode"), "ep1")
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        resolved = store.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == ()
        assert resolved.closed_at is None
        store.close()

    def test_es03_duplicate_ref_gets_new_position_and_survives_reopen(self, tmp_path: Path) -> None:
        from memory.episode import Episode

        path = tmp_path / "es03.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        ref_a = Ref(id=Id(Kind("t.item"), "a"))
        ref_b = Ref(id=Id(Kind("t.item"), "b"))
        store = SqliteMemoryStore(path)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.append_episode(episode_id, ref_a)
        store.append_episode(episode_id, ref_b)
        store.append_episode(episode_id, ref_a)  # duplicate Ref -- new position, not deduplicated
        store.close()

        reopened = SqliteMemoryStore(path)
        resolved = reopened.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == (ref_a, ref_b, ref_a)
        reopened.close()

    def test_es04_append_call_order_survives_reversed_member_timestamps(
        self, tmp_path: Path
    ) -> None:
        """ES-04: append_episode() takes no timestamp of its own -- the only
        way "member timestamps" could reorder stored append order is if the
        durable layer keyed off the referenced items' own `at` fields. Prove
        appending a later-timestamped item before an earlier-timestamped one
        preserves pure call order, including after reopen.
        """
        from memory.episode import Episode

        path = tmp_path / "es04.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        newer_item = Ref(id=Id(Kind("t.item"), "newer"))
        older_item = Ref(id=Id(Kind("t.item"), "older"))
        earlier = AT
        later = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        store = SqliteMemoryStore(path)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.persist(Observation(
            id=older_item.id, subject=SUBJECT, value="old", at=earlier, source="s", context=CTX,
        ))
        store.persist(Observation(
            id=newer_item.id, subject=SUBJECT, value="new", at=later, source="s", context=CTX,
        ))
        store.append_episode(episode_id, newer_item)  # appended first (later `at`)
        store.append_episode(episode_id, older_item)  # appended second (earlier `at`)
        store.close()

        reopened = SqliteMemoryStore(path)
        resolved = reopened.resolve(episode_id)
        assert isinstance(resolved, Episode)
        assert resolved.items() == (newer_item, older_item)  # call order, not timestamp order
        reopened.close()

    def test_es06_live_append_after_close_rejected_with_no_journal_row(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "es06.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        store = SqliteMemoryStore(path)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.close_episode(episode_id, AT)
        with pytest.raises(ValueError):
            store.append_episode(episode_id, Ref(id=Id(Kind("t.item"), "x")))
        store.close()

        # rejected append wrote nothing
        assert _journal_op_kinds(path) == ["create_episode", "close_episode"]

    def test_es07_live_double_close_rejected_with_no_journal_row(self, tmp_path: Path) -> None:
        path = tmp_path / "es07.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        store = SqliteMemoryStore(path)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=AT)
        store.close_episode(episode_id, AT)
        with pytest.raises(ValueError):
            store.close_episode(episode_id, AT)
        store.close()

        # second close wrote nothing
        assert _journal_op_kinds(path) == ["create_episode", "close_episode"]

    def test_es08_live_close_before_opened_at_rejected_with_no_journal_row(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "es08.sqlite"
        episode_id = Id(Kind("t.episode"), "ep1")
        opened_at = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        too_early = AT  # 2024-01-01, before opened_at
        store = SqliteMemoryStore(path)
        store.create_episode(id=episode_id, subject=SUBJECT, context=CTX, opened_at=opened_at)
        with pytest.raises(ValueError):
            store.close_episode(episode_id, too_early)
        store.close()

        assert _journal_op_kinds(path) == ["create_episode"]  # rejected close wrote nothing


class TestEpisodeSnapshotIsNotAMutationMechanism:
    def test_persist_does_not_accept_an_episode(self, tmp_path: Path) -> None:
        from memory.episode import Episode
        from memory.store import UnsupportedMemoryRecord

        store = SqliteMemoryStore(tmp_path / "es12.sqlite")
        episode = Episode(
            id=Id(Kind("t.episode"), "ep1"), subject=SUBJECT, context=CTX, opened_at=AT
        )
        with pytest.raises(UnsupportedMemoryRecord):
            store.persist(episode)  # type: ignore[arg-type]
        store.close()


class TestConstructorResourceCleanup:
    def test_replay_failure_closes_connection_before_raising(self, tmp_path: Path) -> None:
        """Every OTHER constructor failure path (_initialize_new_database,
        _validate_existing_schema, the FTS-rebuild-after-replay step, the
        partial-schema branch) explicitly closes self._conn before
        re-raising. _replay_journal()'s own raise sites never close
        anything, so the call site must -- otherwise a corrupted database
        leaves a dangling open connection/file handle behind a constructor
        call that never returned an object the caller could call close()
        on. Confirmed by direct reproduction that this actually holds an OS
        file lock (PermissionError removing the file right after, no gc
        needed) before the fix in this task.
        """
        path = tmp_path / "leak-check.sqlite"
        conn = _fresh_v1_schema(path)
        _insert_op_row(conn, 1, "teleport", b"whatever")  # unknown op kind -> StoreCorruption
        conn.commit()
        conn.close()

        with pytest.raises(StoreCorruption) as excinfo:
            SqliteMemoryStore(path)

        # Dig the partially-constructed `self` out of the traceback -- the
        # constructor never returned an instance we could hold a name to,
        # but the traceback frame for __init__ still has `self` in its
        # locals, which is exactly how this leak is reachable/observable at
        # all outside the process (and exactly why it mattered).
        tb = excinfo.tb
        init_frame = None
        while tb is not None:
            if tb.tb_frame.f_code.co_name == "__init__":
                init_frame = tb.tb_frame
                break
            tb = tb.tb_next
        assert init_frame is not None, "expected the raise to unwind through __init__"
        leaked_self = init_frame.f_locals["self"]
        with pytest.raises(sqlite3.ProgrammingError):
            leaked_self._conn.execute("SELECT 1")  # closed connections refuse further use


class TestDatabaseIntegrity:
    def test_db02_interrupted_episode_append_leaves_no_partial_append(self, tmp_path: Path) -> None:
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

    def test_db04_retention_mark_and_derived_index_atomic(self, tmp_path: Path) -> None:
        path = tmp_path / "db04.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store.persist(obs)

        with _BlockNewOps(store):
            with pytest.raises(sqlite3.IntegrityError):
                store.persist(RetentionMark(item=Ref(id=obs.id), accessibility=ACTIVE, at=AT))
            assert store.retention_for(obs.id) == ()
        store.close()

    def test_db08_two_readers_of_committed_data_agree(self, tmp_path: Path) -> None:
        path = tmp_path / "db08.sqlite"
        store = SqliteMemoryStore(path)
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="x", at=AT, source="s", context=CTX,
        )
        store.persist(obs)
        store.close()

        reader1 = SqliteMemoryStore(path)
        reader2 = SqliteMemoryStore(path)
        assert reader1.resolve(obs.id) == reader2.resolve(obs.id)
        reader1.close()
        reader2.close()

    def test_db09_second_writer_on_locked_database_fails_explicitly(self, tmp_path: Path) -> None:
        # VERIFIED before this plan was written: the failure happens inside
        # the SECOND store's CONSTRUCTOR, not a later persist() call -- the
        # constructor's own FTS-rebuild-after-replay step (Task 6) needs
        # BEGIN IMMEDIATE too, and that's what collides with store1's held
        # lock. store2 never finishes constructing, so there is no store2
        # object afterward -- direct reproduction confirmed this exact
        # sequence, see the "DB-09 implication" note in Task 4. This task's
        # own audit additionally confirmed (see test_db09_failure_is_the_
        # constructors_fts_rebuild_step_not_initialize_new_database below)
        # that this specifically exercises the FTS-rebuild except-branch,
        # not the _initialize_new_database except-branch -- store1 already
        # completed initialization before store2 is ever constructed, so
        # store2 necessarily takes the "existing schema" branch.
        path = tmp_path / "db09.sqlite"
        store1 = SqliteMemoryStore(path)
        store1._conn.execute("BEGIN IMMEDIATE")  # pyright: ignore[reportPrivateUsage]
        store1._conn.execute(  # pyright: ignore[reportPrivateUsage]
            "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (1, 'persist', ?, ?)",
            (b"x", b"y"),
        )

        with pytest.raises(sqlite3.OperationalError):
            SqliteMemoryStore(path)  # lock contention -- explicit failure, no silent retry/wait

        store1._conn.execute("ROLLBACK")  # pyright: ignore[reportPrivateUsage]
        store1.close()

        # After store1 releases the lock, a fresh construction succeeds cleanly.
        store3 = SqliteMemoryStore(path)
        store3.close()

    def test_db09_failure_is_the_constructors_fts_rebuild_step_not_initialize_new_database(
        self, tmp_path: Path
    ) -> None:
        """This task's brief flagged DB-09 as the one test in this suite
        specifically responsible for exercising Task 6's FTS-rebuild-after-
        replay rollback/close/re-raise branch, which had no test covering
        it as of Task 6's own commit. Reasoning alone (store1 already holds
        a committed, schema-complete database before store2 is constructed,
        so store2 must take the "existing schema" branch, never
        "_initialize_new_database") is confirmed here empirically by
        walking the traceback of the raised OperationalError and asserting
        the failing frame is literally inside SqliteMemoryStore.__init__
        while the object's _reference is already a *replayed* InMemoryStore
        (proving journal replay already completed) and NOT inside
        _initialize_new_database (which never sets self._reference from
        _replay_journal -- it sets it directly to a fresh empty InMemoryStore
        with no _known_entity_ids populated from replay).
        """
        path = tmp_path / "db09-branch-check.sqlite"
        store1 = SqliteMemoryStore(path)
        # Give store1 committed durable state so replay on the second store
        # is nontrivial (proves replay ran, not just "0 rows, trivially ok").
        obs: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT, value="findme",
            at=AT, source="s", context=CTX,
        )
        store1.persist(obs)
        store1._conn.execute("BEGIN IMMEDIATE")  # pyright: ignore[reportPrivateUsage]
        store1._conn.execute(  # pyright: ignore[reportPrivateUsage]
            "INSERT INTO memory_ops(seq, op_kind, payload, digest) VALUES (99, 'persist', ?, ?)",
            (b"x", b"y"),
        )

        with pytest.raises(sqlite3.OperationalError) as excinfo:
            SqliteMemoryStore(path)

        tb = excinfo.tb
        init_frame = None
        while tb is not None:
            if tb.tb_frame.f_code.co_name == "__init__":
                init_frame = tb.tb_frame
            tb = tb.tb_next
        assert init_frame is not None, "expected OperationalError to unwind through __init__"
        failed_self = init_frame.f_locals["self"]
        # If this failure had instead come from _initialize_new_database
        # (the OTHER call site that issues BEGIN IMMEDIATE), self._reference
        # would never have been set at all by the time the exception fires,
        # because _initialize_new_database's own BEGIN IMMEDIATE happens
        # BEFORE self._reference is assigned in that branch. Here, self
        # already carries a fully-replayed reference containing store1's
        # committed Observation -- proof this is the FTS-rebuild branch,
        # reached only after a successful _replay_journal().
        assert hasattr(failed_self, "_reference"), (
            "self._reference must already be set -- proves replay completed "
            "and this is the FTS-rebuild branch, not _initialize_new_database"
        )
        assert failed_self._reference.resolve(obs.id) == obs

        store1._conn.execute("ROLLBACK")  # pyright: ignore[reportPrivateUsage]
        store1.close()

    def test_db10_sql_injection_like_text_cannot_mutate_database(self, tmp_path: Path) -> None:
        path = tmp_path / "db10.sqlite"
        store = SqliteMemoryStore(path)
        malicious: Observation[object] = Observation(
            id=Id(Kind("t.obs"), "o1"), subject=SUBJECT,
            value="'; DROP TABLE memory_ops; --", at=AT, source="s", context=CTX,
        )
        store.persist(malicious)
        from memory.store import RetrievalQuery
        candidates = store.retrieve(
            RetrievalQuery(context=CTX, text="'; DROP TABLE memory_ops; --"), retrieved_at=AT
        )
        assert len(candidates) == 1
        store.close()

        conn = sqlite3.connect(str(path))
        count = conn.execute("SELECT COUNT(*) FROM memory_ops").fetchone()[0]
        conn.close()
        assert count == 1  # table intact, row survived


def test_fts5_is_available_in_this_environment() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    finally:
        conn.close()
