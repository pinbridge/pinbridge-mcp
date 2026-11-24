"""stdio start-up checks the key before serving."""

from __future__ import annotations

import asyncio

import pytest

from pinbridge_mcp import cli
from pinbridge_mcp.config import Settings


class _Verifier:
    answer: tuple[bool, str | None, str] = (True, None, "starter")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def verify(self, api_key: str) -> tuple[bool, str | None, str]:
        return self.answer


def test_stdio_startup_accepts_a_valid_key(monkeypatch) -> None:
    monkeypatch.setattr(cli, "PinBridgeAPIKeyVerifier", _Verifier)
    _Verifier.answer = (True, None, "starter")
    asyncio.run(cli._validate_stdio_startup(Settings(pinbridge_api_key="pb_local")))


def test_stdio_startup_stops_on_a_refused_key(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "PinBridgeAPIKeyVerifier", _Verifier)
    _Verifier.answer = (False, "Invalid PinBridge API key", "")
    with pytest.raises(SystemExit):
        asyncio.run(cli._validate_stdio_startup(Settings(pinbridge_api_key="pb_local")))
    assert "Invalid PinBridge API key" in capsys.readouterr().err
