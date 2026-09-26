"""Optional, idempotent recall mirror of finance's durable effect evidence.

Finance remains authoritative. Each call opens its own Memory connection; no
Memory transaction is claimed to participate in the finance transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.context import Context
from core.effect import Effect
from core.identity import Id, Namespace, Ref
from core.observation import Observation
from core.time import WallInstant
from core.value import Kind
from memory.recall import admit
from memory.sqlite_store import SqliteMemoryStore
from memory.store import RetrievalQuery
from personal_finance.adapters.sqlite import FinanceStore
from personal_finance.application.evidence_codec import decode_effect
from personal_finance.domain.codec import decode_id

MEMORY_EFFECT = Kind("finance.memory_effect")


@dataclass(frozen=True, slots=True)
class RecalledEvidence:
    id: Id
    subject: Ref
    description: str
    source: str
    at: WallInstant
    relevance: tuple[Kind, ...]


@dataclass(frozen=True, slots=True)
class RecallResult:
    items: tuple[RecalledEvidence, ...]
    excluded: int
    pending: int
    delivered: int
    failed: int
    unavailable: str | None = None


def _observation(effect: Effect) -> Observation[object]:
    if not isinstance(effect.target, Ref):
        raise ValueError("Finance effect has no identified subject")
    if effect.context is None:
        raise ValueError("Finance effect has no context")
    return Observation(
        id=Id(MEMORY_EFFECT, effect.id.value),
        subject=effect.target,
        value=effect.description,
        at=effect.at,
        source=effect.kind.value,
        context=effect.context,
        observer="finance_effect_outbox_v1",
    )


class FinanceMemory:
    """Explicit local dispatcher and bounded evidence recall capability."""

    def __init__(self, finance: FinanceStore, path: Path, profile: str) -> None:
        self.finance = finance
        self.path = path
        self.profile = profile

    def deliver(self, limit: int = 100) -> tuple[int, int, int]:
        pending = self.finance.pending_memory_effects(limit)
        if not pending:
            return self.finance.memory_delivery_status()
        try:
            memory = SqliteMemoryStore(self.path)
        except Exception as exc:
            for raw_id, _ in pending:
                self.finance.mark_memory_delivery(raw_id, str(exc))
            return self.finance.memory_delivery_status()
        try:
            for raw_id, payload in pending:
                try:
                    effect = decode_effect(payload)
                    if decode_id(raw_id) != effect.id:
                        raise ValueError("Outbox effect identity does not match its payload")
                    memory.persist(_observation(effect))
                except Exception as exc:
                    self.finance.mark_memory_delivery(raw_id, str(exc))
                else:
                    self.finance.mark_memory_delivery(raw_id, None)
        finally:
            memory.close()
        return self.finance.memory_delivery_status()

    def recall(self, text: str, at: WallInstant, limit: int = 20) -> RecallResult:
        if not text.strip() or not 1 <= limit <= 100:
            raise ValueError("Recall needs nonempty text and a 1–100 item limit")
        context = Context(
            as_of=at,
            namespace=Namespace(("personal_finance", self.profile)),
            source="finance_memory_recall",
            scope="accepted_finance_changes",
        )
        try:
            memory = SqliteMemoryStore(self.path)
        except Exception as exc:
            pending, delivered, failed = self.finance.memory_delivery_status()
            return RecallResult((), 0, pending, delivered, failed, str(exc))
        try:
            candidates = memory.retrieve(
                RetrievalQuery(context=context, text=text.strip()), retrieved_at=at
            )
            working, excluded = admit(candidates, limit)
            items: list[RecalledEvidence] = []
            for candidate in working.admitted:
                record = memory.resolve(candidate.item)
                if not isinstance(record, Observation) or record.id.kind != MEMORY_EFFECT:
                    continue
                if not isinstance(record.subject, Ref):
                    continue
                if not isinstance(record.value, str) or not isinstance(record.source, str):
                    continue
                items.append(
                    RecalledEvidence(
                        record.id,
                        record.subject,
                        record.value,
                        record.source,
                        record.at,
                        candidate.relevance,
                    )
                )
        finally:
            memory.close()
        pending, delivered, failed = self.finance.memory_delivery_status()
        return RecallResult(tuple(items), len(excluded), pending, delivered, failed)
