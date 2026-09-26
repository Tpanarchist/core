"""Read-only Coinbase Advanced Trade evidence preview.

The credential is resolved only when explicitly requested. No order, transfer,
withdrawal, or other mutating SDK method is exposed by this adapter.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib import import_module
from io import StringIO
from pathlib import Path
from typing import Protocol, cast


class CoinbaseReadError(Exception):
    """A safe, credential-free error for the local review surface."""


class _Response(Protocol):
    def to_dict(self) -> dict[str, object]: ...


class _ReadClient(Protocol):
    def get_api_key_permissions(self) -> _Response: ...
    def get_accounts(self, **kwargs: object) -> _Response: ...
    def get_fills(self, **kwargs: object) -> _Response: ...


@dataclass(frozen=True, slots=True)
class CoinbaseBalance:
    account_id: str
    name: str
    currency: str
    available: Decimal
    hold: Decimal


@dataclass(frozen=True, slots=True)
class CoinbaseFill:
    entry_id: str
    product_id: str
    trade_time: str
    price: Decimal
    size: Decimal
    commission: Decimal


@dataclass(frozen=True, slots=True)
class CoinbasePreview:
    balances: tuple[CoinbaseBalance, ...]
    fills: tuple[CoinbaseFill, ...]
    fills_complete: bool


def load_credential(key_file: Path | None = None) -> StringIO | Path:
    value = str(key_file) if key_file is not None else os.environ.get("core_read")
    if not value and os.name == "nt":
        # User variables created after this process started are absent from
        # os.environ until restart. Read only the intended registry value.
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry:
                registered, _ = winreg.QueryValueEx(registry, "core_read")
            value = registered if isinstance(registered, str) else None
        except OSError:
            pass
    if not value:
        raise CoinbaseReadError("core_read is unavailable to this process")
    if value.lstrip().startswith("{"):
        try:
            payload = json.loads(value)
        except ValueError as exc:
            raise CoinbaseReadError("core_read is not valid Coinbase key JSON") from exc
        if not _valid_key_payload(payload):
            raise CoinbaseReadError("core_read lacks Coinbase key fields")
        return StringIO(value)
    path = Path(value)
    if not path.is_file():
        raise CoinbaseReadError("core_read does not point to a readable key file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CoinbaseReadError("core_read does not point to valid key JSON") from exc
    if not _valid_key_payload(payload):
        raise CoinbaseReadError("core_read key file lacks Coinbase key fields")
    return path


def _valid_key_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    fields = cast(dict[str, object], payload)
    return all(
        isinstance(fields.get(field), str) and bool(fields[field])
        for field in ("name", "privateKey")
    )


def _client(key_file: Path | None = None) -> _ReadClient:
    key = load_credential(key_file)
    try:
        client_type = cast(Callable[..., object], import_module("coinbase.rest").RESTClient)
    except (ImportError, AttributeError) as exc:
        raise CoinbaseReadError("Install the finance extra to enable Coinbase") from exc
    # The official SDK accepts a path or file-like object for downloaded JSON.
    try:
        return cast(
            _ReadClient,
            client_type(key_file=str(key) if isinstance(key, Path) else key, timeout=10),
        )
    except Exception as exc:
        raise CoinbaseReadError("Coinbase key could not be loaded") from exc


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in cast(dict[object, object], value)
    ):
        raise CoinbaseReadError(f"Coinbase returned malformed {label}")
    return cast(dict[str, object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CoinbaseReadError(f"Coinbase returned invalid {label}")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise CoinbaseReadError(f"Coinbase returned invalid {label}")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise CoinbaseReadError(f"Coinbase returned invalid {label}") from exc
    if not amount.is_finite():
        raise CoinbaseReadError(f"Coinbase returned invalid {label}")
    return amount


def _page(client: _ReadClient, kind: str, cursor: str | None) -> dict[str, object]:
    kwargs: dict[str, object] = {"limit": 250 if kind == "accounts" else 100}
    if cursor is not None:
        kwargs["cursor"] = cursor
    try:
        response = (
            client.get_accounts(**kwargs) if kind == "accounts" else client.get_fills(**kwargs)
        )
        return _mapping(response.to_dict(), kind)
    except CoinbaseReadError:
        raise
    except Exception as exc:
        # SDK errors can include HTTP payloads; never echo them near credentials.
        raise CoinbaseReadError(f"Coinbase {kind} read failed ({type(exc).__name__})") from exc


def _balance(value: object) -> CoinbaseBalance:
    item = _mapping(value, "account")
    available = _mapping(item.get("available_balance"), "available balance")
    hold = _mapping(item.get("hold"), "hold")
    currency = _text(item.get("currency"), "currency")
    if available.get("currency") != currency or hold.get("currency") != currency:
        raise CoinbaseReadError("Coinbase account balance currency mismatch")
    return CoinbaseBalance(
        _text(item.get("uuid"), "account ID"),
        _text(item.get("name"), "account name"),
        currency,
        _decimal(available.get("value"), "available balance"),
        _decimal(hold.get("value"), "hold"),
    )


def _fill(value: object) -> CoinbaseFill:
    item = _mapping(value, "fill")
    return CoinbaseFill(
        _text(item.get("entry_id"), "fill ID"),
        _text(item.get("product_id"), "product ID"),
        _text(item.get("trade_time"), "trade time"),
        _decimal(item.get("price"), "fill price"),
        _decimal(item.get("size"), "fill size"),
        _decimal(item.get("commission"), "fill commission"),
    )


def preview(
    client: _ReadClient | None = None,
    *,
    key_file: Path | None = None,
    max_fill_pages: int = 5,
) -> CoinbasePreview:
    """Read accounts and recent fills without changing any finance record."""
    if max_fill_pages < 1:
        raise ValueError("At least one fill page is required")
    source = client if client is not None else _client(key_file)
    try:
        permissions = _mapping(source.get_api_key_permissions().to_dict(), "key permissions")
    except CoinbaseReadError:
        raise
    except Exception as exc:
        raise CoinbaseReadError("Coinbase key permission check failed") from exc
    if permissions.get("can_view") is not True:
        raise CoinbaseReadError("Coinbase key has no view permission")
    if permissions.get("can_trade") is not False or permissions.get("can_transfer") is not False:
        raise CoinbaseReadError("Coinbase key must have view-only permissions")
    balances: list[CoinbaseBalance] = []
    fills: list[CoinbaseFill] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(100):
        page = _page(source, "accounts", cursor)
        items = page.get("accounts")
        if not isinstance(items, list) or not isinstance(page.get("has_next"), bool):
            raise CoinbaseReadError("Coinbase returned malformed accounts")
        balances.extend(_balance(item) for item in cast(list[object], items))
        if page.get("has_next") is not True:
            break
        cursor = _text(page.get("cursor"), "account cursor")
        if cursor in seen:
            raise CoinbaseReadError("Coinbase repeated an account cursor")
        seen.add(cursor)
    else:
        raise CoinbaseReadError("Coinbase account pagination exceeded safety limit")
    cursor = None
    seen.clear()
    complete = False
    for _ in range(max_fill_pages):
        page = _page(source, "fills", cursor)
        items = page.get("fills")
        if not isinstance(items, list):
            raise CoinbaseReadError("Coinbase returned malformed fills")
        if page.get("proof_token_required") is True:
            break
        fills.extend(_fill(item) for item in cast(list[object], items))
        next_cursor = page.get("cursor")
        if not next_cursor:
            complete = True
            break
        cursor = _text(next_cursor, "fill cursor")
        if cursor in seen:
            raise CoinbaseReadError("Coinbase repeated a fill cursor")
        seen.add(cursor)
    return CoinbasePreview(tuple(balances), tuple(fills), complete)
