"""
Tests for the unofficial client's connection lifecycle.

The production failure these cover: TickTick answered /user/signon with 429 at
boot, the client latched that failure for the life of the process, and every
unofficial tool then reported "not configured" - pointing at credentials that
were fine and hiding a rate limit that clears on its own.
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
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    monkeypatch.delenv("TICKTICK_DEVICE_ID", raising=False)
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "state.json"))
    UnofficialAPIClient._instance = None
    yield tmp_path / "state.json"
    UnofficialAPIClient._instance = None


class FakeTransport(httpx.BaseTransport):
    """Serves the endpoints startup touches, recording what was called."""

    def __init__(self, settings_status=200, login_status=200, token="fresh-token"):
        self.settings_status = settings_status
        self.login_status = login_status
        self.token = token
        self.logins = 0
        self.paths = []
        self.device_ids = []

    def handle_request(self, request):
        self.paths.append(request.url.path)
        device = request.headers.get("x-device")
        if device:
            self.device_ids.append(device)
        if request.url.path.endswith("/user/signon"):
            self.logins += 1
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={}, request=request)
            return httpx.Response(200, json={"token": self.token}, request=request)
        if request.url.path.endswith("/user/preferences/settings"):
            return httpx.Response(
                self.settings_status,
                json={"timeZone": "America/New_York", "id": "p1"},
                request=request,
            )
        if "batch/check" in request.url.path:
            return httpx.Response(200, json={"inboxId": "inbox1"}, request=request)
        return httpx.Response(404, request=request)


@pytest.fixture
def transport(monkeypatch):
    def make(**kwargs):
        t = FakeTransport(**kwargs)
        real_init = httpx.Client.__init__

        def patched(self, *a, **kw):
            kw["transport"] = t
            real_init(self, *a, **kw)

        monkeypatch.setattr(httpx.Client, "__init__", patched)
        return t

    return make


# ==================== Booting must not cost a login ====================


def test_construction_alone_never_logs_in(transport):
    t = transport()
    UnofficialAPIClient()
    assert t.logins == 0, "importing the module must not spend a login"


def test_startup_resume_does_not_log_in(transport, isolated_state):
    isolated_state.write_text('{"device_id": "d", "session_token": "cached"}')
    t = transport()

    client = UnofficialAPIClient()
    assert client.ensure_connected(allow_login=False) is True
    assert t.logins == 0
    assert client.status()["session_source"] == "cached"


def test_startup_without_a_cached_session_defers_the_login(transport):
    t = transport()
    client = UnofficialAPIClient()

    assert client.ensure_connected(allow_login=False) is False
    assert t.logins == 0
    # Deferring is not a failure, so it must not start a backoff.
    assert client.status()["failed_attempts"] == 0
    assert client.status()["retry_in_seconds"] == 0


def test_first_tool_use_authenticates(transport):
    t = transport()
    client = UnofficialAPIClient()
    client.ensure_connected(allow_login=False)

    assert client.ensure_connected() is True
    assert t.logins == 1
    assert client.status()["session_source"] == "login"


# ==================== Session caching ====================


def test_login_caches_the_session(transport, isolated_state):
    import json

    transport()
    UnofficialAPIClient().ensure_connected()
    assert json.loads(isolated_state.read_text())["session_token"] == "fresh-token"


def test_restart_reuses_the_cached_session(transport, isolated_state):
    isolated_state.write_text('{"device_id": "d", "session_token": "cached"}')
    t = transport()

    assert UnofficialAPIClient().ensure_connected() is True
    assert t.logins == 0, "a valid cached session must not trigger a login"


def test_resume_costs_one_request(transport, isolated_state):
    isolated_state.write_text('{"device_id": "d", "session_token": "cached"}')
    t = transport()
    UnofficialAPIClient().ensure_connected()

    settings = [p for p in t.paths if p.endswith("/user/preferences/settings")]
    assert len(settings) == 1, "validation doubles as the settings load"


def test_rejected_cache_falls_back_to_login(transport, isolated_state):
    isolated_state.write_text('{"device_id": "d", "session_token": "stale"}')
    t = transport(settings_status=401)

    client = UnofficialAPIClient()
    assert client.ensure_connected() is True
    assert t.logins == 1


# ==================== The production failure ====================


def test_rate_limited_login_is_retried_not_latched(transport):
    t = transport(login_status=429)
    client = UnofficialAPIClient()

    assert client.ensure_connected() is False
    # A 429 walks the whole device ladder before giving up on the attempt.
    attempts_per_try = t.logins
    assert attempts_per_try >= 1

    # Once the backoff passes, the next use tries again on its own.
    client._next_retry_at = 0.0
    t.login_status = 200
    assert client.ensure_connected() is True
    assert t.logins > attempts_per_try


def test_failure_reason_names_the_rate_limit(transport):
    transport(login_status=429)
    client = UnofficialAPIClient()
    client.ensure_connected()

    reason = client.unavailable_reason()
    assert "429" in reason and "rate-limiting" in reason
    # The old message blamed env vars that were never the problem.
    assert "TICKTICK_USERNAME" not in reason


def test_missing_credentials_still_says_not_configured(monkeypatch, transport):
    monkeypatch.setattr(uc, "USERNAME", None)
    monkeypatch.setattr(uc, "PASSWORD", None)
    transport()

    client = UnofficialAPIClient()
    assert client.ensure_connected() is False
    assert "TICKTICK_USERNAME" in client.unavailable_reason()


def test_no_retry_storm_while_backing_off(transport):
    t = transport(login_status=429)
    client = UnofficialAPIClient()
    client.ensure_connected()
    after_one_attempt = t.logins

    for _ in range(6):
        client.ensure_connected()

    assert t.logins == after_one_attempt, (
        "backoff must not hammer a rate-limited endpoint"
    )


def test_backoff_grows_and_is_capped(transport):
    transport(login_status=429)
    client = UnofficialAPIClient()

    delays = []
    for _ in range(8):
        client._next_retry_at = 0.0
        client.ensure_connected()
        delays.append(client.status()["retry_in_seconds"])

    assert delays[0] < delays[1] < delays[2], f"should back off progressively: {delays}"
    assert max(delays) <= UnofficialAPIClient.RETRY_MAX_SECONDS


# ==================== Retry-After ====================


@pytest.mark.parametrize(
    "header, expected",
    [("120", 120), ("0", 0), (None, None), ("garbage", None)],
)
def test_parse_retry_after(header, expected):
    result = _parse_retry_after(header)
    assert result is None if expected is None else result == pytest.approx(expected)


def test_parse_retry_after_http_date():
    import email.utils

    header = email.utils.formatdate(time.time() + 300, usegmt=True)
    assert _parse_retry_after(header) == pytest.approx(300, abs=5)


def test_retry_after_overrides_a_shorter_backoff(monkeypatch):
    monkeypatch.setattr(
        UnofficialAPIClient,
        "_initialize_client",
        lambda self, allow_login=True: (_ for _ in ()).throw(
            LoginRateLimited("Login failed: 429 - ", retry_after=600)
        ),
    )
    client = UnofficialAPIClient()
    client.ensure_connected()
    assert client.status()["retry_in_seconds"] == pytest.approx(600, abs=2)


def test_absurd_retry_after_is_capped(monkeypatch):
    monkeypatch.setattr(
        UnofficialAPIClient,
        "_initialize_client",
        lambda self, allow_login=True: (_ for _ in ()).throw(
            LoginRateLimited("Login failed: 429 - ", retry_after=999999)
        ),
    )
    client = UnofficialAPIClient()
    client.ensure_connected()
    assert (
        client.status()["retry_in_seconds"]
        <= UnofficialAPIClient.RETRY_AFTER_MAX_SECONDS
    )


# ==================== Session expiry ====================


def test_expired_session_triggers_one_reauth(transport):
    t = transport()
    client = UnofficialAPIClient()
    client.ensure_connected()
    assert t.logins == 1

    responses = [
        httpx.Response(401, request=httpx.Request("GET", "https://x/")),
        httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "https://x/")),
    ]
    sent = []

    def fake_send(self, method, url, data, params):
        sent.append(method)
        return responses.pop(0)

    UnofficialAPIClient._send, original = fake_send, UnofficialAPIClient._send
    try:
        assert client.call_api("/api/v2/task/abc") == {"ok": True}
    finally:
        UnofficialAPIClient._send = original

    assert len(sent) == 2, "should retry after re-authenticating"
    assert t.logins == 2, "should have logged in again"


# ==================== Silent degradation is now visible ====================


def test_settings_failure_is_reported_in_status(transport):
    transport(settings_status=500)
    client = UnofficialAPIClient()
    client.ensure_connected()

    # Still connected - this is non-fatal by design - but no longer silent.
    assert client.status()["connected"] is True
    assert client.status()["settings_error"] is not None


# ==================== Rejected credentials are terminal ====================

REJECTION_BODY = (
    '{"errorId":"wx45ygqs@tw10","errorCode":"username_password_not_match",'
    '"errorMessage":"user@example.com","data":null}'
)


class RejectingTransport(httpx.BaseTransport):
    """TickTick's real response to a wrong password: HTTP 500 with an errorCode."""

    def __init__(self):
        self.logins = 0

    def handle_request(self, request):
        if request.url.path.endswith("/user/signon"):
            self.logins += 1
            return httpx.Response(
                500, text=REJECTION_BODY, request=request
            )
        return httpx.Response(404, request=request)


