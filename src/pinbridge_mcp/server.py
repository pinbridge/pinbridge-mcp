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
            "PinBridge MCP server — manage Pinterest publishing through the PinBridge API.\n\n"
            "AUTHENTICATION\n"
            "  HTTP mode:  Authorization: Bearer <pinbridge_api_key>\n"
            "  stdio mode: PINBRIDGE_MCP_PINBRIDGE_API_KEY env var\n\n"
            "SCOPE\n"
            "  All operations are scoped to the workspace associated with the API key.\n\n"
            "WORKFLOW\n"
            "  1. Call list_pinterest_accounts() to get account IDs.\n"
            "  2. Call list_boards(account_id) to get board IDs.\n"
            "  3. Create or schedule pins using those IDs.\n\n"
            "WRITE TOOLS\n"
            "  create_pin, create_schedule, cancel_schedule, create_board, delete_board\n"
            "  are only available when the server is configured with write tools enabled.\n\n"
            "PLAN GATE\n"
            "  The server may require a minimum plan. If your request is rejected with a\n"
            "  plan error, upgrade your PinBridge workspace at https://pinbridge.io."
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
        """Return server configuration and auth details.

        Use this to confirm the server is reachable, check which PinBridge API
        endpoint it targets, and whether write tools are enabled.

        Returns a dict with keys: server, version, pinbridge_base_url,
        public_base_url, auth_mode, workspace_scope, streamable_http_path,
        write_tools_enabled.
        """
        return await service.server_info()

    @mcp.tool()
    async def list_pinterest_accounts() -> list[dict]:
        """List Pinterest accounts connected to the current workspace.

        Returns one entry per connected Pinterest account. Each entry includes
        the account_id (UUID string) needed by list_boards, list_related_terms,
        create_pin, create_schedule, create_board, and get_rate_meter.

        Call this first if you don't already have an account_id.

        Returns a list of dicts with keys: id, username, profile_image, and
        other Pinterest account metadata.
        """
        try:
            return await service.list_pinterest_accounts()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def list_boards(account_id: str) -> list[dict]:
        """List Pinterest boards for a connected account.

        Returns all boards visible to the given Pinterest account. Use the
        board id from the results as board_id in create_pin and create_schedule.

        Args:
            account_id: UUID of the Pinterest account (from list_pinterest_accounts).

        Returns a list of dicts with keys: id, name, description, privacy.
        """
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
        """Look up Pinterest related/suggested terms for one or more seed terms.

        Useful for expanding keyword coverage when crafting pin descriptions or
        choosing related_terms for create_pin. Pinterest's search algorithm uses
        these terms to surface pins in feeds.

        Args:
            account_id: UUID of the Pinterest account.
            terms: One term (string) or multiple terms (list of strings or
                   comma-separated string). Example: "home decor" or
                   ["home decor", "minimalist"].
            exact_match: If True, only return terms that exactly match. Defaults
                         to False (broader related terms).

        Returns a dict with keys: id, related_term_count, related_terms_list
        (each item has term and related_terms list).
        """
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
        """List pins in the current workspace.

        Returns published pins scoped to the API key's workspace. Use limit and
        offset for pagination (max limit is typically 100).

        Args:
            limit:  Number of pins to return. Default 20.
            offset: Number of pins to skip for pagination. Default 0.

        Returns a list of pin dicts with keys: id, title, description,
        board_id, account_id, status, image_url, link_url, created_at, etc.
        """
        try:
            return await service.list_pins(limit=limit, offset=offset)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_pin(pin_id: str) -> dict:
        """Fetch a single published pin by its ID.

        Use this to retrieve full details of a specific pin, including its
        current status, image URL, board assignment, and metadata.

        Args:
            pin_id: The pin's UUID string (from list_pins or a prior create_pin).

        Returns a pin dict with keys: id, title, description, board_id,
        account_id, status, image_url, link_url, alt_text, created_at, etc.
        """
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
        """List activity logs for the current workspace.

        Returns a paginated log of all API and publishing activity in the
        workspace. Useful for auditing, debugging failed publishes, and
        monitoring schedule execution.

        Args:
            limit:         Number of log entries to return. Default 20.
            cursor:        Pagination cursor from a previous response's
                           next_cursor field.
            category:      Filter by category (e.g. "pin", "schedule", "auth").
            action:        Filter by action (e.g. "create", "cancel", "publish").
            status:        Filter by status (e.g. "success", "failed", "pending").
            resource_type: Filter by resource type (e.g. "pin", "board").
            since:         ISO 8601 datetime string. Only return logs after this
                           time. Example: "2026-03-01T00:00:00Z".

        Returns a dict with keys: items (list of log entries), next_cursor
        (pass to cursor for next page, null if no more results).
        """
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
        """List webhooks configured for the current workspace.

        Returns all registered webhook endpoints. Webhooks notify your server
        of events like pin publishes, schedule completions, and failures.

        Returns a list of dicts with keys: id, url, events, status, created_at.
        """
        try:
            return await service.list_webhooks()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_billing_status() -> dict:
        """Return workspace billing plan, usage, and quota information.

        Use this to check the current plan, API call usage vs quota, storage
        usage, and which features are available (e.g. bulk imports, media assets).

        Returns a dict with keys: plan, billing_status, trial_active,
        quota_calls_monthly, calls_used, credits_remaining, quota_exhausted,
        storage_quota_bytes, storage_used_bytes, pinterest_accounts_limit,
        uploaded_media_assets, bulk_imports, and related fields.
        """
        try:
            return await service.get_billing_status()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_rate_meter(account_id: str) -> dict:
        """Return current Pinterest API rate-limit usage for an account.

        Pinterest enforces rate limits per connected account. Call this before
        bulk operations to check remaining capacity and avoid hitting limits.

        Args:
            account_id: UUID of the Pinterest account (from list_pinterest_accounts).

        Returns a dict with rate limit fields including current usage, limit,
        and reset time.
        """
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
        """List scheduled pins for the current workspace.

        Returns pending, completed, failed, and canceled schedules. Use status
        to filter to a specific state.

        Args:
            limit:  Number of schedules to return. Default 20.
            offset: Number of schedules to skip for pagination. Default 0.
            status: Optional filter. One of: "scheduled", "published",
                    "failed", "canceled".

        Returns a list of schedule dicts with keys: id, account_id, board_id,
        run_at (ISO 8601 UTC), status, payload (pin details), pin_id (set after
        successful publish), last_error, created_at, updated_at.
        """
        try:
            return await service.list_schedules(limit=limit, offset=offset, status=status)
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    @mcp.tool()
    async def get_schedule(schedule_id: str) -> dict:
        """Fetch a single scheduled pin by its ID.

        Use this to check the current status and details of a specific schedule,
        including whether it has published successfully or failed.

        Args:
            schedule_id: UUID of the schedule (from list_schedules or create_schedule).

        Returns a schedule dict with keys: id, account_id, board_id, run_at,
        status, payload, pin_id, last_error, created_at, updated_at.
        """
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
            """Schedule a pin for future publishing at a specific time.

            The pin will be published to Pinterest automatically at run_at.
            Provide either image_url (direct URL) or asset_id (pre-uploaded
            asset), but not both.

            Args:
                account_id:            UUID of the Pinterest account.
                board_id:              ID of the board to publish to.
                title:                 Pin title. Max 100 characters.
                run_at:                ISO 8601 datetime with timezone when the
                                       pin should publish. Must be in the future.
                                       Example: "2026-04-01T10:00:00Z".
                image_url:             Public URL of the image to use. Mutually
                                       exclusive with asset_id.
                asset_id:              UUID of a pre-uploaded PinBridge asset.
                                       Mutually exclusive with image_url.
                description:           Pin description. Max 800 characters.
                link_url:              Destination URL when users click the pin.
                                       Max 2048 characters.
                cover_image_url:       URL for a custom video cover image.
                cover_image_asset_id:  Asset UUID for a custom video cover image.
                idempotency_key:       Optional unique key to prevent duplicate
                                       schedules on retry. Auto-generated if omitted.

            Returns the created schedule dict with status "scheduled".
            """
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
            """Cancel a pending scheduled pin before it publishes.

            Only schedules in "scheduled" status can be canceled. Already
            published or failed schedules cannot be canceled.

            Args:
                schedule_id: UUID of the schedule to cancel.

            Returns the updated schedule dict with status "canceled".
            """
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
            """Create a new Pinterest board on a connected account.

            Args:
                account_id:  UUID of the Pinterest account to create the board on.
                name:        Board name. Must be unique within the account.
                description: Optional board description.
                privacy:     "PUBLIC" (default) or "SECRET".

            Returns the created board dict with keys: id, name, description, privacy.
            The board id can immediately be used in create_pin and create_schedule.
            """
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
            """Permanently delete a Pinterest board and all its pins.

            This action is irreversible. All pins on the board will be deleted
            from Pinterest. Confirm with the user before calling this tool.

            Args:
                board_id:   ID of the board to delete.
                account_id: UUID of the Pinterest account that owns the board.

            Returns {"deleted": true, "board_id": "<id>"} on success.
            """
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
            """Publish a pin to Pinterest immediately.

            Publishes directly without scheduling. Use create_schedule instead
            if you want to publish at a future time. Provide either image_url
            or asset_id, not both.

            Args:
                account_id:            UUID of the Pinterest account to publish from.
                board_id:              ID of the board to pin to.
                title:                 Pin title.
                image_url:             Public URL of the pin image. Mutually
                                       exclusive with asset_id.
                asset_id:              UUID of a pre-uploaded PinBridge asset.
                                       Mutually exclusive with image_url.
                description:           Pin description for search and context.
                related_terms:         List of keyword strings to improve
                                       discoverability. Use list_related_terms
                                       to find good candidates.
                alt_text:              Accessibility text for the image.
                dominant_color:        Hex color code (e.g. "#FF5733") for the
                                       pin's dominant color.
                cover_image_url:       URL for a custom cover image (video pins).
                cover_image_asset_id:  Asset UUID for a custom cover image.
                link_url:              Destination URL when users click the pin.
                idempotency_key:       Unique key to prevent duplicate pins on
                                       retry. Auto-generated if omitted.

            Returns the created pin dict with id and status.
            """
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
