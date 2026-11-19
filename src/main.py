#!/usr/bin/env python3
"""
TickTick MCP Server - Main Entry Point

Transports (MCP_TRANSPORT):
- stdio (default): for local MCP clients.
- http (or sse, the old name): the cloud server. Serves
    /mcp                Streamable HTTP, signed in through the browser with
                        TickTick's own login (ticktick_mcp/oauth.py). This is
                        the connector URL.
    /authorize, /token, /register, /revoke, /.well-known/*   (the MCP SDK)
    /oauth/callback     TickTick's redirect back, for both sign-ins below
    /oauth/start        sign in to TickTick by hand to renew the server's token
    /sse, /messages/    the old UNAUTHENTICATED endpoint (see legacy_enabled)
    /health, /status    state, never secrets
    /favicon.ico, /favicon.svg, /apple-touch-icon.png, /icon.png
"""

import contextlib
import logging
import os
import threading

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

# Import config (initializes environment variables)
from ticktick_mcp import config

# Import the MCP instance
from ticktick_mcp.mcp_instance import mcp, oauth_provider
from ticktick_mcp.icons import icon_routes
from ticktick_mcp.oauth import page, public_url, redirect_uri_source, ticktick_redirect_uri
from ticktick_mcp.token_store import get_store
from ticktick_mcp.unofficial_client import UnofficialAPIClient, client_status as unofficial_client_status
from ticktick_mcp.unofficial_client import credentials_shape

# Import and register tools
# These imports cause the @mcp.tool() decorators to register the tools
logging.info("Registering MCP tools...")
from ticktick_mcp.tools import project_tools  # noqa: F401,E402
from ticktick_mcp.tools import task_tools  # noqa: F401,E402
from ticktick_mcp.tools import unofficial_tools  # noqa: F401,E402
logging.info("Tool registration complete.")


# --- Unofficial API: connect in the background at startup ---------------------- #
#
# So a deploy says in its own logs whether the private API works, without the
# server waiting on TickTick before it can answer anything. It resumes a cached
# session when there is one (no login) and is never fatal: the client retries
# with backoff and the official tools are unaffected. TICKTICK_DEFER_LOGIN=1
# authenticates on first use instead.

def connect_unofficial_in_background() -> None:
    client = UnofficialAPIClient()
    logging.info(f"Unofficial API device id: {client.device_id}")
    # Lengths and whitespace flags only, never the values.
    logging.info(f"Unofficial API credential shape: {credentials_shape()}")
    if not client.credentials_configured() and not os.getenv("TICKTICK_SESSION_TOKEN"):
        logging.warning("Unofficial API: no TICKTICK_USERNAME/PASSWORD or TICKTICK_SESSION_TOKEN; "
                        "unofficial_* tools will say they are unavailable.")
        return
    defer = os.environ.get("TICKTICK_DEFER_LOGIN", "").lower() in ("1", "true", "yes")

    def run():
        if client.ensure_connected(allow_login=not defer):
            logging.info(f"Unofficial API connected (session: {client.status()['session_source']}).")
        elif defer:
            logging.info("Unofficial API: TICKTICK_DEFER_LOGIN set; will authenticate on first use.")
        else:
            logging.error(f"Unofficial API not connected: {client.unavailable_reason()}")

    threading.Thread(target=run, name="unofficial-connect", daemon=True).start()


# --- The old unauthenticated /sse endpoint -------------------------------------- #

def legacy_enabled() -> bool:
    """The old /sse endpoint: anyone with the URL can use it, no sign-in.

    Kept so the connector that exists today keeps working until the owner
    connects through /mcp; it switches itself off for good the first time an AI
    app gets a token from the browser sign-in. LEGACY_SSE=on / off overrides.
    """
    flag = (os.environ.get("LEGACY_SSE") or "").strip().lower()
    if flag in ("on", "1", "true", "yes"):
        return True
    if flag in ("off", "0", "false", "no"):
        return False
    return not get_store().connected_at()


def _legacy_gone() -> JSONResponse:
    return JSONResponse({
        "error": "This endpoint is closed. Connect to /mcp instead (it signs you in with TickTick).",
        "mcp_url": public_url() + "/mcp",
    }, status_code=410)


# --- OAuth routes ---------------------------------------------------------------- #

