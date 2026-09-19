"""Propositions for core.provenance.

See SPECIFICATION.md #15 and docs/passes/04-facts-and-relationships.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.identity import Entity, Id, Namespace, Ref
from core.provenance import Provenance, Traced
from core.time import Duration, WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
PROV_KIND = Kind("core.provenance")
TRANSFORM_KIND = Kind("core.transform")


def make_provenance(suffix: str, parents: tuple[Ref, ...] = ()) -> Provenance:
    return Provenance(
        id=Id(PROV_KIND, suffix),
        transform_id=Id(TRANSFORM_KIND, f"t-{suffix}"),
        transform_name="double",
        transform_version="1.0.0",
        inputs=(),
        parents=parents,
        at=AT,
        duration=Duration(100),
    )


class TestConstruction:
    def test_satisfies_entity(self) -> None:
        assert isinstance(make_provenance("p1"), Entity)

    def test_rejects_empty_transform_name(self) -> None:
        with pytest.raises(ValueError, match="transform_name must not be empty"):
            Provenance(
                id=Id(PROV_KIND, "p1"),
                transform_id=Id(TRANSFORM_KIND, "t1"),
                transform_name="",
                transform_version="1.0.0",
                inputs=(),
                parents=(),
                at=AT,
                duration=Duration(0),
            )

    def test_rejects_empty_transform_version(self) -> None:
        with pytest.raises(ValueError, match="transform_version must not be empty"):
            Provenance(
                id=Id(PROV_KIND, "p1"),
                transform_id=Id(TRANSFORM_KIND, "t1"),
                transform_name="double",
                transform_version="",
                inputs=(),
                parents=(),
                at=AT,
                duration=Duration(0),
            )

    def test_inputs_and_parents_are_tuples(self) -> None:
        prov = make_provenance("p1")
        assert isinstance(prov.inputs, tuple)
        assert isinstance(prov.parents, tuple)


class TestTraced:
    def test_can_carry_a_value_without_provenance(self) -> None:
        traced = Traced(42)
        assert traced.value == 42
        assert traced.provenance is None

    def test_can_carry_a_value_with_provenance(self) -> None:
        prov = make_provenance("p1")
        traced = Traced(42, prov)
        assert traced.provenance is prov


class TestAncestors:
    def test_root_with_no_parents_reports_all_empty(self) -> None:
        root = make_provenance("root")
        report = root.ancestors(resolve=lambda ref: None)
        assert report.found == ()
        assert report.unresolved == ()
        assert report.cycles == ()

    def test_found_excludes_the_traversal_root(self) -> None:
        parent = make_provenance("parent")
        root = make_provenance("root", parents=(Ref(parent.id),))
        report = root.ancestors(resolve=lambda ref: parent if ref.id == parent.id else None)
        assert root not in report.found
        assert report.found == (parent,)

    def test_simple_chain_is_deterministic_dfs_preorder(self) -> None:
        grandparent = make_provenance("gp")
        parent = make_provenance("p", parents=(Ref(grandparent.id),))
        root = make_provenance("root", parents=(Ref(parent.id),))

        store = {grandparent.id: grandparent, parent.id: parent}

        report = root.ancestors(resolve=lambda ref: store.get(ref.id))
        assert report.found == (parent, grandparent)

    def test_convergence_reports_shared_ancestor_once_not_as_a_cycle(self) -> None:
        shared = make_provenance("shared")
        left = make_provenance("left", parents=(Ref(shared.id),))
        right = make_provenance("right", parents=(Ref(shared.id),))
        root = make_provenance("root", parents=(Ref(left.id), Ref(right.id)))

        store = {shared.id: shared, left.id: left, right.id: right}
        report = root.ancestors(resolve=lambda ref: store.get(ref.id))

        # DFS preorder: fully expand `left` (which pulls in `shared`) before
        # moving to the sibling `right`, which then finds `shared` already
        # done and does not re-add or re-traverse it.
        assert report.found == (left, shared, right)
        assert report.cycles == ()

    def test_genuine_back_edge_is_reported_as_a_cycle(self) -> None:
        # a <- b <- a: a genuine cycle, not convergence.
        a_id = Id(PROV_KIND, "a")
        b_id = Id(PROV_KIND, "b")
        a = Provenance(
            id=a_id,
            transform_id=Id(TRANSFORM_KIND, "t-a"),
            transform_name="x",
            transform_version="1",
            inputs=(),
            parents=(Ref(b_id),),
            at=AT,
            duration=Duration(0),
        )
        b = Provenance(
            id=b_id,
            transform_id=Id(TRANSFORM_KIND, "t-b"),
            transform_name="x",
            transform_version="1",
            inputs=(),
            parents=(Ref(a_id),),
            at=AT,
            duration=Duration(0),
        )
        store = {a_id: a, b_id: b}
        report = a.ancestors(resolve=lambda ref: store.get(ref.id))
        assert report.found == (b,)
        assert report.cycles == (Ref(a_id),)

    def test_loop_back_to_root_is_detected(self) -> None:
        root_id = Id(PROV_KIND, "root")
        child_id = Id(PROV_KIND, "child")
        child = Provenance(
            id=child_id,
            transform_id=Id(TRANSFORM_KIND, "t-c"),
            transform_name="x",
            transform_version="1",
            inputs=(),
            parents=(Ref(root_id),),
            at=AT,
            duration=Duration(0),
        )
        root = Provenance(
            id=root_id,
            transform_id=Id(TRANSFORM_KIND, "t-r"),
            transform_name="x",
            transform_version="1",
            inputs=(),
            parents=(Ref(child_id),),
            at=AT,
            duration=Duration(0),
        )
        store = {child_id: child, root_id: root}
        report = root.ancestors(resolve=lambda ref: store.get(ref.id))
        assert report.found == (child,)
        assert report.cycles == (Ref(root_id),)

    def test_unresolved_refs_appear_explicitly(self) -> None:
        missing_ref = Ref(Id(PROV_KIND, "missing"))
        root = make_provenance("root", parents=(missing_ref,))
        report = root.ancestors(resolve=lambda ref: None)
        assert report.unresolved == (missing_ref,)
        assert report.found == ()

    def test_duplicate_identical_unresolved_refs_reported_once(self) -> None:
        missing_ref = Ref(Id(PROV_KIND, "missing"))
        middle = make_provenance("middle", parents=(missing_ref,))
        root = make_provenance("root", parents=(Ref(middle.id), missing_ref))
        store = {middle.id: middle}
        report = root.ancestors(resolve=lambda ref: store.get(ref.id))
        assert report.unresolved == (missing_ref,)

    def test_exact_same_repeated_cycle_ref_reported_once(self) -> None:
        a_id = Id(PROV_KIND, "a")
        b_id = Id(PROV_KIND, "b")
        cycle_ref = Ref(a_id)
        b = Provenance(
            id=b_id,
            transform_id=Id(TRANSFORM_KIND, "t-b"),
            transform_name="x",
            transform_version="1",
            inputs=(),
            parents=(cycle_ref, cycle_ref),  # duplicate parent entry
            at=AT,
            duration=Duration(0),
        )
        a = Provenance(
            id=a_id,
            transform_id=Id(TRANSFORM_KIND, "t-a"),
            transform_name="x",
            transform_version="1",
            inputs=(),
            parents=(Ref(b_id),),
            at=AT,
            duration=Duration(0),
        )
        store = {b_id: b}
        report = a.ancestors(resolve=lambda ref: store.get(ref.id))
        assert report.cycles == (cycle_ref,)

    def test_different_namespaces_to_same_id_are_distinct_facts(self) -> None:
        missing_id = Id(PROV_KIND, "missing")
        ref_a = Ref(missing_id, Namespace(("a",)))
        ref_b = Ref(missing_id, Namespace(("b",)))
        root = make_provenance("root", parents=(ref_a, ref_b))
        report = root.ancestors(resolve=lambda ref: None)
        assert set(report.unresolved) == {ref_a, ref_b}
        assert len(report.unresolved) == 2

    def test_resolver_returning_wrong_id_raises(self) -> None:
        wrong = make_provenance("wrong")
        expected_ref = Ref(Id(PROV_KIND, "expected"))
        root = make_provenance("root", parents=(expected_ref,))
        with pytest.raises(ValueError, match="resolver returned"):
            root.ancestors(resolve=lambda ref: wrong)

    def test_traversal_does_not_mutate_any_node(self) -> None:
        parent = make_provenance("parent")
        root = make_provenance("root", parents=(Ref(parent.id),))
        parent_parents_before = parent.parents
        root.ancestors(resolve=lambda ref: parent if ref.id == parent.id else None)
        assert parent.parents == parent_parents_before


def test_importing_provenance_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.provenance", ("uuid.uuid4",))
