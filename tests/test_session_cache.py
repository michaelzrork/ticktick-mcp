"""
Tests for session-token caching and Retry-After handling.

Logging in on every boot is what got the deployment rate-limited: TickTick
throttles /user/signon hard, and a server that re-authenticates on each restart
trips it during any debugging session. Caching the session means a restart
normally makes no login call at all.
"""

import time

import httpx
import pytest

from ticktick_mcp import unofficial_client as uc
from ticktick_mcp.unofficial_client import (
    LoginRateLimited,
    UnofficialAPIClient,
    _parse_retry_after,
)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "session"))
    UnofficialAPIClient._instance = None
    yield tmp_path / "session"
    UnofficialAPIClient._instance = None


class FakeTransport(httpx.BaseTransport):
    """Serves the two endpoints startup touches, recording what was called."""

    def __init__(self, settings_status=200, login_status=200, token="fresh-token"):
        self.settings_status = settings_status
        self.login_status = login_status
        self.token = token
        self.logins = 0
        self.paths = []

    def handle_request(self, request):
        path = request.url.path
        self.paths.append(path)
        if path.endswith("/user/signon"):
            self.logins += 1
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={}, request=request)
            return httpx.Response(200, json={"token": self.token}, request=request)
        if path.endswith("/user/preferences/settings"):
            return httpx.Response(
                self.settings_status,
                json={"timeZone": "America/New_York", "id": "profile-1"},
                request=request,
            )
        if "batch/check" in path:
            return httpx.Response(200, json={"inboxId": "inbox1"}, request=request)
        return httpx.Response(404, request=request)


@pytest.fixture
def transport(monkeypatch):
    """Route the client's HTTP through a fake transport."""
    holder = {}

    def make(**kwargs):
        t = FakeTransport(**kwargs)
        holder["t"] = t
        real_init = httpx.Client.__init__

        def patched(self, *a, **kw):
            kw["transport"] = t
            real_init(self, *a, **kw)

        monkeypatch.setattr(httpx.Client, "__init__", patched)
        return t

    return make


# ==================== Caching ====================


def test_first_connect_logs_in_and_caches(transport, isolated_cache):
    t = transport()
    client = UnofficialAPIClient()

    assert client.status()["connected"] is True
    assert t.logins == 1
    assert isolated_cache.read_text().strip() == "fresh-token"


def test_restart_reuses_the_cached_session_without_logging_in(
    transport, isolated_cache
):
    """The whole point: a redeploy must not cost a login."""
    isolated_cache.write_text("cached-token")
    t = transport()

    client = UnofficialAPIClient()

    assert client.status()["connected"] is True
    assert t.logins == 0, "a valid cached session must not trigger a login"
    assert "/api/v2/user/signon" not in t.paths


def test_resume_costs_one_request_and_still_loads_settings(
    transport, isolated_cache
):
    isolated_cache.write_text("cached-token")
    t = transport()

    client = UnofficialAPIClient()

    assert client._time_zone == "America/New_York"
    settings_calls = [p for p in t.paths if p.endswith("/user/preferences/settings")]
    assert len(settings_calls) == 1, "validation doubles as the settings load"


def test_rejected_cache_falls_back_to_login_and_is_cleared(
    transport, isolated_cache
):
    isolated_cache.write_text("stale-token")
    t = transport(settings_status=401)

    client = UnofficialAPIClient()

    assert client.status()["connected"] is True
    assert t.logins == 1
    assert isolated_cache.read_text().strip() == "fresh-token"


def test_missing_cache_file_is_not_an_error(transport, isolated_cache):
    t = transport()
    assert not isolated_cache.exists()

    client = UnofficialAPIClient()

    assert client.status()["connected"] is True
    assert t.logins == 1


def test_cached_token_is_owner_only(transport, isolated_cache):
    transport()
    UnofficialAPIClient()
    assert isolated_cache.stat().st_mode & 0o077 == 0, "session token must not be world readable"


def test_reconnect_drops_the_cache_so_it_does_not_replay_a_dead_token(
    transport, isolated_cache
):
    isolated_cache.write_text("cached-token")
    t = transport()
    client = UnofficialAPIClient()
    assert t.logins == 0

    client.reconnect()

    assert t.logins == 1, "reconnect must log in, not replay the rejected session"


def test_failed_login_leaves_no_cache_behind(transport, isolated_cache):
    transport(login_status=429)
    client = UnofficialAPIClient()

    assert client.status()["connected"] is False
    assert not isolated_cache.exists()


# ==================== Retry-After ====================


@pytest.mark.parametrize(
    "header, expected",
    [
        ("120", 120),
        ("0", 0),
        (None, None),
        ("not-a-number", None),
    ],
)
def test_parse_retry_after_values(header, expected):
    result = _parse_retry_after(header)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_parse_retry_after_http_date():
    future = time.time() + 300
    header = __import__("email.utils", fromlist=["utils"]).formatdate(future, usegmt=True)
    assert _parse_retry_after(header) == pytest.approx(300, abs=5)


def test_retry_after_overrides_a_shorter_backoff(monkeypatch):
    def raise_rate_limited(self):
        raise LoginRateLimited("Login failed: 429 - ", retry_after=600)

    monkeypatch.setattr(UnofficialAPIClient, "_initialize_client", raise_rate_limited)

    client = UnofficialAPIClient()

    # First failure would otherwise back off only 30s.
    assert client.status()["retry_in_seconds"] == pytest.approx(600, abs=2)


def test_absurd_retry_after_is_capped(monkeypatch):
    def raise_rate_limited(self):
        raise LoginRateLimited("Login failed: 429 - ", retry_after=999999)

    monkeypatch.setattr(UnofficialAPIClient, "_initialize_client", raise_rate_limited)

    client = UnofficialAPIClient()

    assert (
        client.status()["retry_in_seconds"]
        <= UnofficialAPIClient.RETRY_AFTER_MAX_SECONDS
    )


def test_login_429_raises_the_rate_limited_error(transport):
    transport(login_status=429)
    client = UnofficialAPIClient()

    reason = client.unavailable_reason()
    assert "429" in reason and "rate-limiting" in reason
