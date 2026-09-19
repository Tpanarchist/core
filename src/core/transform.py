"""Transform: a named, describable operation that turns one thing into another.
Pipeline: an ordered composition of Transforms — a derived construction,
not itself a Transformation.

See SPECIFICATION.md #10 and ARCHITECTURE.md (transform.py, tier 6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.context import Context
from core.effect import EffectSpec
from core.error import Error
from core.identity import Id, IdSource, Ref
from core.provenance import Provenance, Traced
from core.result import Err, Ok, Result
from core.time import Clock, MonotonicClock
from core.value import Kind

_ERROR_ID_KIND = Kind("core.error")
_PROVENANCE_ID_KIND = Kind("core.provenance")

_REQUIREMENT_REJECTED_KIND = Kind("core.transform.requirement_rejected")
_REQUIREMENT_EXCEPTION_KIND = Kind("core.transform.requirement_exception")
_EXECUTION_EXCEPTION_KIND = Kind("core.transform.execution_exception")


@dataclass(frozen=True, slots=True, kw_only=True, eq=False)
class Transform[A, B]:
    """A single, named, describable hop.

    Semantic identity is ``.id`` — Transform does not define structural
    equality over its callable fields (matching Event's own by-Id pattern).
    No constructor invokes ``fn``, requirements, clocks, Id sources, or
    effects; ``effect_specs`` are declarations only.
    """

    id: Id
    name: str
    version: str
    fn: Callable[[A], B]
    requires: tuple[Callable[[A], bool], ...] = ()
    effect_specs: tuple[EffectSpec, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Transform.name must not be empty")
        if not self.version:
            raise ValueError("Transform.version must not be empty")
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "effect_specs", tuple(self.effect_specs))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Transform):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def apply(
        self,
        input: A,
        *,
        context: Context | None = None,
        clock: Clock,
        monotonic_clock: MonotonicClock,
        ids: IdSource,
        input_refs: tuple[Ref, ...] = (),
        parents: tuple[Provenance, ...] = (),
    ) -> Result[Traced[B], Error]:
        """Requirements, then ``fn``, then (on success) exactly one Provenance.

        Never inspects ``input``'s structure — ``input_refs``/``parents``
        are the only sources of lineage. Capability failures (a broken
        ``ids``/``clock``/``monotonic_clock``) propagate as ordinary
        exceptions rather than becoming a Result.Err.
        """
        started = monotonic_clock.now()

        for index, predicate in enumerate(self.requires):
            try:
                satisfied = predicate(input)
            except Exception as exc:
                return Err(
                    Error(
                        id=ids.new(_ERROR_ID_KIND),
                        kind=_REQUIREMENT_EXCEPTION_KIND,
                        message=f"requirement[{index}] raised",
                        at=clock.now(),
                        exception=exc,
                        context=context,
                        operation=self.name,
                    )
                )
            if not satisfied:
                return Err(
                    Error(
                        id=ids.new(_ERROR_ID_KIND),
                        kind=_REQUIREMENT_REJECTED_KIND,
                        message=f"requirement[{index}] rejected input",
                        at=clock.now(),
                        context=context,
                        operation=self.name,
                    )
                )

        try:
            output = self.fn(input)
        except Exception as exc:
            return Err(
                Error(
                    id=ids.new(_ERROR_ID_KIND),
                    kind=_EXECUTION_EXCEPTION_KIND,
                    message="transformation function raised",
                    at=clock.now(),
                    exception=exc,
                    context=context,
                    operation=self.name,
                )
            )

        finished = monotonic_clock.now()
        duration = finished - started

        provenance = Provenance(
            id=ids.new(_PROVENANCE_ID_KIND),
            transform_id=self.id,
            transform_name=self.name,
            transform_version=self.version,
            inputs=tuple(input_refs),
            parents=tuple(Ref(parent.id) for parent in parents),
            at=clock.now(),
            duration=duration,
            context=context,
        )
        return Ok(Traced(output, provenance))

    def then[C](self, other: Transform[B, C]) -> Pipeline[A, C]:
        """Composition, not another Transform — creates no Provenance, executes nothing."""
        return Pipeline((self, other))


class Pipeline[A, B]:
    """An ordered composition of Transforms.

    A derived construction, not a Transformation: no Id, no Provenance of
    its own, no name/version, no hidden clock/Id source. ``Transform.then()``
    is the canonical creation path. The internal stage sequence is
    heterogeneous, so it's stored behind a narrowly-contained internal
    ``Any`` — this never leaks into the public, typed ``apply()`` result.
    """

    def __init__(self, stages: tuple[Transform[Any, Any], ...]) -> None:
        self._stages = stages

    def then[C](self, other: Transform[B, C]) -> Pipeline[A, C]:
        """Returns a new Pipeline with ``other`` appended; the original is unchanged."""
        return Pipeline((*self._stages, other))

    def apply(
        self,
        input: A,
        *,
        context: Context | None = None,
        clock: Clock,
        monotonic_clock: MonotonicClock,
        ids: IdSource,
        input_refs: tuple[Ref, ...] = (),
        parents: tuple[Provenance, ...] = (),
    ) -> Result[Traced[B], Error]:
        """Stage 0 gets the caller's input/input_refs/parents; each later stage
        gets the previous stage's plain value and exactly its Provenance as
        its sole parent. Short-circuits on the first Err, unchanged. N
        successful stages produce exactly N Provenance nodes, never N+1 —
        Pipeline itself allocates nothing.
        """
        if not self._stages:
            raise RuntimeError("Pipeline has no stages")

        current_input: Any = input
        current_input_refs = input_refs
        current_parents = parents
        last_index = len(self._stages) - 1

        for index, stage in enumerate(self._stages):
            result = stage.apply(
                current_input,
                context=context,
                clock=clock,
                monotonic_clock=monotonic_clock,
                ids=ids,
                input_refs=current_input_refs,
                parents=current_parents,
            )
            if isinstance(result, Err):
                return result
            if index == last_index:
                return result

            traced = result.value
            if traced.provenance is None:
                raise RuntimeError(
                    "Pipeline stage succeeded without producing Provenance, "
                    "violating Transform.apply()'s own guarantee"
                )
            current_input = traced.value
            current_input_refs = ()
            current_parents = (traced.provenance,)

        raise RuntimeError("unreachable — Pipeline.stages is non-empty by construction")
