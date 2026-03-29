"""FastMCP server registration."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import Settings, get_settings
from .service import PinBridgeService


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the FastMCP server."""
    settings = settings or get_settings()
    service = PinBridgeService(settings)

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

    if settings.enable_write_tools:

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

