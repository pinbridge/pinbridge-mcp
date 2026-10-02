"""Weekly assistant-request quota, enforced per tool call via the PinBridge API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class QuotaClient:
    """Calls /v1/mcp/quota and /v1/mcp/track on the PinBridge API.

    Only tool calls count. Before a tool runs, ``check_quota`` asks the API
    whether the workspace has requests left this week; once they are used up the
    tool fails with the API's upgrade message, so the assistant can show the
    person why and where to upgrade. After the tool runs, ``track_background``
    adds one to the count. The handshake, tool listing and resource reads are
    never counted or blocked.

    If the API is unreachable, quota checks are skipped (fail open) and
    tracking is best-effort. This keeps the MCP usable even if the API
    has a hiccup.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 5.0,
        *,
        app_base_url: str = "https://app.pinbridge.io",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._app_base_url = app_base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "X-Pinbridge-Source": "mcp",
            "Content-Type": "application/json",
        }

    def _exhausted_message(self, data: dict[str, Any]) -> str:
        upgrade = data.get("upgrade")
        if isinstance(upgrade, dict) and upgrade.get("message"):
            text = str(upgrade["message"])
            if upgrade.get("remediation"):
                text += f" {upgrade['remediation']}"
            return text
        # API older than 1.46: build the prompt from the counts.
        used = data.get("requests_used", "?")
        limit = data.get("requests_limit", "?")
        resets_at = data.get("resets_at", "?")
        return (
            f"This workspace has used all {used}/{limit} assistant requests in its plan "
            f"this week. Upgrade for more: {self._app_base_url}/pricing?"
            f"upgrade=assistant_requests_weekly. Or wait until the count resets on "
            f"Monday {resets_at}."
        )

    async def check_quota(self, api_key: str) -> tuple[bool, str | None]:
        """Check whether the workspace has quota remaining.

        Returns (True, None) if quota is available or check fails open.
        Returns (False, message) once it is used up; the message carries the
        upgrade link.
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
                    return False, self._exhausted_message(data)
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
