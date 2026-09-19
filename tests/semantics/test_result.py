"""Propositions for core.result — see SPECIFICATION.md #8 and docs/passes/01-atoms.md."""

from typing import assert_type

import pytest

from core.result import Err, Ok, Result, UnwrapError


class TestBranchExclusivity:
    def test_ok_has_no_error_field(self) -> None:
        assert not hasattr(Ok(1), "error")

    def test_err_has_no_value_field(self) -> None:
        assert not hasattr(Err("boom"), "value")

    def test_ok_and_err_are_distinct_types(self) -> None:
        assert type(Ok(1)) is not type(Err(1))


class TestMap:
    def test_ok_map_transforms_value(self) -> None:
        assert Ok(2).map(lambda x: x * 10) == Ok(20)

    def test_err_map_is_noop(self) -> None:
        err = Err("boom")
        called = False

        def fn(x: object) -> object:
            nonlocal called
            called = True
            return x

        assert err.map(fn) == err
        assert called is False


class TestMapErr:
    def test_err_map_err_transforms_error(self) -> None:
        assert Err("boom").map_err(str.upper) == Err("BOOM")

    def test_ok_map_err_is_noop(self) -> None:
        ok = Ok(2)
        called = False

        def fn(x: object) -> object:
            nonlocal called
            called = True
            return x

        assert ok.map_err(fn) == ok
        assert called is False


class TestAndThen:
    def test_ok_and_then_chains(self) -> None:
        def add_one(x: int) -> Ok[int] | Err[str]:
            return Ok(x + 1)

        result = Ok(2).and_then(add_one)
        assert result == Ok(3)

    def test_ok_and_then_can_produce_err(self) -> None:
        def fail(x: int) -> Ok[int] | Err[str]:
            return Err("failed downstream")

        result = Ok(2).and_then(fail)
        assert result == Err("failed downstream")

    def test_err_and_then_short_circuits(self) -> None:
        err = Err("boom")
        called = False

        def fn(x: object) -> object:
            nonlocal called
            called = True
            return Ok(x)

        assert err.and_then(fn) == err
        assert called is False


class TestUnwrap:
    def test_ok_unwrap_returns_value(self) -> None:
        assert Ok(42).unwrap() == 42

    def test_err_unwrap_raises_unwrap_error_with_original_error(self) -> None:
        original = {"reason": "boom"}
        with pytest.raises(UnwrapError) as exc_info:
            Err(original).unwrap()
        assert exc_info.value.error is original

    def test_ok_unwrap_or_returns_value_not_default(self) -> None:
        assert Ok(1).unwrap_or(99) == 1

    def test_err_unwrap_or_returns_default(self) -> None:
        assert Err("boom").unwrap_or(99) == 99


class TestUnwrapType:
    def test_unwrap_type_is_not_widened_to_any(self) -> None:
        # Err.unwrap() is typed -> Never (it always raises), so a statically
        # typed Result[int, str].unwrap() resolves to plain `int`, not `Any` —
        # if Err.unwrap() ever regresses back to `Any`, pyright fails this.
        def make(flag: bool) -> Result[int, str]:
            return Ok(1) if flag else Err("boom")

        result = make(True)
        assert_type(result.unwrap(), int)
        assert result.unwrap() == 1


class TestComposition:
    def test_pipeline_of_and_thens_short_circuits_on_first_err(self) -> None:
        def half(x: int) -> Ok[int] | Err[str]:
            if x % 2 != 0:
                return Err(f"{x} is odd")
            return Ok(x // 2)

        result = Ok(8).and_then(half).and_then(half).and_then(half)
        assert result == Ok(1)

        result = Ok(6).and_then(half).and_then(half).and_then(half)
        assert result == Err("3 is odd")
