"""Propositions for memory.codec — the durable-value domain.

Matrix references: MEMORY_ADVERSARIAL_MATRIX.md sections H (codec domain)
and I (float handling).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from _memory_side_effects import assert_fresh_import_has_no_side_effects

from core.context import Context
from core.identity import Id, Namespace, Ref
from core.time import Duration, WallInstant
from core.value import Kind
from memory.codec import (
    UnsupportedPersistedValue,
    _envelope,  # pyright: ignore[reportPrivateUsage]
    as_persisted_value,
    decode_context,
    decode_duration,
    decode_id,
    decode_kind,
    decode_namespace,
    decode_persisted_value,
    decode_ref,
    decode_wall_instant,
    encode_context,
    encode_duration,
    encode_id,
    encode_kind,
    encode_namespace,
    encode_persisted_value,
    encode_ref,
    encode_wall_instant,
)


class TestScalars:
    def test_cd_01_none_round_trips(self) -> None:
        assert as_persisted_value(None) is None

    def test_cd_02_false_and_zero_stay_distinguishable(self) -> None:
        assert as_persisted_value(False) is False
        assert as_persisted_value(0) == 0
        assert type(as_persisted_value(False)) is bool
        assert type(as_persisted_value(0)) is int

    def test_cd_03_true_and_one_stay_distinguishable(self) -> None:
        assert as_persisted_value(True) is True
        assert type(as_persisted_value(1)) is int

    def test_cd_04_cd_05_arbitrary_precision_int_round_trips(self) -> None:
        huge_positive = 2**256 + 1
        huge_negative = -(2**256) - 1
        assert as_persisted_value(huge_positive) == huge_positive
        assert as_persisted_value(huge_negative) == huge_negative

    def test_cd_06_unicode_string_preserved_exactly(self) -> None:
        text = "café \U0001f600 ́"
        assert as_persisted_value(text) == text

    def test_cd_08_arbitrary_bytes_round_trip(self) -> None:
        data = b"\x00\x01\xff\xfe"
        assert as_persisted_value(data) == data


class TestContainers:
    def test_cd_09_nested_tuples_round_trip_structure(self) -> None:
        nested = (1, ("a", (b"x", None)), (True, 2.5))
        assert as_persisted_value(nested) == nested

    def test_cd_10_mapping_iteration_order_does_not_change_content(self) -> None:
        first = as_persisted_value({"a": 1, "b": 2})
        second = as_persisted_value({"b": 2, "a": 1})
        assert dict(first) == dict(second) == {"a": 1, "b": 2}  # type: ignore[arg-type]

    def test_cd_16_defensive_snapshot_survives_source_mutation(self) -> None:
        source = {"nested": [1, 2]} if False else {"nested": (1, 2)}
        snapshot = as_persisted_value(source)
        source["nested"] = (99,)  # type: ignore[typeddict-item]
        assert dict(snapshot)["nested"] == (1, 2)  # type: ignore[arg-type]

    def test_validated_mapping_is_immutable(self) -> None:
        snapshot = as_persisted_value({"a": 1})
        assert isinstance(snapshot, MappingProxyType)
        with pytest.raises(TypeError):
            snapshot["a"] = 2  # type: ignore[index]


class TestRejections:
    def test_cd_11_mapping_containing_list_is_rejected_with_path(self) -> None:
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value({"items": [1, 2]})
        assert exc_info.value.path == ("items",)

    def test_cd_12_unsupported_object_three_levels_deep_reports_exact_path(self) -> None:
        class Exotic:
            pass

        payload = {"a": {"b": (1, Exotic())}}
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value(payload)
        assert exc_info.value.path == ("a", "b", 1)

    def test_cd_13_tuple_containing_unsupported_object_reports_index(self) -> None:
        class Exotic:
            pass

        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            as_persisted_value((1, 2, Exotic()))
        assert exc_info.value.path == (2,)

    def test_cd_14_non_string_mapping_key_is_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value({1: "a"})

    def test_cd_14_repr_raising_key_is_rejected_cleanly(self) -> None:
        class LoudKey:
            def __hash__(self) -> int:
                return 0

            def __eq__(self, other: object) -> bool:
                return self is other

            def __repr__(self) -> str:
                raise AssertionError("repr() must never be called on a rejected key")

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value({LoudKey(): "value"})

    def test_cd_15_cycles_fail_explicitly(self) -> None:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(cyclic)

    def test_cd_17_id_is_rejected_as_a_bare_domain_payload(self) -> None:
        from core.identity import Id
        from core.value import Kind

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Id(Kind("test.subject"), "s1"))

    def test_cd_19_repr_able_object_is_rejected_not_stringified(self) -> None:
        class HasRepr:
            def __repr__(self) -> str:
                return "HasRepr()"

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(HasRepr())

    def test_cd_20_pickleable_object_is_rejected(self) -> None:
        class Pickleable:
            def __init__(self) -> None:
                self.x = 1

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Pickleable())

    def test_cd_21_decimal_is_rejected_in_v0(self) -> None:
        from decimal import Decimal

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Decimal("1.5"))

    def test_cd_22_dataclass_is_rejected_without_structural_auto_conversion(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class PersistedValueShaped:
            a: int
            b: str

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(PersistedValueShaped(a=1, b="x"))

    def test_cd_23_list_is_rejected_even_though_json_could_encode_it(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value([1, 2, 3])


class TestFloats:
    def test_fl_01_ordinary_finite_float_round_trips(self) -> None:
        assert as_persisted_value(1.5) == 1.5

    def test_fl_02_negative_zero_sign_preserved(self) -> None:
        import math

        result = as_persisted_value(-0.0)
        assert math.copysign(1.0, result) == -1.0  # type: ignore[arg-type]

    def test_fl_03_positive_infinity_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(float("inf"))

    def test_fl_04_negative_infinity_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(float("-inf"))

    def test_fl_05_nan_rejected(self) -> None:
        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(float("nan"))


class TestNoStringificationFallback:
    def test_codec_never_calls_str_or_repr_as_a_fallback(self) -> None:
        class Loud:
            def __str__(self) -> str:
                raise AssertionError("str() must never be called during validation")

            def __repr__(self) -> str:
                raise AssertionError("repr() must never be called during validation")

        with pytest.raises(UnsupportedPersistedValue):
            as_persisted_value(Loud())


class TestImportSideEffects:
    def test_codec_import_has_no_side_effects(self) -> None:
        assert_fresh_import_has_no_side_effects(
            "memory.codec",
            patch_targets=("uuid.uuid4", "time.time", "time.monotonic"),
        )


class TestCanonicalEncoding:
    def test_cd_07_unicode_spellings_not_normalized(self) -> None:
        import unicodedata

        # NFC form (single codepoint U+00E9)
        composed = "é"  # e + combining acute
        composed = unicodedata.normalize("NFC", composed)
        # NFD form (e + combining acute, two codepoints)
        decomposed = unicodedata.normalize("NFD", "é")  # Starting from single codepoint
        assert composed != decomposed
        assert decode_persisted_value(encode_persisted_value(composed)) == composed
        assert decode_persisted_value(encode_persisted_value(decomposed)) == decomposed
        assert encode_persisted_value(composed) != encode_persisted_value(decomposed)

    def test_cd_10_mapping_key_order_yields_identical_bytes(self) -> None:
        first = encode_persisted_value(MappingProxyType({"a": 1, "b": 2}))
        second = encode_persisted_value(MappingProxyType({"b": 2, "a": 1}))
        assert first == second

    def test_round_trips_every_scalar_kind(self) -> None:
        for value in (None, True, False, 0, -1, 2**300, 1.5, "s", b"\x00\x01"):
            assert decode_persisted_value(encode_persisted_value(value)) == value

    def test_round_trips_nested_containers(self) -> None:
        value = (1, MappingProxyType({"x": (True, None, "y")}), b"z")
        assert decode_persisted_value(encode_persisted_value(value)) == value

    def test_fl_02_negative_zero_round_trips_with_sign(self) -> None:
        import math

        decoded = decode_persisted_value(encode_persisted_value(-0.0))
        assert isinstance(decoded, float)
        assert math.copysign(1.0, decoded) == -1.0

    def test_fl_07_repeated_encoding_is_byte_identical(self) -> None:
        value = (1, "x", 2.5, MappingProxyType({"k": True}))
        assert encode_persisted_value(value) == encode_persisted_value(value)

    def test_cd_04_large_int_round_trips_exactly(self) -> None:
        huge = 2**256 + 12345
        assert decode_persisted_value(encode_persisted_value(huge)) == huge

    def test_unknown_codec_version_fails_loudly(self) -> None:
        import json

        malformed = json.dumps(["memory.persisted_value", 999, ["none"]]).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

    def test_malformed_envelope_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            decode_persisted_value(b"not json at all")

    def test_encode_never_calls_repr_as_fallback(self) -> None:
        """Regression test: _encode_node must not call repr() on unvalidated values."""

        class Loud:
            def __repr__(self) -> str:
                raise AssertionError("repr() must never be called in _encode_node fallback")

        with pytest.raises(TypeError):
            encode_persisted_value(Loud())  # type: ignore[arg-type]

    def test_decode_str_payload_must_be_string(self) -> None:
        """Regression test: ["str", 42] must raise ValueError, not silently return 42."""
        import json

        malformed = json.dumps(["memory.persisted_value", 1, ["str", 42]]).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

    def test_decode_map_key_must_be_string(self) -> None:
        """Regression test: map with non-string key must raise ValueError."""
        import json

        malformed = json.dumps(
            ["memory.persisted_value", 1, ["map", [[42, ["none"]]]]]
        ).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

    def test_decode_bool_missing_payload_raises_valueerror(self) -> None:
        """Regression test: ["bool"] must raise ValueError, not IndexError."""
        import json

        malformed = json.dumps(["memory.persisted_value", 1, ["bool"]]).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

    def test_decode_bool_non_bool_payload_raises_valueerror(self) -> None:
        """Regression test: ["bool", "not a bool"] must raise ValueError, not silently coerce."""
        import json

        malformed = json.dumps(
            ["memory.persisted_value", 1, ["bool", "not a bool"]]
        ).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

    def test_decode_map_entry_must_be_list(self) -> None:
        """Regression: map entry not a list must raise ValueError, not TypeError.

        Tests isinstance() is used (not cast()) for runtime validation.
        """
        import json

        # Test with int entry
        malformed = json.dumps(["memory.persisted_value", 1, ["map", [42]]]).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

        # Test with None entry
        malformed = json.dumps(
            ["memory.persisted_value", 1, ["map", [None]]]
        ).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)

        # Test with dict entry
        malformed = json.dumps(
            ["memory.persisted_value", 1, ["map", [{"a": 1, "b": 2}]]]
        ).encode("utf-8")
        with pytest.raises(ValueError):
            decode_persisted_value(malformed)


class TestCorePrimitiveCodecs:
    def test_cp_01_kind_round_trips(self) -> None:
        kind = Kind("memory.test.subject")
        assert decode_kind(encode_kind(kind)) == kind

    def test_cp_02_id_round_trips(self) -> None:
        value = Id(Kind("memory.test.subject"), "s1")
        assert decode_id(encode_id(value)) == value

    def test_cp_03_namespace_round_trips(self) -> None:
        namespace = Namespace(("finance", "checking"))
        assert decode_namespace(encode_namespace(namespace)) == namespace

    def test_cp_04_ref_without_namespace_round_trips(self) -> None:
        ref = Ref(id=Id(Kind("memory.test.subject"), "s1"))
        decoded = decode_ref(encode_ref(ref))
        assert decoded == ref
        assert decoded.namespace is None

    def test_cp_05_ref_with_namespace_preserves_it(self) -> None:
        ref = Ref(
            id=Id(Kind("memory.test.subject"), "s1"),
            namespace=Namespace(("finance", "checking")),
        )
        decoded = decode_ref(encode_ref(ref))
        assert decoded == ref
        assert decoded.namespace == Namespace(("finance", "checking"))

    def test_cp_06_wall_instant_persists_canonical_utc(self) -> None:
        instant = WallInstant(datetime(2024, 1, 1, 12, 0, tzinfo=UTC))
        assert decode_wall_instant(encode_wall_instant(instant)) == instant

    def test_cp_07_duration_zero_round_trips(self) -> None:
        duration = Duration(0)
        assert decode_duration(encode_duration(duration)) == duration

    def test_cp_08_very_large_duration_round_trips(self) -> None:
        duration = Duration(2**200)
        assert decode_duration(encode_duration(duration)) == duration

    def test_cp_09_context_with_only_as_of_round_trips(self) -> None:
        context = Context(as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)))
        assert decode_context(encode_context(context)) == context

    def test_cp_10_context_with_every_supported_field_round_trips(self) -> None:
        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)),
            namespace=Namespace(("finance",)),
            scope="checking",
            environment="prod",
            source="bank-api",
            authority="user",
            version="1",
            units="usd",
            metadata={"note": "test"},
        )
        assert decode_context(encode_context(context)) == context

    def test_cp_11_context_metadata_order_yields_identical_bytes(self) -> None:
        base = WallInstant(datetime(2024, 1, 1, tzinfo=UTC))
        first = Context(as_of=base, metadata={"a": 1, "b": 2})
        second = Context(as_of=base, metadata={"b": 2, "a": 1})
        assert encode_context(first) == encode_context(second)

    def test_cp_12_context_unsupported_object_fails_with_field_path(self) -> None:
        class Exotic:
            pass

        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)), scope=Exotic()
        )
        with pytest.raises(UnsupportedPersistedValue) as exc_info:
            encode_context(context)
        assert exc_info.value.path == ("scope",)

    def test_cd_18_context_scope_as_id_is_rejected_in_v0(self) -> None:
        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)),
            scope=Id(Kind("memory.test.subject"), "s1"),
        )
        with pytest.raises(UnsupportedPersistedValue):
            encode_context(context)

    def test_cp_13_decode_malformed_kind_fails_loudly(self) -> None:
        with pytest.raises(ValueError):
            decode_kind(b"not an envelope")

    def test_cp_14_decode_unknown_tag_fails_loudly(self) -> None:
        malformed = _envelope("memory.not_a_kind", 1, "x")
        with pytest.raises(ValueError):
            decode_kind(malformed)

    def test_cp_15_encode_decode_encode_is_stable(self) -> None:
        context = Context(
            as_of=WallInstant(datetime(2024, 1, 1, tzinfo=UTC)),
            metadata={"a": 1, "b": 2},
        )
        once = encode_context(context)
        twice = encode_context(decode_context(once))
        assert once == twice


class TestCorePrimitiveCodecsMalformedPayloads:
    """Regression tests: a well-formed envelope (right tag/version) whose
    payload has the wrong shape must raise ValueError, never an incidental
    TypeError/IndexError/AttributeError, mirroring the discipline already
    established for _decode_node() in Task 3.
    """

    def test_decode_kind_non_string_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.kind", 1, 42)
        with pytest.raises(ValueError):
            decode_kind(malformed)

    def test_decode_kind_null_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.kind", 1, None)
        with pytest.raises(ValueError):
            decode_kind(malformed)

    def test_decode_id_non_list_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.id", 1, "not a list")
        with pytest.raises(ValueError):
            decode_id(malformed)

    def test_decode_id_wrong_length_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.id", 1, ["memory.test.subject"])
        with pytest.raises(ValueError):
            decode_id(malformed)

    def test_decode_id_non_string_element_raises_valueerror(self) -> None:
        malformed = _envelope("memory.id", 1, [42, "s1"])
        with pytest.raises(ValueError):
            decode_id(malformed)

    def test_decode_namespace_non_list_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.namespace", 1, "finance")
        with pytest.raises(ValueError):
            decode_namespace(malformed)

    def test_decode_namespace_non_string_segment_raises_valueerror(self) -> None:
        malformed = _envelope("memory.namespace", 1, [1, 2])
        with pytest.raises(ValueError):
            decode_namespace(malformed)

    def test_decode_ref_wrong_shape_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.ref", 1, 42)
        with pytest.raises(ValueError):
            decode_ref(malformed)

    def test_decode_ref_malformed_inner_id_raises_valueerror(self) -> None:
        malformed = _envelope("memory.ref", 1, [["only_kind"], None])
        with pytest.raises(ValueError):
            decode_ref(malformed)

    def test_decode_ref_malformed_namespace_raises_valueerror(self) -> None:
        malformed = _envelope(
            "memory.ref", 1, [["memory.test.subject", "s1"], "not a list"]
        )
        with pytest.raises(ValueError):
            decode_ref(malformed)

    def test_decode_wall_instant_non_string_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.wall_instant", 1, 12345)
        with pytest.raises(ValueError):
            decode_wall_instant(malformed)

    def test_decode_wall_instant_unparsable_string_raises_valueerror(self) -> None:
        malformed = _envelope("memory.wall_instant", 1, "not a timestamp")
        with pytest.raises(ValueError):
            decode_wall_instant(malformed)

    def test_decode_duration_non_string_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.duration", 1, 42)
        with pytest.raises(ValueError):
            decode_duration(malformed)

    def test_decode_duration_non_numeric_string_raises_valueerror(self) -> None:
        malformed = _envelope("memory.duration", 1, "not a number")
        with pytest.raises(ValueError):
            decode_duration(malformed)

    def test_decode_context_non_dict_payload_raises_valueerror(self) -> None:
        malformed = _envelope("memory.context", 1, ["not", "a", "dict"])
        with pytest.raises(ValueError):
            decode_context(malformed)

    def test_decode_context_missing_field_raises_valueerror(self) -> None:
        malformed = _envelope("memory.context", 1, {"as_of": "2024-01-01T00:00:00+00:00"})
        with pytest.raises(ValueError):
            decode_context(malformed)

    def test_decode_context_malformed_namespace_raises_valueerror(self) -> None:
        malformed = _envelope(
            "memory.context",
            1,
            {
                "as_of": "2024-01-01T00:00:00+00:00",
                "namespace": "not a list",
                "scope": None,
                "environment": None,
                "source": None,
                "authority": None,
                "version": None,
                "units": None,
                "metadata": None,
            },
        )
        with pytest.raises(ValueError):
            decode_context(malformed)

    def test_decode_context_non_mapping_metadata_raises_valueerror(self) -> None:
        """A well-formed persisted-value node that decodes to a non-Mapping
        (e.g. a plain string) must be rejected: Context.metadata requires a
        Mapping, and this must fail as ValueError, not be passed through and
        fail later with an incidental TypeError/AttributeError.
        """
        malformed = _envelope(
            "memory.context",
            1,
            {
                "as_of": "2024-01-01T00:00:00+00:00",
                "namespace": None,
                "scope": None,
                "environment": None,
                "source": None,
                "authority": None,
                "version": None,
                "units": None,
                "metadata": ["str", "not a mapping"],
            },
        )
        with pytest.raises(ValueError):
            decode_context(malformed)
