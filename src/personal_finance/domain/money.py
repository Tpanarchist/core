"""Exact signed minor units with a deliberately bounded currency policy.

Supported exponents are application policy, not inferred from symbols or locale.
The symmetric SQLite-compatible range permits every amount to be reversed.
Conversion never rounds; a trailing zero beyond the exponent loses no value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

CURRENCY_EXPONENTS = MappingProxyType(
    {
        "USD": 2,
        "EUR": 2,
        "GBP": 2,
        "CAD": 2,
        "AUD": 2,
        "CHF": 2,
        "JPY": 0,
        "KWD": 3,
        "BHD": 3,
    }
)
MAX_MINOR = 2**63 - 1


def currency_exponent(currency: str) -> int:
    """Reject unsupported and noncanonical currency codes."""
    if type(currency) is not str or currency not in CURRENCY_EXPONENTS:
        raise ValueError(f"Unsupported currency: {currency!r}")
    return CURRENCY_EXPONENTS[currency]


@dataclass(frozen=True, slots=True)
class Money:
    """An amount, never an observation, authority, or implicit exchange rate."""

    minor: int
    currency: str

    def __post_init__(self) -> None:
        if type(self.minor) is not int:
            raise TypeError("Money minor units must be an integer, never a float or bool")
        currency_exponent(self.currency)
        if not -MAX_MINOR <= self.minor <= MAX_MINOR:
            raise ValueError("Money exceeds the reversible signed 64-bit minor-unit range")

    @classmethod
    def from_decimal(cls, value: str | Decimal, currency: str) -> Money:
        """Convert exactly without depending on the process Decimal context."""
        exponent = currency_exponent(currency)
        if type(value) is not str and type(value) is not Decimal:
            raise TypeError("Money requires a decimal string or Decimal")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Money requires a valid decimal amount") from exc
        if not amount.is_finite():
            raise ValueError("Money requires a finite decimal amount")
        parts = amount.as_tuple()
        if not isinstance(parts.exponent, int):
            raise ValueError("Money requires a finite decimal exponent")
        digits = list(parts.digits)
        scale = parts.exponent + exponent
        while digits and digits[-1] == 0 and scale < 0:
            digits.pop()
            scale += 1
        if not digits or not any(digits):
            return cls(0, currency)
        if scale < 0:
            raise ValueError(f"Amount has excess fractional precision for {currency}")
        if len(digits) + scale > 19:
            raise ValueError("Money exceeds the reversible signed 64-bit minor-unit range")
        coefficient = 0
        for digit in digits:
            coefficient = coefficient * 10 + digit
        minor = coefficient * 10**scale
        return cls(-minor if parts.sign else minor, currency)

    def decimal_string(self) -> str:
        """Return a canonical decimal amount without grouping or currency."""
        exponent = currency_exponent(self.currency)
        magnitude = abs(self.minor)
        sign = "-" if self.minor < 0 else ""
        if exponent == 0:
            return f"{sign}{magnitude}"
        whole, fraction = divmod(magnitude, 10**exponent)
        return f"{sign}{whole}.{fraction:0{exponent}d}"

    def format(self) -> str:
        return f"{self.decimal_string()} {self.currency}"

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError("Cannot add money in different currencies")
        return Money(self.minor + other.minor, self.currency)
