"""Relation: a connection between two identified things, of a named kind.

See SPECIFICATION.md #13 and ARCHITECTURE.md (relation.py, tier 4).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from core.context import Context
from core.identity import Id, Ref, identity_of
from core.time import WallInstant
from core.value import Kind


def _validate_and_freeze_metadata(
    metadata: Mapping[str, object] | None,
) -> Mapping[str, object] | None:
    if metadata is None:
        return None
    for key in metadata:
        if not isinstance(key, str) or not key:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("metadata keys must be non-empty strings")
    return MappingProxyType(dict(metadata))


@dataclass(frozen=True, slots=True, kw_only=True)
class Relation:
    """A connection between two identified things, of a named kind.

    Source and target are always identified things, never bare Values.
    Not itself an Entity — nothing in v0 targets a Relation by Ref.
    """

    source: Id | Ref
    kind: Kind
    target: Id | Ref
    at: WallInstant
    context: Context | None = None
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _validate_and_freeze_metadata(self.metadata))


class RelationSet:
    """An in-memory adjacency helper over Relation facts — no graph engine.

    Single-writer, not thread-safe in v0. Duplicates are preserved:
    RelationSet never decides two separately-added facts are redundant.
    """

    def __init__(self) -> None:
        self._relations: list[Relation] = []

    def add(self, relation: Relation) -> Relation:
        self._relations.append(relation)
        return relation

    def relations(self) -> tuple[Relation, ...]:
        return tuple(self._relations)

    def query(
        self,
        *,
        kind: Kind | None = None,
        source: Id | Ref | None = None,
        target: Id | Ref | None = None,
    ) -> tuple[Relation, ...]:
        """Conjunctive filtering. Kind matches exactly; source/target match by
        underlying identity via ``identity_of()``, not raw Id/Ref equality.
        """
        results: list[Relation] = []
        for relation in self._relations:
            if kind is not None and relation.kind != kind:
                continue
            if source is not None and identity_of(relation.source) != identity_of(source):
                continue
            if target is not None and identity_of(relation.target) != identity_of(target):
                continue
            results.append(relation)
        return tuple(results)

    def path_exists(
        self,
        source: Id | Ref,
        target: Id | Ref,
        *,
        kind: Kind | None = None,
    ) -> bool:
        """Directed plain BFS from source to target. Reflexive: the same
        identity is always reachable in zero steps, even with no stored edge.
        """
        source_id = identity_of(source)
        target_id = identity_of(target)
        if source_id == target_id:
            return True

        adjacency: dict[Id, list[Id]] = {}
        for relation in self._relations:
            if kind is not None and relation.kind != kind:
                continue
            adjacency.setdefault(identity_of(relation.source), []).append(
                identity_of(relation.target)
            )

        visited = {source_id}
        queue: deque[Id] = deque([source_id])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency.get(current, []):
                if neighbor == target_id:
                    return True
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        return False
