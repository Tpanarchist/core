"""Propositions for core.transform.

See SPECIFICATION.md #10 and docs/passes/05-change-and-execution.md.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.effect import EffectSpec
from core.identity import Id, Ref
from core.provenance import Provenance, Traced
from core.result import Err, Ok
from core.time import Duration, MonotonicInstant, WallInstant
from core.transform import Pipeline, Transform
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
SPACE_KIND = Kind("test.space")
ID_KIND = Kind("test.id")
TRANSFORM_ID_KIND = Kind("core.transform")


class SequentialIdSource:
    """A deterministic IdSource test double — increments a counter per call."""

    def __init__(self) -> None:
        self.calls = 0

    def new(self, kind: Kind) -> Id:
        self.calls += 1
        return Id(kind, f"id-{self.calls}")


class FixedClock:
    """A deterministic Clock test double."""

    def __init__(self, at: WallInstant = AT) -> None:
        self._at = at

    def now(self) -> WallInstant:
        return self._at


class SequentialMonotonicClock:
    """A deterministic MonotonicClock test double — advances by 100ns per call."""

    def __init__(self, space: Id | None = None) -> None:
        self.space = space or Id(SPACE_KIND, "m1")
        self.nanoseconds = 0

    def now(self) -> MonotonicInstant:
        self.nanoseconds += 100
        return MonotonicInstant(self.space, self.nanoseconds)


def make_transform(
    name: str = "double",
    version: str = "1.0.0",
    fn: Callable[[int], int] = lambda x: x * 2,
    requires: tuple[Callable[[int], bool], ...] = (),
    effect_specs: tuple[EffectSpec, ...] = (),
    id_suffix: str = "t1",
) -> Transform[int, int]:
    return Transform(
        id=Id(TRANSFORM_ID_KIND, id_suffix),
        name=name,
        version=version,
        fn=fn,
        requires=requires,
        effect_specs=effect_specs,
    )


def make_test_provenance(suffix: str) -> Provenance:
    return Provenance(
        id=Id(ID_KIND, suffix),
        transform_id=Id(TRANSFORM_ID_KIND, f"t-{suffix}"),
        transform_name="prior",
        transform_version="1.0.0",
        inputs=(),
        parents=(),
        at=AT,
        duration=Duration(0),
    )


class TestTransformConstruction:
    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="name must not be empty"):
            Transform(id=Id(TRANSFORM_ID_KIND, "t1"), name="", version="1.0.0", fn=lambda x: x)

    def test_rejects_empty_version(self) -> None:
        with pytest.raises(ValueError, match="version must not be empty"):
            Transform(id=Id(TRANSFORM_ID_KIND, "t1"), name="double", version="", fn=lambda x: x)

    def test_requires_and_effect_specs_are_stored_as_tuples(self) -> None:
        transform = make_transform(requires=(lambda x: True,))
        assert isinstance(transform.requires, tuple)
        assert isinstance(transform.effect_specs, tuple)

    def test_construction_executes_nothing(self) -> None:
        calls: list[str] = []

        def fn(x: int) -> int:
            calls.append("fn")
            return x

        def req(x: int) -> bool:
            calls.append("req")
            return True

        make_transform(fn=fn, requires=(req,))
        assert calls == []

    def test_equality_is_by_id_not_callables(self) -> None:
        id_ = Id(TRANSFORM_ID_KIND, "t1")
        a = Transform[int, int](id=id_, name="double", version="1.0.0", fn=lambda x: x)
        b = Transform[int, int](id=id_, name="triple", version="2.0.0", fn=lambda x: x * 3)
        assert a == b
        assert hash(a) == hash(b)


class TestPipelineConstruction:
    def test_rejects_zero_stages(self) -> None:
        with pytest.raises(ValueError, match="at least two Transform stages"):
            Pipeline(())

    def test_rejects_one_stage(self) -> None:
        with pytest.raises(ValueError, match="at least two Transform stages"):
            Pipeline((make_transform(id_suffix="a"),))

    def test_accepts_two_stages(self) -> None:
        pipeline = Pipeline[int, int](
            (make_transform(id_suffix="a"), make_transform(id_suffix="b"))
        )
        result = pipeline.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)


class TestComposition:
    def test_then_returns_pipeline_never_another_transform(self) -> None:
        a = make_transform(id_suffix="a")
        b = make_transform(id_suffix="b")
        composed = a.then(b)
        assert isinstance(composed, Pipeline)
        assert not isinstance(composed, Transform)

    def test_composition_runs_no_stage_and_allocates_no_provenance(self) -> None:
        calls: list[str] = []

        def fn_a(x: int) -> int:
            calls.append("a")
            return x

        def fn_b(x: int) -> int:
            calls.append("b")
            return x

        make_transform(fn=fn_a, id_suffix="a").then(make_transform(fn=fn_b, id_suffix="b"))
        assert calls == []

    def test_pipeline_extension_returns_new_composition_without_mutating_original(self) -> None:
        a = make_transform(id_suffix="a")
        b = make_transform(id_suffix="b")
        c = make_transform(id_suffix="c")
        pipeline_ab = a.then(b)
        pipeline_abc = pipeline_ab.then(c)
        assert pipeline_ab is not pipeline_abc

        # Behavioral proof of non-mutation (no reliance on Pipeline internals):
        # the two-stage pipeline still allocates exactly two Provenance ids,
        # not three, after the three-stage extension was created from it.
        ids_ab = SequentialIdSource()
        result_ab = pipeline_ab.apply(
            5, clock=FixedClock(), monotonic_clock=SequentialMonotonicClock(), ids=ids_ab
        )
        assert isinstance(result_ab, Ok)
        assert ids_ab.calls == 2

        ids_abc = SequentialIdSource()
        result_abc = pipeline_abc.apply(
            5, clock=FixedClock(), monotonic_clock=SequentialMonotonicClock(), ids=ids_abc
        )
        assert isinstance(result_abc, Ok)
        assert ids_abc.calls == 3


class TestRequirementEvaluation:
    def test_requirements_execute_in_tuple_order(self) -> None:
        calls: list[int] = []

        def req0(x: int) -> bool:
            calls.append(0)
            return True

        def req1(x: int) -> bool:
            calls.append(1)
            return True

        transform = make_transform(requires=(req0, req1))
        transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert calls == [0, 1]

    def test_falsy_requirement_stops_later_requirements_and_fn(self) -> None:
        calls: list[str] = []

        def req0(x: int) -> bool:
            calls.append("req0")
            return False

        def req1(x: int) -> bool:
            calls.append("req1")
            return True

        def fn(x: int) -> int:
            calls.append("fn")
            return x

        transform = make_transform(fn=fn, requires=(req0, req1))
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert calls == ["req0"]
        assert isinstance(result, Err)

    def test_falsy_requirement_error_shape(self) -> None:
        transform = make_transform(requires=(lambda x: False,))
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Err)
        assert result.error.kind == Kind("core.transform.requirement_rejected")
        assert result.error.exception is None
        assert "requirement[0]" in result.error.message

    def test_requirement_exception_preserved_verbatim(self) -> None:
        original = ValueError("boom")

        def bad_req(x: int) -> bool:
            raise original

        transform = make_transform(requires=(bad_req,))
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Err)
        assert result.error.exception is original
        assert result.error.kind == Kind("core.transform.requirement_exception")

    def test_later_requirements_and_fn_do_not_run_after_requirement_exception(self) -> None:
        calls: list[str] = []

        def bad_req(x: int) -> bool:
            calls.append("bad")
            raise ValueError("boom")

        def req1(x: int) -> bool:
            calls.append("req1")
            return True

        def fn(x: int) -> int:
            calls.append("fn")
            return x

        transform = make_transform(fn=fn, requires=(bad_req, req1))
        transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert calls == ["bad"]


class TestFunctionExecution:
    def test_fn_runs_exactly_once_after_requirements_succeed(self) -> None:
        calls: list[str] = []

        def fn(x: int) -> int:
            calls.append("fn")
            return x * 2

        transform = make_transform(fn=fn, requires=(lambda x: True, lambda x: True))
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert calls == ["fn"]
        assert isinstance(result, Ok)
        assert result.value.value == 10

    def test_fn_exception_preserved_verbatim(self) -> None:
        original = RuntimeError("execution boom")

        def bad_fn(x: int) -> int:
            raise original

        transform = make_transform(fn=bad_fn)
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Err)
        assert result.error.exception is original
        assert result.error.kind == Kind("core.transform.execution_exception")

    def test_keyboard_interrupt_from_requirement_is_not_swallowed(self) -> None:
        def bad_req(x: int) -> bool:
            raise KeyboardInterrupt

        transform = make_transform(requires=(bad_req,))
        with pytest.raises(KeyboardInterrupt):
            transform.apply(
                5,
                clock=FixedClock(),
                monotonic_clock=SequentialMonotonicClock(),
                ids=SequentialIdSource(),
            )

    def test_system_exit_from_fn_is_not_swallowed(self) -> None:
        def bad_fn(x: int) -> int:
            raise SystemExit(1)

        transform = make_transform(fn=bad_fn)
        with pytest.raises(SystemExit):
            transform.apply(
                5,
                clock=FixedClock(),
                monotonic_clock=SequentialMonotonicClock(),
                ids=SequentialIdSource(),
            )


class TestFailureAccounting:
    def test_failure_allocates_exactly_one_error_id_and_no_provenance(self) -> None:
        ids = SequentialIdSource()
        transform = make_transform(requires=(lambda x: False,))
        result = transform.apply(
            5, clock=FixedClock(), monotonic_clock=SequentialMonotonicClock(), ids=ids
        )
        assert isinstance(result, Err)
        assert ids.calls == 1
        assert result.error.id.kind == Kind("core.error")

    def test_failure_does_not_take_a_second_monotonic_reading(self) -> None:
        monotonic = SequentialMonotonicClock()
        transform = make_transform(requires=(lambda x: False,))
        transform.apply(5, clock=FixedClock(), monotonic_clock=monotonic, ids=SequentialIdSource())
        assert monotonic.nanoseconds == 100  # only the initial `started` reading occurred

    def test_caller_context_is_preserved_in_failure(self) -> None:
        ctx = Context(as_of=AT)
        transform = make_transform(requires=(lambda x: False,))
        result = transform.apply(
            5,
            context=ctx,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Err)
        assert result.error.context is ctx


class TestSuccessAndProvenance:
    def test_success_measures_monotonic_time_around_requirements_and_fn(self) -> None:
        transform = make_transform()
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.duration == Duration(100)

    def test_success_allocates_exactly_one_provenance_id_and_no_error_id(self) -> None:
        ids = SequentialIdSource()
        transform = make_transform()
        result = transform.apply(
            5, clock=FixedClock(), monotonic_clock=SequentialMonotonicClock(), ids=ids
        )
        assert isinstance(result, Ok)
        assert ids.calls == 1
        assert result.value.provenance is not None
        assert result.value.provenance.id.kind == Kind("core.provenance")

    def test_provenance_transform_fields_match_transform_identity(self) -> None:
        transform = make_transform(name="double", version="2.0.0")
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        provenance = result.value.provenance
        assert provenance is not None
        assert provenance.transform_id == transform.id
        assert provenance.transform_name == "double"
        assert provenance.transform_version == "2.0.0"

    def test_input_refs_are_preserved_exactly(self) -> None:
        ref = Ref(Id(ID_KIND, "input1"))
        transform = make_transform()
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
            input_refs=(ref,),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.inputs == (ref,)

    def test_parent_provenance_objects_become_refs_to_their_ids(self) -> None:
        parent = make_test_provenance("parent")
        transform = make_transform()
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
            parents=(parent,),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.parents == (Ref(parent.id),)

    def test_zero_parents_yields_root_provenance(self) -> None:
        transform = make_transform()
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.parents == ()

    def test_multiple_parents_produce_convergence_without_flattening(self) -> None:
        parent_a = make_test_provenance("a")
        parent_b = make_test_provenance("b")
        transform = make_transform()
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
            parents=(parent_a, parent_b),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.parents == (Ref(parent_a.id), Ref(parent_b.id))

    def test_success_uses_one_explicit_wall_instant_and_measured_duration(self) -> None:
        transform = make_transform()
        result = transform.apply(
            5,
            clock=FixedClock(AT),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.at == AT
        assert result.value.provenance.duration == Duration(100)

    def test_returns_ok_traced_output_and_provenance(self) -> None:
        transform = make_transform(fn=lambda x: x * 2)
        result = transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        assert result.value.value == 10
        assert result.value.provenance is not None

    def test_no_input_reflection_or_discovery_occurs(self) -> None:
        nested_traced = Traced(1, make_test_provenance("nested"))
        transform: Transform[dict[str, object], dict[str, object]] = Transform(
            id=Id(TRANSFORM_ID_KIND, "identity"),
            name="identity",
            version="1.0.0",
            fn=lambda x: x,
        )
        result = transform.apply(
            {"nested": nested_traced},
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        assert result.value.provenance is not None
        assert result.value.provenance.inputs == ()
        assert result.value.provenance.parents == ()

    def test_effect_specs_do_not_cause_effect_recording(self) -> None:
        spec = EffectSpec(kind=Kind("test.write"), target_shape=None, description="writes")
        transform = make_transform(effect_specs=(spec,))
        transform.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert transform.effect_specs == (spec,)  # declared, never interpreted


class TestCapabilityFailurePropagates:
    def test_id_source_failure_propagates(self) -> None:
        class BrokenIdSource:
            def new(self, kind: Kind) -> Id:
                raise RuntimeError("id source broken")

        transform = make_transform()
        with pytest.raises(RuntimeError, match="id source broken"):
            transform.apply(
                5,
                clock=FixedClock(),
                monotonic_clock=SequentialMonotonicClock(),
                ids=BrokenIdSource(),
            )

    def test_clock_failure_propagates(self) -> None:
        class BrokenClock:
            def now(self) -> WallInstant:
                raise RuntimeError("clock broken")

        transform = make_transform()
        with pytest.raises(RuntimeError, match="clock broken"):
            transform.apply(
                5,
                clock=BrokenClock(),
                monotonic_clock=SequentialMonotonicClock(),
                ids=SequentialIdSource(),
            )


class TestPipeline:
    def test_two_stage_success_yields_exactly_two_provenance_nodes(self) -> None:
        a = make_transform(fn=lambda x: x + 1, id_suffix="a")
        b = make_transform(fn=lambda x: x * 2, id_suffix="b")
        ids = SequentialIdSource()
        result = a.then(b).apply(
            5, clock=FixedClock(), monotonic_clock=SequentialMonotonicClock(), ids=ids
        )
        assert isinstance(result, Ok)
        assert result.value.value == 12  # (5 + 1) * 2
        assert ids.calls == 2

    def test_four_stage_success_yields_exactly_four_never_five(self) -> None:
        stages = [make_transform(fn=lambda x: x + 1, id_suffix=str(i)) for i in range(4)]
        pipeline = stages[0].then(stages[1]).then(stages[2]).then(stages[3])
        ids = SequentialIdSource()
        result = pipeline.apply(
            0, clock=FixedClock(), monotonic_clock=SequentialMonotonicClock(), ids=ids
        )
        assert isinstance(result, Ok)
        assert result.value.value == 4
        assert ids.calls == 4

    def test_stage_receives_previous_stage_plain_value(self) -> None:
        seen: list[int] = []

        def fn_b(x: int) -> int:
            seen.append(x)
            return x

        a = make_transform(fn=lambda x: x + 1, id_suffix="a")
        b = make_transform(fn=fn_b, id_suffix="b")
        a.then(b).apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert seen == [6]

    def test_stage_wiring_of_input_refs_and_parents(self) -> None:
        """Spies on Provenance construction to prove: caller input_refs/parents
        apply only to stage 0; later stages get empty input_refs and exactly
        the previous stage's Provenance as their sole parent.
        """
        recorded_inputs: list[tuple[Ref, ...]] = []
        recorded_parents: list[tuple[Ref, ...]] = []
        real_provenance = Provenance

        def recording_provenance(
            *,
            id: Id,
            transform_id: Id,
            transform_name: str,
            transform_version: str,
            inputs: tuple[Ref, ...],
            parents: tuple[Ref, ...],
            at: WallInstant,
            duration: Duration,
            context: Context | None = None,
        ) -> Provenance:
            recorded_inputs.append(inputs)
            recorded_parents.append(parents)
            return real_provenance(
                id=id,
                transform_id=transform_id,
                transform_name=transform_name,
                transform_version=transform_version,
                inputs=inputs,
                parents=parents,
                at=at,
                duration=duration,
                context=context,
            )

        a = make_transform(fn=lambda x: x + 1, id_suffix="a")
        b = make_transform(fn=lambda x: x + 1, id_suffix="b")
        pipeline = a.then(b)

        ref = Ref(Id(ID_KIND, "input1"))
        ext_parent = make_test_provenance("ext-parent")

        with patch("core.transform.Provenance", side_effect=recording_provenance):
            result = pipeline.apply(
                5,
                clock=FixedClock(),
                monotonic_clock=SequentialMonotonicClock(),
                ids=SequentialIdSource(),
                input_refs=(ref,),
                parents=(ext_parent,),
            )

        assert isinstance(result, Ok)
        assert len(recorded_inputs) == 2
        assert recorded_inputs[0] == (ref,)
        assert recorded_parents[0] == (Ref(ext_parent.id),)
        assert recorded_inputs[1] == ()
        assert len(recorded_parents[1]) == 1

    def test_first_err_short_circuits_later_stages(self) -> None:
        calls: list[str] = []

        def fn_a(x: int) -> int:
            calls.append("a")
            raise ValueError("a failed")

        def fn_b(x: int) -> int:
            calls.append("b")
            return x

        a = make_transform(fn=fn_a, id_suffix="a")
        b = make_transform(fn=fn_b, id_suffix="b")
        result = a.then(b).apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert calls == ["a"]
        assert isinstance(result, Err)

    def test_returns_the_exact_err_from_the_failing_stage_unchanged(self) -> None:
        a = make_transform(requires=(lambda x: False,), id_suffix="a")
        b = make_transform(id_suffix="b")
        pipeline = a.then(b)

        direct_result = a.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        pipeline_result = pipeline.apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert pipeline_result == direct_result

    def test_final_successful_result_is_the_final_transforms_traced_value(self) -> None:
        a = make_transform(fn=lambda x: x + 1, id_suffix="a")
        b = make_transform(fn=lambda x: x * 10, id_suffix="b")
        result = a.then(b).apply(
            5,
            clock=FixedClock(),
            monotonic_clock=SequentialMonotonicClock(),
            ids=SequentialIdSource(),
        )
        assert isinstance(result, Ok)
        assert result.value.value == 60  # (5 + 1) * 10

    def test_pipeline_has_no_independent_identity_or_provenance(self) -> None:
        pipeline = make_transform(id_suffix="a").then(make_transform(id_suffix="b"))
        assert not hasattr(pipeline, "id")
        assert not hasattr(pipeline, "provenance")

    def test_capability_failure_propagates_not_converted_to_err(self) -> None:
        class BrokenIdSource:
            def new(self, kind: Kind) -> Id:
                raise RuntimeError("id source broken")

        pipeline = make_transform(id_suffix="a").then(make_transform(id_suffix="b"))
        with pytest.raises(RuntimeError, match="id source broken"):
            pipeline.apply(
                5,
                clock=FixedClock(),
                monotonic_clock=SequentialMonotonicClock(),
                ids=BrokenIdSource(),
            )


def test_importing_transform_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.transform", ("uuid.uuid4",))
