"""
Tests for supplying a session token directly.

The v2 API only ever wanted a session cookie; the password login was just one
way to obtain one. A token lifted from a signed-in browser skips that endpoint
entirely, which is what makes the account usable when the endpoint refuses to
issue a token.
"""

import json

import httpx
import pytest

from ticktick_mcp import unofficial_client as uc
from ticktick_mcp.unofficial_client import UnofficialAPIClient

SUPPLIED = "token-from-a-signed-in-browser"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    monkeypatch.setenv("TICKTICK_DEVICE_ID", "aaaabbbbccccddddeeeeffff")
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "state.json"))
    monkeypatch.delenv("TICKTICK_SESSION_TOKEN", raising=False)
    UnofficialAPIClient._instance = None
    yield tmp_path / "state.json"
    UnofficialAPIClient._instance = None


class Transport(httpx.BaseTransport):
    def __init__(self, valid_tokens=(), login_status=500):
        self.valid_tokens = set(valid_tokens)
        self.login_status = login_status
        self.logins = 0
        self.validated = []

    def handle_request(self, request):
        path = request.url.path
        if path.endswith("/user/signon"):
            self.logins += 1
            if self.login_status == 200:
                return httpx.Response(200, json={"token": "tok"}, request=request)
            return httpx.Response(
                self.login_status,
                text='{"errorCode":"username_password_not_match"}',
                request=request,
            )
        if path.endswith("/user/preferences/settings"):
            token = request.headers.get("cookie", "")
            self.validated.append(token)
            ok = any(f"t={t}" in token for t in self.valid_tokens)
            return httpx.Response(
                200 if ok else 401,
                json={"timeZone": "America/New_York", "id": "p"},
                request=request,
            )
        if "batch/check" in path:
            return httpx.Response(200, json={"inboxId": "i"}, request=request)
        return httpx.Response(404, request=request)


@pytest.fixture
def transport(monkeypatch):
    def make(**kw):
        t = Transport(**kw)
        real = httpx.Client.__init__

        def patched(self, *a, **kwargs):
            kwargs["transport"] = t
            real(self, *a, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched)
        return t

    return make


# ==================== The point of the feature ====================


def test_supplied_token_avoids_the_login_endpoint_entirely(monkeypatch, transport):
    """With a good token, a broken login endpoint must not matter."""
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", SUPPLIED)
    t = transport(valid_tokens=[SUPPLIED], login_status=500)

    client = UnofficialAPIClient()

    assert client.ensure_connected() is True
    assert t.logins == 0, "must not call signon when a supplied token works"
    assert client.status()["session_source"] == "env"


def test_supplied_token_beats_a_stale_cache(monkeypatch, transport, isolated):
    isolated.write_text(json.dumps({"device_id": "d", "session_token": "stale"}))
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", SUPPLIED)
    t = transport(valid_tokens=[SUPPLIED])

    client = UnofficialAPIClient()
    assert client.ensure_connected() is True
    assert client.status()["session_source"] == "env"
    assert t.logins == 0


def test_expired_supplied_token_falls_back_to_login(monkeypatch, transport):
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", "expired")
    t = transport(valid_tokens=[], login_status=200)

    client = UnofficialAPIClient()
    assert client.ensure_connected() is True
    assert t.logins == 1, "a dead token should still allow a normal login"
    assert client.status()["session_source"] == "login"


def test_whitespace_around_a_pasted_token_is_tolerated(monkeypatch, transport):
    """Copy-paste picks up newlines; that must not break it."""
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", f"  {SUPPLIED}\n")
    transport(valid_tokens=[SUPPLIED])

    assert UnofficialAPIClient().ensure_connected() is True


def test_cached_token_still_works_with_no_env_token(transport, isolated):
    isolated.write_text(json.dumps({"device_id": "d", "session_token": "cached-tok"}))
    t = transport(valid_tokens=["cached-tok"])

    client = UnofficialAPIClient()
    assert client.ensure_connected() is True
    assert client.status()["session_source"] == "cached"
    assert t.logins == 0


def test_status_reports_whether_a_token_was_supplied(monkeypatch, transport):
    transport(valid_tokens=[])
    assert UnofficialAPIClient().status()["supplied_token_configured"] is False

    UnofficialAPIClient._instance = None
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", SUPPLIED)
    assert UnofficialAPIClient().status()["supplied_token_configured"] is True


# ==================== The token is a credential ====================


def test_the_token_never_appears_in_status(monkeypatch, transport):
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", SUPPLIED)
    transport(valid_tokens=[SUPPLIED])

    client = UnofficialAPIClient()
    client.ensure_connected()

    assert SUPPLIED not in json.dumps(client.status()), (
        "/status is unauthenticated - it must never carry the session token"
    )


def test_a_rejected_supplied_token_is_not_written_to_the_cache(
    monkeypatch, transport, isolated
):
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", "expired")
    transport(valid_tokens=[], login_status=500)

    UnofficialAPIClient().ensure_connected()

    state = json.loads(isolated.read_text()) if isolated.exists() else {}
    assert state.get("session_token") != "expired"