@pytest.fixture
def rejecting(monkeypatch):
    t = RejectingTransport()
    real_init = httpx.Client.__init__

    def patched(self, *a, **kw):
        kw["transport"] = t
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    return t


def test_rejection_is_not_hammered(rejecting):
    """Retrying a rejected login cannot help and invites a lockout."""
    client = UnofficialAPIClient()

    for _ in range(5):
        assert client.ensure_connected() is False

    assert rejecting.logins == 1, "must stop after the first rejection"
    assert client.status()["credentials_rejected"] is True


def test_rejection_uses_a_long_cooldown_not_a_permanent_stop(rejecting):
    """
    The same error code covers a wrong password and an anti-abuse block. A block
    lifts on its own, so the client must eventually try again without a redeploy.
    """
    client = UnofficialAPIClient()
    client.ensure_connected()

    cooldown = client.status()["retry_in_seconds"]
    assert cooldown > UnofficialAPIClient.RETRY_MAX_SECONDS, (
        f"cooldown {cooldown}s should be much longer than a normal backoff"
    )

    client._next_retry_at = 0.0
    client.ensure_connected()
    assert rejecting.logins == 2, "must retry once the cooldown expires"


def test_rejection_message_does_not_assert_the_password_is_wrong(rejecting):
    client = UnofficialAPIClient()
    client.ensure_connected()

    reason = client.unavailable_reason()
    assert "username_password_not_match" in reason
    assert "anti-abuse" in reason
    assert "not proof" in reason


