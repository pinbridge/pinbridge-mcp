"""FastMCP server registration: tools, resources and prompts."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .service import PinBridgeService

T = TypeVar("T")


class PinInput(BaseModel):
    """One entry of create_pins_batch; the same fields as create_pin."""

    account_id: str = Field(description="UUID of the Pinterest account to publish from.")
    board_id: str = Field(description="ID of the board to pin to.")
    title: str = Field(description="Pin title (<= 100 characters).", max_length=100)
    image_url: str | None = Field(
        default=None, description="Public URL of the pin image (or use asset_id)."
    )
    asset_id: str | None = Field(
        default=None, description="UUID of an uploaded PinBridge asset (or use image_url)."
    )
    description: str | None = Field(
        default=None, description="Pin description (<= 800 characters).", max_length=800
    )
    related_terms: list[str] | None = Field(default=None, description="Discovery keywords.")
    alt_text: str | None = Field(default=None, description="Accessibility text for the image.")
    dominant_color: str | None = Field(default=None, description="Hex color, e.g. #FF5733.")
    cover_image_url: str | None = Field(default=None, description="Video cover image URL.")
    cover_image_asset_id: str | None = Field(default=None, description="Video cover asset UUID.")
    link_url: str | None = Field(default=None, description="Destination URL for the pin.")
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "Unique key per pin so a resend never duplicates it. Generated when omitted, "
            "which makes the batch NOT safe to resend after a timeout."
        ),
    )


# Every tool talks to the PinBridge API and, through it, Pinterest: an open world.
READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE_NON_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
)
DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)

INSTRUCTIONS = """PinBridge MCP server — manage Pinterest publishing through the PinBridge API.

AUTHENTICATION
  HTTP mode:  Authorization: Bearer <pinbridge_api_key>  (or the built-in OAuth flow)
  stdio mode: PINBRIDGE_MCP_PINBRIDGE_API_KEY env var

SCOPE
  All operations are scoped to the workspace of the API key. Keys can carry
  read / write / destructive scopes and a Pinterest-account allow-list; a call
  outside the key's grants fails with error code insufficient_scope or
  account_not_permitted.

