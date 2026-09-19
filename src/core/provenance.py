"""Provenance: the derivation history of a value.

See SPECIFICATION.md #15 and ARCHITECTURE.md (provenance.py, tier 4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.context import Context
from core.identity import Id, Ref
from core.time import Duration, WallInstant


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Which Transformation, from which inputs, under which conditions, produced
    a value. A lazy DAG, never eagerly flattened; attaching Provenance never
    mutates the value itself.

    ``inputs`` cites identified inputs; ``parents`` cites prior Provenance
    nodes — intentionally different relationships, never conflated.
    """

    id: Id

    transform_id: Id
    transform_name: str
    transform_version: str

    inputs: tuple[Ref, ...]
    parents: tuple[Ref, ...]

    at: WallInstant
    duration: Duration
    context: Context | None = None

    def __post_init__(self) -> None:
        if not self.transform_name:
            raise ValueError("Provenance.transform_name must not be empty")
        if not self.transform_version:
            raise ValueError("Provenance.transform_version must not be empty")
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "parents", tuple(self.parents))

    def ancestors(self, resolve: Callable[[Ref], Provenance | None]) -> AncestorReport:
        """A lazy, deterministic DFS-preorder traversal of ``parents``.

        No registry, no ambient resolver — ``resolve`` is supplied explicitly.
        Never stores flattened ancestry back onto this Provenance; never
        mutates any traversed node.
        """
        found: list[Provenance] = []
        unresolved: list[Ref] = []
        unresolved_seen: set[Ref] = set()
        cycles: list[Ref] = []
        cycles_seen: set[Ref] = set()

        active: set[Id] = {self.id}
        done: set[Id] = set()

        def visit(node: Provenance) -> None:
            active.add(node.id)
            for ref in node.parents:
                if ref.id in active:
                    if ref not in cycles_seen:
                        cycles.append(ref)
                        cycles_seen.add(ref)
                    continue
                if ref.id in done:
                    continue
                resolved = resolve(ref)
                if resolved is None:
                    if ref not in unresolved_seen:
                        unresolved.append(ref)
                        unresolved_seen.add(ref)
                    continue
                if resolved.id != ref.id:
                    raise ValueError(
                        f"resolver returned a Provenance with id {resolved.id!r} "
                        f"for Ref {ref!r} (expected {ref.id!r})"
                    )
                found.append(resolved)
                visit(resolved)
            active.discard(node.id)
            done.add(node.id)

        visit(self)
        return AncestorReport(
            found=tuple(found), unresolved=tuple(unresolved), cycles=tuple(cycles)
        )


@dataclass(frozen=True, slots=True)
class Traced[T]:
    """A minimal carrier: a value plus optional Provenance. Opt-in, not forced.

    Deliberately minimal — does not proxy attributes, inspect ``value``,
    or automatically merge lineage.
    """

    value: T
    provenance: Provenance | None = None


@dataclass(frozen=True, slots=True)
class AncestorReport:
    """The result of a Provenance.ancestors() traversal.

    ``found`` excludes the traversal root itself — reachable parent nodes
    only. ``unresolved``/``cycles`` are deduplicated by exact Ref equality
    (id and namespace) — two differently-namespaced Refs to the same Id
    remain distinct reportable facts.
    """

    found: tuple[Provenance, ...]
    unresolved: tuple[Ref, ...]
    cycles: tuple[Ref, ...]
