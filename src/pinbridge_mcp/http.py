"""ASGI app for remote PinBridge MCP hosting."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .auth import APIKeyPassthroughMiddleware, PinBridgeAPIKeyVerifier
from .config import Settings, get_settings
from .server import create_mcp_server


async def healthcheck(_: object) -> JSONResponse:
    settings = get_settings()
    return JSONResponse(
        {
            "status": "ok",
            "server": "pinbridge-mcp",
            "pinbridge_base_url": settings.pinbridge_base_url,
        }
    )


def create_app(settings: Settings | None = None) -> Starlette:
    """Create the Starlette app used for streamable HTTP hosting."""
    settings = settings or get_settings()
    mcp_app = create_mcp_server(settings).streamable_http_app()

    app = Starlette(
        routes=[
            Route("/healthz", endpoint=healthcheck, methods=["GET"]),
            Mount("/", app=mcp_app),
        ]
    )
    app.add_middleware(
        APIKeyPassthroughMiddleware,
        settings=settings,
        verifier=PinBridgeAPIKeyVerifier(settings),
    )
    return app


app = create_app()

