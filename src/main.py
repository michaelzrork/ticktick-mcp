#!/usr/bin/env python3

import sys
import logging
import uvicorn
import os
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response
from mcp.server.sse import SseServerTransport
import json
from urllib.parse import urlencode
from starlette.responses import RedirectResponse, JSONResponse

from ticktick_mcp import config

# --- Core Imports --- #
# Import the MCP instance
from ticktick_mcp.mcp_instance import mcp

# TickTick Client Initialization (using the new singleton)
from ticktick_mcp.client import TickTickClientSingleton

# --- Tool Registration --- #
# Import tool modules AFTER mcp instance is created.
# The @mcp.tool() decorators in these modules will register functions
# with the imported 'mcp' instance.
logging.info("Registering MCP tools...")
from ticktick_mcp.tools import task_tools
from ticktick_mcp.tools import filter_tools
from ticktick_mcp.tools import conversion_tools
logging.info("Tool registration complete.")

# --- OAuth Routes (for cloud deployment) --- #
async def start_oauth(request):
    """Initiate OAuth flow - redirects user to TickTick authorization page."""
    from ticktick_mcp.config import CLIENT_ID, REDIRECT_URI
    
    auth_url = "https://ticktick.com/oauth/authorize"
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "tasks:write tasks:read",
        "state": "ticktick_oauth"
    }
    url = f"{auth_url}?{urlencode(params)}"
    return RedirectResponse(url=url)

async def oauth_callback(request):
    """Handle OAuth callback - exchanges code for token."""
    from ticktick_mcp.config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI
    import httpx
    
    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error": "No authorization code received"}, status_code=400)
    
    # Exchange code for token
    token_url = "https://ticktick.com/oauth/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        token_data = response.json()
    
    if "access_token" in token_data:
        # Return token as JSON for user to copy
        return JSONResponse({
            "success": True,
            "message": "Copy this entire token JSON and set it as TICKTICK_OAUTH_TOKEN environment variable",
            "token": token_data
        })
    else:
        return JSONResponse({"error": "Failed to get access token", "details": token_data}, status_code=400)

# --- Main Execution Logic --- #
def main():
    # Check if running in HTTP mode (Railway/cloud) or stdio mode (local)
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
        
        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages", app=sse.handle_post_message),
                Route("/health", endpoint=health_check, methods=["GET"]),
                Route("/oauth/start", endpoint=start_oauth, methods=["GET"]),
                Route("/oauth/callback", endpoint=oauth_callback, methods=["GET"]),
            ]
        )
        
        print(f"Starting TickTick MCP server on port {port}")
        print(f"Health check available at: /health")
        
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        # stdio mode for local development
        mcp.run(transport="stdio")

# --- Script Entry Point --- #
if __name__ == "__main__":
    main()