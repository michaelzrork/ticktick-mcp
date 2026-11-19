"""Timed values reach TickTick's private API as UTC, converted from the user's zone.

The bug (2026-11-19): a bare "2026-11-19T08:00:00" with time_zone
America/New_York was sent as-is, TickTick read it as UTC, and the task (and
its reminder) showed at 3:00 AM.
"""
import pytest

from ticktick_mcp.tools import unofficial_tools as ut

NY = "America/New_York"


@pytest.mark.parametrize("given,expected", [
    ("2026-11-19T08:00:00", "2026-11-19T13:00:00.000+0000"),   # EST, UTC-5
    ("2026-07-19T08:00:00", "2026-07-19T12:00:00.000+0000"),   # EDT, UTC-4
    ("2026-11-19T20:00:00", "2026-11-20T01:00:00.000+0000"),   # evening rolls the UTC date
    ("2026-11-20T23:30:00", "2026-11-21T04:30:00.000+0000"),
    ("2026-11-19T08:00:00-05:00", "2026-11-19T13:00:00.000+0000"),  # explicit offset honored
    ("2026-11-19T13:00:00Z", "2026-11-19T13:00:00.000+0000"),
    ("2026-11-19", "2026-11-19"),                                 # all-day: unchanged
    (None, None),
])
def test_to_ticktick_time(given, expected):
    assert ut._to_ticktick_time(given, NY) == expected


def test_other_zone():
    assert ut._to_ticktick_time("2026-11-19T08:00:00", "America/Los_Angeles") == "2026-11-19T16:00:00.000+0000"


def test_repeat_first_date_follows_daylight_saving():
    assert ut._local_midnight_utc("2026-11-19", NY) == "2026-11-19T05:00:00.000+0000"
    assert ut._local_midnight_utc("2026-07-19", NY) == "2026-07-19T04:00:00.000+0000"


class FakeClient:
    def __init__(self, existing=None):
        self.sent = []
        self.existing = existing or {}

    def call_api(self, endpoint, method="GET", data=None, params=None):
        if method == "GET":
            return dict(self.existing)
        self.sent.append(data)
        return {"id2etag": {}, "id2error": {}}


def _fn(tool):
    return getattr(tool, "fn", tool)


def _sent_task(fake):
    payload = fake.sent[-1]
    return (payload.get("add") or payload.get("update"))[0]


def test_create_sends_utc(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(ut, "_get_api_client", lambda: fake)
    _fn(ut.unofficial_create_task)(title="Weigh-in", project_id="inbox1",
                                   start_date="2026-11-19T08:00:00", due_date="2026-11-19T08:00:00",
                                   is_all_day=False, time_zone=NY)
    t = _sent_task(fake)
    assert t["startDate"] == t["dueDate"] == "2026-11-19T13:00:00.000+0000"
    assert t["timeZone"] == NY


def test_update_sends_utc(monkeypatch):
    fake = FakeClient(existing={"id": "t1", "projectId": "inbox1", "title": "x"})
    monkeypatch.setattr(ut, "_get_api_client", lambda: fake)
    _fn(ut.unofficial_update_task)(task_id="t1", start_date="2026-11-19T20:00:00",
                                   due_date="2026-11-19T20:00:00", time_zone=NY)
    t = _sent_task(fake)
    assert t["startDate"] == t["dueDate"] == "2026-11-20T01:00:00.000+0000"


def test_all_day_create_unchanged(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(ut, "_get_api_client", lambda: fake)
    _fn(ut.unofficial_create_task)(title="Refi", project_id="inbox1",
                                   start_date="2026-11-19", due_date="2026-11-19", time_zone=NY)
    t = _sent_task(fake)
    assert t["startDate"] == t["dueDate"] == "2026-11-19"
