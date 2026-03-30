"""PinBridge SDK adapter used by MCP tools."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import uuid4

from pinbridge_sdk import AsyncPinbridgeClient
from pinbridge_sdk.errors import APIError, AuthenticationError, PinbridgeError

from .auth import get_current_api_key
from .config import Settings


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


def _normalize_terms(terms: str | list[str]) -> list[str]:
    if isinstance(terms, str):
        raw_terms = [segment.strip() for segment in terms.split(",")]
    else:
        raw_terms = [segment.strip() for segment in terms]
    return [term for term in raw_terms if term]


async def _aclose_client(client: Any) -> None:
    close = getattr(client, "aclose", None)
    if close is not None:
        await close()


class PinBridgeService:
    """Thin async wrapper around the PinBridge SDK."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: type[AsyncPinbridgeClient] | Any = AsyncPinbridgeClient,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory

    def resolve_api_key(self) -> str:
        api_key = get_current_api_key() or self.settings.pinbridge_api_key
        if api_key is None:
            raise RuntimeError(
                "No PinBridge API key available. Send Authorization: Bearer <PinBridge API key> "
                "or set PINBRIDGE_MCP_PINBRIDGE_API_KEY."
            )
        return api_key

    @asynccontextmanager
    async def client(self) -> Any:
        client = self._client_factory(
            base_url=self.settings.pinbridge_base_url,
            api_key=self.resolve_api_key(),
            user_agent="pinbridge-mcp/0.1.0",
        )
        try:
            yield client
        finally:
            await _aclose_client(client)

    async def server_info(self) -> dict[str, Any]:
        return {
            "server": "pinbridge-mcp",
            "version": "0.1.0",
            "pinbridge_base_url": self.settings.pinbridge_base_url,
            "public_base_url": self.settings.normalized_public_base_url,
            "auth_mode": "bearer_api_key_passthrough",
            "workspace_scope": "api_key",
            "streamable_http_path": self.settings.streamable_http_path,
            "write_tools_enabled": self.settings.enable_write_tools,
        }

    async def list_pinterest_accounts(self) -> list[dict[str, Any]]:
        async with self.client() as client:
            accounts = await client.pinterest.list_accounts()
        return [account.model_dump(mode="json") for account in accounts]

    async def list_boards(self, account_id: str) -> list[dict[str, Any]]:
        async with self.client() as client:
            boards = await client.pinterest.list_boards(account_id)
        return [board.model_dump(mode="json") for board in boards]

    async def list_related_terms(
        self,
        *,
        account_id: str,
        terms: str | list[str],
        exact_match: bool = False,
    ) -> dict[str, Any]:
        normalized_terms = _normalize_terms(terms)
        if not normalized_terms:
            raise ValueError("At least one non-empty term is required")

        async with self.client() as client:
            response = await client.pinterest.list_related_terms(
                account_id=account_id,
                terms=normalized_terms,
                exact_match=exact_match,
            )
        return response.model_dump(mode="json")

    async def list_pins(self, *, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        async with self.client() as client:
            pins = await client.pins.list(limit=limit, offset=offset)
        return [pin.model_dump(mode="json") for pin in pins]

    async def get_pin(self, pin_id: str) -> dict[str, Any]:
        async with self.client() as client:
            pin = await client.pins.get(pin_id)
        return pin.model_dump(mode="json")

    async def list_activity_logs(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        category: str | None = None,
        action: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if category is not None:
            params["category"] = category
        if action is not None:
            params["action"] = action
        if status is not None:
            params["status"] = status
        if resource_type is not None:
            params["resource_type"] = resource_type
        if since is not None:
            params["since"] = _parse_iso_datetime(since).isoformat()

        async with self.client() as client:
            response = await client.request("GET", "/v1/activity-logs", params=params)
        return response.json()

    async def list_webhooks(self) -> list[dict[str, Any]]:
        async with self.client() as client:
            webhooks = await client.webhooks.list()
        return [webhook.model_dump(mode="json") for webhook in webhooks]

    async def get_billing_status(self) -> dict[str, Any]:
        async with self.client() as client:
            status = await client.billing.status()
        return status.model_dump(mode="json")

    async def get_rate_meter(self, account_id: str) -> dict[str, Any]:
        async with self.client() as client:
            rate_meter = await client.rate_meter.get(account_id)
        return rate_meter.model_dump(mode="json")

    async def create_pin(
        self,
        *,
        account_id: str,
        board_id: str,
        title: str,
        image_url: str | None = None,
        asset_id: str | None = None,
        description: str | None = None,
        related_terms: list[str] | None = None,
        alt_text: str | None = None,
        dominant_color: str | None = None,
        cover_image_url: str | None = None,
        cover_image_asset_id: str | None = None,
        link_url: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "account_id": account_id,
            "board_id": board_id,
            "title": title,
            "description": description,
            "related_terms": related_terms,
            "alt_text": alt_text,
            "dominant_color": dominant_color,
            "cover_image_url": cover_image_url,
            "cover_image_asset_id": cover_image_asset_id,
            "link_url": link_url,
            "image_url": image_url,
            "asset_id": asset_id,
            "idempotency_key": idempotency_key or uuid4().hex,
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        async with self.client() as client:
            pin = await client.pins.create(payload)
        return pin.model_dump(mode="json")

    async def create_schedule(
        self,
        *,
        account_id: str,
        board_id: str,
        title: str,
        run_at: str,
        image_url: str | None = None,
        asset_id: str | None = None,
        description: str | None = None,
        link_url: str | None = None,
        cover_image_url: str | None = None,
        cover_image_asset_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "account_id": account_id,
            "board_id": board_id,
            "title": title,
            "run_at": _parse_iso_datetime(run_at).isoformat(),
        }
        if image_url is not None:
            payload["image_url"] = image_url
        if asset_id is not None:
            payload["asset_id"] = asset_id
        if description is not None:
            payload["description"] = description
        if link_url is not None:
            payload["link_url"] = link_url
        if cover_image_url is not None:
            payload["cover_image_url"] = cover_image_url
        if cover_image_asset_id is not None:
            payload["cover_image_asset_id"] = cover_image_asset_id
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key

        async with self.client() as client:
            schedule = await client.schedules.create(payload)
        return schedule.model_dump(mode="json")

    async def list_schedules(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self.client() as client:
            schedules = await client.schedules.list(limit=limit, offset=offset)
        results = [s.model_dump(mode="json") for s in schedules]
        if status is not None:
            results = [s for s in results if s.get("status") == status]
        return results

    async def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        async with self.client() as client:
            schedule = await client.schedules.get(schedule_id)
        return schedule.model_dump(mode="json")

    async def cancel_schedule(self, schedule_id: str) -> dict[str, Any]:
        async with self.client() as client:
            schedule = await client.schedules.cancel(schedule_id)
        return schedule.model_dump(mode="json")

    async def create_board(
        self,
        *,
        account_id: str,
        name: str,
        description: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"account_id": account_id, "name": name}
        if description is not None:
            payload["description"] = description
        if privacy is not None:
            payload["privacy"] = privacy

        async with self.client() as client:
            board = await client.pinterest.create_board(payload)
        return board.model_dump(mode="json")

    async def delete_board(self, board_id: str, *, account_id: str) -> dict[str, Any]:
        async with self.client() as client:
            await client.pinterest.delete_board(board_id, account_id=account_id)
        return {"deleted": True, "board_id": board_id}

    @staticmethod
    def format_error(exc: Exception) -> str:
        if isinstance(exc, AuthenticationError):
            return f"PinBridge authentication failed: {exc.message}"
        if isinstance(exc, APIError):
            return f"PinBridge API error ({exc.status_code}): {exc.message}"
        if isinstance(exc, PinbridgeError):
            return str(exc)
        return str(exc)
