"""PinBridge SDK adapter used by MCP tools.

Typed SDK resources are used where they exist. Endpoints that shipped with
PinBridge API 1.30 (pin update/delete-from-Pinterest, validate, analytics,
batch, board access, list filters) go through the SDK's raw ``request`` so the
MCP server does not wait on an SDK release; see the SDK roadmap for the swap.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import socket
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from pinbridge_sdk import AsyncPinbridgeClient
from pinbridge_sdk.errors import APIError, AuthenticationError, PinbridgeError

from . import __version__
from .auth import get_current_api_key
from .config import Settings

_MAX_ASSET_BYTES = 200 * 1024 * 1024
_ASSET_FETCH_TIMEOUT_SECONDS = 60.0
_FETCH_CHUNK_BYTES = 1024 * 1024


def _parse_iso_datetime(value: str, field: str = "timestamp") -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be an ISO 8601 timestamp such as 2026-04-01T10:00:00Z (got {value!r})"
        ) from exc


def _decode_base64(value: str) -> bytes:
    """Decode base64 the way agents produce it: line-wrapped, possibly unpadded."""
    compact = "".join(value.split())
    if compact.startswith("data:") and "," in compact:
        compact = compact.split(",", 1)[1]
    compact += "=" * (-len(compact) % 4)
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc


def _host_is_public(hostname: str) -> bool:
    """Resolve a hostname and refuse anything that is not a globally routable address."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            return False
    return True


async def _fetch_public_url(url: str, content_type: str | None) -> tuple[bytes, str | None]:
    """Download a public URL with SSRF and size guards.

    Only http(s), only globally routable hosts, no redirects (the caller passes
    the final URL), and the body is streamed against the size ceiling instead of
    being buffered first.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url must be an http(s) URL")
    if not await asyncio.to_thread(_host_is_public, parsed.hostname):
        raise ValueError(
            "source_url must point at a public host; private, loopback and link-local "
            "addresses are refused"
        )
    async with httpx.AsyncClient(
        timeout=_ASSET_FETCH_TIMEOUT_SECONDS, follow_redirects=False
    ) as fetcher:
        async with fetcher.stream("GET", url) as fetched:
            if 300 <= fetched.status_code < 400:
                raise ValueError(
                    "source_url redirects; pass the final URL it redirects to "
                    f"({fetched.headers.get('location', 'unknown')})"
                )
            if fetched.status_code >= 400:
                raise ValueError(
                    f"Could not fetch source_url (HTTP {fetched.status_code}); the URL must "
                    "be publicly readable."
                )
            declared = fetched.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > _MAX_ASSET_BYTES:
                raise ValueError("The asset exceeds the 200 MB upload ceiling")
            chunks: list[bytes] = []
            total = 0
            async for chunk in fetched.aiter_bytes(_FETCH_CHUNK_BYTES):
                total += len(chunk)
                if total > _MAX_ASSET_BYTES:
                    raise ValueError("The asset exceeds the 200 MB upload ceiling")
                chunks.append(chunk)
            if not content_type:
                header = fetched.headers.get("content-type", "")
                content_type = header.split(";", 1)[0].strip() or None
    return b"".join(chunks), content_type


def _normalize_terms(terms: str | list[str]) -> list[str]:
    if isinstance(terms, str):
        raw_terms = [segment.strip() for segment in terms.split(",")]
    else:
        raw_terms = [segment.strip() for segment in terms]
    return [term for term in raw_terms if term]


def _clean(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so optional tool arguments are not sent as nulls."""
    return {key: value for key, value in payload.items() if value is not None}


