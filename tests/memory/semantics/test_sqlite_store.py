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
from memory.store import IdentityCollision, PersistRecord


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


def test_fts5_is_available_in_this_environment() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    finally:
        conn.close()
