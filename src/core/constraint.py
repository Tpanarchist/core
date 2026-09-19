"""Constraint: require/ensure/invariant — executable conditions, and the
control-flow carrier for their violations.

See SPECIFICATION.md #14 and ARCHITECTURE.md (constraint.py, tier 5).
"""

from __future__ import annotations

from core.context import Context
from core.error import Error
from core.identity import IdSource
from core.time import Clock
from core.value import Kind

_ERROR_ID_KIND = Kind("core.error")
_CONSTRAINT_VIOLATION_KIND = Kind("core.constraint.violation")


class ContractError(Exception):
    """The Python control-flow carrier for a structured Core Error.

    Does not construct or alter the Error it carries; not an Error subclass
    — Error is information, this exception is the mechanism carrying it.
    """

    def __init__(self, error: Error) -> None:
        super().__init__(error.message)
        self.error = error


def _check(
    condition: bool,
    message: str,
    *,
    ids: IdSource,
    clock: Clock,
    context: Context | None,
    operation: str,
) -> None:
    if not message:
        raise ValueError(f"{operation}() message must not be empty")
    if condition:
        return
    error = Error(
        id=ids.new(_ERROR_ID_KIND),
        kind=_CONSTRAINT_VIOLATION_KIND,
        message=message,
        at=clock.now(),
        context=context,
        operation=operation,
        recoverable=False,
    )
    raise ContractError(error)


def require(
    condition: bool,
    message: str,
    *,
    ids: IdSource,
    clock: Clock,
    context: Context | None = None,
) -> None:
    """A precondition — the name at the call site is documentation."""
    _check(condition, message, ids=ids, clock=clock, context=context, operation="require")


def ensure(
    condition: bool,
    message: str,
    *,
    ids: IdSource,
    clock: Clock,
    context: Context | None = None,
) -> None:
    """A postcondition — identical mechanism to require(), distinct name."""
    _check(condition, message, ids=ids, clock=clock, context=context, operation="ensure")


def invariant(
    condition: bool,
    message: str,
    *,
    ids: IdSource,
    clock: Clock,
    context: Context | None = None,
) -> None:
    """A structural invariant — identical mechanism to require(), distinct name."""
    _check(condition, message, ids=ids, clock=clock, context=context, operation="invariant")
