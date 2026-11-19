"""
Sign-in for the MCP endpoint (/mcp): an OAuth 2.1 authorization server whose
login IS TickTick's own, in the browser.

How a connection goes (Claude, or any MCP client):
  1. The client calls /mcp with no token, gets 401, and finds this server's
     OAuth endpoints through the discovery documents the SDK serves.
  2. It registers itself (/register) and sends you to /authorize.
  3. /authorize sends you straight on to TickTick's sign-in page
     (https://ticktick.com/oauth/authorize, scopes tasks:read tasks:write).
  4. TickTick sends you back to /oauth/callback. The code is traded for a
     TickTick token, the account is checked against the owner (below), and only
     then is the token saved (token store) and the client given a one-time code.
     Any other account is turned away and nothing is saved.
  5. The client trades the code for an access token (1 hour) and a refresh token
     (90 days, replaced on every use) at /token, and calls /mcp with it.

Who the owner is. TickTick's OpenAPI exposes no user id: /open/v1/user/info
returns a display name and an avatar URL, nothing else. So the account is
recognised by what CAN be read:
  - the numeric user id, when the Inbox has a task in it (Inbox tasks carry
    projectId "inbox<userId>"); compared with TICKTICK_USER_ID when that is set,
    or with the id recorded at the first sign-in. Decisive when both sides know it.
  - otherwise the display name AND at least half of the owner's project ids
    (project ids are per account; a shared project appears in both lists, which
    is why it is half and not "any"). With no projects to compare, the avatar URL
    stands in for them.
The reference is the identity recorded at the last accepted sign-in, or, before
any, the account behind TICKTICK_ACCESS_TOKEN (the token this deployment already
runs on). Only when neither exists is the first sign-in accepted and locked in.

The MCP SDK (mcp.server.auth) supplies the OAuth endpoints, discovery, PKCE
checks and the bearer-token check on /mcp; this module supplies the storage and
the TickTick step.
"""

import base64
import logging
import math
import os
import secrets
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode, urlparse

import httpx
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from ticktick_mcp.token_store import get_store, token_hash

logger = logging.getLogger(__name__)

TICKTICK_AUTHORIZE_URL = "https://ticktick.com/oauth/authorize"
TICKTICK_TOKEN_URL = "https://ticktick.com/oauth/token"
TICKTICK_API_BASE = "https://api.ticktick.com/open/v1"
TICKTICK_SCOPES = "tasks:read tasks:write"

MCP_SCOPE = "mcp"
ACCESS_TTL = 60 * 60              # 1 hour
REFRESH_TTL = 90 * 24 * 60 * 60   # 90 days, rotated on every use
CODE_TTL = 5 * 60
PENDING_TTL = 15 * 60
# TickTick's tokens last about 180 days and come without a refresh token.
DEFAULT_TICKTICK_TTL = 15551999

KIND_MCP = "mcp"        # a sign-in started by an AI app through /authorize
KIND_DIRECT = "direct"  # /oauth/start: renew the server's TickTick token by hand

# Tests swap in an httpx.MockTransport that plays TickTick.
http_transport: Optional[httpx.AsyncBaseTransport] = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=http_transport, timeout=20.0)


# -- configuration --------------------------------------------------------------

def public_url() -> str:
    """This server's public address, e.g. https://ticktick-mcp.up.railway.app"""
    url = os.environ.get("PUBLIC_URL") or os.environ.get("MCP_PUBLIC_URL")
    if not url and os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        url = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
    if not url:
        url = f"http://localhost:{os.environ.get('PORT', '8000')}"
    return url.rstrip("/")


def ticktick_redirect_uri() -> str:
    """The redirect URI sent to TickTick: it must be registered in the developer portal.

    TICKTICK_REDIRECT_URI is reused when it already points at this server's
    /oauth/callback (so a redirect registered for the old /oauth/start flow keeps
    working); anything else there, a localhost value for instance, is ignored.
    """
    computed = public_url() + "/oauth/callback"
    configured = (os.environ.get("TICKTICK_REDIRECT_URI") or "").strip()
    if configured and _same_callback(configured, computed):
        return configured
    return computed


def redirect_uri_source() -> str:
    configured = (os.environ.get("TICKTICK_REDIRECT_URI") or "").strip()
    if configured and _same_callback(configured, public_url() + "/oauth/callback"):
        return "TICKTICK_REDIRECT_URI"
    return "computed from the public URL"


