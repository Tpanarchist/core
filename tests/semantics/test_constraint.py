"""Propositions for core.constraint.

See SPECIFICATION.md #14 and docs/passes/05-change-and-execution.md.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.constraint import ContractError, ensure, invariant, require
from core.context import Context
from core.identity import Id
from core.time import WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
ERROR_ID_KIND = Kind("core.error")

_CHECKS = (require, ensure, invariant)


def make_ids(id_value: str = "e1") -> MagicMock:
    ids = MagicMock()
    ids.new.return_value = Id(ERROR_ID_KIND, id_value)
    return ids


def make_clock() -> MagicMock:
    clock = MagicMock()
    clock.now.return_value = AT
    return clock


class TestContractError:
    def test_is_an_exception_carrying_the_exact_error(self) -> None:
        with pytest.raises(ContractError) as exc_info:
            require(False, "must hold", ids=make_ids(), clock=make_clock())
        assert isinstance(exc_info.value, Exception)
        assert exc_info.value.error.message == "must hold"


class TestSuccessfulChecks:
    @pytest.mark.parametrize("fn", _CHECKS)
    def test_true_condition_returns_none(self, fn: Callable[..., None]) -> None:
        assert fn(True, "must hold", ids=make_ids(), clock=make_clock()) is None

    def test_does_not_consume_an_id(self) -> None:
        ids = make_ids()
        require(True, "must hold", ids=ids, clock=make_clock())
        ids.new.assert_not_called()

    def test_does_not_read_the_clock(self) -> None:
        clock = make_clock()
        require(True, "must hold", ids=make_ids(), clock=clock)
        clock.now.assert_not_called()


class TestMessageValidation:
    def test_empty_message_rejected_when_condition_true(self) -> None:
        with pytest.raises(ValueError, match="message must not be empty"):
            require(True, "", ids=make_ids(), clock=make_clock())

    def test_empty_message_rejected_when_condition_false(self) -> None:
        with pytest.raises(ValueError, match="message must not be empty"):
            require(False, "", ids=make_ids(), clock=make_clock())


class TestFailedChecks:
    def test_creates_exactly_one_error(self) -> None:
        ids = make_ids()
        clock = make_clock()
        with pytest.raises(ContractError):
            require(False, "must hold", ids=ids, clock=clock)
        assert ids.new.call_count == 1
        assert clock.now.call_count == 1

    def test_error_id_uses_core_error_kind(self) -> None:
        ids = make_ids()
        with pytest.raises(ContractError):
            require(False, "must hold", ids=ids, clock=make_clock())
        called_kind = ids.new.call_args.args[0]
        assert called_kind == Kind("core.error")

    def test_error_kind_is_constraint_violation(self) -> None:
        with pytest.raises(ContractError) as exc_info:
            require(False, "must hold", ids=make_ids(), clock=make_clock())
        assert exc_info.value.error.kind == Kind("core.constraint.violation")

    @pytest.mark.parametrize(
        ("fn", "expected_operation"),
        [(require, "require"), (ensure, "ensure"), (invariant, "invariant")],
    )
    def test_operation_distinguishes_the_three(
        self, fn: Callable[..., None], expected_operation: str
    ) -> None:
        with pytest.raises(ContractError) as exc_info:
            fn(False, "must hold", ids=make_ids(), clock=make_clock())
        assert exc_info.value.error.operation == expected_operation

    def test_supplied_context_is_preserved(self) -> None:
        ctx = Context(as_of=AT)
        with pytest.raises(ContractError) as exc_info:
            require(False, "must hold", ids=make_ids(), clock=make_clock(), context=ctx)
        assert exc_info.value.error.context is ctx

    def test_cause_and_exception_are_none(self) -> None:
        with pytest.raises(ContractError) as exc_info:
            require(False, "must hold", ids=make_ids(), clock=make_clock())
        assert exc_info.value.error.cause is None
        assert exc_info.value.error.exception is None

    def test_recoverable_is_false(self) -> None:
        with pytest.raises(ContractError) as exc_info:
            require(False, "must hold", ids=make_ids(), clock=make_clock())
        assert exc_info.value.error.recoverable is False


class TestCapabilityFailurePropagates:
    def test_id_source_failure_propagates(self) -> None:
        ids = MagicMock()
        ids.new.side_effect = RuntimeError("id source broken")
        with pytest.raises(RuntimeError, match="id source broken"):
            require(False, "must hold", ids=ids, clock=make_clock())

    def test_clock_failure_propagates(self) -> None:
        clock = MagicMock()
        clock.now.side_effect = RuntimeError("clock broken")
        with pytest.raises(RuntimeError, match="clock broken"):
            require(False, "must hold", ids=make_ids(), clock=clock)


def test_importing_constraint_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.constraint", ("uuid.uuid4",))
