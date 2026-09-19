"""Propositions for core.identity — see SPECIFICATION.md #1 and docs/passes/02-identity-time.md."""

import importlib
import uuid
from dataclasses import dataclass
from unittest import mock

import pytest

import core.identity as core_identity
from core.identity import Entity, Id, Key, Namespace, Ref, UuidIdSource, identity_of
from core.value import Kind

USER = Kind("test.user")
ORDER = Kind("test.order")


class TestId:
    def test_equality_depends_on_kind_and_value(self) -> None:
        assert Id(USER, "42") == Id(USER, "42")
        assert Id(USER, "42") != Id(USER, "43")

    def test_same_value_under_different_kinds_is_a_different_identity(self) -> None:
        assert Id(USER, "42") != Id(ORDER, "42")

    def test_hash_depends_on_kind_and_value(self) -> None:
        assert hash(Id(USER, "42")) == hash(Id(USER, "42"))
        assert {Id(USER, "42"), Id(ORDER, "42"), Id(USER, "42")} == {
            Id(USER, "42"),
            Id(ORDER, "42"),
        }

    def test_empty_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="Id.value must not be empty"):
            Id(USER, "")


class TestNamespace:
    def test_rejects_empty_structure(self) -> None:
        with pytest.raises(ValueError, match="at least one segment"):
            Namespace(())

    def test_rejects_empty_segment(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            Namespace(("a", "", "b"))

    def test_valid_namespace_preserves_segments(self) -> None:
        assert Namespace(("a", "b")).segments == ("a", "b")


class TestRef:
    def test_equality_includes_namespace(self) -> None:
        id_ = Id(USER, "42")
        ns_a = Namespace(("a",))
        ns_b = Namespace(("b",))
        assert Ref(id_, ns_a) != Ref(id_, ns_b)
        assert Ref(id_, ns_a) == Ref(id_, ns_a)
        assert Ref(id_) != Ref(id_, ns_a)

    def test_identity_of_strips_namespace_for_entity_comparison(self) -> None:
        id_ = Id(USER, "42")
        ref_a = Ref(id_, Namespace(("a",)))
        ref_b = Ref(id_, Namespace(("b",)))
        assert ref_a != ref_b  # different references
        assert identity_of(ref_a) == identity_of(ref_b) == id_  # same entity

    def test_identity_of_passes_through_a_bare_id(self) -> None:
        id_ = Id(USER, "42")
        assert identity_of(id_) == id_


class TestKey:
    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="Key.name must not be empty"):
            Key(Namespace(("a",)), "")

    def test_equality_is_structural(self) -> None:
        ns = Namespace(("a",))
        assert Key(ns, "x") == Key(ns, "x")
        assert Key(ns, "x") != Key(ns, "y")


class TestEntity:
    def test_structural_object_with_id_satisfies_entity(self) -> None:
        @dataclass(frozen=True, slots=True)
        class Thing:
            id: Id

        assert isinstance(Thing(Id(USER, "42")), Entity)

    def test_object_without_id_does_not_satisfy_entity(self) -> None:
        class NotAThing:
            pass

        assert not isinstance(NotAThing(), Entity)


class TestUuidIdSource:
    def test_preserves_kind(self) -> None:
        assert UuidIdSource().new(USER).kind == USER

    def test_value_is_a_parseable_uuid(self) -> None:
        id_ = UuidIdSource().new(USER)
        uuid.UUID(id_.value)  # raises ValueError if not parseable — the assertion

    def test_two_calls_produce_distinct_tokens(self) -> None:
        source = UuidIdSource()
        assert source.new(USER) != source.new(USER)


def test_importing_identity_module_has_no_side_effects() -> None:
    with mock.patch(
        "uuid.uuid4", side_effect=AssertionError("import must not allocate a UUID")
    ):
        importlib.reload(core_identity)
