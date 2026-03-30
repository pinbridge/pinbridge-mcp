from __future__ import annotations

import asyncio

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from pinbridge_mcp.auth import (
    APIKeyPassthroughMiddleware,
    PinBridgeAPIKeyVerifier,
    extract_bearer_token,
    get_current_api_key,
)
from pinbridge_mcp.config import Settings


class _FakePinterest:
    def __init__(self, *, should_fail: bool, calls: list[str]) -> None:
        self._should_fail = should_fail
        self._calls = calls

    async def list_accounts(self) -> list[dict]:
        self._calls.append("list_accounts")
        if self._should_fail:
            from pinbridge_sdk.errors import AuthenticationError

            raise AuthenticationError(status_code=401, message="bad key")
        return []


class _FakeBillingStatus:
    plan: str = "free"


class _FakeBilling:
    async def status(self) -> _FakeBillingStatus:
        return _FakeBillingStatus()


class _FakeClient:
    def __init__(self, *args, should_fail: bool = False, calls: list[str] | None = None, **kwargs):
        self.pinterest = _FakePinterest(
            should_fail=should_fail,
            calls=calls if calls is not None else [],
        )
        self.billing = _FakeBilling()

    async def aclose(self) -> None:
        return None


def test_extract_bearer_token() -> None:
    assert extract_bearer_token("Bearer abc123") == "abc123"
    assert extract_bearer_token("bearer abc123") == "abc123"
    assert extract_bearer_token("Token abc123") is None
    assert extract_bearer_token(None) is None


async def _echo_api_key(_: object) -> JSONResponse:
    return JSONResponse({"api_key": get_current_api_key()})


def test_http_middleware_binds_authorization_header() -> None:
    app = Starlette(routes=[Route("/", endpoint=_echo_api_key)])
    settings = Settings(verify_incoming_api_keys=False)
    app.add_middleware(
        APIKeyPassthroughMiddleware,
        settings=settings,
        verifier=PinBridgeAPIKeyVerifier(settings),
    )

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/", headers={"Authorization": "Bearer pb_test_123"})
        assert response.status_code == 200
        assert response.json() == {"api_key": "pb_test_123"}

    asyncio.run(run())


def test_verifier_caches_successful_validation() -> None:
    calls: list[str] = []

    class FakeClient(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, calls=calls, **kwargs)

    settings = Settings(auth_cache_ttl_seconds=300)
    verifier = PinBridgeAPIKeyVerifier(settings, client_factory=FakeClient)

    async def run() -> None:
        is_valid, reason, plan = await verifier.verify("pb_test_123")
        assert is_valid is True
        assert reason is None
        assert plan == "free"
        is_valid2, reason2, plan2 = await verifier.verify("pb_test_123")
        assert is_valid2 is True
        assert reason2 is None
        assert plan2 == "free"

    asyncio.run(run())

    assert calls == ["list_accounts"]


def test_quota_client_passes_when_api_returns_not_exhausted() -> None:
    """QuotaClient.check_quota returns (True, None) when API says quota is available."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from pinbridge_mcp.quota import QuotaClient

    client = QuotaClient(base_url="https://api.pinbridge.io")

    async def run() -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "quota_exhausted": False,
            "requests_used": 5,
            "requests_limit": 100,
            "resets_at": "2026-04-06T00:00:00Z",
        }
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("pinbridge_mcp.quota.httpx.AsyncClient", return_value=mock_client):
            ok, reason = await client.check_quota("pb_test_key")

        assert ok is True
        assert reason is None

    asyncio.run(run())


def test_quota_client_blocks_when_api_returns_exhausted() -> None:
    """QuotaClient.check_quota returns (False, reason) when quota_exhausted=true."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from pinbridge_mcp.quota import QuotaClient

    client = QuotaClient(base_url="https://api.pinbridge.io")

    async def run() -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "quota_exhausted": True,
            "requests_used": 100,
            "requests_limit": 100,
            "resets_at": "2026-04-06T00:00:00Z",
        }
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("pinbridge_mcp.quota.httpx.AsyncClient", return_value=mock_client):
            ok, reason = await client.check_quota("pb_test_key")

        assert ok is False
        assert reason is not None
        assert "100/100" in reason

    asyncio.run(run())


def test_quota_client_fails_open_on_api_error() -> None:
    """QuotaClient.check_quota allows request through if API is unreachable."""
    from unittest.mock import AsyncMock, patch

    from pinbridge_mcp.quota import QuotaClient

    client = QuotaClient(base_url="https://api.pinbridge.io")

    async def run() -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        with patch("pinbridge_mcp.quota.httpx.AsyncClient", return_value=mock_client):
            ok, reason = await client.check_quota("pb_test_key")

        assert ok is True  # fail open
        assert reason is None

    asyncio.run(run())