async def start_oauth(request: Request):
    """Sign in to TickTick in the browser to renew the server's token by hand.

    The account is checked against the owner exactly as in the /mcp sign-in, and
    the token is saved, never shown.
    """
    if not (config.CLIENT_ID and config.CLIENT_SECRET):
        return page("Not configured", "TICKTICK_CLIENT_ID and TICKTICK_CLIENT_SECRET are not set.", 500)
    return RedirectResponse(oauth_provider.start_direct(), status_code=302)


async def oauth_callback(request: Request):
    """TickTick's redirect back, for the /mcp sign-in and /oauth/start alike."""
    handled = await oauth_provider.ticktick_callback(request)
    if handled is not None:
        return handled
    return page("Unknown or expired sign-in",
                "This sign-in isn't one this server started, or it was already used. Start again.", 400)


# --- Health and status ------------------------------------------------------------ #

def _official_state() -> dict:
    token, source = config.official_token()
    saved = get_store().ticktick_token() or {}
    return {
        "authenticated": token is not None,
        "token_source": source,  # "sign-in", "env", "sign-in (expired)" or null
        "signed_in_token_expires_at": saved.get("expires_at"),
        "env_token_configured": bool(config.ACCESS_TOKEN),
        "user_id_configured": config.USER_ID is not None,
    }


async def health_check(request: Request):
    store = get_store()
    owner = store.owner()
    return JSONResponse({
        "status": "ok",
        "service": "TickTick MCP",
        "mcp_url": public_url() + "/mcp",
        "ticktick_redirect_uri": ticktick_redirect_uri(),
        "ticktick_redirect_uri_source": redirect_uri_source(),
        "official_api": _official_state(),
        "owner_locked": bool(owner),
        "legacy_sse": legacy_enabled(),
        "storage_persistent": store.persistent(),
        "commit": (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:12] or None,
    })


async def status_check(request: Request):
    """Server status for both APIs. Unauthenticated, so it never holds a secret."""
    client = config.get_ticktick_client()
    return JSONResponse({
        "status": "running",
        **_official_state(),
        "inbox_available": client.inbox_id if client else None,
        "legacy_sse": legacy_enabled(),
        # The official and unofficial clients authenticate differently and
        # fail independently, so report them separately.
        "unofficial_api": unofficial_client_status(),
    })


# --- The app ------------------------------------------------------------------------ #

def build_app():
    """The ASGI app: /mcp and the OAuth endpoints from the SDK, plus ours."""
    from mcp.server.sse import SseServerTransport

    app = mcp.streamable_http_app()  # /mcp, /authorize, /token, /register, /revoke, discovery

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app.router.lifespan_context = lifespan

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        if not legacy_enabled():
            return _legacy_gone()
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await mcp._mcp_server.run(streams[0], streams[1],
                                      mcp._mcp_server.create_initialization_options())
        return Response()

    async def handle_messages(scope, receive, send):
        if not legacy_enabled():
            await _legacy_gone()(scope, receive, send)
            return
        await sse.handle_post_message(scope, receive, send)

    app.router.routes.extend([
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=handle_messages),
        Route("/oauth/start", endpoint=start_oauth, methods=["GET"]),
        Route("/oauth/callback", endpoint=oauth_callback, methods=["GET"]),
        Route("/health", endpoint=health_check, methods=["GET"]),
        Route("/status", endpoint=status_check, methods=["GET"]),
        *icon_routes(),
    ])
    return app


def main():
    """Run the MCP server over stdio, or over HTTP for a cloud deploy."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport not in ("sse", "http", "streamable-http"):
        connect_unofficial_in_background()
        mcp.run(transport="stdio")
        return

    port = int(os.environ.get("PORT", 8000))
    app = build_app()
    store = get_store()
    connect_unofficial_in_background()

    print(f"Starting TickTick MCP server on port {port}")
    print(f"Connector URL: {public_url()}/mcp (sign in with TickTick in the browser)")
    print(f"TickTick redirect URI: {ticktick_redirect_uri()} ({redirect_uri_source()})")
    print(f"Legacy /sse: {'ON' if legacy_enabled() else 'off'}")
    print(f"Token store: {store.path} "
          f"({'persistent' if store.persistent() else 'NOT persistent: mount a volume and set DATA_DIR'})")
    print(f"Official API token: {config.official_token()[1] or 'none'}")

    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