def _same_callback(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc.lower(), pa.path.rstrip("/")) == (pb.scheme, pb.netloc.lower(), pb.path.rstrip("/"))


def client_credentials() -> tuple[Optional[str], Optional[str]]:
    return os.environ.get("TICKTICK_CLIENT_ID"), os.environ.get("TICKTICK_CLIENT_SECRET")


def configured_user_id() -> Optional[str]:
    v = (os.environ.get("TICKTICK_OWNER_USER_ID") or os.environ.get("TICKTICK_USER_ID") or "").strip()
    return v or None


# -- talking to TickTick ----------------------------------------------------------

async def exchange_code(code: str) -> Dict[str, Any]:
    """Trade TickTick's authorization code for a token.

    TickTick's docs put the client credentials in a Basic header; this server's
    original flow sent them in the form body, which also worked. Basic first,
    then the body if TickTick refuses the client.
    """
    client_id, client_secret = client_credentials()
    form = {
        "code": code,
        "grant_type": "authorization_code",
        "scope": TICKTICK_SCOPES,
        "redirect_uri": ticktick_redirect_uri(),
    }
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with _client() as http:
        r = await http.post(TICKTICK_TOKEN_URL, data=form, headers={"Authorization": f"Basic {basic}"})
        if r.status_code in (400, 401) and "access_token" not in r.text:
            r = await http.post(TICKTICK_TOKEN_URL, data={**form, "client_id": client_id, "client_secret": client_secret})
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code != 200 or not body.get("access_token"):
        # Error codes only: the body could echo something sensitive.
        raise RuntimeError(f"TickTick token exchange failed: HTTP {r.status_code} "
                           f"{body.get('error') or ''} {body.get('error_description') or ''}".strip())
    expires_in = int(body.get("expires_in") or DEFAULT_TICKTICK_TTL)
    info = {
        "access_token": body["access_token"],
        "token_type": body.get("token_type", "bearer"),
        "scope": body.get("scope", TICKTICK_SCOPES),
        "expires_at": int(time.time()) + expires_in,
        "obtained_at": int(time.time()),
    }
    if body.get("refresh_token"):
        info["refresh_token"] = body["refresh_token"]
    return info


async def fetch_identity(access_token: str) -> Dict[str, Any]:
    """What can be learned about the account behind a TickTick token (see module docstring)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    identity: Dict[str, Any] = {"name": None, "avatar": None, "user_id": None, "project_ids": []}
    async with _client() as http:
        r = await http.get(f"{TICKTICK_API_BASE}/user/info", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"TickTick /user/info answered HTTP {r.status_code}")
        info = r.json() or {}
        identity["name"] = info.get("name")
        identity["avatar"] = info.get("avatarUrl")
        r = await http.get(f"{TICKTICK_API_BASE}/project", headers=headers)
        if r.status_code == 200 and isinstance(r.json(), list):
            identity["project_ids"] = sorted(p["id"] for p in r.json() if isinstance(p, dict) and p.get("id"))
        r = await http.get(f"{TICKTICK_API_BASE}/project/inbox/data", headers=headers)
        if r.status_code == 200:
            for task in (r.json() or {}).get("tasks") or []:
                pid = str(task.get("projectId") or "")
                if pid.startswith("inbox") and pid[5:].isdigit():
                    identity["user_id"] = pid[5:]
                    break
    return identity


def identity_matches(new: Dict[str, Any], reference: Optional[Dict[str, Any]],
                     expected_user_id: Optional[str]) -> tuple[bool, str]:
    """Is `new` the owner's account? Returns (ok, why)."""
    if new.get("user_id") and expected_user_id:
        ok = str(new["user_id"]) == str(expected_user_id)
        return ok, "user id matches TICKTICK_USER_ID" if ok else "user id differs from TICKTICK_USER_ID"
    if reference is None:
        return True, "first sign-in: locked to this account"
    if new.get("user_id") and reference.get("user_id"):
        ok = str(new["user_id"]) == str(reference["user_id"])
        return ok, "user id matches" if ok else "user id differs"
    if not new.get("name") or new.get("name") != reference.get("name"):
        return False, "display name differs"
    ref_projects = set(reference.get("project_ids") or [])
    if ref_projects:
        shared = len(ref_projects & set(new.get("project_ids") or []))
        need = max(1, math.ceil(len(ref_projects) / 2))
        ok = shared >= need
        return ok, f"{shared} of {len(ref_projects)} owner projects present (need {need})"
    ok = bool(new.get("avatar")) and new.get("avatar") == reference.get("avatar")
    return ok, "name and avatar match" if ok else "no projects to compare and avatar differs"


