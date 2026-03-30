"""CLI entrypoint for local development."""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn

from .auth import PinBridgeAPIKeyVerifier
from .config import get_settings
from .http import create_app
from .server import create_mcp_server


async def _validate_stdio_startup(settings) -> None:
    """Validate API key and plan gate before accepting stdio connections."""
    if not settings.pinbridge_api_key:
        print(
            "Error: PINBRIDGE_MCP_PINBRIDGE_API_KEY is required for stdio mode.\n"
            "Set it in your environment or .env file.",
            file=sys.stderr,
        )
        sys.exit(1)

    if settings.verify_incoming_api_keys:
        verifier = PinBridgeAPIKeyVerifier(settings)
        is_valid, reason = await verifier.verify(settings.pinbridge_api_key)
        if not is_valid:
            print(f"Error: {reason}", file=sys.stderr)
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PinBridge MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="Transport to run",
    )
    parser.add_argument("--host", default=None, help="HTTP bind host")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port")
    args = parser.parse_args()

    settings = get_settings()
    if args.transport == "stdio":
        asyncio.run(_validate_stdio_startup(settings))
        create_mcp_server(settings).run()
        return

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
