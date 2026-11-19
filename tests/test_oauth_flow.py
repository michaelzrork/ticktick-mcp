"""
The browser sign-in end to end, against a fake TickTick.

One app and one TestClient for the whole module: the SDK's Streamable HTTP
session manager can only be started once per process. The tests run in order
and build on each other (the owner connects first, then others try).
"""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.testclient import TestClient

from ticktick_mcp import config, oauth
from ticktick_mcp import token_store

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PUBLIC = "https://tt.example.com"

# Accounts the fake TickTick knows, by the access token it issued.
OWNER = {"name": "Owner Person", "avatarUrl": "https://img/owner.jpg",
         "projects": ["p1", "p2", "p3", "p4"], "inbox": []}
STRANGER = {"name": "Owner Person", "avatarUrl": "https://img/x.jpg",  # same display name on purpose
            "projects": ["p1", "q2", "q3", "q4"], "inbox": []}         # shares one project
ACCOUNTS = {"env-token": OWNER, "tt-owner": OWNER, "tt-owner2": OWNER, "tt-stranger": STRANGER}


class FakeTickTick(httpx.AsyncBaseTransport):
    def __init__(self):
        self.token_requests = []

    async def handle_async_request(self, request):
        url = request.url
        if url.host == "ticktick.com" and url.path == "/oauth/token":
            form = parse_qs(request.content.decode())
            self.token_requests.append({"form": form, "auth": request.headers.get("authorization")})
            code = form["code"][0]
            if request.headers.get("authorization", "") != "Basic " + base64.b64encode(
                    b"test-client-id:test-client-secret").decode():
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": f"tt-{code}", "token_type": "bearer",
                                             "expires_in": 15551999, "scope": "tasks:read tasks:write"})
        if url.host == "api.ticktick.com" and url.path.startswith("/open/v1/"):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            acct = ACCOUNTS.get(token)
            if not acct:
                return httpx.Response(401)
            path = url.path[len("/open/v1"):]
            if path == "/user/info":
                return httpx.Response(200, json={"name": acct["name"], "avatarUrl": acct["avatarUrl"]})
            if path == "/project":
                return httpx.Response(200, json=[{"id": p, "name": p} for p in acct["projects"]])
            if path == "/project/inbox/data":
                return httpx.Response(200, json={"tasks": acct["inbox"], "columns": []})
        return httpx.Response(404)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DATA_DIR", str(tmp_path_factory.mktemp("store")))
    mp.setenv("PUBLIC_URL", PUBLIC)
    mp.setenv("TICKTICK_CLIENT_ID", "test-client-id")
    mp.setenv("TICKTICK_CLIENT_SECRET", "test-client-secret")
    mp.setenv("TICKTICK_ACCESS_TOKEN", "env-token")
    mp.delenv("TICKTICK_USER_ID", raising=False)
    mp.delenv("TICKTICK_OWNER_USER_ID", raising=False)
    mp.delenv("TICKTICK_REDIRECT_URI", raising=False)
    mp.delenv("LEGACY_SSE", raising=False)
    mp.setattr(config, "ACCESS_TOKEN", "env-token")
    mp.setattr(config, "CLIENT_ID", "test-client-id")
    mp.setattr(config, "CLIENT_SECRET", "test-client-secret")
    fake = FakeTickTick()
    mp.setattr(oauth, "http_transport", fake)
    token_store.reset_store()
    config.reset_official_client()

    import main
    with TestClient(main.build_app(), base_url=PUBLIC) as client:
        yield {"client": client, "fake": fake, "main": main}
    mp.undo()
    token_store.reset_store()
    config.reset_official_client()


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _register(c):
    r = c.post("/register", json={"redirect_uris": [REDIRECT], "client_name": "test",
                                  "token_endpoint_auth_method": "none",
                                  "grant_types": ["authorization_code", "refresh_token"],
                                  "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _authorize(c, client_id, challenge):
    r = c.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "client-state",
        "resource": PUBLIC + "/mcp", "scope": "mcp"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert (loc.scheme, loc.netloc, loc.path) == ("https", "ticktick.com", "/oauth/authorize")
    q = parse_qs(loc.query)
    assert q["scope"] == ["tasks:read tasks:write"]
    assert q["redirect_uri"] == [PUBLIC + "/oauth/callback"]
    assert q["client_id"] == ["test-client-id"]
    assert q["response_type"] == ["code"]
    return q["state"][0]


def _connect(c, ticktick_code):
    """The whole browser dance. Returns (callback response, client_id, verifier)."""
    client_id = _register(c)
    verifier, challenge = _pkce()
    state = _authorize(c, client_id, challenge)
    r = c.get("/oauth/callback", params={"code": ticktick_code, "state": state}, follow_redirects=False)
    return r, client_id, verifier


def _token(c, client_id, verifier, code):
    return c.post("/token", data={"grant_type": "authorization_code", "code": code,
                                  "redirect_uri": REDIRECT, "client_id": client_id,
                                  "code_verifier": verifier, "resource": PUBLIC + "/mcp"})


INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_mcp_requires_a_token(env):
    r = env["client"].post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


def test_discovery_points_at_this_server(env):
    meta = env["client"].get("/.well-known/oauth-authorization-server").json()
    assert meta["authorization_endpoint"] == PUBLIC + "/authorize"
    pr = env["client"].get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"] == PUBLIC + "/mcp"


def test_legacy_sse_is_open_before_anyone_connects(env):
    assert env["main"].legacy_enabled() is True
    assert env["client"].get("/health").json()["legacy_sse"] is True


def test_owner_signs_in_through_the_browser(env):
    c = env["client"]
    r, client_id, verifier = _connect(c, "owner")
    assert r.status_code == 302, r.text
    back = urlparse(r.headers["location"])
    q = parse_qs(back.query)
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    assert q["state"] == ["client-state"]

    # Basic auth to TickTick, with the registered redirect URI.
    req = env["fake"].token_requests[-1]
    assert req["form"]["redirect_uri"] == [PUBLIC + "/oauth/callback"]
    assert req["form"]["grant_type"] == ["authorization_code"]

    tok = _token(c, client_id, verifier, q["code"][0])
    assert tok.status_code == 200, tok.text
    access = tok.json()["access_token"]

    # The TickTick token was saved and the official tools now use it.
    store = token_store.get_store()
    assert store.ticktick_token()["access_token"] == "tt-owner"
    assert store.owner()["name"] == "Owner Person"
    assert config.official_token() == ("tt-owner", "sign-in")
    assert config.get_ticktick_client().access_token == "tt-owner"

    # The MCP endpoint answers with the token, and serverInfo carries the icon.
    r = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text
    info = r.json()["result"]["serverInfo"]
    assert info["name"] == "TickTick"
    assert {i["src"] for i in info["icons"]} == {PUBLIC + "/favicon.svg", PUBLIC + "/icon.png"}

    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
               headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"ticktick_list_projects", "unofficial_pin_task"} <= names

    env["owner_client"] = (client_id, tok.json()["refresh_token"])


def test_legacy_sse_closes_once_mcp_is_in_use(env):
    c = env["client"]
    assert env["main"].legacy_enabled() is False
    r = c.get("/sse")
    assert r.status_code == 410
    assert r.json()["mcp_url"] == PUBLIC + "/mcp"
    assert c.post("/messages/?session_id=x", json={}).status_code == 410


def test_another_account_is_refused_and_nothing_is_saved(env):
    c = env["client"]
    before = token_store.get_store().ticktick_token()
    r, client_id, _ = _connect(c, "stranger")
    assert r.status_code == 403
    assert "belongs to someone else" in r.text
    assert token_store.get_store().ticktick_token() == before
    assert token_store.get_store().owner()["avatar"] == OWNER["avatarUrl"]


def test_a_used_or_unknown_state_is_refused(env):
    r = env["client"].get("/oauth/callback", params={"code": "owner", "state": "nope"})
    assert r.status_code == 400


def test_oauth_start_renews_the_token_without_showing_it(env):
    c = env["client"]
    r = c.get("/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = c.get("/oauth/callback", params={"code": "owner2", "state": state})
    assert r.status_code == 200
    assert "tt-owner2" not in r.text
    assert token_store.get_store().ticktick_token()["access_token"] == "tt-owner2"
    assert config.official_token()[0] == "tt-owner2"


def test_refresh_rotates_and_stops_once_ticktick_token_expires(env):
    c = env["client"]
    client_id, refresh = env["owner_client"]
    r = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})
    assert r.status_code == 200, r.text
    new_refresh = r.json()["refresh_token"]
    # The old refresh token is gone.
    r = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id})
    assert r.status_code == 400

    store = token_store.get_store()
    saved = store.ticktick_token()
    store.set_ticktick_token({**saved, "expires_at": int(time.time()) - 10})
    try:
        # Expired sign-in: the env token takes over for tools, and the refresh is refused
        # so the AI app signs in again.
        assert config.official_token() == ("env-token", "env")
        r = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": new_refresh,
                                   "client_id": client_id})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"
    finally:
        store.set_ticktick_token(saved)


def test_health_reports_state_without_secrets(env):
    body = env["client"].get("/health").text
    data = json.loads(body)
    assert data["mcp_url"] == PUBLIC + "/mcp"
    assert data["ticktick_redirect_uri"] == PUBLIC + "/oauth/callback"
    assert data["official_api"]["token_source"] == "sign-in"
    assert data["owner_locked"] is True
    for secret in ("tt-owner", "env-token", "test-client-secret"):
        assert secret not in body
    assert "tt-owner" not in env["client"].get("/status").text
