"""Result: the outcome of an attempted operation — either what came out, or why it didn't.

``Ok`` and ``Err`` are separate frozen dataclasses, not a shared mutable
base, so exactly one branch's field ever exists on a given instance (law:
failure always carries structured information, never a bare bool; success
and failure are never simultaneously representable).

See SPECIFICATION.md #8 and ARCHITECTURE.md (result.py, tier 0).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class UnwrapError(Exception):
    """Raised by ``Err.unwrap()``.

    Carries the original failure value verbatim as ``.error``, whatever
    type ``E`` happens to be — ``Result[T, E]`` is generic and ``E`` need
    not be exception-shaped, so ``unwrap()`` needs one exception type to
    represent misuse of the failure branch regardless of ``E``.
    """

    def __init__(self, error: object) -> None:
        super().__init__(f"called unwrap() on an Err: {error!r}")
        self.error = error


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def map[U](self, fn: Callable[[T], U]) -> Ok[U]:
        return Ok(fn(self.value))

    def map_err(self, fn: Callable[[Any], Any]) -> Ok[T]:
        return self

    def and_then[U, E](self, fn: Callable[[T], Ok[U] | Err[E]]) -> Ok[U] | Err[E]:
        return fn(self.value)

    def unwrap(self) -> T:
        return self.value

    def unwrap_or(self, default: T) -> T:
        return self.value


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E

    def map(self, fn: Callable[[Any], Any]) -> Err[E]:
        return self

    def map_err[F](self, fn: Callable[[E], F]) -> Err[F]:
        return Err(fn(self.error))

    def and_then(self, fn: Callable[[Any], Any]) -> Err[E]:
        return self

    def unwrap(self) -> Any:
        raise UnwrapError(self.error)

    def unwrap_or[T](self, default: T) -> T:
        return default


type Result[T, E] = Ok[T] | Err[E]
