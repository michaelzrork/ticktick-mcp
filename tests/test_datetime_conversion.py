"""
Regression tests for the timezone bug.

unofficial_create_task / unofficial_update_task used to send the naive local
datetime string straight to TickTick, which reads a naive string as UTC. A 9:30 AM
America/New_York task was stored as 09:30 UTC and fired at 5:30 AM local. Nothing
errored and the tool response echoed back the local string, so the only evidence is
the payload on the wire (asserted here) or a read-back (see test_live_api.py).
"""

import pytest

from ticktick_mcp.tools import unofficial_tools
from ticktick_mcp.tools.unofficial_tools import (
    _format_datetime_field,
    unofficial_create_task,
    unofficial_update_task,
)

from fake_client import FakeAPIClient

NY = "America/New_York"

TIMED_TASK = {
    "id": "task-timed",
    "projectId": "proj-1",
    "title": "Existing timed task",
    "isAllDay": False,
    "timeZone": NY,
    "startDate": "2026-08-01T13:30:00.000+0000",
    "dueDate": "2026-08-01T13:30:00.000+0000",
    "content": "keep me",
}

ALL_DAY_TASK = {
    "id": "task-all-day",
    "projectId": "proj-1",
    "title": "Existing all-day task",
    "isAllDay": True,
    "timeZone": NY,
    "startDate": "2026-08-01T04:00:00.000+0000",
    "dueDate": "2026-08-01T04:00:00.000+0000",
}


@pytest.fixture
def client(monkeypatch):
    fake = FakeAPIClient([TIMED_TASK, ALL_DAY_TASK])
    monkeypatch.setattr(unofficial_tools, "_get_api_client", lambda: fake)
    return fake


# ==================== The reported bug ====================


def test_update_converts_local_time_to_utc(client):
    """9:30 AM in America/New_York (UTC-4 in August) must be stored as 13:30 UTC."""
    result = unofficial_update_task(
        task_id="task-timed",
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        time_zone=NY,
    )

    assert "error" not in result
    sent = client.last_task_update()
    assert sent["startDate"] == "2026-08-29T13:30:00.000+0000"
    assert sent["dueDate"] == "2026-08-29T13:30:00.000+0000"
    assert sent["isAllDay"] is False
    assert sent["timeZone"] == NY
    # The old bug wrote the local string through unchanged.
    assert sent["startDate"] != "2026-08-29T09:30:00.000+0000"


def test_create_converts_local_time_to_utc(client):
    result = unofficial_create_task(
        title="Meeting",
        project_id="proj-1",
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        is_all_day=False,
        time_zone=NY,
    )

    assert "error" not in result
    sent = client.last_task_add()
    assert sent["startDate"] == "2026-08-29T13:30:00.000+0000"
    assert sent["dueDate"] == "2026-08-29T13:30:00.000+0000"


def test_stored_time_reads_back_as_the_local_time_requested(client):
    """Round-trip: what the fake stores, converted back, is the wall clock asked for."""
    unofficial_update_task(
        task_id="task-timed",
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        time_zone=NY,
    )

    stored = client.tasks["task-timed"]["startDate"]
    local = unofficial_tools._parse_datetime_input(stored).astimezone(
        unofficial_tools._zone(NY)
    )
    assert local.strftime("%Y-%m-%dT%H:%M:%S") == "2026-08-29T09:30:00"
    assert local.utcoffset().total_seconds() == -4 * 3600


# ==================== All-day tasks must not be shifted ====================


def test_all_day_create_is_not_shifted(client):
    unofficial_create_task(
        title="Buy groceries",
        project_id="proj-1",
        start_date="2026-08-29",
        due_date="2026-08-29",
        time_zone=NY,
    )

    sent = client.last_task_add()
    assert sent["isAllDay"] is True
    # Date-only, unconverted: TickTick applies timeZone itself and stores
    # 2026-08-29T04:00:00.000+0000 for ET in summer.
    assert sent["startDate"] == "2026-08-29"
    assert sent["dueDate"] == "2026-08-29"


def test_all_day_update_is_not_shifted(client):
    unofficial_update_task(
        task_id="task-all-day",
        start_date="2026-08-29",
        due_date="2026-08-29",
        time_zone=NY,
    )

    sent = client.last_task_update()
    assert sent["isAllDay"] is True
    assert sent["startDate"] == "2026-08-29"


def test_all_day_roundtrip_is_not_double_shifted(client):
    """Feeding a stored all-day value back in keeps the same calendar date."""
    unofficial_update_task(
        task_id="task-all-day",
        start_date="2026-08-29T04:00:00.000+0000",
        due_date="2026-08-29T04:00:00.000+0000",
        is_all_day=True,
        time_zone=NY,
    )

    sent = client.last_task_update()
    assert sent["startDate"] == "2026-08-29"
    assert sent["dueDate"] == "2026-08-29"


# ==================== is_all_day inference ====================


def test_time_component_implies_timed_task(client):
    unofficial_create_task(
        title="Standup",
        project_id="proj-1",
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        time_zone=NY,
    )

    sent = client.last_task_add()
    assert sent["isAllDay"] is False
    assert sent["startDate"] == "2026-08-29T13:30:00.000+0000"