async def owner_reference() -> Optional[Dict[str, Any]]:
    """The identity a new sign-in is compared with (see module docstring)."""
    stored = get_store().owner()
    if stored:
        return stored
    env_token = os.environ.get("TICKTICK_ACCESS_TOKEN")
    if env_token:
        try:
            return await fetch_identity(env_token)
        except Exception as e:
            logger.warning(f"Could not read the account behind TICKTICK_ACCESS_TOKEN: {e}")
    return None


def _merge_identity(new: Dict[str, Any], old: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(new)
    if not merged.get("user_id") and old and old.get("user_id"):
        merged["user_id"] = old["user_id"]
    merged["verified_at"] = int(time.time())
    return merged


# -- the provider ---------------------------------------------------------------

class LenientClient(OAuthClientInformationFull):
    """A registered client that is never refused over a scope name: whatever it
    asks for, it gets "mcp", this server's only scope."""

    def validate_scope(self, requested_scope):  # noqa: D401
        return [MCP_SCOPE]


class TickTickOAuthProvider:
    """OAuthAuthorizationServerProvider for the SDK, backed by the token store."""

    def __init__(self, on_ticktick_token: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.on_ticktick_token = on_ticktick_token

    @property
    def store(self):
        return get_store()

    # -- clients ----------------------------------------------------------------
    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        info = self.store.get("clients", client_id)
        return LenientClient.model_validate(info) if info else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.scope:
            client_info.scope = MCP_SCOPE
        self.store.put("clients", client_info.client_id, client_info.model_dump(mode="json"))

    # -- authorize: hand off to TickTick ------------------------------------------
    def ticktick_authorize_url(self, state: str) -> str:
        client_id, _ = client_credentials()
        return TICKTICK_AUTHORIZE_URL + "?" + urlencode({
            "client_id": client_id,
            "scope": TICKTICK_SCOPES,
            "state": state,
            "redirect_uri": ticktick_redirect_uri(),
            "response_type": "code",
        })

    def start_direct(self) -> str:
        """/oauth/start: sign in to TickTick only to renew the server's token."""
        self.store.prune()
        state = secrets.token_urlsafe(32)
        self.store.put("pending", state, {"kind": KIND_DIRECT, "expires_at": time.time() + PENDING_TTL})
        return self.ticktick_authorize_url(state)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        client_id, client_secret = client_credentials()
        if not client_id or not client_secret:
            raise AuthorizeError("server_error",
                                 "TICKTICK_CLIENT_ID / TICKTICK_CLIENT_SECRET are not set on this server.")
        self.store.prune()
        state = secrets.token_urlsafe(32)
        self.store.put("pending", state, {
            "kind": KIND_MCP,
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "code_challenge": params.code_challenge,
            "state": params.state,
            "resource": params.resource,
            "expires_at": time.time() + PENDING_TTL,
        })
        return self.ticktick_authorize_url(state)

    async def ticktick_callback(self, request: Request):
        """TickTick's redirect back. None when the state isn't a sign-in of ours."""
        state = request.query_params.get("state")
        pending = self.store.get("pending", state) if state else None
        if not pending:
            return None
        self.store.pop("pending", state)
        direct = pending.get("kind") == KIND_DIRECT
        if pending.get("expires_at", 0) < time.time():
            return page("That sign-in took too long", "Start it again.", 400)

        def fail(error: str, description: str, status: int = 400):
            if direct:
                return page("TickTick sign-in failed", description, status)
            return RedirectResponse(construct_redirect_uri(
                pending["redirect_uri"], state=pending.get("state"),
                error=error, error_description=description), status_code=302)

        if request.query_params.get("error"):
            return fail("access_denied", "TickTick sign-in was cancelled.")
        code = request.query_params.get("code")
        if not code:
            return fail("invalid_request", "TickTick returned no code.")
        try:
            token = await exchange_code(code)
            identity = await fetch_identity(token["access_token"])
        except Exception as e:
            logger.error(f"[oauth] TickTick sign-in failed: {e}")
            return fail("server_error", "TickTick sign-in failed. Check that the redirect URI is "
                                        "registered in the TickTick developer portal.")

        reference = await owner_reference()
        ok, why = identity_matches(identity, reference, configured_user_id())
        if not ok:
            logger.warning(f"[oauth] refused a TickTick sign-in from another account ({why})")
            return page("This server belongs to someone else",
                        "Only the owner's TickTick account can connect to it. Nothing was saved.", 403)
        logger.info(f"[oauth] owner signed in to TickTick ({why})")
        self.store.set_owner(_merge_identity(identity, reference))
        self.store.set_ticktick_token(token)
        if self.on_ticktick_token:
            self.on_ticktick_token(token)

        if direct:
            return page("TickTick connected",
                        "The server's TickTick token was renewed and saved. You can close this tab.")
        mcp_code = secrets.token_urlsafe(32)
        self.store.put("codes", mcp_code, {
            "client_id": pending["client_id"],
            "redirect_uri": pending["redirect_uri"],
            "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
            "code_challenge": pending["code_challenge"],
            "resource": pending.get("resource"),
            "scopes": [MCP_SCOPE],
            "expires_at": time.time() + CODE_TTL,
        })
        return RedirectResponse(construct_redirect_uri(
            pending["redirect_uri"], state=pending.get("state"), code=mcp_code), status_code=302)

    # -- codes and tokens ---------------------------------------------------------
    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> Optional[AuthorizationCode]:
        row = self.store.get("codes", authorization_code)
        if not row or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(code=authorization_code, **{k: row[k] for k in (
            "scopes", "expires_at", "client_id", "code_challenge", "redirect_uri",
            "redirect_uri_provided_explicitly", "resource")})

    def _issue(self, client_id: str, scopes, resource: Optional[str]) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(40), secrets.token_urlsafe(48)
        now = int(time.time())
        pair = token_hash(refresh)
        self.store.put("access", token_hash(access), {
            "client_id": client_id, "scopes": scopes, "resource": resource,
            "expires_at": now + ACCESS_TTL, "refresh": pair})
        self.store.put("refresh", pair, {
            "client_id": client_id, "scopes": scopes, "resource": resource,
            "expires_at": now + REFRESH_TTL})
        self.store.mark_connected()
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        row = self.store.pop("codes", authorization_code.code)
        if not row:
            raise TokenError("invalid_grant", "That code was already used.")
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> Optional[RefreshToken]:
        row = self.store.get("refresh", token_hash(refresh_token))
        if not row or row["client_id"] != client.client_id or row["expires_at"] < time.time():
            return None
        return RefreshToken(token=refresh_token, client_id=row["client_id"],
                            scopes=row["scopes"], expires_at=row["expires_at"])

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken, scopes) -> OAuthToken:
        # TickTick tokens can't be refreshed. When the saved one has run out,
        # refuse the refresh so the AI app signs in again (a new TickTick token)
        # instead of holding an MCP token that only produces 401s.
        tt = self.store.ticktick_token()
        if tt and tt.get("expires_at", 0) < time.time():
            raise TokenError("invalid_grant", "The TickTick sign-in has expired; sign in again.")
        h = token_hash(refresh_token.token)
        row = self.store.pop("refresh", h)
        if not row:
            raise TokenError("invalid_grant", "Refresh token is no longer valid.")
        self.store.delete_where("access", refresh=h)
        return self._issue(client.client_id, row["scopes"], row.get("resource"))

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        row = self.store.get("access", token_hash(token))
        if not row or row["expires_at"] < time.time():
            return None
        return AccessToken(token=token, client_id=row["client_id"], scopes=row["scopes"],
                           expires_at=row["expires_at"], resource=row.get("resource"))

    async def revoke_token(self, token) -> None:
        h = token_hash(token.token)
        if isinstance(token, RefreshToken):
            self.store.pop("refresh", h)
            self.store.delete_where("access", refresh=h)
        else:
            row = self.store.pop("access", h)
            if row and row.get("refresh"):
                self.store.pop("refresh", row["refresh"])


def page(title: str, text: str, status: int = 200) -> HTMLResponse:
    from html import escape
    return HTMLResponse(
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<link rel=icon href='/favicon.svg' type='image/svg+xml'>"
        f"<title>{escape(title)}</title><body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1 style='font-size:1.3rem'>{escape(title)}</h1><p>{escape(text)}</p></body>", status_code=status)
