#!/usr/bin/env python3
"""
TickTick MCP Server - Main Entry Point

Supports two transport modes:
- stdio: For local MCP clients (default)
- sse: For cloud deployment (Railway, etc.)

Set MCP_TRANSPORT=sse environment variable to use SSE mode.
"""

import logging
import os
from urllib.parse import urlencode

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response, RedirectResponse, JSONResponse
from mcp.server.sse import SseServerTransport

# Import config (initializes environment variables)
from ticktick_mcp import config

# Import the MCP instance
from ticktick_mcp.mcp_instance import mcp

# Import and register tools
# These imports cause the @mcp.tool() decorators to register the tools
logging.info("Registering MCP tools...")
from ticktick_mcp.tools import project_tools  # noqa: F401
from ticktick_mcp.tools import task_tools  # noqa: F401
from ticktick_mcp.tools import unofficial_tools  # noqa: F401
logging.info("Tool registration complete.")


# --- OAuth Routes (for cloud deployment) --- #

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

    if transport == "sse":
        # HTTP/SSE mode for cloud deployment
        port = int(os.environ.get("PORT", 8000))

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await mcp._mcp_server.run(
                    streams[0], streams[1],
                    mcp._mcp_server.create_initialization_options()
                )
            return Response()

        async def health_check(request):
            return Response("OK", status_code=200)

        async def status_check(request):
            """Return server status including auth state."""
            client = config.get_ticktick_client()
            return JSONResponse({
                "status": "running",
                "authenticated": client is not None,
                "user_id_configured": config.USER_ID is not None,
                "inbox_available": client.inbox_id if client else None
            })

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages", app=sse.handle_post_message),
                Route("/health", endpoint=health_check, methods=["GET"]),
                Route("/status", endpoint=status_check, methods=["GET"]),
                Route("/oauth/start", endpoint=start_oauth, methods=["GET"]),
                Route("/oauth/callback", endpoint=oauth_callback, methods=["GET"]),
            ]
        )

        print(f"Starting TickTick MCP server on port {port}")
        print(f"Health check: /health")
        print(f"Status check: /status")
        print(f"OAuth start: /oauth/start")

        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        # stdio mode for local development
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
