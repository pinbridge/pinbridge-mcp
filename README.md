# PinBridge MCP

[![pinbridge MCP connector – tool definition quality and endpoint health on Glama](https://glama.ai/mcp/connectors/io.pinbridge/pinbridge/badges/score.svg)](https://glama.ai/mcp/connectors/io.pinbridge/pinbridge)

`pinbridge-mcp` is the MCP server behind `https://mcp.pinbridge.io`. It lets an AI assistant (Claude, Cursor, any MCP client) publish, schedule, edit and measure Pinterest pins through the [PinBridge API](https://api.pinbridge.io/docs).

**Using it, not running it?** Start with the website:

- [Connect PinBridge to your AI assistant](https://www.pinbridge.io/docs/mcp/setup/) — API key or OAuth, client config, first pin
- [MCP documentation](https://www.pinbridge.io/docs/mcp/) and [usage policy](https://www.pinbridge.io/docs/mcp/usage-policy/)
- [Product page](https://www.pinbridge.io/mcp/)

This README covers the server itself: tools, auth, configuration and self-hosting.

## What it provides

- streamable HTTP transport (stateless, JSON responses) for remote hosting, `stdio` for local use
- two ways to authenticate: a PinBridge API key as a bearer token, or the OAuth 2.1 flow (with dynamic client registration) so clients such as Claude connect without a pasted key
- the full publishing loop: upload → dry run → publish → measure → edit / delete / retry
- MCP annotations on every tool, resources for accounts and boards, and a `publish_pin` prompt

The server is workspace-scoped: the API key (or the key minted by OAuth) resolves to one workspace and one project. Connecting or disconnecting a Pinterest account is deliberately not exposed: connecting needs a person to approve Pinterest's OAuth grant in a browser, and disconnecting drops every pending schedule on the account. Both live in the [dashboard](https://app.pinbridge.io), and the server instructions tell agents so.

## Tools

Read tools (always registered):

| Area | Tools |
|---|---|
| Server | `server_info` |
| Accounts and boards | `list_pinterest_accounts`, `list_boards`, `check_board_access`, `list_related_terms` |
| Pins | `list_pins` (search `q`, `sort`, filters: account, board, status, error code, since/until; returns `items`, `total`, `has_more`), `get_pin`, `get_pin_analytics` |
| Schedules | `list_schedules` (same search, sort and filters; `sort=run_at_asc` puts the next run first), `get_schedule` |
| Reporting | `get_dashboard_summary` (pin outcomes, success rate, previous-period comparison, hourly or daily series, queue and upcoming schedules for any range up to 366 days) |
| Workspace | `get_account_analytics`, `list_activity_logs`, `list_webhooks`, `get_billing_status`, `get_rate_meter` |

Write tools (registered when `PINBRIDGE_MCP_ENABLE_WRITE_TOOLS=true`):

| Area | Tools |
|---|---|
| Media | `upload_asset` (base64 or a public URL the server downloads; returns an `asset_id`) |
| Pins | `create_pin` (`dry_run` runs every API check, publishes nothing), `create_pins_batch`, `update_pin`, `delete_pin` (Pinterest-side by default), `retry_pin` |
| Schedules | `create_schedule` (`dry_run`), `update_schedule` (edit a pending schedule in place), `cancel_schedule`, `retry_schedule` (failed → scheduled), `delete_schedule` (finished schedules only) |
| Boards | `create_board`, `update_board` (name / description / privacy), `delete_board` |
| Webhooks | `create_webhook`, `update_webhook` (partial; pause with `is_enabled=false`), `delete_webhook` |

### Lists and paging

`list_pins` and `list_schedules` return one page as an object, and publish it as the tool's MCP `outputSchema`, so a client sees every field before calling:

```json
{
  "items": [{ "id": "…", "title": "Autumn salad", "status": "published", "…": "…" }],
  "total": 57,
  "limit": 20,
  "offset": 0,
  "has_more": true
}
```

- `total` counts every match across all pages. To answer "how many failed pins this week?", call `list_pins(status="failed", since=…, limit=1)` and read `total`; there's no need to page through the rows.
- For the next page, repeat the call with the same filters, `q` and `sort`, and `offset = offset + limit`, while `has_more` is `true`.
- `total` is `null` only against a PinBridge API older than 1.34. `has_more` then means "this page came back full".
- `list_activity_logs` pages by cursor instead: pass `next_cursor` back as `cursor`.

Every tool carries `readOnlyHint`, `destructiveHint` and `idempotentHint`, so a client can decide what needs a confirmation. Creates that mint their own idempotency key are marked non-idempotent; pass `idempotency_key` yourself to make a retry safe.

Resources: `pinbridge://accounts` and `pinbridge://accounts/{account_id}/boards`. Prompt: `publish_pin` (upload → validate → publish → measure).

### Errors

Every failure carries the API's stable `code` and a remediation sentence, for example `PinBridge API error (422) [board_not_found]: ... Fix: ...`. Two families look alike but mean different things:

- `insufficient_scope`, `account_not_permitted`: this API key's grants (scopes, account allow-list)
- `scope_missing`, `token_expired`, `token_revoked`: the connected Pinterest account; reconnect it in the dashboard

Other codes: `board_not_found`, `board_not_owned`, `board_deleted`, `board_access_denied`, `quota_exceeded`, `rate_limited` (with `retry_after_seconds`), `validation_error`.

### API version

Requires PinBridge API ≥ 1.34 for `get_dashboard_summary`, list search (`q`), `sort` and `total`; ≥ 1.31 for `update_schedule` and `update_board`; and ≥ 1.30 otherwise. Against 1.30 to 1.33, `get_dashboard_summary` fails with `PinBridge API error (404): Not Found`, `q` and `sort` are ignored, and list `total` is `null` (`has_more` then means "this page was full"). Against an older API, `check_board_access`, `update_pin`, `delete_pin`, `dry_run`, analytics, batch and the list filters fail with `PinBridge API error (404): Not Found`, and `delete_pin` reports `reason: api_version_too_old` (record deleted, pin still live on Pinterest).

## Auth

**Bearer API key.** Send a [PinBridge API key](https://app.pinbridge.io) on every request:

```text
Authorization: Bearer pb_<64 hex characters>
```

The token is passed through to the PinBridge API; the server keeps no credential store. Keys are verified against the API on first use and cached for `PINBRIDGE_MCP_AUTH_CACHE_TTL_SECONDS`. A key's scopes (`read`, `write`, `destructive`) and Pinterest-account allow-list apply to every tool call.

**OAuth.** With no header, an OAuth-capable client discovers the authorization server through `/.well-known/oauth-protected-resource` (RFC 9728), which points at the PinBridge API. The API implements OAuth 2.1 with dynamic client registration; the user signs in, picks a workspace, and the resulting token is a PinBridge API key that shows up in their key list and can be revoked like any other. The [setup guide](https://www.pinbridge.io/docs/mcp/setup/) walks through it for Claude.

**Plan gate and quota.** `PINBRIDGE_MCP_MIN_PLAN` rejects keys whose workspace plan is below the minimum. With `PINBRIDGE_MCP_ENABLE_QUOTA=true` every streamable-HTTP request (tool calls and resource reads alike) is checked against the workspace's weekly MCP quota via the API and counted afterwards; an exhausted quota answers `429` with the reset time. Quota checks fail open if the API is unreachable.

For local `stdio` use, set `PINBRIDGE_MCP_PINBRIDGE_API_KEY` instead.

## Configuration

All settings are environment variables prefixed `PINBRIDGE_MCP_` (a `.env` file is read too; see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://127.0.0.1:57289` | Public origin of this server; used for host/origin checks and OAuth resource metadata |
| `PINBRIDGE_BASE_URL` | `https://api.pinbridge.io` | PinBridge API to talk to |
| `PINBRIDGE_API_KEY` | unset | Key used in `stdio` mode (HTTP mode uses the incoming bearer token) |
| `VERIFY_INCOMING_API_KEYS` | `true` | Verify bearer tokens against the API before serving |
| `AUTH_CACHE_TTL_SECONDS` | `60` | How long a verified key (and its plan) is cached |
| `HOST` / `PORT` | `127.0.0.1` / `57289` | HTTP bind address |
| `STREAMABLE_HTTP_PATH` | `/` | Where the MCP endpoint is mounted |
| `ENABLE_WRITE_TOOLS` | `false` | Register the write tools |
| `MIN_PLAN` | `free` | Lowest workspace plan allowed to connect |
| `ENABLE_QUOTA` | `true` | Enforce the weekly MCP request quota through the API |
| `LOG_LEVEL` | `INFO` | Python log level |

## Run locally

```bash
poetry install
```

`stdio` mode (one key, one workspace):

```bash
export PINBRIDGE_MCP_PINBRIDGE_API_KEY=pb_...
poetry run pinbridge-mcp --transport stdio
```

HTTP mode:

```bash
poetry run pinbridge-mcp --transport http --host 127.0.0.1 --port 57289
```

Connect an MCP client to `http://127.0.0.1:57289/` with an `Authorization: Bearer pb_...` header. `/healthz` answers without auth. To target a local API stack instead of production, set `PINBRIDGE_MCP_PINBRIDGE_BASE_URL` (for example `http://127.0.0.1:8976` for the compose stack below).

Tests and lint:

```bash
poetry run pytest
poetry run ruff check src tests && poetry run ruff format --check src tests
```

## Docker

The image installs `pinbridge-sdk` from PyPI, so it does not depend on a sibling SDK checkout. Commands run from this directory.

```bash
docker build -t pinbridge-mcp:local .
docker compose up --build mcp            # MCP only, against PINBRIDGE_MCP_PINBRIDGE_BASE_URL
```

The `full` profile also starts Postgres, Redis and the PinBridge API, worker and scheduler from the sibling `api/` checkout (it reads `../api/.env`):

```bash
cp .env.example .env
PINBRIDGE_MCP_PINBRIDGE_BASE_URL=http://api:8000 docker compose --profile full up --build
```

Ports: MCP `57289`, API `8976`, Postgres `5433`, Redis `6379`.

## Deploying

Run the ASGI app from `pinbridge_mcp.http:create_app` (the Docker `CMD` does this) behind TLS on a dedicated hostname:

- keep the MCP endpoint at `/` and `/healthz` unauthenticated for probes
- set `PINBRIDGE_MCP_PUBLIC_BASE_URL` to the public origin, or the transport-security host check rejects requests
- treat it as a stateless edge service; scale horizontally
- `nginx/mcp.pinbridge.io.conf` is the vhost used in production (host nginx proxying to the published container port)

`server.json` is the manifest for the MCP registry (`io.pinbridge/pinbridge`).

## Safety

Write tools are off by default. Production runs with them on, relying on three layers: API-key scopes and account allow-lists on the PinBridge side, `destructiveHint` on `delete_pin`, `delete_board`, `delete_schedule` and `delete_webhook` so clients confirm before calling, and `dry_run` on the publish tools so an agent can preflight for free. `delete_board` removes every pin on the board; the tool description tells agents to use `update_pin` or `delete_pin` for a single wrong pin instead.
