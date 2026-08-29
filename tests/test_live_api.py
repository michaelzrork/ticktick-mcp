"""
Live round-trip tests against the real TickTick account.

These are the tests that actually caught the timezone bug: the tool response echoes
back whatever local string you sent, so only re-fetching the task shows what was
stored. They create throwaway tasks in the Inbox and delete them again.

Opt in with real credentials plus:

    TICKTICK_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_api.py
"""

import os
from datetime import timezone

import pytest

from conftest import has_live_credentials

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("TICKTICK_RUN_LIVE_TESTS") != "1" or not has_live_credentials(),
        reason="live API tests need real credentials and TICKTICK_RUN_LIVE_TESTS=1",
    ),
]

NY = "America/New_York"


@pytest.fixture(scope="module")
def tools():
    from ticktick_mcp.tools import unofficial_tools

    return unofficial_tools


@pytest.fixture
def inbox_id(tools):
    client = tools._get_api_client()
    if not client.inbox_id:
        pytest.skip("no inbox id available")
    return client.inbox_id


@pytest.fixture
def task_factory(tools, inbox_id):
    """Create tasks and clean them up, whatever the test does."""
    created: list[str] = []

    def create(**kwargs):
        result = tools.unofficial_create_task(project_id=inbox_id, **kwargs)
        assert "error" not in result, result
        task_id = result["task"]["id"]
        created.append(task_id)
        return task_id

    yield create

    for task_id in created:
        tools.unofficial_delete_task(task_id)


@pytest.mark.parametrize(
    "local, expected_utc, expected_offset_hours",
    [
        ("2026-08-29T09:30:00", "2026-08-29T13:30:00.000+0000", -4),  # EDT
        ("2026-01-15T09:30:00", "2026-01-15T14:30:00.000+0000", -5),  # EST
    ],
)
def test_timed_task_round_trips_in_the_right_zone(
    tools, task_factory, local, expected_utc, expected_offset_hours
):
    """Write a timed task, read it back, assert the UTC offset matches the zone."""
    task_id = task_factory(
        title=f"[test] timezone round-trip {local}",
        start_date=local,
        due_date=local,
        is_all_day=False,
        time_zone=NY,
    )

    stored = tools.unofficial_get_task(task_id)
    assert stored["startDate"] == expected_utc
    assert stored["dueDate"] == expected_utc

    # And the stored instant, viewed in the task's zone, is the wall clock we asked
    # for at the offset that zone is really on that date.
    back = tools._parse_datetime_input(stored["startDate"]).astimezone(
        tools._zone(NY)
    )
    assert back.strftime("%Y-%m-%dT%H:%M:%S") == local
    assert back.utcoffset().total_seconds() == expected_offset_hours * 3600
    assert stored["startDate"].endswith("+0000")
    assert (
        tools._parse_datetime_input(stored["startDate"]).astimezone(timezone.utc).hour
        == int(expected_utc[11:13])
    )


def test_updating_a_timed_task_round_trips(tools, task_factory):
    task_id = task_factory(
        title="[test] timezone update round-trip",
        start_date="2026-08-01T08:00:00",
        due_date="2026-08-01T08:00:00",
        is_all_day=False,
        time_zone=NY,
    )

    result = tools.unofficial_update_task(
        task_id=task_id,
        start_date="2026-08-29T09:30:00",
        due_date="2026-08-29T09:30:00",
        time_zone=NY,
    )
    assert "error" not in result, result

    stored = tools.unofficial_get_task(task_id)
    assert stored["startDate"] == "2026-08-29T13:30:00.000+0000"


def test_all_day_task_keeps_its_calendar_date(tools, task_factory):
    """The all-day convention (T04:00:00.000+0000 for ET) must not double-shift."""
    task_id = task_factory(
        title="[test] all-day round-trip",
        start_date="2026-08-29",
        due_date="2026-08-29",
        time_zone=NY,
    )

    stored = tools.unofficial_get_task(task_id)
    assert stored["isAllDay"] is True
    local_date = (
        tools._parse_datetime_input(stored["startDate"])
        .astimezone(tools._zone(NY))
        .strftime("%Y-%m-%d")
    )
    assert local_date == "2026-08-29"


def test_location_round_trips(tools, task_factory):
    """A 200 proves nothing — assert loc.latitude / loc.longitude are non-null."""
    task_id = task_factory(title="[test] location round-trip")

    result = tools.unofficial_set_task_location(
        task_id=task_id,
        alias="Mattress Recycling",
        address="525 Riverside Ave, Burlington, VT 05401",
        short_address="525 Riverside Ave",
        latitude=44.4893668,
        longitude=-73.2027386,
        radius=300,
        transition_type=1,
    )
    assert "error" not in result, result
    assert result["location_verified"] is True

    stored = tools.unofficial_get_task(task_id)["location"]
    assert stored["loc"]["latitude"] == pytest.approx(44.4893668)
    assert stored["loc"]["longitude"] == pytest.approx(-73.2027386)
    assert stored["radius"] == pytest.approx(300)
    assert stored["transitionType"] == 1

    removal = tools.unofficial_remove_task_location(task_id=task_id)
    assert "error" not in removal, removal
    assert not (tools.unofficial_get_task(task_id) or {}).get("location")