def test_login_failure_captures_headers_and_body(rejecting):
    """Headers are the only place a captcha or throttling hint would appear."""
    client = UnofficialAPIClient()
    client.ensure_connected()

    diag = client.status()["last_login_diagnostics"]
    assert diag["status"] == 500
    assert diag["error_code"] == "username_password_not_match"
    assert "errorId" in diag["body"]
    assert isinstance(diag["headers"], dict)
    # The rejection happens on the first rung, so that is the id recorded.
    assert diag["device_id"] == uc.LEGACY_SHARED_DEVICE_ID
    assert diag["device_label"] == "legacy-shared"


def test_diagnostics_never_leak_a_session_cookie(rejecting):
    client = UnofficialAPIClient()
    client.ensure_connected()
    headers = client.status()["last_login_diagnostics"]["headers"]
    assert not any(k.lower() == "set-cookie" for k in headers)


def test_a_later_success_clears_the_rejected_state(rejecting, monkeypatch):
    client = UnofficialAPIClient()
    client.ensure_connected()
    assert client.status()["credentials_rejected"] is True

    monkeypatch.setattr(
        UnofficialAPIClient, "_initialize_client", lambda self, allow_login=True: None
    )
    client._client = httpx.Client()
    client._next_retry_at = 0.0
    client._client = None
    client.ensure_connected()

    assert client.status()["credentials_rejected"] is False
    assert client.status()["last_login_diagnostics"] is None


def test_rate_limit_is_still_retried(transport):
    """A 429 is transient and must stay retryable - only rejection is terminal."""
    t = transport(login_status=429)
    client = UnofficialAPIClient()
    client.ensure_connected()

    assert client.status()["credentials_rejected"] is False
    client._next_retry_at = 0.0
    t.login_status = 200
    assert client.ensure_connected() is True
