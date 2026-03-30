"""FastMCP server registration."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import Settings, get_settings
from .service import PinBridgeService


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the FastMCP server."""
    settings = settings or get_settings()
    service = PinBridgeService(settings)

    # Derive allowed hosts from public_base_url so the SDK host-header check passes
    from urllib.parse import urlparse
    parsed = urlparse(settings.normalized_public_base_url)
    allowed_hosts = [parsed.netloc, "localhost", "127.0.0.1"]

    mcp = FastMCP(
        "PinBridge",
        instructions=(
            "PinBridge MCP server. Authenticate with a PinBridge API key using "
            "Authorization: Bearer <pinbridge_api_key> for HTTP mode, or set "
            "PINBRIDGE_MCP_PINBRIDGE_API_KEY for stdio mode. The server is scoped "
            "to the workspace associated with that API key."
        ),
        streamable_http_path=settings.streamable_http_path,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=[settings.normalized_public_base_url],
        ),
    )

    @mcp.tool()
    async def server_info() -> dict:
        """Return basic server and auth details."""
        return await service.server_info()

    @mcp.tool()
    async def list_pinterest_accounts() -> list[dict]:
        """List Pinterest accounts connected to the current workspace."""
        try:
            return await service.list_pinterest_accounts()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_boards(account_id: str) -> list[dict]:
        """List Pinterest boards for a connected account."""
        try:
            return await service.list_boards(account_id)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_related_terms(
        account_id: str,
        terms: str | list[str],
        exact_match: bool = False,
    ) -> dict:
        """Look up Pinterest related terms for one or more seed terms."""
        try:
            return await service.list_related_terms(
                account_id=account_id,
                terms=terms,
                exact_match=exact_match,
            )
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_pins(limit: int = 20, offset: int = 0) -> list[dict]:
        """List pins in the current workspace."""
        try:
            return await service.list_pins(limit=limit, offset=offset)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_pin(pin_id: str) -> dict:
        """Fetch a single pin by ID."""
        try:
            return await service.get_pin(pin_id)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_activity_logs(
        limit: int = 20,
        cursor: str | None = None,
        category: str | None = None,
        action: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        since: str | None = None,
    ) -> dict:
        """List recent activity logs for the current workspace."""
        try:
            return await service.list_activity_logs(
                limit=limit,
                cursor=cursor,
                category=category,
                action=action,
                status=status,
                resource_type=resource_type,
                since=since,
            )
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_webhooks() -> list[dict]:
        """List webhooks configured for the current workspace."""
        try:
            return await service.list_webhooks()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_billing_status() -> dict:
        """Return workspace billing and usage status."""
        try:
            return await service.get_billing_status()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_rate_meter(account_id: str) -> dict:
        """Return current Pinterest rate-limit status for an account."""
        try:
            return await service.get_rate_meter(account_id)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_schedules(
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[dict]:
        """List scheduled pins for the current workspace. Optionally filter by status."""
        try:
            return await service.list_schedules(limit=limit, offset=offset, status=status)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_schedule(schedule_id: str) -> dict:
        """Fetch a single scheduled pin by ID."""
        try:
            return await service.get_schedule(schedule_id)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    if settings.enable_write_tools:

        @mcp.tool()
        async def create_schedule(
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
        ) -> dict:
            """Schedule a pin for future publishing. run_at must be ISO 8601 with timezone (e.g. 2026-04-01T10:00:00Z)."""
            try:
                return await service.create_schedule(
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
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                raise ValueError(service.format_error(exc)) from exc

        @mcp.tool()
        async def cancel_schedule(schedule_id: str) -> dict:
            """Cancel a pending scheduled pin by ID."""
            try:
                return await service.cancel_schedule(schedule_id)
            except Exception as exc:
                raise ValueError(service.format_error(exc)) from exc

        @mcp.tool()
        async def create_board(
            account_id: str,
            name: str,
            description: str | None = None,
            privacy: str | None = None,
        ) -> dict:
            """Create a new Pinterest board."""
            try:
                return await service.create_board(
                    account_id=account_id,
                    name=name,
                    description=description,
                    privacy=privacy,
                )
            except Exception as exc:
                raise ValueError(service.format_error(exc)) from exc

        @mcp.tool()
        async def delete_board(board_id: str, account_id: str) -> dict:
            """Permanently delete a Pinterest board."""
            try:
                return await service.delete_board(board_id, account_id=account_id)
            except Exception as exc:
                raise ValueError(service.format_error(exc)) from exc

        @mcp.tool()
        async def create_pin(
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
        ) -> dict:
            """Create a pin. Disabled by default."""
            try:
                return await service.create_pin(
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
            except Exception as exc:
                raise ValueError(service.format_error(exc)) from exc

    return mcp