def _dump(value: Any) -> Any:
    """Serialize an SDK model (or pass a plain dict/list through)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


async def _aclose_client(client: Any) -> None:
    close = getattr(client, "aclose", None)
    if close is not None:
        await close()


def _error_envelope(details: Any) -> dict[str, Any]:
    """Pull the documented ``error`` envelope out of an API error body, if present."""
    if not isinstance(details, Mapping):
        return {}
    envelope = details.get("error")
    if isinstance(envelope, Mapping):
        return dict(envelope)
    detail = details.get("detail")
    if isinstance(detail, Mapping) and isinstance(detail.get("error"), Mapping):
        return dict(detail["error"])
    return {}


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
            user_agent=f"pinbridge-mcp/{__version__}",
        )
        try:
            yield client
        finally:
            await _aclose_client(client)

    # ------------------------------------------------------------------ info

    async def server_info(self) -> dict[str, Any]:
        return {
            "server": "pinbridge-mcp",
            "version": __version__,
            "pinbridge_base_url": self.settings.pinbridge_base_url,
            "public_base_url": self.settings.normalized_public_base_url,
            "auth_mode": "bearer_api_key_passthrough",
            "workspace_scope": "api_key",
            "streamable_http_path": self.settings.streamable_http_path,
            "write_tools_enabled": self.settings.enable_write_tools,
            "requires_pinbridge_api": ">=1.30",
        }

    # ------------------------------------------------------------------ accounts / boards

    async def list_pinterest_accounts(self) -> list[dict[str, Any]]:
        async with self.client() as client:
            accounts = await client.pinterest.list_accounts()
        return [_dump(account) for account in accounts]

    async def list_boards(self, account_id: str) -> list[dict[str, Any]]:
        async with self.client() as client:
            boards = await client.pinterest.list_boards(account_id)
        return [_dump(board) for board in boards]

    async def check_board_access(
        self, *, account_id: str, board_id: str, fresh: bool = False
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"account_id": account_id}
        if fresh:
            params["fresh"] = "true"
        async with self.client() as client:
            response = await client.request(
                "GET",
                "/v1/pinterest/boards/{board_id}/access",
                path_params={"board_id": board_id},
                params=params,
            )
        return response.json()

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
        return _dump(response)

    # ------------------------------------------------------------------ assets

    async def upload_asset(
        self,
        *,
        filename: str,
        content_base64: str | None = None,
        source_url: str | None = None,
        content_type: str | None = None,
        asset_type: str = "image",
    ) -> dict[str, Any]:
        """Upload an image or video and return the asset (``id`` feeds ``asset_id``)."""
        if asset_type not in {"image", "video"}:
            raise ValueError("asset_type must be 'image' or 'video'")
        if bool(content_base64) == bool(source_url):
            raise ValueError("Provide exactly one of content_base64 or source_url")

        if content_base64 is not None:
            data = _decode_base64(content_base64)
        else:
            assert source_url is not None
            data, content_type = await _fetch_public_url(source_url, content_type)
        if not data:
            raise ValueError("The asset is empty")
        if len(data) > _MAX_ASSET_BYTES:
            raise ValueError("The asset exceeds the 200 MB upload ceiling")

        async with self.client() as client:
            uploader = (
                client.assets.upload_image if asset_type == "image" else client.assets.upload_video
            )
            asset = await uploader(data, filename=filename, content_type=content_type)
        return _dump(asset)

    # ------------------------------------------------------------------ pins

    async def list_pins(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        account_id: str | None = None,
        board_id: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        params = _clean(
            {
                "limit": limit,
                "offset": offset,
                "account_id": account_id,
                "board_id": board_id,
                "status": status,
                "error_code": error_code,
                "since": _parse_iso_datetime(since, "since").isoformat() if since else None,
                "until": _parse_iso_datetime(until, "until").isoformat() if until else None,
            }
        )
        async with self.client() as client:
            response = await client.request("GET", "/v1/pins", params=params)
        return response.json()

    async def get_pin(self, pin_id: str) -> dict[str, Any]:
        async with self.client() as client:
            pin = await client.pins.get(pin_id)
        return _dump(pin)

    def _pin_payload(
        self,
        *,
        account_id: str,
        board_id: str,
        title: str,
        image_url: str | None,
        asset_id: str | None,
        description: str | None,
        related_terms: list[str] | None,
        alt_text: str | None,
        dominant_color: str | None,
        cover_image_url: str | None,
        cover_image_asset_id: str | None,
        link_url: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        return _clean(
            {
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
        )

    async def validate_pin(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        async with self.client() as client:
            response = await client.request("POST", "/v1/pins/validate", json=dict(payload))
        return response.json()

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
        dry_run: bool = False,
    ) -> dict[str, Any]:
        payload = self._pin_payload(
            account_id=account_id,
            board_id=board_id,
            title=title,
            image_url=image_url,
            asset_id=asset_id,
            description=description,
            related_terms=related_terms,
            alt_text=alt_text,
            dominant_color=dominant_color,
            cover_image_url=cover_image_url,
            cover_image_asset_id=cover_image_asset_id,
            link_url=link_url,
            idempotency_key=idempotency_key,
        )
        if dry_run:
            return await self.validate_pin(payload)
        async with self.client() as client:
            pin = await client.pins.create(payload)
        return _dump(pin)

    async def create_pins_batch(self, pins: list[Mapping[str, Any]]) -> dict[str, Any]:
        if not pins:
            raise ValueError("pins must contain at least one entry")
        entries: list[dict[str, Any]] = []
        for index, item in enumerate(pins):
            entry = _clean(dict(item))
            for required in ("account_id", "board_id", "title"):
                if not entry.get(required):
                    raise ValueError(f"pins[{index}] is missing required field '{required}'")
            entry.setdefault("idempotency_key", uuid4().hex)
            entries.append(entry)
        async with self.client() as client:
            response = await client.request("POST", "/v1/pins/batch", json={"pins": entries})
        return response.json()

    async def update_pin(
        self,
        pin_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        link_url: str | None = None,
        alt_text: str | None = None,
        board_id: str | None = None,
    ) -> dict[str, Any]:
        payload = _clean(
            {
                "title": title,
                "description": description,
                "link_url": link_url,
                "alt_text": alt_text,
                "board_id": board_id,
            }
        )
        if not payload:
            raise ValueError("Provide at least one field to update")
        async with self.client() as client:
            response = await client.request(
                "PATCH", "/v1/pins/{pin_id}", path_params={"pin_id": pin_id}, json=payload
            )
        return response.json()

    async def delete_pin(
        self, pin_id: str, *, delete_from_pinterest: bool = True
    ) -> dict[str, Any]:
        params = {"delete_from_pinterest": "true"} if delete_from_pinterest else None
        async with self.client() as client:
            response = await client.request(
                "DELETE", "/v1/pins/{pin_id}", path_params={"pin_id": pin_id}, params=params
            )
        if response.status_code == 204 or not response.content:
            # API >= 1.30 answers the flagged variant with a 200 body; a bare 204
            # means an older API ignored the flag and only dropped the record.
            return {
                "id": pin_id,
                "deleted": True,
                "removed_from_pinterest": False,
                "pinterest_pin_id": None,
                "reason": "api_version_too_old" if delete_from_pinterest else "record_only",
                **(
                    {
                        "warning": (
                            "The PinBridge API behind this server predates 1.30: the record "
                            "was deleted but the pin is still live on Pinterest."
                        )
                    }
                    if delete_from_pinterest
                    else {}
                ),
            }
        return response.json()

    async def retry_pin(
        self, pin_id: str, *, board_id: str | None = None, account_id: str | None = None
    ) -> dict[str, Any]:
        overrides = _clean({"board_id": board_id, "account_id": account_id})
        async with self.client() as client:
            pin = await client.pins.retry(pin_id, overrides or None)
        return _dump(pin)

    async def get_pin_analytics(
        self,
        pin_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        metrics: str | None = None,
    ) -> dict[str, Any]:
        params = _clean({"start_date": start_date, "end_date": end_date, "metrics": metrics})
        async with self.client() as client:
            response = await client.request(
                "GET", "/v1/pins/{pin_id}/analytics", path_params={"pin_id": pin_id}, params=params
            )
        return response.json()

    async def get_account_analytics(
        self,
        account_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        metrics: str | None = None,
    ) -> dict[str, Any]:
        params = _clean({"start_date": start_date, "end_date": end_date, "metrics": metrics})
        async with self.client() as client:
            response = await client.request(
                "GET",
                "/v1/pinterest/accounts/{account_id}/analytics",
                path_params={"account_id": account_id},
                params=params,
            )
        return response.json()

    # ------------------------------------------------ activity / webhooks / billing

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
        params = _clean(
            {
                "limit": limit,
                "cursor": cursor,
                "category": category,
                "action": action,
                "status": status,
                "resource_type": resource_type,
                "since": _parse_iso_datetime(since, "since").isoformat() if since else None,
            }
        )
        async with self.client() as client:
            response = await client.request("GET", "/v1/activity-logs", params=params)
        return response.json()

    async def list_webhooks(self) -> list[dict[str, Any]]:
        async with self.client() as client:
            webhooks = await client.webhooks.list()
        return [_dump(webhook) for webhook in webhooks]

    async def create_webhook(
        self,
        *,
        url: str,
        secret: str,
        events: list[str] | None = None,
        is_enabled: bool = True,
    ) -> dict[str, Any]:
        payload = _clean({"url": url, "secret": secret, "events": events, "is_enabled": is_enabled})
        async with self.client() as client:
            webhook = await client.webhooks.create(payload)
        return _dump(webhook)

    async def update_webhook(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        secret: str | None = None,
        events: list[str] | None = None,
        is_enabled: bool | None = None,
    ) -> dict[str, Any]:
        payload = _clean({"url": url, "secret": secret, "events": events, "is_enabled": is_enabled})
        if not payload:
            raise ValueError(
                "update_webhook needs at least one of url, secret, events or is_enabled."
            )
        async with self.client() as client:
            webhook = await client.webhooks.update(webhook_id, payload)
        return _dump(webhook)

    async def delete_webhook(self, webhook_id: str) -> dict[str, Any]:
        async with self.client() as client:
            await client.webhooks.delete(webhook_id)
        return {"deleted": True, "webhook_id": webhook_id}

    async def get_billing_status(self) -> dict[str, Any]:
        async with self.client() as client:
            status = await client.billing.status()
        return _dump(status)

    async def get_rate_meter(self, account_id: str) -> dict[str, Any]:
        async with self.client() as client:
            rate_meter = await client.rate_meter.get(account_id)
        return _dump(rate_meter)

    # ------------------------------------------------------------------ schedules

    def _schedule_payload(
        self,
        *,
        account_id: str,
        board_id: str,
        title: str,
        run_at: str,
        image_url: str | None,
        asset_id: str | None,
        description: str | None,
        link_url: str | None,
        cover_image_url: str | None,
        cover_image_asset_id: str | None,
    ) -> dict[str, Any]:
        return _clean(
            {
                "account_id": account_id,
                "board_id": board_id,
                "title": title,
                "run_at": _parse_iso_datetime(run_at, "run_at").isoformat(),
                "image_url": image_url,
                "asset_id": asset_id,
                "description": description,
                "link_url": link_url,
                "cover_image_url": cover_image_url,
                "cover_image_asset_id": cover_image_asset_id,
            }
        )

    async def validate_schedule(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        async with self.client() as client:
            response = await client.request("POST", "/v1/schedules/validate", json=dict(payload))
        return response.json()

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
        dry_run: bool = False,
    ) -> dict[str, Any]:
        payload = self._schedule_payload(
            account_id=account_id,
            board_id=board_id,
            title=title,
            run_at=run_at,
            image_url=image_url,
            asset_id=asset_id,
            description=description,
            link_url=link_url,
            cover_image_url=cover_image_url,
            cover_image_asset_id=cover_image_asset_id,
        )
        if dry_run:
            return await self.validate_schedule(payload)
        async with self.client() as client:
            schedule = await client.schedules.create(payload)
        return _dump(schedule)

    async def list_schedules(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        account_id: str | None = None,
        board_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        params = _clean(
            {
                "limit": limit,
                "offset": offset,
                "status": status,
                "account_id": account_id,
                "board_id": board_id,
                "since": _parse_iso_datetime(since, "since").isoformat() if since else None,
                "until": _parse_iso_datetime(until, "until").isoformat() if until else None,
            }
        )
        async with self.client() as client:
            response = await client.request("GET", "/v1/schedules", params=params)
        return response.json()

    async def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        async with self.client() as client:
            schedule = await client.schedules.get(schedule_id)
        return _dump(schedule)

    async def cancel_schedule(self, schedule_id: str) -> dict[str, Any]:
        async with self.client() as client:
            schedule = await client.schedules.cancel(schedule_id)
        return _dump(schedule)

    async def retry_schedule(self, schedule_id: str) -> dict[str, Any]:
        async with self.client() as client:
            schedule = await client.schedules.retry(schedule_id)
        return _dump(schedule)

    async def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        async with self.client() as client:
            await client.schedules.delete(schedule_id)
        return {"deleted": True, "schedule_id": schedule_id}

    # ------------------------------------------------------------------ boards

    async def create_board(
        self,
        *,
        account_id: str,
        name: str,
        description: str | None = None,
        privacy: str | None = None,
    ) -> dict[str, Any]:
        payload = _clean(
            {"account_id": account_id, "name": name, "description": description, "privacy": privacy}
        )
        async with self.client() as client:
            board = await client.pinterest.create_board(payload)
        return _dump(board)

    async def delete_board(self, board_id: str, *, account_id: str) -> dict[str, Any]:
        async with self.client() as client:
            await client.pinterest.delete_board(board_id, account_id=account_id)
        return {"deleted": True, "board_id": board_id}

    # ------------------------------------------------------------------ errors

    @staticmethod
    def format_error(exc: Exception) -> str:
        """Render an exception as the text an MCP client shows the user.

        API errors carry the documented envelope: a stable ``code`` the agent can
        branch on and a ``remediation`` sentence it can act on. 401/403 go through
        the same path so ``insufficient_scope`` / ``account_not_permitted`` keep
        their code instead of collapsing into "authentication failed".
        """
        if isinstance(exc, APIError):
            envelope = _error_envelope(exc.details)
            code = envelope.get("code") or exc.code
            message = envelope.get("message") or exc.message
            remediation = envelope.get("remediation")
            if isinstance(exc, AuthenticationError) and exc.status_code == 401 and not code:
                return f"PinBridge authentication failed: {message}"
            text = f"PinBridge API error ({exc.status_code})"
            if code:
                text += f" [{code}]"
            text += f": {message}"
            field_errors = envelope.get("errors")
            if isinstance(field_errors, list) and field_errors:
                rendered = []
                for entry in field_errors[:3]:
                    if not isinstance(entry, Mapping):
                        continue
                    loc = ".".join(str(part) for part in (entry.get("loc") or ()) if part != "body")
                    rendered.append(f"{loc or 'request'}: {entry.get('msg', '')}".strip())
                if rendered:
                    text += " (" + "; ".join(rendered) + ")"
            retry_after = envelope.get("retry_after_seconds")
            if retry_after is not None:
                text += f" Retry after {retry_after}s."
            if remediation:
                text += f" Fix: {remediation}"
            return text
        if isinstance(exc, PinbridgeError):
            return str(exc)
        return str(exc)