WORKFLOW (use the publish_pin prompt for the full sequence)
  1. list_pinterest_accounts()  → account_id   (also: resource pinbridge://accounts)
  2. list_boards(account_id)    → board_id     (also: pinbridge://accounts/{id}/boards)
  3. Need an image the internet cannot fetch? upload_asset(...) → asset_id
  4. create_pin(..., dry_run=true) to preflight for free (nothing is published)
  5. create_pin(...) / create_schedule(...) / create_pins_batch(...)
  6. get_pin_analytics(pin_id) after publishing; update_pin / delete_pin to fix a mistake

ACCOUNTS
  Connecting or disconnecting a Pinterest account is deliberately dashboard-only
  (https://app.pinbridge.io): connecting needs a person to approve Pinterest's
  OAuth grant in a browser, and disconnecting drops every pending schedule on
  the account. When a call fails with token_expired, token_revoked or
  scope_missing, tell the user to reconnect the account in the dashboard.

ERRORS
  Every failure carries a stable code and a remediation sentence. Two "scope"
  codes mean different things: insufficient_scope / account_not_permitted are
  about THIS API key's grants (ask a workspace admin for a wider key);
  scope_missing / token_expired / token_revoked are about the connected
  Pinterest account (reconnect it in the PinBridge dashboard). Board codes:
  board_not_found, board_not_owned, board_deleted, board_access_denied. Others:
  quota_exceeded, rate_limited (has retry_after_seconds), validation_error.
  Prefer a dry run (or check_board_access after a board failure) over retrying blind.

WRITE TOOLS
  upload_asset, create_pin, create_pins_batch, update_pin, delete_pin, retry_pin,
  create_schedule, cancel_schedule, retry_schedule, delete_schedule, create_board,
  delete_board, create_webhook, update_webhook, delete_webhook are only
  registered when the server has write tools enabled.

PLAN GATE
  The server may require a minimum plan. If your request is rejected with a
  plan error, upgrade your PinBridge workspace at https://pinbridge.io.
"""


PUBLISH_PIN_STEPS = """Publish one pin with PinBridge, in this order:
1. If no account_id is known, read pinbridge://accounts (or call
   list_pinterest_accounts) and pick one.
2. If no board_id is known, read pinbridge://accounts/{account_id}/boards (or call
   list_boards).
3. If the board was not used recently or a previous publish to it failed, call
   check_board_access(account_id, board_id) and follow its remediation instead of
   retrying; otherwise the dry run in step 6 covers this check.
4. Media: if the image is a public URL, pass it as image_url. If you generated the
   image or it is not publicly fetchable, call upload_asset (content_base64 or
   source_url) and use the returned id as asset_id.
5. Draft title (<=100 chars), description (<=800 chars, use list_related_terms for
   keywords), link_url and alt_text.
6. Run create_pin with dry_run=true. Show the resolved payload and every check to
   the user and stop if any check failed.
7. Only after the user confirms, call create_pin with the same arguments plus
   idempotency_key=resolved.idempotency_key from the dry run, and dry_run=false.
   Reuse that key on any retry so the pin is never duplicated.
8. Poll get_pin until status is published, failed or deferred. deferred means
   PinBridge is pacing the publish (quota or rate limit); report error_code and
   wait rather than resubmitting. On failed, read error_code/error_message;
   retry_pin can move it to another board or account.
9. Later, get_pin_analytics(pin_id) reports impressions, saves and clicks. A wrong
   pin is fixed with update_pin (title/description/link/alt_text/board) or
   delete_pin, never by deleting the board."""


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the FastMCP server."""
    settings = settings or get_settings()
    service = PinBridgeService(settings)

    # Derive allowed hosts from public_base_url so the SDK host-header check passes
    parsed = urlparse(settings.normalized_public_base_url)
    allowed_hosts = [parsed.netloc, "localhost", "127.0.0.1"]

    mcp = FastMCP(
        "PinBridge",
        instructions=INSTRUCTIONS,
        streamable_http_path=settings.streamable_http_path,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=[settings.normalized_public_base_url],
        ),
    )

    async def guarded(call: Callable[[], Awaitable[T]]) -> T:
        """Run a service call and turn any failure into the client-facing message."""
        try:
            return await call()
        except Exception as exc:
            raise ValueError(service.format_error(exc)) from exc

    # ------------------------------------------------------------------ resources

    @mcp.resource(
        "pinbridge://accounts",
        name="pinterest_accounts",
        title="Connected Pinterest accounts",
        description=(
            "Pinterest accounts connected to this workspace; each has the account_id "
            "used by every other tool."
        ),
        mime_type="application/json",
    )
    async def accounts_resource() -> str:
        return json.dumps(await guarded(service.list_pinterest_accounts))

    @mcp.resource(
        "pinbridge://accounts/{account_id}/boards",
        name="pinterest_boards",
        title="Boards for a Pinterest account",
        description=(
            "Boards the given account can publish to; each has the board_id used by "
            "create_pin and create_schedule."
        ),
        mime_type="application/json",
    )
    async def boards_resource(account_id: str) -> str:
        return json.dumps(await guarded(lambda: service.list_boards(account_id)))

    # ------------------------------------------------------------------ prompts

    @mcp.prompt(name="publish_pin", title="Publish a pin end to end")
    def publish_pin_prompt(
        goal: Annotated[
            str, Field(description="What the pin should achieve, in the user's words.")
        ] = "",
        account_id: Annotated[
            str, Field(description="Pinterest account UUID, if already chosen.")
        ] = "",
        board_id: Annotated[str, Field(description="Board ID, if already chosen.")] = "",
    ) -> str:
        """Step-by-step sequence for publishing one pin safely.

        Covers upload, validate, publish and measure, in that order.
        """
        lines: list[str] = []
        if goal:
            lines.append(f"Goal: {goal}")
        if account_id:
            lines.append(f"Pinterest account_id: {account_id}")
        if board_id:
            lines.append(f"board_id: {board_id}")
        lines.append(PUBLISH_PIN_STEPS)
        return "\n".join(lines)

    # ------------------------------------------------------------------ read tools

    @mcp.tool(annotations=READ)
    async def server_info() -> dict:
        """Return server configuration, auth details and capabilities.

        Use this to confirm the server is reachable, check which PinBridge API
        endpoint it targets and whether write tools are enabled. Resources and
        prompts are discoverable through the standard list calls.
        """
        return await service.server_info()

    @mcp.tool(annotations=READ)
    async def list_pinterest_accounts() -> list[dict]:
        """List Pinterest accounts connected to the current workspace.

        Returns one entry per connected Pinterest account. Each entry includes
        the account_id (UUID string) needed by list_boards, create_pin,
        create_schedule, create_board, analytics and get_rate_meter, plus the
        account's health (reconnect_required, missing_scopes).

        Also available as the resource pinbridge://accounts.
        """
        return await guarded(service.list_pinterest_accounts)

    @mcp.tool(annotations=READ)
    async def list_boards(account_id: str) -> list[dict]:
        """List Pinterest boards for a connected account.

        Use the board id from the results as board_id in create_pin and
        create_schedule. Also available as pinbridge://accounts/{account_id}/boards.

        Args:
            account_id: UUID of the Pinterest account (from list_pinterest_accounts).

        Returns a list of dicts with keys: id, name, description, privacy.
        """
        return await guarded(lambda: service.list_boards(account_id))

    @mcp.tool(annotations=READ)
    async def check_board_access(account_id: str, board_id: str, fresh: bool = False) -> dict:
        """Check whether an account can publish to a board right now, and why not.

        Runs the same preflight create_pin uses, but reports instead of failing.
        Call it before publishing to a board you have not used recently, or
        when a publish failed with board_access_denied.

        Args:
            account_id: UUID of the Pinterest account.
            board_id:   Pinterest board ID.
            fresh:      Bypass the cached verdict and ask Pinterest again.

        Returns a dict with keys: publishable (bool), status (ok | failed |
        skipped), code (board_not_found, board_not_owned, board_deleted,
        scope_missing, token_expired, board_access_denied, ...), message,
        remediation, board, account_health, source.
        """
        return await guarded(
            lambda: service.check_board_access(
                account_id=account_id, board_id=board_id, fresh=fresh
            )
        )

    @mcp.tool(annotations=READ)
    async def list_related_terms(
        account_id: str,
        terms: str | list[str],
        exact_match: bool = False,
    ) -> dict:
        """Look up Pinterest related/suggested terms for one or more seed terms.

        Useful for expanding keyword coverage when crafting pin descriptions or
        choosing related_terms for create_pin.

        Args:
            account_id:  UUID of the Pinterest account.
            terms:       One term (string) or multiple terms (list or comma-separated).
            exact_match: If True, only return groups whose term exactly matches.

        Returns a dict with keys: id, related_term_count, related_terms_list.
        """
        return await guarded(
            lambda: service.list_related_terms(
                account_id=account_id, terms=terms, exact_match=exact_match
            )
        )

    @mcp.tool(annotations=READ)
    async def list_pins(
        limit: int = 20,
        offset: int = 0,
        account_id: str | None = None,
        board_id: str | None = None,
        status: str | None = None,
        error_code: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """List pins in the current workspace, newest first, with optional filters.

        Args:
            limit:      Number of pins to return (1-200). Default 20.
            offset:     Number of pins to skip for pagination. Default 0.
            account_id: Only pins for this Pinterest account.
            board_id:   Only pins targeting this board.
            status:     One of queued, deferred, publishing, published, failed.
            error_code: Only failed pins with this error code (e.g. board_access_denied).
            since:      ISO 8601 timestamp; only pins created at or after it.
            until:      ISO 8601 timestamp; only pins created before it.

        Returns a list of pin dicts with keys: id, title, description, board_id,
        pinterest_account_id, status, image_url, link_url, error_code,
        error_message, pinterest_pin_id, created_at, published_at, etc.
        """
        return await guarded(
            lambda: service.list_pins(
                limit=limit,
                offset=offset,
                account_id=account_id,
                board_id=board_id,
                status=status,
                error_code=error_code,
                since=since,
                until=until,
            )
        )

    @mcp.tool(annotations=READ)
    async def get_pin(pin_id: str) -> dict:
        """Fetch a single pin by its ID, including status and any error.

        Args:
            pin_id: The pin's UUID string (from list_pins or a prior create_pin).
        """
        return await guarded(lambda: service.get_pin(pin_id))

    @mcp.tool(annotations=READ)
    async def get_pin_analytics(
        pin_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        metrics: str | None = None,
    ) -> dict:
        """Pinterest analytics for one published pin over a date range.

        Args:
            pin_id:     The pin's UUID string.
            start_date: Inclusive start (YYYY-MM-DD). Default: 30 days ago.
            end_date:   Inclusive end (YYYY-MM-DD). Default: today. Ranges are
                        capped at 90 days.
            metrics:    Comma-separated Pinterest metric types, e.g.
                        "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK". Default set
                        covers organic engagement.

        Returns a dict with keys: pin_id, pinterest_pin_id, account_id,
        start_date, end_date, provider_mode, totals (lowercase metric names),
        daily (list of {date, data_status, metrics}).
        """
        return await guarded(
            lambda: service.get_pin_analytics(
                pin_id, start_date=start_date, end_date=end_date, metrics=metrics
            )
        )

    @mcp.tool(annotations=READ)
    async def get_account_analytics(
        account_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        metrics: str | None = None,
    ) -> dict:
        """Pinterest analytics for a whole connected account over a date range.

        Args:
            account_id: UUID of the Pinterest account.
            start_date: Inclusive start (YYYY-MM-DD). Default: 30 days ago.
            end_date:   Inclusive end (YYYY-MM-DD). Default: today (max 90 days).
            metrics:    Comma-separated metric types, e.g.
                        "IMPRESSION,ENGAGEMENT,SAVE,PIN_CLICK,OUTBOUND_CLICK".

        Returns a dict with keys: account_id, start_date, end_date,
        provider_mode, totals, daily.
        """
        return await guarded(
            lambda: service.get_account_analytics(
                account_id, start_date=start_date, end_date=end_date, metrics=metrics
            )
        )

    @mcp.tool(annotations=READ)
    async def list_activity_logs(
        limit: int = 20,
        cursor: str | None = None,
        category: str | None = None,
        action: str | None = None,
        status: str | None = None,
        resource_type: str | None = None,
        since: str | None = None,
    ) -> dict:
        """List activity logs for the current workspace (audit trail).

        Args:
            limit:         Number of log entries to return. Default 20.
            cursor:        Pagination cursor from a previous response's next_cursor.
            category:      Filter by category (e.g. "publishing", "configuration").
            action:        Filter by action (e.g. "pin.publish_failed").
            status:        Filter by status (e.g. "success", "failed", "queued").
            resource_type: Filter by resource type (e.g. "pin", "board", "api_key").
            since:         ISO 8601 datetime; only logs after this time.

        Returns a dict with keys: items, next_cursor.
        """
        return await guarded(
            lambda: service.list_activity_logs(
                limit=limit,
                cursor=cursor,
                category=category,
                action=action,
                status=status,
                resource_type=resource_type,
                since=since,
            )
        )

    @mcp.tool(annotations=READ)
    async def list_webhooks() -> list[dict]:
        """List webhooks configured for the current workspace.

        Returns a list of dicts with keys: id, url, events, is_enabled, created_at.
        """
        return await guarded(service.list_webhooks)

    @mcp.tool(annotations=READ)
    async def get_billing_status() -> dict:
        """Return workspace billing plan, usage, and quota information.

        Returns a dict with keys: plan, billing_status, quota_calls_monthly,
        calls_used, credits_remaining, quota_exhausted, storage usage, feature
        flags (uploaded_media_assets, bulk_imports), and related fields.
        """
        return await guarded(service.get_billing_status)

    @mcp.tool(annotations=READ)
    async def get_rate_meter(account_id: str) -> dict:
        """Return current Pinterest publish rate headroom for an account.

        Args:
            account_id: UUID of the Pinterest account.

        Returns a dict with account and global token-bucket state
        (tokens_available, capacity, refill_rate).
        """
        return await guarded(lambda: service.get_rate_meter(account_id))

    @mcp.tool(annotations=READ)
    async def list_schedules(
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        account_id: str | None = None,
        board_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """List scheduled pins for the current workspace, latest run_at first.

        Args:
            limit:      Number of schedules to return (1-200). Default 20.
            offset:     Number of schedules to skip. Default 0.
            status:     One of scheduled, queued, deferred, running, done, failed, canceled.
            account_id: Only schedules for this Pinterest account.
            board_id:   Only schedules targeting this board.
            since:      ISO 8601 timestamp; only schedules running at or after it.
            until:      ISO 8601 timestamp; only schedules running before it.

        Returns a list of schedule dicts with keys: id, pinterest_account_id,
        run_at, status, payload, pin_id, last_error, created_at, updated_at.
        """
        return await guarded(
            lambda: service.list_schedules(
                limit=limit,
                offset=offset,
                status=status,
                account_id=account_id,
                board_id=board_id,
                since=since,
                until=until,
            )
        )

    @mcp.tool(annotations=READ)
    async def get_schedule(schedule_id: str) -> dict:
        """Fetch a single scheduled pin by its ID.

        Args:
            schedule_id: UUID of the schedule.
        """
        return await guarded(lambda: service.get_schedule(schedule_id))

    if not settings.enable_write_tools:
        return mcp

    # ------------------------------------------------------------------ write tools

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def upload_asset(
        filename: str,
        content_base64: str | None = None,
        source_url: str | None = None,
        content_type: str | None = None,
        asset_type: Literal["image", "video"] = "image",
    ) -> dict:
        """Upload an image or video to PinBridge and get an asset_id for create_pin.

        Use this when the image was generated by you or is not publicly
        fetchable by Pinterest. Provide exactly one of content_base64 (the file
        bytes, base64-encoded) or source_url (a URL this server can download).

        Args:
            filename:       File name with extension, e.g. "hero.png".
            content_base64: Base64-encoded file content.
            source_url:     URL to download the file from instead.
            content_type:   MIME type (e.g. "image/png"); inferred when omitted.
            asset_type:     "image" (default) or "video".

        source_url must be a public http(s) URL with no redirects; private
        hosts are refused. Files are capped at 200 MB (plans cap lower).

        Returns the asset dict with keys: id (use as asset_id), public_url,
        asset_type, content_type, size_bytes, created_at. Uploads require a
        paid plan (uploaded_media_assets in get_billing_status).
        """
        return await guarded(
            lambda: service.upload_asset(
                filename=filename,
                content_base64=content_base64,
                source_url=source_url,
                content_type=content_type,
                asset_type=asset_type,
            )
        )

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
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
        dry_run: bool = False,
    ) -> dict:
        """Publish a pin to Pinterest now (or dry-run it with dry_run=true).

        Provide either image_url (publicly fetchable) or asset_id (from
        upload_asset), not both. The board is preflighted; an unpublishable
        board fails immediately with a stable error code and remediation.

        Args:
            account_id:            UUID of the Pinterest account to publish from.
            board_id:              ID of the board to pin to.
            title:                 Pin title (<= 100 characters).
            image_url:             Public URL of the pin image.
            asset_id:              UUID of an uploaded PinBridge asset.
            description:           Pin description (<= 800 characters).
            related_terms:         Keywords to improve discoverability.
            alt_text:              Accessibility text for the image.
            dominant_color:        Hex color code (e.g. "#FF5733").
            cover_image_url:       Cover image URL (video pins only).
            cover_image_asset_id:  Cover image asset UUID (video pins only).
            link_url:              Destination URL when users click the pin.
            idempotency_key:       Unique key so a retry never duplicates the pin.
                                   Generated if omitted (then a repeat call
                                   publishes again); reuse it on retries.
            dry_run:               Validate and return the checks and resolved
                                   payload without publishing.

        Returns the created pin dict (id, status "queued") or, with dry_run,
        the validation result (valid, checks, resolved, headroom).
        """
        return await guarded(
            lambda: service.create_pin(
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
                dry_run=dry_run,
            )
        )

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def create_pins_batch(pins: list[PinInput]) -> dict:
        """Publish several pins in one call with a per-entry outcome.

        Each entry takes the same fields as create_pin. Supply an
        idempotency_key per entry if you may need to resend the batch; keys are
        generated when omitted, and a resend then creates every pin again.
        Requires the bulk imports plan feature (`bulk_imports` in
        get_billing_status) and enough monthly quota for every entry that would
        be created.

        Args:
            pins: List of pin objects (max 100).

        Returns a dict with keys: created_count, existing_count, failed_count,
        results (list of {index, idempotency_key, status, pin, error}), headroom.
        """
        return await guarded(
            lambda: service.create_pins_batch(
                [entry.model_dump(exclude_none=True) for entry in pins]
            )
        )

    @mcp.tool(annotations=WRITE)
    async def update_pin(
        pin_id: str,
        title: str | None = None,
        description: str | None = None,
        link_url: str | None = None,
        alt_text: str | None = None,
        board_id: str | None = None,
    ) -> dict:
        """Edit a pin's title, description, link, alt text or board.

        Unpublished pins are edited in place. Published pins are also updated on
        Pinterest, keeping their engagement; fields cannot be cleared there, so
        send a replacement value rather than an empty one. Use this instead of
        delete-and-repost for a typo.

        Args:
            pin_id:      The pin's UUID string.
            title:       New title (<= 100 characters).
            description: New description (<= 800 characters).
            link_url:    New destination URL.
            alt_text:    New accessibility text.
            board_id:    Move the pin to another board (preflighted).

        Returns the updated pin dict.
        """
        return await guarded(
            lambda: service.update_pin(
                pin_id,
                title=title,
                description=description,
                link_url=link_url,
                alt_text=alt_text,
                board_id=board_id,
            )
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_pin(pin_id: str, delete_from_pinterest: bool = True) -> dict:
        """Delete one pin — the proportionate fix for a wrong pin.

        With delete_from_pinterest (default) the published pin is removed from
        Pinterest before the PinBridge record is deleted. Confirm with the user
        before calling this tool.

        Args:
            pin_id:                The pin's UUID string.
            delete_from_pinterest: Also remove the pin on Pinterest. False keeps it
                                   live there and only drops the PinBridge record.

        Returns a dict with keys: id, deleted, removed_from_pinterest,
        pinterest_pin_id, reason (not_published / simulated_sandbox / record_only
        when nothing was removed upstream).
        """
        return await guarded(
            lambda: service.delete_pin(pin_id, delete_from_pinterest=delete_from_pinterest)
        )

    @mcp.tool(annotations=WRITE)
    async def retry_pin(
        pin_id: str, board_id: str | None = None, account_id: str | None = None
    ) -> dict:
        """Retry a failed pin, optionally on another board or account.

        Fixes the root cause of the failure in one call (e.g. the original board
        was deleted). The target board is preflighted like on create_pin.

        Args:
            pin_id:     The failed pin's UUID string.
            board_id:   New board to publish to (optional).
            account_id: New Pinterest account to publish with (optional).

        Returns the pin dict, re-queued for publishing.
        """
        return await guarded(
            lambda: service.retry_pin(pin_id, board_id=board_id, account_id=account_id)
        )

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
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
        dry_run: bool = False,
    ) -> dict:
        """Schedule a pin for future publishing at a specific time.

        The board is preflighted at scheduling time. Provide either image_url or
        asset_id, not both. A repeat call creates a second schedule; check
        list_schedules before resending after a timeout.

        Args:
            account_id:            UUID of the Pinterest account.
            board_id:              ID of the board to publish to.
            title:                 Pin title (<= 100 characters).
            run_at:                ISO 8601 datetime with timezone, in the future.
                                   Example: "2026-04-01T10:00:00Z".
            image_url:             Public URL of the image.
            asset_id:              UUID of a pre-uploaded PinBridge asset.
            description:           Pin description (<= 800 characters).
            link_url:              Destination URL (<= 2048 characters).
            cover_image_url:       Custom video cover image URL.
            cover_image_asset_id:  Custom video cover image asset UUID.
            dry_run:               Validate (including run_at) without scheduling.

        Returns the created schedule dict with status "scheduled", or the
        validation result with dry_run.
        """
        return await guarded(
            lambda: service.create_schedule(
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
                dry_run=dry_run,
            )
        )

    @mcp.tool(annotations=WRITE)
    async def cancel_schedule(schedule_id: str) -> dict:
        """Cancel a pending scheduled pin before it publishes.

        Only schedules in "scheduled" status can be canceled.

        Args:
            schedule_id: UUID of the schedule to cancel.

        Returns the updated schedule dict with status "canceled".
        """
        return await guarded(lambda: service.cancel_schedule(schedule_id))

    @mcp.tool(annotations=WRITE)
    async def retry_schedule(schedule_id: str) -> dict:
        """Re-queue a schedule whose publish failed.

        Only schedules in "failed" status can be retried. The schedule (and its
        linked pin, if any) goes back to "scheduled"; a run_at already in the
        past publishes at the next worker tick. To change the board or account
        first, use retry_pin on the linked pin_id instead.

        Args:
            schedule_id: UUID of the failed schedule (from list_schedules with
                         status="failed").

        Returns the updated schedule dict with status "scheduled".
        """
        return await guarded(lambda: service.retry_schedule(schedule_id))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(schedule_id: str) -> dict:
        """Delete a finished schedule record (status done, failed or canceled).

        A pending schedule cannot be deleted: cancel_schedule it first. Deleting
        does not touch the pin that a done schedule already published. Irreversible.

        Args:
            schedule_id: UUID of the schedule to delete.

        Returns {"deleted": true, "schedule_id": "<id>"} on success.
        """
        return await guarded(lambda: service.delete_schedule(schedule_id))

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def create_board(
        account_id: str,
        name: str,
        description: str | None = None,
        privacy: Literal["PUBLIC", "SECRET"] | None = None,
    ) -> dict:
        """Create a new Pinterest board on a connected account.

        Args:
            account_id:  UUID of the Pinterest account.
            name:        Board name. Must be unique within the account.
            description: Optional board description.
            privacy:     "PUBLIC" (default) or "SECRET".

        Returns the created board dict with keys: id, name, description, privacy.
        """
        return await guarded(
            lambda: service.create_board(
                account_id=account_id, name=name, description=description, privacy=privacy
            )
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_board(board_id: str, account_id: str) -> dict:
        """Delete a Pinterest board and every pin on it.

        This is the nuclear option: to fix one wrong pin use delete_pin or
        update_pin instead. Irreversible. Confirm with the user before calling.

        Args:
            board_id:   ID of the board to delete.
            account_id: UUID of the Pinterest account that owns the board.

        Returns {"deleted": true, "board_id": "<id>"} on success.
        """
        return await guarded(lambda: service.delete_board(board_id, account_id=account_id))

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def create_webhook(
        url: str,
        secret: str,
        events: list[str] | None = None,
        is_enabled: bool = True,
    ) -> dict:
        """Register a webhook endpoint for publishing events.

        Args:
            url:        HTTPS endpoint that receives the events.
            secret:     Shared secret (>= 16 characters) used to sign deliveries
                        (X-PinBridge-Signature, HMAC-SHA256).
            events:     Event names, default ["pin.published", "pin.failed"].
            is_enabled: Whether deliveries start immediately. Default true.

        Returns the webhook dict with keys: id, url, events, is_enabled, created_at.
        """
        return await guarded(
            lambda: service.create_webhook(
                url=url, secret=secret, events=events, is_enabled=is_enabled
            )
        )

    @mcp.tool(annotations=WRITE)
    async def update_webhook(
        webhook_id: str,
        url: str | None = None,
        secret: str | None = None,
        events: list[str] | None = None,
        is_enabled: bool | None = None,
    ) -> dict:
        """Change a webhook's URL, secret, events or enabled flag.

        Only the fields you pass change; omitted fields keep their value. Use
        is_enabled=false to pause deliveries without losing the registration.

        Args:
            webhook_id: UUID of the webhook (from list_webhooks).
            url:        New HTTPS endpoint.
            secret:     New signing secret (>= 16 characters).
            events:     Replacement event list, e.g. ["pin.published", "pin.failed"].
            is_enabled: Pause (false) or resume (true) deliveries.

        Returns the updated webhook dict with keys: id, url, events, is_enabled.
        """
        return await guarded(
            lambda: service.update_webhook(
                webhook_id, url=url, secret=secret, events=events, is_enabled=is_enabled
            )
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_webhook(webhook_id: str) -> dict:
        """Delete a webhook endpoint. Deliveries stop immediately.

        Args:
            webhook_id: UUID of the webhook (from list_webhooks).

        Returns {"deleted": true, "webhook_id": "<id>"} on success.
        """
        return await guarded(lambda: service.delete_webhook(webhook_id))

    return mcp
