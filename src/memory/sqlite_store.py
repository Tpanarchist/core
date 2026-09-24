"""SqliteMemoryStore: the durable, replay-backed MemoryStore implementer.

SQLite's authoritative state is an append-only journal of MemoryStore
operations, never a record-per-table semantic model. Opening a database
replays every journal row, in order, through a fresh InMemoryStore's public
API only. FTS5 is a rebuildable derived index, never retrieval authority.

See MEMORY_ARCHITECTURE.md ("The SQLite backend is not Memory v0") and
docs/memory-passes/03-sqlite-backend.md (sqlite_store.py, tier 2).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import struct
from collections.abc import Callable, Mapping
from typing import cast

from core.context import Context
from core.effect import Effect
from core.epistemic import Claim, Contradiction, Inference, Resolution
from core.error import Error
from core.event import Event
from core.identity import Id, Ref
from core.observation import Observation
from core.provenance import Provenance
from core.time import WallInstant
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
from memory.recall import RecallCandidate
from memory.retention import RetentionMark
from memory.store import (
    EntityMemoryRecord,
    InMemoryStore,
    PersistRecord,
    RetrievalQuery,
    lexical_content,
)

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
        # Safe defaults for the new-database branch below, which needs no
        # further assignment; the existing-database branch replaces both
        # together, atomically, once replay actually succeeds.
        self._known_entity_ids: set[Id] = set()
        self._reference: InMemoryStore = InMemoryStore()

        try:
            present = self._present_required_tables()
            if not present:
                self._initialize_new_database()
            elif present == set(_REQUIRED_TABLES):
                self._validate_existing_schema()
                self._reference, self._known_entity_ids = self._replay_journal()
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._rebuild_fts()
                    self._conn.execute("COMMIT")
                except Exception:
                    # BEGIN IMMEDIATE itself can be the failing statement
                    # (e.g. lock contention from another writer -- this is
                    # exactly Matrix case DB-09, exercised by
                    # test_db09_second_writer_on_locked_database_fails_explicitly
                    # in Task 7). ROLLBACK then has nothing to roll back and
                    # would raise its own error, masking the real one --
                    # same fix as _write_operation and
                    # _initialize_new_database. This failure is operational,
                    # not a corruption finding -- the journal itself already
                    # replayed successfully -- so the original exception
                    # propagates as-is rather than being wrapped in
                    # StoreCorruption.
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise
            else:
                raise StoreCorruption(
                    None,
                    f"partial Memory schema: found {sorted(present)}, "
                    f"expected all of {sorted(_REQUIRED_TABLES)} or none",
                )
        except Exception:
            # Every failure path inside this constructor -- new-schema init,
            # existing-schema validation, journal replay, or the post-replay
            # FTS rebuild -- must leave no dangling open connection behind a
            # constructor call that never returned an object the caller
            # could call close() on. On Windows this actually holds a file
            # lock, confirmed by direct reproduction. One outer handler
            # makes that a structural guarantee instead of a per-branch
            # convention every new failure path has to remember to repeat.
            self._conn.close()
            raise

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
            raise

    def _validate_existing_schema(self) -> None:
        integrity = self._conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise StoreCorruption(None, f"PRAGMA integrity_check failed: {integrity!r}")

        fts_sql_row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'memory_fts'"
        ).fetchone()
        if fts_sql_row is None or "fts5" not in fts_sql_row[0].lower():
            raise StoreCorruption(None, "memory_fts is not a valid FTS5 table")

        self._require_columns("memory_meta", ("key", "value"))
        self._require_columns("memory_ops", ("seq", "op_kind", "payload", "digest"))

        version_row = self._conn.execute(
            "SELECT value FROM memory_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version_row is None:
            raise StoreCorruption(None, "memory_meta missing required 'schema_version' key")
        found = version_row[0]
        if found != _SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(found=found, supported=_SUPPORTED_SCHEMA_VERSION)

    def _require_columns(self, table: str, required: tuple[str, ...]) -> None:
        # table is always one of this module's own hardcoded literals above,
        # never external input — PRAGMA does not accept bound parameters for
        # object names, so this is the standard way to inspect a fixed,
        # known table's shape.
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        found = {row[1] for row in rows}
        missing = [name for name in required if name not in found]
        if missing:
            raise StoreCorruption(
                None, f"{table} is missing required column(s): {missing}"
            )

    def _replay_journal(self) -> tuple[InMemoryStore, set[Id]]:
        """Pure with respect to self: builds a fresh reference projection
        and entity-id set purely from the durable journal and returns both
        together. Callers publish the pair atomically (tuple assignment)
        only once replay fully succeeds — self._reference and
        self._known_entity_ids are never updated separately, so a second
        failure during recovery (e.g. inside _write_operation's except
        block) leaves both attributes at their last-known-good values
        instead of one reset and the other stale.
        """
        reference = InMemoryStore()
        entity_ids: set[Id] = set()
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
                self._apply_decoded_operation(reference, entity_ids, seq, op_kind, payload)
            except StoreCorruption:
                raise
            except Exception as exc:
                raise StoreCorruption(
                    seq, f"replay rejected by reference semantics: {exc}"
                ) from exc

            expected_seq += 1

        return reference, entity_ids

    def _apply_decoded_operation(
        self,
        reference: InMemoryStore,
        entity_ids: set[Id],
        seq: int,
        op_kind: str,
        payload: bytes,
    ) -> None:
        if op_kind == "persist":
            record = _decode_persist_op(payload, seq=seq)
            reference.persist(record)
            _track_entity_ids(entity_ids, record)
        elif op_kind == "create_episode":
            episode_id, subject, context, opened_at = _decode_create_episode_op(payload, seq=seq)
            reference.create_episode(
                id=episode_id, subject=subject, context=context, opened_at=opened_at
            )
            entity_ids.add(episode_id)
        elif op_kind == "append_episode":
            episode, item = _decode_append_episode_op(payload, seq=seq)
            reference.append_episode(episode, item)
        elif op_kind == "close_episode":
            episode, at = _decode_close_episode_op(payload, seq=seq)
            reference.close_episode(episode, at)
        else:
            raise StoreCorruption(seq, f"unknown operation kind: {op_kind!r}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._conn.close()

    def _require_open(self) -> None:
        if self._closed:
            raise SqliteStoreClosed("this SqliteMemoryStore has been closed")

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

    def _write_operation(self, op_kind: str, encode: Callable[[], bytes]) -> None:
        """Shared transaction wrapper for all four mutating methods. The
        caller has ALREADY applied the operation to self._reference before
        calling this — that proved semantic legality. ``encode`` is a
        callable, not an eager payload: encoding happens INSIDE this
        method's try block so that an encoding failure (not just a SQLite
        failure) also triggers the same recovery reload below, closing the
        window where self._reference had already diverged from the durable
        journal but nothing reset it. On any failure this rolls back and
        reloads self._reference/self._known_entity_ids from the last-
        committed journal via the same replay path used at open — as one
        atomic pair (see _replay_journal) — then re-raises.

        If that recovery replay ITSELF fails, this closes the store rather
        than leaving it open: self._reference and self._known_entity_ids
        were already mutated in place by the caller (persist()/
        create_episode() etc. apply the operation before calling this, to
        prove semantic legality first) before this method ever ran, so on
        a double failure there is no from-scratch replacement to discard
        that mutation with, and no later write can self-heal a store whose
        in-memory state may already diverge from its durable journal.
        """
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            seq = self._next_seq()
            payload = encode()
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
            try:
                self._reference, self._known_entity_ids = self._replay_journal()
            except Exception:
                self.close()
                raise
            raise

    def persist(self, record: PersistRecord) -> None:
        self._require_open()
        self._reference.persist(record)
        _track_entity_ids(self._known_entity_ids, record)
        self._write_operation("persist", lambda: _encode_persist_op(record))

    def create_episode(
        self, *, id: Id, subject: Id | Ref, context: Context, opened_at: WallInstant
    ) -> None:
        self._require_open()
        self._reference.create_episode(id=id, subject=subject, context=context, opened_at=opened_at)
        self._known_entity_ids.add(id)
        self._write_operation(
            "create_episode",
            lambda: _encode_create_episode_op(
                id=id, subject=subject, context=context, opened_at=opened_at
            ),
        )

    def append_episode(self, episode: Id | Ref, item: Ref) -> None:
        self._require_open()
        self._reference.append_episode(episode, item)
        self._write_operation("append_episode", lambda: _encode_append_episode_op(episode, item))

    def close_episode(self, episode: Id | Ref, at: WallInstant) -> None:
        self._require_open()
        self._reference.close_episode(episode, at)
        self._write_operation("close_episode", lambda: _encode_close_episode_op(episode, at))

    def resolve(self, item: Id | Ref) -> EntityMemoryRecord | None:
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


_JOURNAL_FORMAT_VERSION = b"memory.sqlite.operation.v1"


# NOTE: Task 2 produced this codec with no caller yet within this module --
# Task 3 (_replay_journal/_apply_decoded_operation below) now calls the
# digest and decode-side functions, so those carry no suppression anymore.
# The encode-side functions below (_encode_persist_op and friends) still have
# no in-module caller until Task 4 wires in the live write path, so those
# still carry an explicit, deliberate reportUnusedFunction suppression rather
# than a false signal of dead code. (Pyright strict's reportUnusedFunction
# only counts in-module references -- a foreign module importing a
# leading-underscore name, as the tests here do, does not count.)
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


def _encode_persist_op(  # pyright: ignore[reportUnusedFunction]
    record: PersistRecord,
) -> bytes:
    return _encode_record(record)


def _decode_persist_op(data: bytes, *, seq: int | None = None) -> PersistRecord:
    return _decode_record(data, seq=seq)


def _encode_create_episode_op(  # pyright: ignore[reportUnusedFunction]
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


def _encode_append_episode_op(  # pyright: ignore[reportUnusedFunction]
    episode: Id | Ref, item: Ref
) -> bytes:
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


def _encode_close_episode_op(  # pyright: ignore[reportUnusedFunction]
    episode: Id | Ref, at: WallInstant
) -> bytes:
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
