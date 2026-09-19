"""Propositions for memory.episode.

Matrix reference: MEMORY_ADVERSARIAL_MATRIX.md section A (Episode).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.identity import Entity, Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind
from memory.episode import Episode

EPISODE_KIND = Kind("memory.test.episode")
SUBJECT_KIND = Kind("memory.test.subject")
OBS_KIND = Kind("memory.test.observation")
CTX = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))


def make_episode(*, opened_at: WallInstant | None = None) -> Episode:
    return Episode(
        id=Id(EPISODE_KIND, "e1"),
        subject=Id(SUBJECT_KIND, "s1"),
        context=CTX,
        opened_at=opened_at or WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC)),
    )


def ref(value: str) -> Ref:
    return Ref(id=Id(OBS_KIND, value))


class TestConstruction:
    def test_episode_satisfies_entity(self) -> None:
        assert isinstance(make_episode(), Entity)

    def test_episode_requires_caller_supplied_id(self) -> None:
        episode = make_episode()
        assert episode.id == Id(EPISODE_KIND, "e1")

    def test_starts_empty_and_open(self) -> None:
        episode = make_episode()
        assert episode.items() == ()
        assert episode.closed_at is None


class TestAppendOrdering:
    def test_ep_03_duplicate_refs_preserved_as_separate_positions(self) -> None:
        episode = make_episode()
        episode.append(ref("a"))
        episode.append(ref("a"))
        assert episode.items() == (ref("a"), ref("a"))

    def test_ep_01_ep_02_member_subject_never_rewritten(self) -> None:
        # Episode groups by asserting its own subject; it never inspects or
        # rewrites a member's own subject (Event has no subject field at
        # all, and an Observation's subject may legitimately differ).
        episode = make_episode()
        member = ref("event-with-no-subject")
        episode.append(member)
        assert episode.items() == (member,)
        assert episode.subject == Id(SUBJECT_KIND, "s1")

    def test_ep_04_insertion_order_beats_wall_time(self) -> None:
        # Members carry no wall-time field on Ref itself — insertion order
        # is the only order Episode tracks, and it must not be reconstructed
        # from any external timestamp a caller might associate with a Ref.
        episode = make_episode()
        episode.append(ref("later-in-reality"))
        episode.append(ref("earlier-in-reality"))
        assert episode.items() == (ref("later-in-reality"), ref("earlier-in-reality"))

    def test_ep_11_distinct_namespaces_on_same_id_preserved_exactly(self) -> None:
        episode = make_episode()
        plain = Ref(id=Id(OBS_KIND, "shared"))
        namespaced = Ref(id=Id(OBS_KIND, "shared"), namespace=Namespace(("finance",)))
        episode.append(plain)
        episode.append(namespaced)
        assert episode.items() == (plain, namespaced)
        assert episode.items()[0].namespace is None
        assert episode.items()[1].namespace == Namespace(("finance",))

    def test_ep_12_ref_to_another_episode_preserved_opaquely(self) -> None:
        episode = make_episode()
        other_episode_ref = Ref(id=Id(EPISODE_KIND, "e2"))
        episode.append(other_episode_ref)
        assert episode.items() == (other_episode_ref,)

    def test_ep_13_self_reference_preserved_opaquely(self) -> None:
        episode = make_episode()
        self_ref = Ref(id=episode.id)
        episode.append(self_ref)
        assert episode.items() == (self_ref,)

    def test_ep_14_old_snapshot_unaffected_by_later_append(self) -> None:
        episode = make_episode()
        episode.append(ref("a"))
        snapshot = episode.items()
        episode.append(ref("b"))
        assert snapshot == (ref("a"),)
        assert episode.items() == (ref("a"), ref("b"))


class TestClose:
    def test_ep_06_append_after_close_raises(self) -> None:
        episode = make_episode()
        episode.close(episode.opened_at)
        with pytest.raises(ValueError):
            episode.append(ref("a"))

    def test_ep_07_close_twice_raises(self) -> None:
        episode = make_episode()
        episode.close(episode.opened_at)
        with pytest.raises(ValueError):
            episode.close(episode.opened_at)

    def test_ep_08_close_before_opened_at_raises(self) -> None:
        episode = make_episode(
            opened_at=WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        )
        with pytest.raises(ValueError):
            episode.close(WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))

    def test_ep_09_equal_open_and_close_instants_permitted(self) -> None:
        episode = make_episode()
        episode.close(episode.opened_at)
        assert episode.closed_at == episode.opened_at

    def test_close_returns_the_accepted_instant(self) -> None:
        episode = make_episode()
        closing = WallInstant(datetime(2024, 1, 2, tzinfo=UTC))
        assert episode.close(closing) == closing


class TestImportSideEffects:
    def test_episode_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.episode",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )
