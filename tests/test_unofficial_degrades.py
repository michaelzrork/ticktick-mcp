"""
The unofficial API failing must never take the server, or the official tools,
down with it - and when it fails it says why.
"""

import json

import anyio
import httpx
import pytest

from ticktick_mcp import config
from ticktick_mcp import unofficial_client as uc
from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp.tools import project_tools  # noqa: F401  (registers tools)
from ticktick_mcp.tools import unofficial_tools
from ticktick_mcp.unofficial_client import UnofficialAPIClient

NEED_UPGRADE = ('{"errorId":"x@tw11","errorCode":"need_upgrade_client",'
                '"errorMessage":"need_upgrade_client","data":null}')


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(uc, "USERNAME", "user@example.com")
    monkeypatch.setattr(uc, "PASSWORD", "pw")
    monkeypatch.setenv("TICKTICK_DEVICE_ID", "aaaabbbbccccddddeeeeffff")
    monkeypatch.setenv("TICKTICK_SESSION_CACHE", str(tmp_path / "state.json"))
    monkeypatch.delenv("TICKTICK_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("TICKTICK_DEVICE_VERSION", raising=False)
    UnofficialAPIClient._instance = None
    yield
    UnofficialAPIClient._instance = None


@pytest.fixture
def fake_signon(monkeypatch):
    """Answer /user/signon with whatever the test sets; record the x-device headers."""
    state = {"response": (500, NEED_UPGRADE), "devices": []}

    class T(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.path.endswith("/user/signon"):
                state["devices"].append(json.loads(request.headers["x-device"]))
                status, body = state["response"]
                return httpx.Response(status, text=body if isinstance(body, str) else json.dumps(body),
                                      request=request)
            if request.url.path.endswith("/user/preferences/settings"):
                return httpx.Response(401, request=request)
            return httpx.Response(404, request=request)

    real_init = httpx.Client.__init__

    def patched(self, *a, **kw):
        kw["transport"] = T()
        real_init(self, *a, **kw)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    return state


def _call(name, args):
    async def go():
        return await mcp.call_tool(name, args)
    result = anyio.run(go)
    # FastMCP returns (content, structured) for tools with an output schema.
    if isinstance(result, tuple):
        result = result[1] if result[1] is not None else result[0]
    if isinstance(result, dict):
        return result.get("result", result)
    return json.loads(result[0].text)


def test_login_sends_a_current_client_version(fake_signon):
    UnofficialAPIClient().ensure_connected()
    assert fake_signon["devices"], "no login was attempted"
    assert all(d["version"] == 8240 for d in fake_signon["devices"])


def test_device_version_can_be_overridden(fake_signon, monkeypatch):
    monkeypatch.setenv("TICKTICK_DEVICE_VERSION", "9001")
    UnofficialAPIClient().ensure_connected()
    assert fake_signon["devices"][0]["version"] == 9001


def test_need_upgrade_client_is_named_and_not_hammered(fake_signon):
    client = UnofficialAPIClient()
    assert client.ensure_connected() is False
    reason = client.unavailable_reason()
    assert "need_upgrade_client" in reason and "TICKTICK_DEVICE_VERSION" in reason
    # Not read as a wrong password, and no second rung: the device id can't fix it.
    assert client.status()["credentials_rejected"] is False
    assert len(fake_signon["devices"]) == 1
    # Cooling down: no new login on the next call.
    assert client.ensure_connected() is False
    assert len(fake_signon["devices"]) == 1


def test_two_factor_is_named(fake_signon):
    fake_signon["response"] = (200, {"authId": "abc"})
    client = UnofficialAPIClient()
    assert client.ensure_connected() is False
    assert "two-factor" in client.unavailable_reason()


def test_session_cache_lives_on_the_volume(monkeypatch, tmp_path):
    monkeypatch.delenv("TICKTICK_SESSION_CACHE")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert uc.state_path() == tmp_path / ".ticktick-session.json"


def test_unofficial_tools_return_a_clear_result_when_unavailable(fake_signon):
    out = _call("unofficial_pin_task", {"task_id": "t1"})
    assert out["unofficial_api_unavailable"] is True
    assert "need_upgrade_client" in out["error"]
    assert "ticktick_*" in out["hint"]


def test_every_unofficial_tool_is_guarded(monkeypatch):
    monkeypatch.setattr(unofficial_tools, "get_client", lambda: None)
    names = [t.name for t in mcp._tool_manager.list_tools() if t.name.startswith("unofficial_")]
    assert len(names) == 19
    for tool in mcp._tool_manager.list_tools():
        if tool.name.startswith("unofficial_"):
            assert tool.is_async, f"{tool.name} would block the event loop"


def test_official_tools_keep_working_while_unofficial_is_down(fake_signon, monkeypatch):
    class FakeOfficial:
        inbox_id = None

        async def get_projects(self):
            return [{"id": "p1", "name": "Work"}]

    monkeypatch.setattr(project_tools, "get_ticktick_client", lambda: FakeOfficial())
    assert _call("unofficial_get_task", {"task_id": "x"})["unofficial_api_unavailable"] is True
    out = _call("ticktick_list_projects", {})
    assert out["count"] == 1 and out["projects"][0]["id"] == "p1"


def test_a_session_token_alone_is_enough(monkeypatch):
    """Google/Apple sign-in accounts may have no password: the token must work alone."""
    monkeypatch.setattr(uc, "USERNAME", None)
    monkeypatch.setattr(uc, "PASSWORD", None)
    monkeypatch.setenv("TICKTICK_SESSION_TOKEN", "good-token")

    class T(httpx.BaseTransport):
        def handle_request(self, request):
            if request.url.path.endswith("/user/preferences/settings"):
                ok = "t=good-token" in request.headers.get("cookie", "")
                return httpx.Response(200 if ok else 401, json={"timeZone": "UTC", "id": "p"}, request=request)
            if "batch/check" in request.url.path:
                return httpx.Response(200, json={"inboxId": "inbox1"}, request=request)
            if request.url.path.endswith("/user/signon"):
                raise AssertionError("must not try a password login")
            return httpx.Response(404, request=request)

    real_init = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__", lambda self, *a, **kw: real_init(self, *a, **{**kw, "transport": T()}))
    client = UnofficialAPIClient()
    assert client.ensure_connected() is True
    assert client.status()["session_source"] == "env"
