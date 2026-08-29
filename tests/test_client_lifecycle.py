"""
Regression tests for the unofficial client's connection lifecycle.

The failure these cover, seen in production: TickTick answered /user/signon with
429 at boot, the client latched that failure for the life of the process, and
every unofficial tool then reported "not configured" — pointing at credentials
that were fine and hiding a rate limit that would have cleared on its own.
"""

import httpx
import pytest

from ticktick_mcp import unofficial_client as uc
from ticktick_mcp.unofficial_client import UnofficialAPIClient


@pytest.fixture(autouse=True)
def reset_singleton(monkeypatch):
    """Each test gets a fresh client with credentials present."""
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    UnofficialAPIClient._instance = None
    yield
    UnofficialAPIClient._instance = None


class FakeLogin:
    """Stands in for _initialize_client, failing a set number of times first."""

    def __init__(self, failures, error="Login failed: 429 - "):
        self.remaining_failures = failures
        self.error = error
        self.attempts = 0

    def __call__(self, client):
        self.attempts += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError(self.error)
        client._client = httpx.Client()


def install(monkeypatch, login):
    monkeypatch.setattr(
        UnofficialAPIClient, "_initialize_client", lambda self: login(self)
    )
    return login


# ==================== The production failure ====================


def test_rate_limited_login_is_retried_not_latched(monkeypatch):
    """A 429 at startup must not disable the tools for the whole process."""
    login = install(monkeypatch, FakeLogin(failures=1))

    client = UnofficialAPIClient()
    assert UnofficialAPIClient.get_instance() is None  # still backing off
    assert login.attempts == 1

    # Once the backoff window passes, the next use reconnects on its own.
    client._next_retry_at = 0.0
    assert UnofficialAPIClient.get_instance() is client
    assert login.attempts == 2
    assert client.status()["connected"] is True


def test_failure_reason_is_reported_not_guessed(monkeypatch):
    install(monkeypatch, FakeLogin(failures=1))
    client = UnofficialAPIClient()

    reason = client.unavailable_reason()
    assert "429" in reason
    assert "rate-limiting" in reason
    # The old message blamed the environment variables, which were never the problem.
    assert "TICKTICK_USERNAME" not in reason


def test_missing_credentials_still_says_not_configured(monkeypatch):
    monkeypatch.setattr(uc, "USERNAME", None)
    monkeypatch.setattr(uc, "PASSWORD", None)
    install(monkeypatch, FakeLogin(failures=0))

    client = UnofficialAPIClient()
    assert client.ensure_connected() is False
    reason = client.unavailable_reason()
    assert "TICKTICK_USERNAME" in reason and "TICKTICK_PASSWORD" in reason


def test_no_network_call_while_backing_off(monkeypatch):
    login = install(monkeypatch, FakeLogin(failures=5))
    client = UnofficialAPIClient()
    assert login.attempts == 1

    for _ in range(5):
        assert client.ensure_connected() is False
    assert login.attempts == 1, "backoff must not hammer a rate-limited endpoint"


def test_backoff_grows_and_is_capped(monkeypatch):
    install(monkeypatch, FakeLogin(failures=99))
    client = UnofficialAPIClient()

    delays = []
    for _ in range(8):
        client._next_retry_at = 0.0
        client.ensure_connected()
        delays.append(client.status()["retry_in_seconds"])

    assert delays[0] < delays[1] < delays[2], f"should back off progressively: {delays}"
    assert max(delays) <= UnofficialAPIClient.RETRY_MAX_SECONDS


def test_connected_client_short_circuits(monkeypatch):
    login = install(monkeypatch, FakeLogin(failures=0))
    client = UnofficialAPIClient()
    assert login.attempts == 1

    for _ in range(3):
        assert client.ensure_connected() is True
    assert login.attempts == 1, "must not re-login when already connected"


# ==================== Status reporting ====================


def test_status_distinguishes_the_two_failures(monkeypatch):
    install(monkeypatch, FakeLogin(failures=1))
    client = UnofficialAPIClient()

    state = client.status()
    assert state["credentials_configured"] is True
    assert state["connected"] is False
    assert "429" in state["last_error"]
    assert state["failed_attempts"] == 1
    assert state["retry_in_seconds"] > 0


def test_module_status_does_not_force_a_connection(monkeypatch):
    login = install(monkeypatch, FakeLogin(failures=99))
    UnofficialAPIClient()
    attempts_before = login.attempts

    uc.client_status()
    assert login.attempts == attempts_before


def test_status_before_construction(monkeypatch):
    state = uc.client_status()
    assert state["connected"] is False
    assert state["credentials_configured"] is True


# ==================== Session expiry ====================


def test_expired_session_triggers_one_reauth(monkeypatch):
    """A 401 mid-session must re-login instead of failing until redeploy."""
    login = install(monkeypatch, FakeLogin(failures=0))
    client = UnofficialAPIClient()

    responses = [
        httpx.Response(401, request=httpx.Request("GET", "https://x/")),
        httpx.Response(
            200, json={"ok": True}, request=httpx.Request("GET", "https://x/")
        ),
    ]
    sent = []

    def fake_send(self, method, url, data, params):
        sent.append((method, url))
        return responses.pop(0)

    monkeypatch.setattr(UnofficialAPIClient, "_send", fake_send)

    assert client.call_api("/api/v2/task/abc") == {"ok": True}
    assert len(sent) == 2, "should retry the request after re-authenticating"
    assert login.attempts == 2, "should have logged in again"


def test_persistent_401_surfaces_as_an_error(monkeypatch):
    install(monkeypatch, FakeLogin(failures=0))
    client = UnofficialAPIClient()

    monkeypatch.setattr(
        UnofficialAPIClient,
        "_send",
        lambda self, method, url, data, params: httpx.Response(
            401, request=httpx.Request("GET", "https://x/")
        ),
    )

    with pytest.raises(RuntimeError, match="401"):
        client.call_api("/api/v2/task/abc")
