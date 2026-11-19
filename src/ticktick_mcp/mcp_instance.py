"""
The shared MCP server. Tools register on it by importing the tools modules.

Over HTTP it serves Streamable HTTP at /mcp, and every request there needs a
token from the browser sign-in (oauth.py): the SDK supplies /authorize, /token,
/register, /revoke and the discovery documents. Stateless JSON responses, so a
redeploy loses no sessions. host 0.0.0.0 keeps the SDK's localhost-only Host
check off the public domain. Under stdio none of the auth applies.
"""

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP

from ticktick_mcp.icons import server_icons
from ticktick_mcp.oauth import MCP_SCOPE, TickTickOAuthProvider, public_url


def _on_ticktick_token(token: dict) -> None:
    # A new token from the sign-in: drop the cached official client so the next
    # call is made with it.
    from ticktick_mcp import config
    config.reset_official_client()


INSTRUCTIONS = """TickTick: the owner's own TickTick account.

- ticktick_* tools use TickTick's official API and always work once signed in.
- unofficial_* tools (pin/unpin, repeatFrom, activity logs, subtasks) use TickTick's
  private web API. When it is unavailable they return an error with
  "unofficial_api_unavailable": true; say so and fall back to a ticktick_* tool.
- Look up real project and task ids before changing anything, and ask before deleting.
"""

oauth_provider = TickTickOAuthProvider(on_ticktick_token=_on_ticktick_token)

mcp = FastMCP(
    "TickTick",
    instructions=INSTRUCTIONS,
    website_url=public_url(),
    icons=server_icons(public_url()),
    auth_server_provider=oauth_provider,
    auth=AuthSettings(
        issuer_url=public_url(),
        resource_server_url=public_url() + "/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True, default_scopes=[MCP_SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[MCP_SCOPE],
        # One resource (/mcp) only; a client that omits `resource` must still work.
        validate_token_resource=False,
    ),
    host="0.0.0.0",
    stateless_http=True,
    json_response=True,
)
