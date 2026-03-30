"""DB-backed MCP quota enforcement via the PinBridge API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class QuotaClient:
    """Calls /v1/mcp/quota and /v1/mcp/track on the PinBridge API.

    check_and_track() is called on each request:
      - GET /v1/mcp/quota  → if quota_exhausted, block with 429
      - POST /v1/mcp/track → fire-and-forget after request proceeds

    If the API is unreachable, quota checks are skipped (fail open) and
    tracking is best-effort. This keeps the MCP usable even if the API
    has a hiccup.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "X-Pinbridge-Source": "mcp",
            "Content-Type": "application/json",
        }

    async def check_quota(self, api_key: str) -> tuple[bool, str | None]:
        """Check whether the workspace has quota remaining.

        Returns (True, None) if quota is available or check fails open.
        Returns (False, reason) if quota is exhausted.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/v1/mcp/quota",
                    headers=self._headers(api_key),
                )
            if resp.status_code == 200:
                data: dict[str, Any] = resp.json()
                if data.get("quota_exhausted"):
                    used = data.get("requests_used", "?")
                    limit = data.get("requests_limit", "?")
                    resets_at = data.get("resets_at", "?")
                    return False, (
                        f"Weekly MCP request quota exceeded ({used}/{limit} requests used). "
                        f"Quota resets Monday {resets_at}."
                    )
            else:
                logger.warning("mcp_quota_check_unexpected_status status=%s", resp.status_code)
        except Exception as exc:
            logger.warning("mcp_quota_check_failed_open error=%s", exc)

        return True, None

    async def track(self, api_key: str) -> None:
        """Increment MCP request counter. Fire-and-forget, errors are logged only."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/mcp/track",
                    headers=self._headers(api_key),
                )
            if resp.status_code != 200:
                logger.warning("mcp_track_unexpected_status status=%s", resp.status_code)
        except Exception as exc:
            logger.warning("mcp_track_failed error=%s", exc)

    def track_background(self, api_key: str) -> None:
        """Schedule track() as a background task (non-blocking)."""
        asyncio.ensure_future(self.track(api_key))
