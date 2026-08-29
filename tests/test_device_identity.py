"""
Tests for the per-install device identity.

The device id used to be a constant copied from ticktick-py
("674c46cf88bb9f5f73c3068a"), so every deployment running this code presented
the same device to TickTick. If TickTick throttles per device, that means
sharing one rate-limit bucket with every other copy of this code - a plausible
cause of 429s out of proportion to your own traffic.
"""

import json
import re

import pytest

from ticktick_mcp import unofficial_client as uc
from ticktick_mcp.unofficial_client import UnofficialAPIClient, _new_device_id

SHARED_ID_FROM_TICKTICK_PY = "674c46cf88bb9f5f73c3068a"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    monkeypatch.delenv("TICKTICK_DEVICE_ID", raising=False)
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "state.json"))
    UnofficialAPIClient._instance = None
    yield tmp_path / "state.json"
    UnofficialAPIClient._instance = None


def read_state(path):
    return json.loads(path.read_text())


# ==================== The shared-id problem ====================


def test_device_id_is_not_the_shared_constant(isolated_state):
    client = UnofficialAPIClient()
    assert client.device_id != SHARED_ID_FROM_TICKTICK_PY


def test_shared_constant_is_gone_from_the_source():
    """Guard against anyone reintroducing the hardcoded id."""
    from pathlib import Path

    source = Path(uc.__file__).read_text()
    assert SHARED_ID_FROM_TICKTICK_PY not in source


def test_two_installs_get_different_device_ids(tmp_path, monkeypatch):
    ids = set()
    for name in ("install-a", "install-b"):
        monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / name))
        UnofficialAPIClient._instance = None
        ids.add(UnofficialAPIClient().device_id)
    assert len(ids) == 2, "each install must present its own device"


def test_device_id_matches_ticktick_format():
    """TickTick's ids are 24 lowercase hex chars; look like one, not an outlier."""
    for _ in range(5):
        assert re.fullmatch(r"[0-9a-f]{24}", _new_device_id())


# ==================== Stability ====================


def test_device_id_is_stable_across_restarts(isolated_state):
    first = UnofficialAPIClient().device_id
    UnofficialAPIClient._instance = None
    second = UnofficialAPIClient().device_id
    assert first == second, "a device that changes every boot is its own red flag"


def test_device_id_is_persisted(isolated_state):
    client = UnofficialAPIClient()
    assert read_state(isolated_state)["device_id"] == client.device_id


def test_env_override_wins(monkeypatch, isolated_state):
    monkeypatch.setenv("TICKTICK_DEVICE_ID", "aaaabbbbccccddddeeeeffff")
    client = UnofficialAPIClient()
    assert client.device_id == "aaaabbbbccccddddeeeeffff"


def test_unwritable_state_still_avoids_the_shared_id(monkeypatch, tmp_path):
    """Even with no writable cache, never fall back to a shared constant."""
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "nope" / "x.json"))
    monkeypatch.setattr(uc, "_write_state", lambda state: False)
    UnofficialAPIClient._instance = None

    client = UnofficialAPIClient()
    assert client.device_id != SHARED_ID_FROM_TICKTICK_PY
    assert re.fullmatch(r"[0-9a-f]{24}", client.device_id)
    assert client.status()["device_id_persisted"] is False


def test_forgetting_the_session_keeps_the_device_id(isolated_state):
    client = UnofficialAPIClient()
    device_id = client.device_id
    client._access_token = "tok"
    client._remember_session()

    client._forget_session()

    assert "session_token" not in read_state(isolated_state)
    assert read_state(isolated_state)["device_id"] == device_id


# ==================== It reaches the wire ====================


def test_device_id_is_sent_in_the_x_device_header(isolated_state):
    client = UnofficialAPIClient()
    headers = client._build_headers()

    device = json.loads(headers["x-device"])
    assert device["id"] == client.device_id
    assert device["platform"] == "web"
    # The rest of the fingerprint still looks like the web app.
    assert device["channel"] == "website"
    assert headers["user-agent"].startswith("Mozilla/5.0")


def test_corrupt_state_file_is_survivable(isolated_state):
    isolated_state.write_text("{not json at all")
    client = UnofficialAPIClient()
    assert re.fullmatch(r"[0-9a-f]{24}", client.device_id)
