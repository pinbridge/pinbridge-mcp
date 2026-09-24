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


# Shared parameter definitions so every tool's JSON schema documents its inputs.
AccountId = Annotated[
    str, Field(description="UUID of a connected Pinterest account, from list_pinterest_accounts.")
]
OptionalAccountId = Annotated[
    str | None,
    Field(description="UUID of a connected Pinterest account, from list_pinterest_accounts."),
]
BoardId = Annotated[
    str, Field(description="Pinterest board ID (numeric string), from list_boards.")
]
OptionalBoardId = Annotated[
    str | None, Field(description="Pinterest board ID (numeric string), from list_boards.")
]
PinId = Annotated[str, Field(description="UUID of the pin, from list_pins or create_pin.")]
ScheduleId = Annotated[
    str, Field(description="UUID of the schedule, from list_schedules or create_schedule.")
]
WebhookId = Annotated[str, Field(description="UUID of the webhook, from list_webhooks.")]
Limit = Annotated[int, Field(description="Page size, 1-200.", ge=1, le=200)]
Offset = Annotated[int, Field(description="Rows to skip for pagination.", ge=0)]
IsoSince = Annotated[
    str | None, Field(description="ISO 8601 timestamp with timezone; lower bound, inclusive.")
]
IsoUntil = Annotated[
    str | None, Field(description="ISO 8601 timestamp with timezone; upper bound, exclusive.")
]
StartDate = Annotated[
    str | None, Field(description="Inclusive start, YYYY-MM-DD. Default: 30 days ago.")
]
EndDate = Annotated[
    str | None,
    Field(description="Inclusive end, YYYY-MM-DD. Default: today. Ranges are capped at 90 days."),
]
Metrics = Annotated[
    str | None,
    Field(
        description=(
            "Comma-separated Pinterest metric types, e.g. "
            "IMPRESSION,SAVE,PIN_CLICK,OUTBOUND_CLICK. Default: the organic engagement set."
        )
    ),
]
Title = Annotated[str, Field(description="Pin title, at most 100 characters.", max_length=100)]
OptionalTitle = Annotated[
    str | None, Field(description="Pin title, at most 100 characters.", max_length=100)
]
Description = Annotated[
    str | None, Field(description="Pin description, at most 800 characters.", max_length=800)
]
LinkUrl = Annotated[
    str | None, Field(description="Destination URL opened when the pin is clicked.")
]
ImageUrl = Annotated[
    str | None,
    Field(description="Public URL of the image or video; Pinterest must be able to fetch it."),
]
AssetId = Annotated[
    str | None, Field(description="UUID of an uploaded PinBridge asset, from upload_asset.")
]
CoverImageUrl = Annotated[str | None, Field(description="Public cover image URL; video pins only.")]
CoverImageAssetId = Annotated[
    str | None, Field(description="Uploaded image asset UUID used as the video cover.")
]
RunAt = Annotated[
    str,
    Field(
        description=(
            'Publish time as ISO 8601 with timezone, in the future, e.g. "2026-04-01T10:00:00Z".'
        )
    ),
]
DryRun = Annotated[
    bool,
    Field(
        description=(
            "true runs every API check (account, board, media, quota, rate headroom) and "
            "returns the resolved payload without publishing anything."
        )
    ),
]
WebhookEvents = Annotated[
    list[str] | None,
    Field(description='Event names to deliver; any of "pin.published", "pin.failed".'),
]

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
  7. Wrong time or board on a pending schedule? update_schedule(...) edits it in place;
     never cancel + recreate for a small fix

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
  create_schedule, update_schedule, cancel_schedule, retry_schedule, delete_schedule,
  create_board, update_board, delete_board, create_webhook, update_webhook,
  delete_webhook are only registered when the server has write tools enabled.

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
        """Report this server's version, target PinBridge API and enabled capabilities.

        Use when a client connects for the first time, or when a call fails
        unexpectedly, to confirm the server is reachable and whether write
        tools are on. For the workspace's plan and quota use get_billing_status.

        Returns server version, pinbridge_base_url, transport and whether write
        tools are enabled. Needs no PinBridge scope.
        """
        return await service.server_info()

    @mcp.tool(annotations=READ)
    async def list_pinterest_accounts() -> list[dict]:
        """List the Pinterest accounts connected to this workspace with their health.

        Use first: list_boards, create_pin, create_schedule and get_rate_meter
        all need an account_id from here. Also read pinbridge://accounts. To
        connect a new account or fix reconnect_required, the user must use the
        PinBridge dashboard; there is no tool for that.

        Returns one entry per account with id (the account_id), username,
        environment, and health (reconnect_required, missing_scopes). An empty
        list means nothing is connected. Accounts outside this API key's
        allow-list are omitted. Never fails for a valid key.
        """
        return await guarded(service.list_pinterest_accounts)

    @mcp.tool(annotations=READ)
    async def list_boards(account_id: AccountId) -> list[dict]:
        """List the boards an account can publish to.

        Use to pick a board_id for create_pin or create_schedule, or after
        board_not_found. Also read pinbridge://accounts/{account_id}/boards. To
        test one board's publishability use check_board_access.

        Returns id, name, description, privacy per board. Fails with not_found
        for an unknown account, account_not_permitted if the key cannot use it,
        and token_expired / token_revoked / scope_missing when the Pinterest
        connection needs a reconnect in the dashboard.
        """
        return await guarded(lambda: service.list_boards(account_id))

    @mcp.tool(annotations=READ)
    async def check_board_access(
        account_id: AccountId,
        board_id: BoardId,
        fresh: Annotated[
            bool, Field(description="true bypasses the cached verdict and asks Pinterest again.")
        ] = False,
    ) -> dict:
        """Check whether an account can publish to a board right now, and why not.

        Use before publishing to a board not used recently, or after a publish
        failed with any board_* code, instead of retrying blind. For a
        full preflight of a specific pin use create_pin with dry_run=true.

        Returns publishable (bool), status (ok | failed | skipped when Pinterest
        was unreachable), code (board_not_found, board_not_owned, board_deleted,
        board_access_denied, scope_missing, token_expired), message, remediation,
        board, account_health, source (cache | pinterest). Never raises for a
        bad board; an unknown account fails with not_found.
        """
        return await guarded(
            lambda: service.check_board_access(
                account_id=account_id, board_id=board_id, fresh=fresh
            )
        )

    @mcp.tool(annotations=READ)
    async def list_related_terms(
        account_id: AccountId,
        terms: Annotated[
            str | list[str],
            Field(description="One seed term, a comma-separated string, or a list of terms."),
        ],
        exact_match: Annotated[
            bool, Field(description="true keeps only groups whose term exactly matches a seed.")
        ] = False,
    ) -> dict:
        """Look up the search terms Pinterest associates with your seed keywords.

        Use while drafting a description or choosing related_terms for
        create_pin; it reads Pinterest's own suggestions. Not needed for
        publishing itself.

        Returns id, related_term_count and related_terms_list (term plus its
        related terms). Counts against the workspace's Pinterest read limit and
        fails with rate_limited (with retry_after_seconds) when it is spent, or
        not_found for an unknown account.
        """
        return await guarded(
            lambda: service.list_related_terms(
                account_id=account_id, terms=terms, exact_match=exact_match
            )
        )

    @mcp.tool(annotations=READ)
    async def list_pins(
        limit: Limit = 20,
        offset: Offset = 0,
        account_id: OptionalAccountId = None,
        board_id: OptionalBoardId = None,
        status: Annotated[
            str | None,
            Field(description="One of queued, deferred, publishing, published, failed."),
        ] = None,
        error_code: Annotated[
            str | None,
            Field(description="Only failed pins with this error code, e.g. board_access_denied."),
        ] = None,
        since: IsoSince = None,
        until: IsoUntil = None,
    ) -> list[dict]:
        """List pins in this workspace, newest first, with optional filters.

        Use to find a pin's id, review what published or failed, or audit one
        board or account. For one known pin use get_pin; for scheduled (not yet
        published) pins use list_schedules.

        Returns pin dicts with id, title, board_id, pinterest_account_id,
        status, error_code, error_message, pinterest_pin_id, image_url,
        link_url, created_at, published_at. An empty list means no match.
        Filtering on an account outside the key's allow-list fails with
        account_not_permitted.
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
    async def get_pin(pin_id: PinId) -> dict:
        """Fetch one pin's current status, Pinterest ID and any publish error.

        Use to poll a pin from create_pin until it is published, failed or
        deferred, or to read why it failed. To find pins without an id use
        list_pins; for impressions and clicks use get_pin_analytics; to fix a
        failure use retry_pin.

        Returns the pin with status, error_code and error_message when failed,
        pinterest_pin_id once published, and its media and board fields. An
        unknown id fails with not_found.
        """
        return await guarded(lambda: service.get_pin(pin_id))

    @mcp.tool(annotations=READ)
    async def get_pin_analytics(
        pin_id: PinId,
        start_date: StartDate = None,
        end_date: EndDate = None,
        metrics: Metrics = None,
    ) -> dict:
        """Pinterest performance metrics for one published pin over a date range.

        Use after a pin has been published for a while to report impressions,
        saves and clicks. For the whole account use get_account_analytics; for
        publish status use get_pin.

        Returns pin_id, pinterest_pin_id, account_id, start_date, end_date,
        provider_mode, totals (lowercase metric names) and daily rows. Fails
        with not_found for an unknown pin and with conflict for a pin that has
        not published yet; sandbox pins return zeroed metrics.
        """
        return await guarded(
            lambda: service.get_pin_analytics(
                pin_id, start_date=start_date, end_date=end_date, metrics=metrics
            )
        )

    @mcp.tool(annotations=READ)
    async def get_account_analytics(
        account_id: AccountId,
        start_date: StartDate = None,
        end_date: EndDate = None,
        metrics: Metrics = None,
    ) -> dict:
        """Pinterest performance metrics for a whole connected account over a date range.

        Use for account-level reporting (all pins, not only ones published
        through PinBridge). For one pin use get_pin_analytics; for publish
        headroom use get_rate_meter.

        Returns account_id, start_date, end_date, provider_mode, totals and
        daily rows. Fails with not_found for an unknown account and with
        token_expired / scope_missing when the Pinterest connection needs a
        reconnect.
        """
        return await guarded(
            lambda: service.get_account_analytics(
                account_id, start_date=start_date, end_date=end_date, metrics=metrics
            )
        )

    @mcp.tool(annotations=READ)
    async def list_activity_logs(
        limit: Annotated[int, Field(description="Entries per page.", ge=1, le=200)] = 20,
        cursor: Annotated[
            str | None, Field(description="next_cursor from the previous page.")
        ] = None,
        category: Annotated[
            str | None, Field(description='Category, e.g. "publishing", "configuration".')
        ] = None,
        action: Annotated[
            str | None, Field(description='Action name, e.g. "pin.publish_failed".')
        ] = None,
        status: Annotated[
            str | None, Field(description='Outcome: "success", "failed", "queued", "canceled".')
        ] = None,
        resource_type: Annotated[
            str | None, Field(description='Resource kind, e.g. "pin", "schedule", "board".')
        ] = None,
        since: Annotated[
            str | None, Field(description="ISO 8601 timestamp; only entries after this time.")
        ] = None,
    ) -> dict:
        """Read the workspace audit trail: who did what, when, with what outcome.

        Use to reconstruct what happened to a pin or schedule, or to see
        changes made outside this session (dashboard, API, other agents). For
        current state use get_pin / get_schedule instead.

        Returns items (log entries with action, status, message, resource_type,
        resource_id, metadata, created_at) and next_cursor for the next page,
        null on the last page. Fails with validation_error for a malformed
        cursor or since value; unknown filter values return an empty page.
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
        """List every webhook endpoint registered in this workspace.

        Use before create_webhook to avoid registering the same URL twice, or
        to find a webhook's id for update_webhook or delete_webhook.

        Returns id, url, events, is_enabled, created_at per webhook; an empty
        list means none are registered. Secrets are never returned. Never fails
        for a valid key.
        """
        return await guarded(service.list_webhooks)

    @mcp.tool(annotations=READ)
    async def get_billing_status() -> dict:
        """Return the workspace's plan, monthly publish quota, usage and feature flags.

        Use before a large create_pins_batch or after quota_exceeded to see how
        many publishes remain, and to check plan features before upload_asset
        (uploaded_media_assets) or create_pins_batch (bulk_imports). For
        Pinterest's per-account publish rate use get_rate_meter; for pin
        performance use get_account_analytics.

        Returns plan, billing_status, quota_calls_monthly, calls_used,
        quota_reset_at, quota_exhausted, credits_remaining, storage_used_bytes /
        storage_quota_bytes, pinterest_accounts_limit, uploaded_media_assets,
        bulk_imports. Never fails for a valid key.
        """
        return await guarded(service.get_billing_status)

    @mcp.tool(annotations=READ)
    async def get_rate_meter(account_id: AccountId) -> dict:
        """Return how many Pinterest publishes an account can make right now.

        Use before publishing many pins at once, or after rate_limited, to
        decide between publishing now and spreading pins out with
        create_schedule. This is Pinterest's publish pacing; for the PinBridge
        monthly quota use get_billing_status.

        Returns account and global token buckets, each with tokens_available,
        capacity and refill_rate (tokens per second). tokens_available of 0
        means the next publish is deferred until the bucket refills. An unknown
        account fails with not_found.
        """
        return await guarded(lambda: service.get_rate_meter(account_id))

    @mcp.tool(annotations=READ)
    async def list_schedules(
        limit: Limit = 20,
        offset: Offset = 0,
        status: Annotated[
            str | None,
            Field(
                description=("One of scheduled, queued, deferred, running, done, failed, canceled.")
            ),
        ] = None,
        account_id: OptionalAccountId = None,
        board_id: OptionalBoardId = None,
        since: IsoSince = None,
        until: IsoUntil = None,
    ) -> list[dict]:
        """List scheduled pins in this workspace, latest run_at first, with filters.

        Use to find a schedule's id, see what is queued for a period, or list
        failed schedules to retry. For one known schedule use get_schedule; for
        pins that already published use list_pins.

        Returns schedule dicts with id, pinterest_account_id, run_at, status,
        payload (board_id, title, media), pin_id once it ran, last_error,
        created_at, updated_at. An empty list means no match. Fails with
        account_not_permitted for an account outside the key's allow-list and
        validation_error for a bad status or timestamp.
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
    async def get_schedule(schedule_id: ScheduleId) -> dict:
        """Fetch one scheduled pin (a pin queued to publish at a future time) by id.

        Use to check whether a schedule is still pending, has published, failed
        or was canceled. To browse schedules use list_schedules; once status is
        done, follow the resulting pin with get_pin using pin_id.

        Returns the schedule with status, run_at, pinterest_account_id, payload
        (board_id, title, media), pin_id and last_error. An unknown id fails
        with not_found.
        """
        return await guarded(lambda: service.get_schedule(schedule_id))

    if not settings.enable_write_tools:
        return mcp

    # ------------------------------------------------------------------ write tools

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def upload_asset(
        filename: Annotated[str, Field(description='File name with extension, e.g. "hero.png".')],
        content_base64: Annotated[
            str | None, Field(description="Base64-encoded file bytes (data: prefix allowed).")
        ] = None,
        source_url: Annotated[
            str | None,
            Field(
                description=(
                    "Public http(s) URL the server downloads instead of content_base64; "
                    "no redirects, private hosts are refused."
                )
            ),
        ] = None,
        content_type: Annotated[
            str | None, Field(description='MIME type, e.g. "image/png"; inferred when omitted.')
        ] = None,
        asset_type: Annotated[
            Literal["image", "video"], Field(description="Kind of media being uploaded.")
        ] = "image",
    ) -> dict:
        """Upload an image or video to PinBridge and get an asset_id for create_pin.

        Use when the media was generated in this session or Pinterest cannot
        fetch it from a public URL; otherwise pass image_url to create_pin
        directly. Provide exactly one of content_base64 or source_url. Videos
        must be uploaded assets.

        Returns id (use as asset_id), public_url, asset_type, content_type,
        size_bytes, created_at. Files are capped at 200 MB (plans cap lower).
        Fails with payment_required on plans without uploaded_media_assets
        (see get_billing_status) and validation_error for unsupported media.
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
        account_id: AccountId,
        board_id: BoardId,
        title: Title,
        image_url: ImageUrl = None,
        asset_id: AssetId = None,
        description: Description = None,
        related_terms: Annotated[
            list[str] | None, Field(description="Keywords that improve discoverability.")
        ] = None,
        alt_text: Annotated[
            str | None, Field(description="Accessibility text for the image, <= 500 characters.")
        ] = None,
        dominant_color: Annotated[
            str | None, Field(description='Hex color of the image, e.g. "#FF5733".')
        ] = None,
        cover_image_url: CoverImageUrl = None,
        cover_image_asset_id: CoverImageAssetId = None,
        link_url: LinkUrl = None,
        idempotency_key: Annotated[
            str | None,
            Field(
                description=(
                    "Unique key so a retry never duplicates the pin. Generated when omitted, "
                    "in which case a repeat call publishes again; reuse the key on retries."
                )
            ),
        ] = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Publish a pin to Pinterest now, or preflight it with dry_run=true.

        Use for a pin that should go out immediately; for a future time use
        create_schedule, for many pins use create_pins_batch. Provide either
        image_url or asset_id (from upload_asset), not both. Run dry_run first
        and reuse resolved.idempotency_key on the real call.

        Returns the pin with id and status "queued" (poll get_pin), or with
        dry_run the validation result (valid, checks, resolved, headroom).
        Fails fast with board_not_found / board_not_owned / board_access_denied
        for an unpublishable board, quota_exceeded when the monthly quota is
        spent, and validation_error for bad fields.
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
    async def create_pins_batch(
        pins: Annotated[
            list[PinInput],
            Field(description="Up to 100 pins, each with the same fields as create_pin."),
        ],
    ) -> dict:
        """Publish several pins in one call with a per-entry outcome.

        Use for bulk publishing (up to 100 pins) when the workspace has the
        bulk_imports feature; otherwise call create_pin per pin. Check
        get_billing_status for quota first: the batch needs quota for every
        entry. Supply an idempotency_key per entry if you may resend.

        Returns created_count, existing_count, failed_count, results (one
        {index, idempotency_key, status, pin, error} per entry) and headroom.
        Fails as a whole with payment_required without bulk_imports and
        quota_exceeded when quota is short; per-entry board or validation
        failures land in results instead.
        """
        return await guarded(
            lambda: service.create_pins_batch(
                [entry.model_dump(exclude_none=True) for entry in pins]
            )
        )

    @mcp.tool(annotations=WRITE)
    async def update_pin(
        pin_id: PinId,
        title: OptionalTitle = None,
        description: Description = None,
        link_url: LinkUrl = None,
        alt_text: Annotated[
            str | None, Field(description="New accessibility text, <= 500 characters.")
        ] = None,
        board_id: OptionalBoardId = None,
    ) -> dict:
        """Edit a pin's title, description, link, alt text or board in place.

        Use to fix a typo or move a pin instead of deleting and reposting: a
        published pin is updated on Pinterest and keeps its engagement. To
        change the image use delete_pin then create_pin; for a failed pin use
        retry_pin. Pass at least one field.

        Returns the updated pin. Fails with not_found for an unknown id,
        conflict (pin_publishing) while the pin is mid-publish, and
        field_not_clearable when sending an empty value for a published pin.
        A new board is preflighted like create_pin.
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
    async def delete_pin(
        pin_id: PinId,
        delete_from_pinterest: Annotated[
            bool,
            Field(
                description=(
                    "true (default) also removes the published pin from Pinterest; false "
                    "keeps it live there and only deletes the PinBridge record."
                )
            ),
        ] = True,
    ) -> dict:
        """Delete one pin, from Pinterest too by default. Irreversible; confirm first.

        Use for a pin that should not exist. For a wrong title, link or board
        use update_pin instead, and never use delete_board to remove one pin.

        Returns id, deleted, removed_from_pinterest, pinterest_pin_id and
        reason when nothing was removed upstream (not_published,
        simulated_sandbox, record_only, api_version_too_old). Fails with
        not_found for an unknown id and insufficient_scope without the
        destructive scope.
        """
        return await guarded(
            lambda: service.delete_pin(pin_id, delete_from_pinterest=delete_from_pinterest)
        )

    @mcp.tool(annotations=WRITE)
    async def retry_pin(
        pin_id: PinId,
        board_id: Annotated[
            str | None,
            Field(description="Board to publish to instead of the original one, from list_boards."),
        ] = None,
        account_id: Annotated[
            str | None,
            Field(description="Pinterest account to publish with instead of the original one."),
        ] = None,
    ) -> dict:
        """Re-queue a failed pin, optionally on another board or account.

        Use only for pins whose status is failed: pass board_id when the
        original board was deleted or inaccessible (check_board_access says
        why), or account_id when the original account needs a reconnect. For a
        published pin use update_pin; for a failed schedule use retry_schedule.

        Returns the pin re-queued with status "queued"; poll get_pin. The new
        board is preflighted like create_pin. Fails with not_found for an
        unknown id and conflict when the pin is not in failed status.
        """
        return await guarded(
            lambda: service.retry_pin(pin_id, board_id=board_id, account_id=account_id)
        )

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def create_schedule(
        account_id: AccountId,
        board_id: BoardId,
        title: Title,
        run_at: RunAt,
        image_url: ImageUrl = None,
        asset_id: AssetId = None,
        description: Description = None,
        link_url: LinkUrl = None,
        cover_image_url: CoverImageUrl = None,
        cover_image_asset_id: CoverImageAssetId = None,
        dry_run: DryRun = False,
    ) -> dict:
        """Schedule a pin to publish at a future time.

        Use when the pin should go out later or when spreading many pins out
        after rate_limited; for an immediate publish use create_pin. Provide
        either image_url or asset_id, not both. The board is preflighted now,
        not at run time. A repeat call creates a second schedule, so check
        list_schedules before resending after a timeout.

        Returns the schedule with id and status "scheduled" (track it with
        get_schedule), or with dry_run the validation result. Fails with
        validation_error for a past or timezone-less run_at, board_* codes
        for an unpublishable board, and quota_exceeded when quota is spent.
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
    async def update_schedule(
        schedule_id: ScheduleId,
        run_at: Annotated[
            str | None,
            Field(description="New publish time, ISO 8601 with timezone, in the future."),
        ] = None,
        board_id: OptionalBoardId = None,
        title: OptionalTitle = None,
        description: Description = None,
        link_url: LinkUrl = None,
        image_url: Annotated[
            str | None,
            Field(description="Replace the media with this public URL (drops any asset_id)."),
        ] = None,
        asset_id: Annotated[
            str | None,
            Field(description="Replace the media with this uploaded asset (drops any image_url)."),
        ] = None,
        cover_image_url: CoverImageUrl = None,
        cover_image_asset_id: CoverImageAssetId = None,
    ) -> dict:
        """Edit a pending schedule in place: time, board, text or media.

        Use to fix a wrong run_at, board, title or link on a schedule that has
        not started publishing, instead of cancel_schedule plus
        create_schedule; the id and history are kept. Once the schedule has
        run, edit the resulting pin with update_pin; for a failed one use
        retry_schedule. Pass at least one field.

        Returns the updated schedule, still in status "scheduled". A new board
        is preflighted like create_schedule. Fails with not_found for an
        unknown id, schedule_not_editable (409) once publishing started, with a
        remediation naming the right tool, validation_error for a past or
        timezone-less run_at, and sandbox_board_fixed when changing the board
        of a sandbox schedule.
        """
        return await guarded(
            lambda: service.update_schedule(
                schedule_id,
                run_at=run_at,
                board_id=board_id,
                title=title,
                description=description,
                link_url=link_url,
                image_url=image_url,
                asset_id=asset_id,
                cover_image_url=cover_image_url,
                cover_image_asset_id=cover_image_asset_id,
            )
        )

    @mcp.tool(annotations=WRITE)
    async def cancel_schedule(schedule_id: ScheduleId) -> dict:
        """Cancel a pending scheduled pin so it never publishes.

        Use when the pin should not go out at all. To remove a finished
        schedule from the list use delete_schedule; to re-arm a failed one use
        retry_schedule.

        Returns the schedule with status "canceled". Fails with not_found for
        an unknown id and bad_request when the schedule already ran (done,
        failed) or was canceled.
        """
        return await guarded(lambda: service.cancel_schedule(schedule_id))

    @mcp.tool(annotations=WRITE)
    async def retry_schedule(schedule_id: ScheduleId) -> dict:
        """Re-queue a schedule whose publish failed.

        Use for schedules in failed status (list_schedules with
        status="failed"). The schedule and its linked pin return to a runnable
        state; a run_at already in the past publishes at the next scheduler
        tick. To change the board or account first, use retry_pin on the linked
        pin_id; for a failed pin created directly, use retry_pin.

        Returns the schedule with status "scheduled". Fails with not_found for
        an unknown id and bad_request when the schedule is not failed.
        """
        return await guarded(lambda: service.retry_schedule(schedule_id))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_schedule(schedule_id: ScheduleId) -> dict:
        """Delete a finished schedule record (status done, failed or canceled). Irreversible.

        Use to clean up history. A pending schedule cannot be deleted: use
        cancel_schedule first. Deleting does not touch the pin a done schedule
        already published.

        Returns {"deleted": true, "schedule_id": "<id>"}. Fails with not_found
        for an unknown id, bad_request while the schedule is still pending, and
        insufficient_scope without the destructive scope.
        """
        return await guarded(lambda: service.delete_schedule(schedule_id))

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def create_board(
        account_id: AccountId,
        name: Annotated[
            str, Field(description="Board name, unique within the account, <= 180 characters.")
        ],
        description: Annotated[str | None, Field(description="Board description.")] = None,
        privacy: Annotated[
            Literal["PUBLIC", "SECRET"] | None,
            Field(description='"PUBLIC" (default) or "SECRET".'),
        ] = None,
    ) -> dict:
        """Create a new board on a connected Pinterest account.

        Use when no existing board from list_boards fits; check list_boards
        first, since Pinterest rejects duplicate names. Not needed for a
        one-off pin: publish to an existing board.

        Returns the board with id (use as board_id), name, description,
        privacy. Fails with forbidden (sandbox_board_limit) when a sandbox
        project hits its board cap and with token_expired / scope_missing when
        the Pinterest connection needs a reconnect.
        """
        return await guarded(
            lambda: service.create_board(
                account_id=account_id, name=name, description=description, privacy=privacy
            )
        )

    @mcp.tool(annotations=WRITE)
    async def update_board(
        board_id: BoardId,
        account_id: AccountId,
        name: Annotated[
            str | None, Field(description="New board name, unique within the account.")
        ] = None,
        description: Annotated[str | None, Field(description="New board description.")] = None,
        privacy: Annotated[
            Literal["PUBLIC", "SECRET"] | None, Field(description='"PUBLIC" or "SECRET".')
        ] = None,
    ) -> dict:
        """Rename a board or change its description or privacy on Pinterest.

        Use for board housekeeping; pins on the board are untouched. To move a
        pin between boards use update_pin; to remove a board use delete_board.
        Pass at least one of name, description or privacy.

        Returns the updated board (id, name, description, privacy). Fails with
        board_not_found for an unknown board, forbidden when sandbox board
        writes are blocked, and token_expired / scope_missing when the
        Pinterest connection needs a reconnect.
        """
        return await guarded(
            lambda: service.update_board(
                board_id,
                account_id=account_id,
                name=name,
                description=description,
                privacy=privacy,
            )
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_board(board_id: BoardId, account_id: AccountId) -> dict:
        """Delete a Pinterest board and every pin on it. Irreversible; confirm first.

        Use only when the whole board should go. To fix one wrong pin use
        update_pin or delete_pin instead.

        Returns {"deleted": true, "board_id": "<id>"}. Fails with
        board_not_found for an unknown board, forbidden when sandbox board
        writes are blocked, and insufficient_scope without the destructive
        scope.
        """
        return await guarded(lambda: service.delete_board(board_id, account_id=account_id))

    @mcp.tool(annotations=WRITE_NON_IDEMPOTENT)
    async def create_webhook(
        url: Annotated[str, Field(description="Public endpoint that receives POSTed events.")],
        secret: Annotated[
            str,
            Field(
                description=(
                    "Shared secret of at least 16 characters used to sign deliveries "
                    "(HMAC-SHA256 in X-PinBridge-Signature)."
                ),
                min_length=16,
            ),
        ],
        events: WebhookEvents = None,
        is_enabled: Annotated[
            bool, Field(description="false registers the endpoint without sending deliveries yet.")
        ] = True,
    ) -> dict:
        """Register an endpoint PinBridge calls when a pin publishes or fails.

        Use instead of polling get_pin when the caller can receive HTTP
        callbacks. Check list_webhooks first to avoid registering the same URL
        twice; use update_webhook to change events or pause an existing one.
        Default events are pin.published and pin.failed.

        Returns id, url, events, is_enabled, created_at. Fails with
        validation_error when the URL is not a valid http(s) URL or the secret
        is shorter than 16 characters.
        """
        return await guarded(
            lambda: service.create_webhook(
                url=url, secret=secret, events=events, is_enabled=is_enabled
            )
        )

    @mcp.tool(annotations=WRITE)
    async def update_webhook(
        webhook_id: WebhookId,
        url: Annotated[str | None, Field(description="New endpoint URL.")] = None,
        secret: Annotated[
            str | None, Field(description="New signing secret, at least 16 characters.")
        ] = None,
        events: Annotated[
            list[str] | None,
            Field(description='Replacement event list, e.g. ["pin.published", "pin.failed"].'),
        ] = None,
        is_enabled: Annotated[
            bool | None, Field(description="false pauses deliveries, true resumes them.")
        ] = None,
    ) -> dict:
        """Change a webhook's URL, secret, events or enabled flag.

        Use to pause deliveries (is_enabled=false) or rotate the secret without
        losing the registration; to stop for good use delete_webhook. Only the
        fields you pass change. Pass at least one.

        Returns the updated webhook (id, url, events, is_enabled). Fails with
        not_found for an unknown id and validation_error for a short secret.
        """
        return await guarded(
            lambda: service.update_webhook(
                webhook_id, url=url, secret=secret, events=events, is_enabled=is_enabled
            )
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_webhook(webhook_id: WebhookId) -> dict:
        """Delete a webhook endpoint; deliveries stop immediately. Irreversible.

        Use when the endpoint is retired. To pause temporarily use
        update_webhook with is_enabled=false instead.

        Returns {"deleted": true, "webhook_id": "<id>"}. Fails with not_found
        for an unknown id and insufficient_scope without the destructive scope.
        """
        return await guarded(lambda: service.delete_webhook(webhook_id))

    return mcp
