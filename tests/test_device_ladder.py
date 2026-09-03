"""
Tests for the login device-identity ladder.

The legacy shared id is tried first because it is what this account
authenticated with for months. Only a 429 falls through to the per-install id -
that is the single failure a different device identity can fix, since the legacy
id shares a rate-limit bucket with every other copy of this code.
"""

import json

import httpx
import pytest

from ticktick_mcp import unofficial_client as uc
from ticktick_mcp.unofficial_client import (
    LEGACY_SHARED_DEVICE_ID,
    LoginRateLimited,
    LoginRejected,
    UnofficialAPIClient,
    credentials_shape,
)

PER_INSTALL_ID = "aaaabbbbccccddddeeeeffff"
REJECTION_BODY = (
    '{"errorId":"y3gr4lwp@tw8","errorCode":"username_password_not_match",'
    '"errorMessage":"user@example.com","data":null}'
)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    monkeypatch.setenv("TICKTICK_DEVICE_ID", PER_INSTALL_ID)
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "state.json"))
    UnofficialAPIClient._instance = None
    yield
    UnofficialAPIClient._instance = None


class LadderTransport(httpx.BaseTransport):
    """Answers the login differently depending on the device id it receives."""

    def __init__(self, by_device):
        self.by_device = by_device
        self.attempts = []

    def handle_request(self, request):
        path = request.url.path
        if path.endswith("/user/signon"):
            device = json.loads(request.headers["x-device"])["id"]
            self.attempts.append(device)
            status = self.by_device.get(device, 500)
            if status == 200:
                return httpx.Response(
                    200, json={"token": f"tok-{device[:6]}"}, request=request
                )
            if status == 429:
                return httpx.Response(429, text="", request=request)
            return httpx.Response(500, text=REJECTION_BODY, request=request)
        if path.endswith("/user/preferences/settings"):
            return httpx.Response(
                200, json={"timeZone": "America/New_York", "id": "p"}, request=request
            )
        if "batch/check" in path:
            return httpx.Response(200, json={"inboxId": "i"}, request=request)
        return httpx.Response(404, request=request)


@pytest.fixture
def ladder(monkeypatch):
    def make(by_device):
        t = LadderTransport(by_device)
        real_init = httpx.Client.__init__

        def patched(self, *a, **kw):
            kw["transport"] = t
            real_init(self, *a, **kw)

        monkeypatch.setattr(httpx.Client, "__init__", patched)
        return t

    return make


# ==================== Order ====================


def test_legacy_id_is_tried_first(ladder):
    t = ladder({LEGACY_SHARED_DEVICE_ID: 200})
    client = UnofficialAPIClient()

    assert client.ensure_connected() is True
    assert t.attempts[0] == LEGACY_SHARED_DEVICE_ID
    assert len(t.attempts) == 1, "a working legacy id must not try anything else"
    assert client.status()["active_device_id"] == LEGACY_SHARED_DEVICE_ID


def test_429_on_legacy_falls_through_to_per_install(ladder):
    """The exact case the ladder exists for."""
    t = ladder({LEGACY_SHARED_DEVICE_ID: 429, PER_INSTALL_ID: 200})
    client = UnofficialAPIClient()

    assert client.ensure_connected() is True
    assert t.attempts == [LEGACY_SHARED_DEVICE_ID, PER_INSTALL_ID]
    assert client.status()["active_device_id"] == PER_INSTALL_ID


def test_both_rate_limited_raises_rate_limited(ladder):
    t = ladder({LEGACY_SHARED_DEVICE_ID: 429, PER_INSTALL_ID: 429})
    client = UnofficialAPIClient()

    assert client.ensure_connected() is False
    assert t.attempts == [LEGACY_SHARED_DEVICE_ID, PER_INSTALL_ID]
    # Rate limiting is transient, so it must stay retryable.
    assert client.status()["credentials_rejected"] is False
    assert client.status()["retry_in_seconds"] > 0


def test_rejection_stops_the_ladder_immediately(ladder):
    """
    A credential rejection is about the account, not the device.

    Trying the second id would spend another login for no possible benefit.
    """
    t = ladder({LEGACY_SHARED_DEVICE_ID: 500, PER_INSTALL_ID: 200})
    client = UnofficialAPIClient()

    assert client.ensure_connected() is False
    assert t.attempts == [LEGACY_SHARED_DEVICE_ID], "must not try the second id"
    assert client.status()["credentials_rejected"] is True


# ==================== The winning identity sticks ====================


def test_session_keeps_the_identity_that_worked(ladder):
    ladder({LEGACY_SHARED_DEVICE_ID: 429, PER_INSTALL_ID: 200})
    client = UnofficialAPIClient()
    client.ensure_connected()

    sent = json.loads(client.client.headers["x-device"])["id"]
    assert sent == PER_INSTALL_ID, "later requests must match the logged-in device"


def test_pinned_device_id_is_used_as_the_second_rung(ladder):
    ladder({LEGACY_SHARED_DEVICE_ID: 429, PER_INSTALL_ID: 200})
    client = UnofficialAPIClient()
    assert client.device_id == PER_INSTALL_ID, "TICKTICK_DEVICE_ID must be honoured"


def test_no_duplicate_rung_when_pinned_to_the_legacy_id(monkeypatch, ladder):
    """Pinning the legacy id must not make the ladder try it twice."""
    monkeypatch.setenv("TICKTICK_DEVICE_ID", LEGACY_SHARED_DEVICE_ID)
    UnofficialAPIClient._instance = None
    t = ladder({LEGACY_SHARED_DEVICE_ID: 429})

    client = UnofficialAPIClient()
    client.ensure_connected()

    assert t.attempts == [LEGACY_SHARED_DEVICE_ID]


# ==================== Diagnostics must not leak the account ====================


def test_public_diagnostics_redact_the_username(ladder):
    """/status is unauthenticated - the account email must not ride along."""
    ladder({LEGACY_SHARED_DEVICE_ID: 500})
    client = UnofficialAPIClient()
    client.ensure_connected()

    diag = client.status()["last_login_diagnostics"]
    assert "user@example.com" not in json.dumps(diag)
    assert "<username>" in diag["body"]
    # The useful part survives redaction.
    assert diag["error_code"] == "username_password_not_match"
    assert diag["device_label"] == "legacy-shared"


def test_credentials_shape_never_reveals_the_values(monkeypatch):
    monkeypatch.setattr(uc, "PASSWORD", "sup3r-s3cret")
    shape = credentials_shape()
    assert "sup3r-s3cret" not in json.dumps(shape)
    assert shape["password"]["length"] == 12


@pytest.mark.parametrize(
    "password, flag",
    [
        ("pw\n", "contains_newline"),
        ("pw ", "trailing_whitespace"),
        (" pw", "leading_whitespace"),
        ('"pw"', "surrounded_by_quotes"),
    ],
)
def test_credentials_shape_catches_mangling(monkeypatch, password, flag):
    """A password that is correct but arrives mangled fails identically."""
    monkeypatch.setattr(uc, "PASSWORD", password)
    assert credentials_shape()["password"][flag] is True


def test_clean_password_shows_no_flags(monkeypatch):
    monkeypatch.setattr(uc, "PASSWORD", "perfectly-normal")
    shape = credentials_shape()["password"]
    assert not any(
        shape[k]
        for k in (
            "leading_whitespace",
            "trailing_whitespace",
            "contains_newline",
            "surrounded_by_quotes",
        )
    )
