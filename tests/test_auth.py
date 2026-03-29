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


class _FakeClient:
    def __init__(self, *args, should_fail: bool = False, calls: list[str] | None = None, **kwargs):
        self.pinterest = _FakePinterest(
            should_fail=should_fail,
            calls=calls if calls is not None else [],
        )

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
        assert await verifier.verify("pb_test_123") is True
        assert await verifier.verify("pb_test_123") is True

    asyncio.run(run())

    assert calls == ["list_accounts"]
