"""Read-only Coinbase boundary tests use synthetic SDK responses only."""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from textual.widgets import Static

from personal_finance.adapters.coinbase import (
    CoinbaseBalance,
    CoinbaseFill,
    CoinbasePreview,
    CoinbaseReadError,
    load_credential,
    preview,
)
from personal_finance.app import FinanceApp
from personal_finance.bootstrap import open_service
from personal_finance.cli import main
from personal_finance.ui.forms import InfoScreen


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return self.payload


class _Client:
    def __init__(self, *, repeated: bool = False, can_trade: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.repeated = repeated
        self.can_trade = can_trade

    def get_api_key_permissions(self) -> _Response:
        self.calls.append(("permissions", {}))
        return _Response({"can_view": True, "can_trade": self.can_trade, "can_transfer": False})

    def get_accounts(self, **kwargs: object) -> _Response:
        self.calls.append(("accounts", kwargs))
        cursor = kwargs.get("cursor")
        return _Response(
            {
                "has_next": cursor is None or self.repeated,
                "cursor": "next",
                "accounts": [
                    {
                        "uuid": "a1" if cursor is None else "a2",
                        "name": "BTC Wallet",
                        "currency": "BTC",
                        "available_balance": {"value": "0.125", "currency": "BTC"},
                        "hold": {"value": "0.001", "currency": "BTC"},
                    }
                ],
            }
        )

    def get_fills(self, **kwargs: object) -> _Response:
        self.calls.append(("fills", kwargs))
        return _Response(
            {
                "cursor": "next" if kwargs.get("cursor") is None else "",
                "fills": [
                    {
                        "entry_id": "f1",
                        "product_id": "BTC-USD",
                        "trade_time": "2026-09-25T12:00:00Z",
                        "price": "100.25",
                        "size": "0.125",
                        "commission": "0.01",
                    }
                ],
            }
        )


def test_preview_reads_only_paginated_accounts_and_fills() -> None:
    client = _Client()
    result = preview(client, max_fill_pages=2)
    assert len(result.balances) == 2
    assert result.balances[0].available.as_tuple().exponent == -3
    assert len(result.fills) == 2
    assert result.fills_complete
    assert client.calls == [
        ("permissions", {}),
        ("accounts", {"limit": 250}),
        ("accounts", {"limit": 250, "cursor": "next"}),
        ("fills", {"limit": 100}),
        ("fills", {"limit": 100, "cursor": "next"}),
    ]


def test_preview_marks_page_limit_incomplete() -> None:
    result = preview(_Client(), max_fill_pages=1)
    assert len(result.fills) == 1
    assert not result.fills_complete


def test_preview_rejects_repeated_account_cursor() -> None:
    with pytest.raises(CoinbaseReadError, match="repeated an account cursor"):
        preview(_Client(repeated=True))


def test_preview_rejects_key_with_trade_permission_before_account_access() -> None:
    client = _Client(can_trade=True)
    with pytest.raises(CoinbaseReadError, match="view-only"):
        preview(client)
    assert client.calls == [("permissions", {})]


def test_credential_validation_does_not_echo_secret(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.delenv("core_read", raising=False)
    if os.name == "nt":
        # load_credential() falls back to reading HKCU\Environment directly
        # (user variables set after this process started are otherwise
        # invisible via os.environ until restart). A developer machine with
        # a real core_read already registered would otherwise make this
        # "unavailable" assertion below fail for a reason that has nothing
        # to do with the code under test -- force the registry lookup to
        # behave like a clean machine regardless of host state.
        import winreg

        def _missing(*_args: object, **_kwargs: object) -> tuple[str, int]:
            raise FileNotFoundError("simulated: core_read not registered")

        monkeypatch.setattr(winreg, "QueryValueEx", _missing)
    with pytest.raises(CoinbaseReadError, match="unavailable"):
        load_credential()
    secret = "sample-secret-not-for-output"
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"name": "key-name", "privateKey": secret}))
    assert load_credential(key) == key
    monkeypatch.setenv("core_read", key.read_text())
    inline = load_credential()
    assert isinstance(inline, StringIO)
    assert secret in inline.read()
    monkeypatch.setenv("core_read", secret)
    with pytest.raises(CoinbaseReadError) as info:
        load_credential()
    assert secret not in str(info.value)


def test_cli_rejects_live_coinbase_in_demo(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("core_read", "secret")
    assert main(["--demo", "coinbase-preview"]) == 1
    assert "synthetic demo" in capsys.readouterr().err


def test_tui_coinbase_preview_keeps_ledger_unchanged(tmp_path: Path) -> None:
    service = open_service(tmp_path)
    app = FinanceApp(service)
    sample = CoinbasePreview(
        (CoinbaseBalance("a1", "Synthetic BTC", "BTC", Decimal("1.25"), Decimal("0")),),
        (
            CoinbaseFill(
                "f1",
                "BTC-USD",
                "2026-09-25T12:00:00Z",
                Decimal("100"),
                Decimal("0.25"),
                Decimal("0.01"),
            ),
        ),
        False,
    )

    async def scenario() -> None:
        with patch("personal_finance.app.coinbase_preview", return_value=sample):
            async with app.run_test(size=(120, 40)) as pilot:
                await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
                await pilot.press("k")
                await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
                await pilot.pause()
                assert isinstance(app.screen, InfoScreen)
                text = str(app.screen.query_one("#info-content", Static).content)
                assert "Synthetic BTC" in text
                assert "partial" in text
                assert service.entries() == ()

    asyncio.run(scenario())
