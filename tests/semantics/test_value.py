"""Propositions for core.value — see SPECIFICATION.md #2 and docs/passes/01-atoms.md."""

import pytest

from core.value import UNKNOWN, Kind, Known, Maybe, Unknown


class TestKind:
    def test_valid_spelling_accepted(self) -> None:
        assert Kind("core.contract").value == "core.contract"
        assert Kind("io.not_found").value == "io.not_found"
        assert Kind("a").value == "a"

    @pytest.mark.parametrize(
        "spelling",
        [
            "",
            "Core.Contract",  # uppercase
            "1core.contract",  # leading digit
            "core..contract",  # empty segment
            "core.contract.",  # trailing dot
            ".core.contract",  # leading dot
            "core contract",  # space
            "core-contract",  # hyphen
        ],
    )
    def test_invalid_spelling_rejected(self, spelling: str) -> None:
        with pytest.raises(ValueError, match="invalid Kind spelling"):
            Kind(spelling)

    def test_equality_and_hash(self) -> None:
        a = Kind("core.contract")
        b = Kind("core.contract")
        c = Kind("core.validation")
        assert a == b
        assert hash(a) == hash(b)
        assert a != c
        assert {a, b, c} == {a, c}  # usable as a set element

    def test_str(self) -> None:
        assert str(Kind("core.contract")) == "core.contract"

    def test_distinct_from_bare_string_by_type(self) -> None:
        # A Kind never silently compares equal to the raw string it wraps —
        # exactly what "one shared type, never mixed with bare strings" means.
        assert Kind("core.contract") != "core.contract"


class TestUnknown:
    def test_is_singleton(self) -> None:
        assert Unknown() is Unknown()
        assert Unknown() is UNKNOWN

    def test_not_equal_to_known_false(self) -> None:
        assert Unknown() != Known(False)
        assert Known(False) != Unknown()

    def test_not_equal_to_none(self) -> None:
        assert Unknown() != None  # noqa: E711 — deliberately testing __eq__, not identity
        assert not (Unknown() == None)  # noqa: E711

    def test_equal_to_itself(self) -> None:
        assert Unknown() == Unknown()

    def test_hashable(self) -> None:
        assert hash(Unknown()) == hash(UNKNOWN)


class TestKnown:
    def test_wraps_value(self) -> None:
        assert Known(42).value == 42

    def test_equality(self) -> None:
        assert Known(42) == Known(42)
        assert Known(42) != Known(43)

    def test_maybe_union_distinguishes_known_and_unknown(self) -> None:
        known: Maybe[int] = Known(5)
        unknown: Maybe[int] = UNKNOWN
        assert isinstance(known, Known)
        assert isinstance(unknown, Unknown)
        assert known != unknown
