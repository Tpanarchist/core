"""Propositions for core.context.

See SPECIFICATION.md #3 and docs/passes/03-context-error-trace.md.
"""

from datetime import UTC, datetime

import pytest

from core.context import Context, ContextConflict
from core.result import Err, Ok
from core.time import WallInstant

NOW = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
LATER = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))


class TestConstruction:
    def test_requires_as_of(self) -> None:
        with pytest.raises(TypeError):
            Context()  # type: ignore[call-arg]

    def test_optional_dimensions_preserve_caller_values_without_new_wrapper_types(self) -> None:
        ctx = Context(as_of=NOW, scope="request", units="seconds")
        assert ctx.scope == "request"
        assert ctx.units == "seconds"
        assert ctx.environment is None

    def test_metadata_is_defensively_copied(self) -> None:
        source = {"a": 1}
        ctx = Context(as_of=NOW, metadata=source)
        source["a"] = 2
        assert ctx.metadata is not None
        assert ctx.metadata["a"] == 1

    def test_metadata_cannot_be_mutated_through_exposed_mapping(self) -> None:
        ctx = Context(as_of=NOW, metadata={"a": 1})
        assert ctx.metadata is not None
        with pytest.raises(TypeError):
            ctx.metadata["a"] = 99  # type: ignore[index]

    def test_metadata_rejects_non_string_keys(self) -> None:
        with pytest.raises(ValueError, match="non-empty strings"):
            Context(as_of=NOW, metadata={1: "x"})  # type: ignore[dict-item]

    def test_metadata_rejects_empty_string_keys(self) -> None:
        with pytest.raises(ValueError, match="non-empty strings"):
            Context(as_of=NOW, metadata={"": 1})


class TestWith:
    def test_changes_only_requested_fields(self) -> None:
        ctx = Context(as_of=NOW, scope="a", units="s")
        updated = ctx.with_(scope="b")
        assert updated.scope == "b"
        assert updated.units == "s"
        assert updated.as_of == NOW

    def test_rejects_unknown_field(self) -> None:
        with pytest.raises(ValueError, match="unknown Context field"):
            Context(as_of=NOW).with_(bogus="x")

    def test_can_explicitly_clear_an_optional_field(self) -> None:
        cleared = Context(as_of=NOW, scope="a").with_(scope=None)
        assert cleared.scope is None

    def test_does_not_mutate_the_original(self) -> None:
        ctx = Context(as_of=NOW, scope="a")
        ctx.with_(scope="b")
        assert ctx.scope == "a"


class TestMerge:
    def test_equal_fields_merge(self) -> None:
        a = Context(as_of=NOW, scope="x")
        b = Context(as_of=NOW, scope="x")
        result = a.merge(b)
        assert isinstance(result, Ok)
        assert result.value.scope == "x"

    def test_absent_vs_present_merges_to_the_present_value(self) -> None:
        absent = Context(as_of=NOW)
        present = Context(as_of=NOW, scope="x")

        result = absent.merge(present)
        assert isinstance(result, Ok)
        assert result.value.scope == "x"

        result_reversed = present.merge(absent)
        assert isinstance(result_reversed, Ok)
        assert result_reversed.value.scope == "x"

    def test_incompatible_non_none_fields_become_err_context_conflict(self) -> None:
        a = Context(as_of=NOW, scope="x")
        b = Context(as_of=NOW, scope="y")
        result = a.merge(b)
        assert isinstance(result, Err)
        assert isinstance(result.error, ContextConflict)
        assert ("scope", "x", "y") in result.error.conflicts

    def test_one_merge_reports_every_conflicting_field_in_declared_order(self) -> None:
        a = Context(as_of=NOW, scope="x", units="s")
        b = Context(as_of=LATER, scope="y", units="ms")
        result = a.merge(b)
        assert isinstance(result, Err)
        field_names = [c[0] for c in result.error.conflicts]
        assert field_names == ["as_of", "scope", "units"]

    def test_unequal_metadata_conflicts_atomically_not_deep_merged(self) -> None:
        a = Context(as_of=NOW, metadata={"a": 1})
        b = Context(as_of=NOW, metadata={"a": 1, "b": 2})
        result = a.merge(b)
        assert isinstance(result, Err)
        assert [c[0] for c in result.error.conflicts] == ["metadata"]

    def test_equal_metadata_merges(self) -> None:
        a = Context(as_of=NOW, metadata={"a": 1})
        b = Context(as_of=NOW, metadata={"a": 1})
        assert isinstance(a.merge(b), Ok)

    def test_merge_is_non_mutating(self) -> None:
        a = Context(as_of=NOW, scope="x")
        b = Context(as_of=NOW, scope="y")
        a.merge(b)
        assert a.scope == "x"
        assert b.scope == "y"

    def test_context_conflict_rejects_empty_conflicts(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            ContextConflict(())


class TestOverride:
    def test_changes_only_explicitly_named_fields(self) -> None:
        a = Context(as_of=NOW, scope="x", units="s")
        b = Context(as_of=LATER, scope="y", units="ms")
        new_ctx, _ = a.override(b, "scope")
        assert new_ctx.scope == "y"
        assert new_ctx.units == "s"
        assert new_ctx.as_of == NOW

    def test_records_every_effective_replacement_in_requested_order(self) -> None:
        a = Context(as_of=NOW, scope="x", units="s")
        b = Context(as_of=LATER, scope="y", units="s")
        _, audit = a.override(b, "as_of", "scope", "units")
        assert audit.changes == (
            ("as_of", NOW, LATER),
            ("scope", "x", "y"),
        )  # units unchanged ("s" == "s") -> no entry

    def test_no_effective_change_produces_no_audit_entry_for_that_field(self) -> None:
        a = Context(as_of=NOW, scope="x")
        b = Context(as_of=NOW, scope="x")
        _, audit = a.override(b, "scope")
        assert audit.changes == ()

    def test_rejects_unknown_field_name(self) -> None:
        with pytest.raises(ValueError, match="unknown Context field"):
            Context(as_of=NOW).override(Context(as_of=LATER), "bogus")

    def test_rejects_duplicate_field_name(self) -> None:
        a = Context(as_of=NOW, scope="x")
        b = Context(as_of=NOW, scope="y")
        with pytest.raises(ValueError, match="duplicate Context field"):
            a.override(b, "scope", "scope")

    def test_can_explicitly_replace_a_value_with_none(self) -> None:
        a = Context(as_of=NOW, scope="x")
        b = Context(as_of=NOW, scope=None)
        new_ctx, audit = a.override(b, "scope")
        assert new_ctx.scope is None
        assert audit.changes == (("scope", "x", None),)


def test_context_has_no_knowledge_of_contradiction() -> None:
    import core.context as core_context

    assert not hasattr(core_context, "Contradiction")


def test_importing_context_module_has_no_side_effects() -> None:
    import importlib
    from unittest import mock

    import core.context as core_context

    with mock.patch("uuid.uuid4", side_effect=AssertionError("import must not allocate an id")):
        importlib.reload(core_context)
