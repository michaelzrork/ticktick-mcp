#!/usr/bin/env python3
"""
TickTick MCP Server - Main Entry Point

Supports two transport modes:
- stdio: For local MCP clients (default)
- http:  For cloud deployment (Railway, etc.)

Set MCP_TRANSPORT to "http" (or "sse", kept for backwards compatibility) to serve
over HTTP. That mode exposes BOTH MCP transports from one app:

- /mcp  Streamable HTTP. The current transport; this is what claude.ai custom
        connectors and other modern clients speak. Point new clients here.
- /sse  Legacy HTTP+SSE, with its companion /messages/ endpoint, for older
        clients that only support it.

A client pointed at /sse that speaks streamable HTTP POSTs its initialize request
and gets 405 back, which reads to the client as "this needs authorization" - it
then looks for OAuth metadata that an unauthenticated server does not publish and
reports a failure to start authorization.
"""

import logging
import os
from urllib.parse import urlencode

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.responses import Response, RedirectResponse, JSONResponse

# Import config (initializes environment variables)
from ticktick_mcp import config

# Import the MCP instance
from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp.unofficial_client import client_status as unofficial_client_status

# Import and register tools
# These imports cause the @mcp.tool() decorators to register the tools
logging.info("Registering MCP tools...")
from ticktick_mcp.tools import project_tools  # noqa: F401
from ticktick_mcp.tools import task_tools  # noqa: F401
from ticktick_mcp.tools import unofficial_tools  # noqa: F401
logging.info("Tool registration complete.")

# Eager init - try to log in to the unofficial API at startup rather than on the
# first tool call. A failure here is not fatal and not permanent: the client
# retries with backoff on next use, so a rate-limited (429) login at boot no
# longer takes the unofficial tools down for the life of the process.
from ticktick_mcp.unofficial_client import UnofficialAPIClient
_unofficial = UnofficialAPIClient()
if _unofficial.ensure_connected():
    logging.info("Unofficial API client connected.")
else:
    logging.warning(f"Unofficial API not connected at startup: {_unofficial.unavailable_reason()}")


# --- OAuth Routes (for cloud deployment) --- #

@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return Response("OK", status_code=200)


@mcp.custom_route("/status", methods=["GET"])
async def status_check(request):
    """Return server status including auth state for both APIs."""
    client = config.get_ticktick_client()
    return JSONResponse({
        "status": "running",
        "authenticated": client is not None,
        "user_id_configured": config.USER_ID is not None,
        "inbox_available": client.inbox_id if client else None,
        # The official and unofficial clients fail independently, so report them
        # separately - the tools that break are usually only the unofficial ones.
        "unofficial_api": unofficial_client_status(),
    })


@mcp.custom_route("/oauth/start", methods=["GET"])
async def start_oauth(request):
    """Initiate OAuth flow - redirects user to TickTick authorization page."""
    auth_url = "https://ticktick.com/oauth/authorize"
    params = {
        "client_id": config.CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "response_type": "code",
        "scope": "tasks:write tasks:read",
        "state": "ticktick_oauth"
    }
    url = f"{auth_url}?{urlencode(params)}"
    return RedirectResponse(url=url)


@mcp.custom_route("/oauth/callback", methods=["GET"])
async def oauth_callback(request):
    """Handle OAuth callback - exchanges code for token."""
    code = request.query_params.get("code")
    if not code:
        return JSONResponse(
            {"error": "No authorization code received"},
            status_code=400
        )

    # Exchange code for token
    token_url = "https://ticktick.com/oauth/token"
    data = {
        "client_id": config.CLIENT_ID,
        "client_secret": config.CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": config.REDIRECT_URI
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        token_data = response.json()

    if "access_token" in token_data:
        # Save the token
        config.save_tokens(
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data.get("expires_in")
        )

        return JSONResponse({
            "success": True,
            "message": "OAuth complete! Token saved. You can now use the MCP tools.",
            "note": "Set TICKTICK_ACCESS_TOKEN environment variable for cloud deployment",
            "access_token": token_data["access_token"],
            "expires_in": token_data.get("expires_in")
        })
    else:
        return JSONResponse(
            {"error": "Failed to get access token", "details": token_data},
            status_code=400
        )


# --- Main Execution Logic --- #

def main():
    """Run the MCP server in either stdio or SSE mode."""
    # Check transport mode
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport in ("http", "streamable-http", "sse"):
        # HTTP mode for cloud deployment
        port = int(os.environ.get("PORT", 8000))

        # Both SDK apps carry the @mcp.custom_route endpoints, so take the
        # streamable-HTTP app as the base (it owns the session-manager lifespan)
        # and add only the legacy SSE routes on top.
        #
        # host="0.0.0.0" matters: these auto-enable DNS-rebinding protection when
        # the host looks like localhost, which would reject requests carrying a
        # public Host header once deployed.
        http_app = mcp.streamable_http_app(host="0.0.0.0")
        legacy_sse_routes = [
            route
            for route in mcp.sse_app(host="0.0.0.0").routes
            if getattr(route, "path", None) in ("/sse", "/messages")
        ]

        app = Starlette(
            routes=[*http_app.routes, *legacy_sse_routes],
            lifespan=lambda scoped_app: http_app.router.lifespan_context(scoped_app),
        )

        print(f"Starting TickTick MCP server on port {port}")
        print(f"MCP endpoint (streamable HTTP): /mcp")
        print(f"MCP endpoint (legacy SSE): /sse")
        print(f"Health check: /health")
        print(f"Status check: /status")
        print(f"OAuth start: /oauth/start")

        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        # stdio mode for local development
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
