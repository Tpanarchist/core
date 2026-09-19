"""Propositions for core.relation.

See SPECIFICATION.md #13 and docs/passes/04-facts-and-relationships.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.identity import Id, Namespace, Ref
from core.relation import Relation, RelationSet
from core.time import WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
NODE_KIND = Kind("test.node")
DEPENDS_ON = Kind("test.depends_on")
REFERENCES = Kind("test.references")


def node(value: str) -> Id:
    return Id(NODE_KIND, value)


class TestRelation:
    def test_metadata_is_defensively_copied_and_read_only(self) -> None:
        source = {"weight": 1}
        rel = Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT, metadata=source)
        source["weight"] = 2
        assert rel.metadata is not None
        assert rel.metadata["weight"] == 1
        with pytest.raises(TypeError):
            rel.metadata["weight"] = 99  # type: ignore[index]

    def test_is_not_entity_bearing(self) -> None:
        rel = Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT)
        assert not hasattr(rel, "id")


class TestRelationSetBasics:
    def test_preserves_insertion_order_and_duplicates(self) -> None:
        rs = RelationSet()
        r1 = Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT)
        r2 = Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT)  # duplicate
        rs.add(r1)
        rs.add(r2)
        assert rs.relations() == (r1, r2)

    def test_relations_returns_a_tuple_snapshot(self) -> None:
        rs = RelationSet()
        rs.add(Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT))
        snapshot = rs.relations()
        rs.add(Relation(source=node("b"), kind=DEPENDS_ON, target=node("c"), at=AT))
        assert len(snapshot) == 1


class TestQuery:
    def test_filters_by_exact_kind(self) -> None:
        rs = RelationSet()
        dep = rs.add(Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT))
        rs.add(Relation(source=node("a"), kind=REFERENCES, target=node("b"), at=AT))
        assert rs.query(kind=DEPENDS_ON) == (dep,)

    def test_source_and_target_match_by_underlying_identity(self) -> None:
        rs = RelationSet()
        a_id = node("a")
        a_ref = Ref(a_id, Namespace(("some", "scope")))
        rel = rs.add(Relation(source=a_ref, kind=DEPENDS_ON, target=node("b"), at=AT))
        # querying with the bare Id matches a Relation stored with a Ref to that Id
        assert rs.query(source=a_id) == (rel,)

    def test_filters_compose_conjunctively(self) -> None:
        rs = RelationSet()
        match = rs.add(Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT))
        rs.add(Relation(source=node("a"), kind=DEPENDS_ON, target=node("c"), at=AT))
        rs.add(Relation(source=node("x"), kind=DEPENDS_ON, target=node("b"), at=AT))
        assert rs.query(kind=DEPENDS_ON, source=node("a"), target=node("b")) == (match,)


class TestPathExists:
    def test_directed_bfs_follows_source_to_target(self) -> None:
        rs = RelationSet()
        rs.add(Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT))
        rs.add(Relation(source=node("b"), kind=DEPENDS_ON, target=node("c"), at=AT))
        assert rs.path_exists(node("a"), node("c")) is True
        assert rs.path_exists(node("c"), node("a")) is False  # direction matters

    def test_same_identity_source_and_target_is_reflexively_reachable(self) -> None:
        rs = RelationSet()
        assert rs.path_exists(node("a"), node("a")) is True

    def test_kind_filtered_search_ignores_other_kinds(self) -> None:
        rs = RelationSet()
        rs.add(Relation(source=node("a"), kind=REFERENCES, target=node("b"), at=AT))
        assert rs.path_exists(node("a"), node("b"), kind=DEPENDS_ON) is False
        assert rs.path_exists(node("a"), node("b"), kind=REFERENCES) is True

    def test_cycles_do_not_cause_infinite_loop(self) -> None:
        rs = RelationSet()
        rs.add(Relation(source=node("a"), kind=DEPENDS_ON, target=node("b"), at=AT))
        rs.add(Relation(source=node("b"), kind=DEPENDS_ON, target=node("a"), at=AT))  # cycle
        assert rs.path_exists(node("a"), node("b")) is True
        assert rs.path_exists(node("a"), node("z")) is False  # terminates, doesn't loop forever


def test_importing_relation_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.relation", ("uuid.uuid4",))