def test_explicit_flag_overrides_inference(client):
    unofficial_create_task(
        title="All day anyway",
        project_id="proj-1",
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        is_all_day=True,
        time_zone=NY,
    )

    sent = client.last_task_add()
    assert sent["isAllDay"] is True
    assert sent["startDate"] == "2026-08-29"


def test_task_without_dates_defaults_to_all_day(client):
    unofficial_create_task(title="Someday", project_id="proj-1")
    assert client.last_task_add()["isAllDay"] is True


def test_update_keeps_existing_all_day_flag(client):
    unofficial_update_task(task_id="task-timed", title="Renamed")
    sent = client.last_task_update()
    assert sent["isAllDay"] is False
    # Dates untouched when not supplied.
    assert sent["startDate"] == TIMED_TASK["startDate"]


# ==================== Recurrence anchor ====================


def test_repeat_first_date_uses_the_zone_offset(client):
    """repeatFirstDate was hardcoded to T05:00 (EST) — wrong all summer."""
    unofficial_create_task(
        title="Take medication",
        project_id="proj-1",
        specific_dates=["2026-08-29", "2026-09-05"],
        time_zone=NY,
    )

    sent = client.last_task_add()
    assert sent["repeatFirstDate"] == "2026-08-29T04:00:00.000+0000"  # EDT
    assert sent["repeatFlag"] == "ERULE:NAME=CUSTOM;BYDATE=20260829,20260905"


def test_repeat_first_date_in_winter(client):
    unofficial_create_task(
        title="Take medication",
        project_id="proj-1",
        specific_dates=["2026-01-15"],
        time_zone=NY,
    )

    assert client.last_task_add()["repeatFirstDate"] == "2026-01-15T05:00:00.000+0000"


# ==================== Bad input surfaces as an error ====================


def test_unknown_timezone_returns_an_error(client):
    result = unofficial_update_task(
        task_id="task-timed",
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        time_zone="Mars/Olympus_Mons",
    )
    assert "error" in result
    assert "Mars/Olympus_Mons" in result["error"]
    assert not client.posts_to("/api/v2/batch/task")


def test_unparseable_date_returns_an_error(client):
    result = unofficial_update_task(
        task_id="task-timed",
        start_date="next tuesday",
        due_date="next tuesday",
        time_zone=NY,
    )
    assert "error" in result
    assert not client.posts_to("/api/v2/batch/task")


# ==================== The conversion helper itself ====================


@pytest.mark.parametrize(
    "local, zone, expected",
    [
        # America/New_York: UTC-4 in summer (EDT), UTC-5 in winter (EST).
        ("2026-08-29T09:30:00", "America/New_York", "2026-08-29T13:30:00.000+0000"),
        ("2026-01-15T09:30:00", "America/New_York", "2026-01-15T14:30:00.000+0000"),
        # Crossing midnight backwards.
        ("2026-08-29T22:00:00", "America/Los_Angeles", "2026-08-30T05:00:00.000+0000"),
        # Ahead of UTC: the UTC instant lands on the previous day.
        ("2026-08-29T07:00:00", "Asia/Tokyo", "2026-08-28T22:00:00.000+0000"),
        ("2026-08-29T09:30:00", "Europe/London", "2026-08-29T08:30:00.000+0000"),
        ("2026-08-29T09:30:00", "UTC", "2026-08-29T09:30:00.000+0000"),
        # Half-hour offset.
        ("2026-08-29T09:30:00", "Asia/Kolkata", "2026-08-29T04:00:00.000+0000"),
        # Already-aware input is converted, not re-localized.
        (
            "2026-08-29T13:30:00.000+0000",
            "America/New_York",
            "2026-08-29T13:30:00.000+0000",
        ),
        ("2026-08-29T13:30:00Z", "America/New_York", "2026-08-29T13:30:00.000+0000"),
        # Accepted spellings of the same local time.
        ("2026-08-29 09:30:00", "America/New_York", "2026-08-29T13:30:00.000+0000"),
        ("2026-08-29T09:30", "America/New_York", "2026-08-29T13:30:00.000+0000"),
        # A bare date on a timed task means local midnight.
        ("2026-08-29", "America/New_York", "2026-08-29T04:00:00.000+0000"),
    ],
)
def test_format_timed_datetime(local, zone, expected):
    assert _format_datetime_field(local, zone, is_all_day=False) == expected


@pytest.mark.parametrize(
    "value, zone, expected",
    [
        ("2026-08-29", "America/New_York", "2026-08-29"),
        # Stored all-day values map back to their local calendar date.
        ("2026-08-29T04:00:00.000+0000", "America/New_York", "2026-08-29"),
        ("2026-08-28T15:00:00.000+0000", "Asia/Tokyo", "2026-08-29"),
        # A stray time component is dropped rather than shifting the date.
        ("2026-08-29T00:00:00", "America/New_York", "2026-08-29"),
    ],
)
def test_format_all_day_datetime(value, zone, expected):
    assert _format_datetime_field(value, zone, is_all_day=True) == expected


def test_format_none_stays_none():
    assert _format_datetime_field(None, NY, is_all_day=False) is None
