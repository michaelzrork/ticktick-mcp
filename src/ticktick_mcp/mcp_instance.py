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

SENDING DATES AND TIMES (every tool that takes start_date / due_date):
- Send the time the user means, as plain local wall-clock time with NO offset
  and NO "Z": "2026-11-19T08:00:00" for 8:00 AM. Always pass time_zone too
  (the user's IANA zone, default "America/New_York").
- The server converts it to UTC itself, daylight saving included. NEVER convert
  to UTC yourself: sending "13:00:00" to mean 8:00 AM Eastern makes it 1:00 PM.
- A time with an explicit offset ("2026-11-19T08:00:00-05:00") is also accepted
  and honored as written, but plain local time is the expected form.
- All-day tasks: a date only, no time ("2026-11-19"), with is_all_day=True.
  Don't add T00:00:00.
- Set start_date and due_date to the same value for a task at one moment;
  different values make a date range.
- Converting timed to all-day (or back) needs is_all_day AND both dates.

READING DATES BACK:
- TickTick returns times in UTC, written with "+0000" (for example
  "2026-11-19T13:00:00.000+0000"). Convert to the task's timeZone before telling
  the user: that example is 8:00 AM in America/New_York.
- After creating or moving a timed task, read it back once and check the local
  time is the one the user asked for.
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
