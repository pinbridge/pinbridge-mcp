"""CLI entrypoint for local development."""

from __future__ import annotations

import argparse

import uvicorn

from .config import get_settings
from .http import create_app
from .server import create_mcp_server


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
        create_mcp_server(settings).run()
        return

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )

