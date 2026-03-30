"""HTTP auth helpers for bearer API-key passthrough."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pinbridge_sdk import AsyncPinbridgeClient
from pinbridge_sdk.errors import AuthenticationError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .config import Settings

_current_api_key: ContextVar[str | None] = ContextVar("pinbridge_mcp_api_key", default=None)


def extract_bearer_token(authorization: str | None) -> str | None:
    """Extract a bearer token from an Authorization header value."""
    if not isinstance(authorization, str):
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None

    cleaned = token.strip()
    return cleaned or None


def get_current_api_key() -> str | None:
    """Return the API key bound to the current HTTP request context."""
    return _current_api_key.get()


def bind_current_api_key(api_key: str) -> Token[str | None]:
    """Bind an API key to the current async context."""
    return _current_api_key.set(api_key)


def reset_current_api_key(token: Token[str | None]) -> None:
    """Reset the current async-context API key binding."""
    _current_api_key.reset(token)


async def _aclose_client(client: Any) -> None:
    close = getattr(client, "aclose", None)
    if close is not None:
        await close()


PLAN_RANK: dict[str, int] = {
    "playground": 0,
    "free": 0,
    "starter": 1,
    "growth": 2,
    "pro": 3,
    "enterprise": 4,
}


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    plan: str = "free"


class PinBridgeAPIKeyVerifier:
    """Validate incoming bearer tokens against the PinBridge API."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: type[AsyncPinbridgeClient] | Any = AsyncPinbridgeClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def verify(self, api_key: str) -> tuple[bool, str | None]:
        """Return (is_valid, rejection_reason).

        Returns (True, None) on success.
        Returns (False, reason) on auth failure or insufficient plan.
        """
        now = monotonic()
        cached = self._cache.get(api_key)
        if cached and cached.expires_at > now:
            return self._check_plan(cached.plan)

        async with self._lock:
            cached = self._cache.get(api_key)
            if cached and cached.expires_at > monotonic():
                return self._check_plan(cached.plan)

            client = self._client_factory(
                base_url=self._settings.pinbridge_base_url,
                api_key=api_key,
                user_agent="pinbridge-mcp/0.1.0 verifier",
            )
            try:
                await client.pinterest.list_accounts()
                billing = await client.billing.status()
                plan = str(getattr(billing, "plan", "free") or "free")
            except AuthenticationError:
                return False, "Invalid PinBridge API key"
            finally:
                await _aclose_client(client)

            self._cache[api_key] = _CacheEntry(
                expires_at=monotonic() + self._settings.auth_cache_ttl_seconds,
                plan=plan,
            )
            return self._check_plan(plan)

    def _check_plan(self, plan: str) -> tuple[bool, str | None]:
        min_plan = self._settings.min_plan
        if PLAN_RANK.get(plan, 0) < PLAN_RANK.get(min_plan, 0):
            return False, (
                f"Your workspace is on the '{plan}' plan. "
                f"This MCP server requires '{min_plan}' or higher."
            )
        return True, None


class APIKeyPassthroughMiddleware:
    """Require a bearer token and expose it to MCP tool handlers."""

    def __init__(
        self,
        app: Any,
        *,
        settings: Settings,
        verifier: PinBridgeAPIKeyVerifier,
    ) -> None:
        self.app = app
        self.settings = settings
        self.verifier = verifier

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if path == "/healthz" or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        api_key = (
            extract_bearer_token(headers.get("authorization"))
            or self.settings.pinbridge_api_key
        )
        if api_key is None:
            response = JSONResponse(
                {"error": "Missing Authorization: Bearer <PinBridge API key>"},
                status_code=401,
                headers={"www-authenticate": 'Bearer realm="pinbridge-mcp"'},
            )
            await response(scope, receive, send)
            return

        if self.settings.verify_incoming_api_keys:
            is_valid, reason = await self.verifier.verify(api_key)
            if not is_valid:
                plan_gate = reason is not None and "plan" in reason.lower()
                response = JSONResponse(
                    {"error": reason or "Invalid PinBridge API key"},
                    status_code=403 if plan_gate else 401,
                    headers=(
                        {}
                        if plan_gate
                        else {"www-authenticate": 'Bearer realm="pinbridge-mcp"'}
                    ),
                )
                await response(scope, receive, send)
                return

        token = bind_current_api_key(api_key)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_api_key(token)
