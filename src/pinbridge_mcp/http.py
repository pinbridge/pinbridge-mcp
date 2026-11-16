"""ASGI app for remote PinBridge MCP hosting."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

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


async def oauth_protected_resource(_: object) -> JSONResponse:
    """OAuth 2.0 Protected Resource Metadata (RFC 9728).

    Lets an MCP client (e.g. Claude) discover the PinBridge authorization server
    so it can run the OAuth flow instead of prompting for a pasted API key.
    """
    settings = get_settings()
    return JSONResponse(
        {
            "resource": settings.normalized_public_base_url,
            "authorization_servers": [settings.pinbridge_base_url.rstrip("/")],
            "scopes_supported": ["mcp"],
        }
    )


GOOGLE_CONNECTED_APPS_PATH = "/.well-known/google-connected-apps"


def google_connected_apps_endpoint(
    settings: Settings,
) -> Callable[[object], Awaitable[JSONResponse]]:
    """Domain-ownership file for Google's Connected Console.

    Google fetches it when it verifies the MCP domain and wants 200, no redirect,
    and the console's tokens in ``verification_tokens``. With no token configured
    the path is a 404, so the server never claims a verification it doesn't have.
    """

    async def google_connected_apps(_: object) -> JSONResponse:
        tokens = settings.google_connected_apps_token_list
        if not tokens:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({"verification_tokens": tokens})

    return google_connected_apps


def create_app(settings: Settings | None = None) -> Starlette:
    """Create the Starlette app used for streamable HTTP hosting."""
    settings = settings or get_settings()
    mcp_server = create_mcp_server(settings)
    mcp_app = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        async with mcp_server.session_manager.run():
            yield

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/healthz", endpoint=healthcheck, methods=["GET"]),
            Route(
                "/.well-known/oauth-protected-resource",
                endpoint=oauth_protected_resource,
                methods=["GET"],
            ),
            Route(
                GOOGLE_CONNECTED_APPS_PATH,
                endpoint=google_connected_apps_endpoint(settings),
                methods=["GET"],
            ),
            Mount("/", app=mcp_app),
        ],
    )
    app.add_middleware(
        APIKeyPassthroughMiddleware,
        settings=settings,
        verifier=PinBridgeAPIKeyVerifier(settings),
    )
    return app


app = create_app()
