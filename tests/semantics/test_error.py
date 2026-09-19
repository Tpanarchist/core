"""Propositions for core.error.

See SPECIFICATION.md #9 and docs/passes/03-context-error-trace.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.error import Error
from core.identity import Id
from core.time import WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
KIND = Kind("core.validation")
ERROR_ID_KIND = Kind("core.error")


def make_error(**overrides: object) -> Error:
    defaults: dict[str, object] = {
        "id": Id(ERROR_ID_KIND, "e1"),
        "kind": KIND,
        "message": "boom",
        "at": AT,
    }
    defaults.update(overrides)
    return Error(**defaults)  # type: ignore[arg-type]


class TestConstruction:
    def test_requires_id_and_at(self) -> None:
        with pytest.raises(TypeError):
            Error(kind=KIND, message="boom")  # type: ignore[call-arg]

    def test_rejects_empty_message(self) -> None:
        with pytest.raises(ValueError, match="message must not be empty"):
            make_error(message="")

    def test_rejects_empty_operation_if_supplied(self) -> None:
        with pytest.raises(ValueError, match="operation must not be empty"):
            make_error(operation="")

    def test_preserves_context_operation_recoverable_metadata_and_exception(self) -> None:
        ctx = Context(as_of=AT)
        original = ValueError("underlying")
        error = make_error(
            context=ctx,
            operation="save",
            recoverable=True,
            metadata={"attempt": 1},
            exception=original,
        )
        assert error.context is ctx
        assert error.operation == "save"
        assert error.recoverable is True
        assert error.metadata is not None
        assert error.metadata["attempt"] == 1
        assert error.exception is original

    def test_metadata_is_defensively_copied_and_read_only(self) -> None:
        source = {"a": 1}
        error = make_error(metadata=source)
        source["a"] = 2
        assert error.metadata is not None
        assert error.metadata["a"] == 1
        with pytest.raises(TypeError):
            error.metadata["a"] = 99  # type: ignore[index]


class TestChain:
    def test_follows_only_structured_cause_self_first(self) -> None:
        root = make_error(message="root cause")
        middle = make_error(message="middle", cause=root)
        top = make_error(message="top", cause=middle)
        assert top.chain() == (top, middle, root)

    def test_exception_is_not_part_of_the_chain(self) -> None:
        original = ValueError("boom")
        error = make_error(exception=original)
        assert error.chain() == (error,)

    def test_single_error_chain_is_itself(self) -> None:
        error = make_error()
        assert error.chain() == (error,)


class TestIsKind:
    def test_is_exact_kind_equality(self) -> None:
        error = make_error(kind=KIND)
        assert error.is_kind(KIND) is True
        assert error.is_kind(Kind("core.other")) is False


def test_error_is_not_an_exception_subclass() -> None:
    assert not issubclass(Error, BaseException)


def test_importing_error_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.error", ("uuid.uuid4",))
