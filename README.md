# PinBridge MCP

`pinbridge-mcp` is a remote-capable MCP server for PinBridge.

What it provides:

- HTTP transport for `mcp.pinbridge.io` (streamable HTTP, stateless)
- `stdio` transport for local development
- bearer API-key passthrough, plus the built-in OAuth flow for clients such as Claude
- the full publish loop: upload → dry run → publish → measure → edit/delete

This server is workspace-scoped. A PinBridge API key resolves to one workspace, so project switching is out of scope for the API-key transport.

## Current Tools

Read tools (always registered):

- `server_info`, `list_pinterest_accounts`, `list_boards`, `check_board_access`
- `list_related_terms`, `list_pins` (filters: account, board, status, error code, since/until), `get_pin`
- `get_pin_analytics`, `get_account_analytics`
- `list_activity_logs`, `list_webhooks`, `get_billing_status`, `get_rate_meter`
- `list_schedules` (server-side filters), `get_schedule`

Write tools (registered when `PINBRIDGE_MCP_ENABLE_WRITE_TOOLS=true`):

- `upload_asset` (base64 or a URL this server can download; returns an `asset_id`)
- `create_pin` and `create_schedule` (both take `dry_run`: every check the API runs, nothing published), `create_pins_batch`
- `update_pin`, `delete_pin` (Pinterest-side by default), `retry_pin`
- `cancel_schedule`, `retry_schedule` (failed → scheduled), `delete_schedule` (terminal schedules only)
- `create_board`, `delete_board`, `create_webhook`, `update_webhook` (partial: pause with `is_enabled=false`), `delete_webhook`

Every tool carries MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`), so clients can decide what needs a confirmation. Errors surface the API's stable `code` and `remediation`, e.g. `PinBridge API error (422) [board_not_found]: ... Fix: ...`.

Connecting or disconnecting a Pinterest account is deliberately not exposed: connecting needs a person to approve Pinterest's OAuth grant in a browser, and disconnecting drops every pending schedule on the account. Both live in the dashboard, and the server instructions tell agents so.

Resources: `pinbridge://accounts` and `pinbridge://accounts/{account_id}/boards`. Prompt: `publish_pin` (upload → validate → publish → measure).

Requires PinBridge API ≥ 1.30 for `check_board_access`, `update_pin`, `delete_pin`, `dry_run`, analytics, batch and the list filters. Against an older API those tools fail with `PinBridge API error (404): Not Found`, and `delete_pin` reports `reason: api_version_too_old` (record deleted, pin still live on Pinterest). Those calls use the SDK's raw `request`; typed SDK methods follow (see `roadmap/SDK_Roadmap_Sept2026.md` in the workspace).

## Auth Model

Remote HTTP mode expects:

```text
Authorization: Bearer <pinbridge_api_key>
```

The incoming bearer token is passed through to the PinBridge API. This keeps the MCP server stateless and avoids maintaining a second credential store.

For local `stdio` use, set `PINBRIDGE_MCP_PINBRIDGE_API_KEY`.

## Local Setup

Use Poetry for dependency management:

```bash
cd pinbridge-mcp
poetry install
```

If you want to point at local API containers:

```bash
export PINBRIDGE_MCP_PINBRIDGE_BASE_URL=http://127.0.0.1:8976
```

## Run Locally

`stdio` mode:

```bash
export PINBRIDGE_MCP_PINBRIDGE_API_KEY=pb_...
poetry run pinbridge-mcp --transport stdio
```

HTTP mode:

```bash
poetry run pinbridge-mcp --transport http --host 127.0.0.1 --port 57289
```

Then connect an MCP client to:

```text
http://127.0.0.1:57289/
```

For HTTP mode, send:

```text
Authorization: Bearer <pinbridge_api_key>
```

`/healthz` is exposed without auth.

## Deploy Shape

For remote deployment, run the ASGI app from `pinbridge_mcp.http:create_app` behind TLS on a dedicated hostname.

Notes:

- keep the streamable HTTP endpoint at `/`
- keep `/healthz` unauthenticated for probes
- treat this as a stateless edge service

## Docker

The Docker setup assumes you run commands from inside the `pinbridge-mcp` directory.
The image installs `pinbridge-sdk` from PyPI, so it does not depend on a sibling `python-sdk` checkout in production.

```bash
cd pinbridge-mcp
docker build -t pinbridge-mcp:local .
```

Run only the MCP server against a remote or already-running PinBridge API:

```bash
cd pinbridge-mcp
docker compose up --build mcp
```

Run the full local integration stack from this repo:

```bash
cd ../pinbridge-api
cp .env.example .env

cd ../pinbridge-mcp
cp .env.example .env
PINBRIDGE_MCP_PINBRIDGE_BASE_URL=http://api:8000 docker compose --profile full up --build
```

That `full` profile starts:

- `mcp`
- `postgres`
- `redis`
- `api`
- `worker`
- `scheduler`

Ports:

- MCP HTTP: `57289`
- PinBridge API: `8976`
- Postgres: `5433`
- Redis: `6379`

## Safety

Write tools are disabled by default. Set `PINBRIDGE_MCP_ENABLE_WRITE_TOOLS=true` only after adding stricter safeguards.
