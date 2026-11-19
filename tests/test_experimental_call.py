"""ticktick_experimental_api_call takes a body as an object or a JSON string."""
import asyncio

import pytest

from ticktick_mcp.tools import task_tools


class FakeClient:
    def __init__(self):
        self.calls = []

    async def _request(self, method, endpoint, json=None, params=None):
        self.calls.append((method, endpoint, json, params))
        return {"ok": True}


@pytest.fixture
def fake(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(task_tools, "get_ticktick_client", lambda: c)
    return c


def _call(**kw):
    fn = getattr(task_tools.ticktick_experimental_api_call, "fn", task_tools.ticktick_experimental_api_call)
    return asyncio.run(fn(**kw))


def test_object_body_and_params(fake):
    assert _call(endpoint="/task/x", method="POST", data={"title": "a"}, params={"q": 1}) == {"ok": True}
    assert fake.calls[-1] == ("POST", "/task/x", {"title": "a"}, {"q": 1})


def test_list_body(fake):
    _call(endpoint="/batch", method="POST", data=[{"id": "1"}])
    assert fake.calls[-1][2] == [{"id": "1"}]


def test_string_body_still_parsed(fake):
    _call(endpoint="/task/x", method="POST", data='{"title": "a"}', params='{"q": 1}')
    assert fake.calls[-1] == ("POST", "/task/x", {"title": "a"}, {"q": 1})


def test_no_body(fake):
    _call(endpoint="/project")
    assert fake.calls[-1] == ("GET", "/project", None, None)


def test_bad_json_string(fake):
    assert "Invalid JSON" in _call(endpoint="/x", method="POST", data="{nope")["error"]


def test_schema_accepts_an_object():
    """The bug: pydantic refused a dict before the function ran."""
    from ticktick_mcp.mcp_instance import mcp
    tool = mcp._tool_manager.get_tool("ticktick_experimental_api_call")
    tool.fn_metadata.arg_model.model_validate({"endpoint": "/x", "data": {"a": 1}, "params": {"b": 2}})
