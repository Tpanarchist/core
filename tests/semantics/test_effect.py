"""Propositions for core.effect.

See SPECIFICATION.md #11 and docs/passes/04-facts-and-relationships.md.
"""

from datetime import UTC, datetime

import pytest
from _side_effects import assert_fresh_import_has_no_side_effects

from core.effect import Effect, EffectSink, EffectSpec, MemoryEffectSink
from core.identity import Entity, Id
from core.time import WallInstant
from core.value import Kind

AT = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
EFFECT_ID_KIND = Kind("core.effect")
KIND = Kind("test.write")


class TestEffectSpec:
    def test_rejects_empty_description(self) -> None:
        with pytest.raises(ValueError, match="description must not be empty"):
            EffectSpec(kind=KIND, target_shape=None, description="")

    def test_is_a_distinct_type_from_effect(self) -> None:
        spec = EffectSpec(kind=KIND, target_shape=None, description="writes a file")
        assert not isinstance(spec, Effect)

    def test_is_not_entity_bearing(self) -> None:
        spec = EffectSpec(kind=KIND, target_shape=None, description="writes a file")
        assert not hasattr(spec, "id")


class TestEffect:
    def test_satisfies_entity(self) -> None:
        effect = Effect(
            id=Id(EFFECT_ID_KIND, "eff1"), kind=KIND, description="wrote", target="file.txt", at=AT
        )
        assert isinstance(effect, Entity)

    def test_rejects_empty_description(self) -> None:
        with pytest.raises(ValueError, match="description must not be empty"):
            Effect(id=Id(EFFECT_ID_KIND, "eff1"), kind=KIND, description="", target=None, at=AT)

    def test_metadata_is_defensively_copied_and_read_only(self) -> None:
        source = {"bytes": 10}
        effect = Effect(
            id=Id(EFFECT_ID_KIND, "eff1"),
            kind=KIND,
            description="wrote",
            target="file.txt",
            at=AT,
            metadata=source,
        )
        source["bytes"] = 99
        assert effect.metadata is not None
        assert effect.metadata["bytes"] == 10
        with pytest.raises(TypeError):
            effect.metadata["bytes"] = 1  # type: ignore[index]


class TestEffectSink:
    def test_memory_effect_sink_satisfies_effect_sink(self) -> None:
        assert isinstance(MemoryEffectSink(), EffectSink)

    def test_preserves_duplicates_and_insertion_order(self) -> None:
        sink = MemoryEffectSink()
        e1 = Effect(id=Id(EFFECT_ID_KIND, "eff1"), kind=KIND, description="a", target=None, at=AT)
        e2 = Effect(id=Id(EFFECT_ID_KIND, "eff1"), kind=KIND, description="a", target=None, at=AT)
        sink.record(e1)
        sink.record(e2)
        assert sink.effects() == (e1, e2)

    def test_effects_snapshot_unaffected_by_later_recordings(self) -> None:
        sink = MemoryEffectSink()
        sink.record(
            Effect(id=Id(EFFECT_ID_KIND, "eff1"), kind=KIND, description="a", target=None, at=AT)
        )
        snapshot = sink.effects()
        sink.record(
            Effect(id=Id(EFFECT_ID_KIND, "eff2"), kind=KIND, description="b", target=None, at=AT)
        )
        assert len(snapshot) == 1

    def test_recording_does_not_alter_the_effects_id_or_timestamp(self) -> None:
        sink = MemoryEffectSink()
        effect = Effect(
            id=Id(EFFECT_ID_KIND, "eff1"), kind=KIND, description="a", target=None, at=AT
        )
        sink.record(effect)
        recorded = sink.effects()[0]
        assert recorded.id == effect.id
        assert recorded.at == effect.at


def test_importing_effect_module_has_no_side_effects() -> None:
    assert_fresh_import_has_no_side_effects("core.effect", ("uuid.uuid4",))
