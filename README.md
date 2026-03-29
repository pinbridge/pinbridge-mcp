# PinBridge MCP

`pinbridge-mcp` is a remote-capable MCP server for PinBridge.

The first version is intentionally narrow:

- HTTP transport for `mcp.pinbridge.io`
- `stdio` transport for local development
- bearer API-key passthrough for remote access
- read-focused tools over the existing PinBridge API-key surface

This server is workspace-scoped. A PinBridge API key resolves to one workspace, so project switching is out of scope for the API-key transport.

## Current Tools

- `server_info`
- `list_pinterest_accounts`
- `list_boards`
- `list_related_terms`
- `list_pins`
- `get_pin`
- `list_activity_logs`
- `list_webhooks`
- `get_billing_status`
- `get_rate_meter`

## Auth Model

Remote HTTP mode expects:

```text
Authorization: Bearer <pinbridge_api_key>
```

The incoming bearer token is passed through to the PinBridge API. This keeps the MCP server stateless and avoids maintaining a second credential store.

For local `stdio` use, set `PINBRIDGE_MCP_PINBRIDGE_API_KEY`.

## Local Setup

Create a virtualenv and install the local SDK first so the MCP server uses the current workspace copy:

```bash
cd pinbridge-mcp
python3 -m venv .venv
. .venv/bin/activate
pip install -e ../python-sdk
pip install -e ".[dev]"
```

If you want to point at local API containers:

```bash
export PINBRIDGE_MCP_PINBRIDGE_BASE_URL=http://127.0.0.1:8000
```

## Run Locally

`stdio` mode:

```bash
export PINBRIDGE_MCP_PINBRIDGE_API_KEY=pb_...
pinbridge-mcp --transport stdio
```

HTTP mode:

```bash
pinbridge-mcp --transport http --host 127.0.0.1 --port 8001
```

Then connect an MCP client to:

```text
http://127.0.0.1:8001/
```

For HTTP mode, send:

```text
Authorization: Bearer <pinbridge_api_key>
```

`/healthz` is exposed without auth.

## Deploy Shape

For `mcp.pinbridge.io`, run the ASGI app from `pinbridge_mcp.http:create_app` behind TLS on a dedicated hostname.

Notes:

- keep the streamable HTTP endpoint at `/`
- keep `/healthz` unauthenticated for probes
- treat this as a stateless edge service
- add OAuth later if you want broader automatic client auth compatibility

## Safety

Write tools are disabled by default. Set `PINBRIDGE_MCP_ENABLE_WRITE_TOOLS=true` only after adding stricter safeguards.

