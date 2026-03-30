"""Weekly per-API-key request quota enforcement."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any


class QuotaTracker:
    """In-memory weekly request quota tracker.

    Counts requests per (api_key, ISO-week) pair. The counter resets
    automatically each Monday at 00:00 UTC — no explicit flush needed,
    since the week key changes and old entries are simply never matched.

    Limits are looked up by plan slug. A limit of 0 means unlimited.
    """

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _week_key() -> str:
        """Return the current ISO week key, e.g. '2026-W14'."""
        now = datetime.now(timezone.utc)
        return f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    @staticmethod
    def _next_reset() -> datetime:
        """Return the next Monday 00:00 UTC."""
        now = datetime.now(timezone.utc)
        days_ahead = (7 - now.weekday()) % 7 or 7
        return (now + timedelta(days=days_ahead)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    async def check_and_increment(
        self,
        api_key: str,
        plan: str,
        limits: dict[str, int],
    ) -> tuple[bool, str | None]:
        """Check quota and increment the counter if allowed.

        Returns (True, None) if the request is within quota.
        Returns (False, reason) if the weekly limit is exceeded.

        A plan limit of 0 means unlimited — the check is skipped entirely.
        """
        limit = limits.get(plan, 0)
        if limit == 0:
            return True, None

        week_key = self._week_key()
        cache_key = (api_key, week_key)

        async with self._lock:
            count = self._counts.get(cache_key, 0)
            if count >= limit:
                reset_at = self._next_reset().strftime("%Y-%m-%dT%H:%M:%SZ")
                return False, (
                    f"Weekly request quota exceeded ({count}/{limit} requests used). "
                    f"Quota resets Monday {reset_at}."
                )
            self._counts[cache_key] = count + 1

        return True, None

    def usage(self, api_key: str) -> dict[str, Any]:
        """Return current week usage for an API key (for diagnostics)."""
        week_key = self._week_key()
        count = self._counts.get((api_key, week_key), 0)
        return {
            "week": week_key,
            "requests_used": count,
            "resets_at": self._next_reset().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
