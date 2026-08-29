"""
Tests for geofenced task locations.

The API's failure mode here is silence: a wrongly shaped location returns 200 and is
then discarded, so these tests assert on the exact payload sent and on the read-back
the tools perform.
"""

import pytest

from ticktick_mcp.tools import unofficial_tools
from ticktick_mcp.tools.unofficial_tools import (
    _normalize_location,
    unofficial_create_task,
    unofficial_remove_task_location,
    unofficial_set_task_location,
    unofficial_update_task,
)

from fake_client import FakeAPIClient

NY = "America/New_York"

VERIFIED_LOCATION = {
    "alias": "Mattress Recycling",
    "address": "525 Riverside Ave, Burlington, VT 05401",
    "shortAddress": "525 Riverside Ave",
    "loc": {"latitude": 44.4893668, "longitude": -73.2027386},
    "radius": 300,
    "transitionType": 1,
}

PLAIN_TASK = {
    "id": "task-1",
    "projectId": "proj-1",
    "title": "Drop off mattress",
    "isAllDay": True,
    "timeZone": NY,
    "content": "keep me",
    "tags": ["errands"],
}

LOCATED_TASK = {
    "id": "task-2",
    "projectId": "proj-1",
    "title": "Already located",
    "isAllDay": True,
    "location": {**VERIFIED_LOCATION, "removed": False},
}


@pytest.fixture
def client(monkeypatch):
    fake = FakeAPIClient([PLAIN_TASK, LOCATED_TASK])
    monkeypatch.setattr(unofficial_tools, "_get_api_client", lambda: fake)
    return fake


# ==================== set / remove ====================


def test_set_location_sends_the_verified_shape(client):
    result = unofficial_set_task_location(
        task_id="task-1",
        alias="Mattress Recycling",
        address="525 Riverside Ave, Burlington, VT 05401",
        short_address="525 Riverside Ave",
        latitude=44.4893668,
        longitude=-73.2027386,
        radius=300,
        transition_type=1,
    )

    assert result["success"] is True
    assert result["location_verified"] is True

    sent = client.last_task_update()["location"]
    assert sent["loc"] == {"latitude": 44.4893668, "longitude": -73.2027386}
    assert sent["alias"] == "Mattress Recycling"
    assert sent["address"] == "525 Riverside Ave, Burlington, VT 05401"
    assert sent["shortAddress"] == "525 Riverside Ave"
    assert sent["radius"] == 300
    assert sent["transitionType"] == 1
    # Coordinates must not also appear at the top level, and "removed" is
    # server-managed.
    assert "latitude" not in sent and "longitude" not in sent
    assert "removed" not in sent


def test_set_location_writes_the_whole_task_back(client):
    """batch/task nulls omitted fields, so the merge must carry everything."""
    unofficial_set_task_location(
        task_id="task-1",
        address="525 Riverside Ave, Burlington, VT 05401",
        latitude=44.4893668,
        longitude=-73.2027386,
    )

    sent = client.last_task_update()
    assert sent["title"] == PLAIN_TASK["title"]
    assert sent["content"] == PLAIN_TASK["content"]
    assert sent["tags"] == PLAIN_TASK["tags"]
    assert sent["projectId"] == PLAIN_TASK["projectId"]


def test_set_location_defaults(client):
    unofficial_set_task_location(
        task_id="task-1",
        address="525 Riverside Ave, Burlington, VT 05401",
        latitude=44.4893668,
        longitude=-73.2027386,
    )

    sent = client.last_task_update()["location"]
    assert sent["radius"] == 300  # meters
    assert sent["transitionType"] == 1  # arrive/entering
    assert sent["alias"] == "525 Riverside Ave, Burlington, VT 05401"


def test_set_location_reports_a_silent_discard(client):
    """A 200 that drops the coordinates must surface as an error, not success."""
    client.drop_location_coords = True

    result = unofficial_set_task_location(
        task_id="task-1",
        address="525 Riverside Ave, Burlington, VT 05401",
        latitude=44.4893668,
        longitude=-73.2027386,
    )

    assert "error" in result
    assert result["location_verified"] is False


def test_set_location_rejects_a_bad_transition_type(client):
    result = unofficial_set_task_location(
        task_id="task-1",
        address="525 Riverside Ave",
        latitude=44.4893668,
        longitude=-73.2027386,
        transition_type=3,
    )
    assert "error" in result
    assert not client.posts_to("/api/v2/batch/task")


def test_set_location_on_a_missing_task(client):
    result = unofficial_set_task_location(
        task_id="nope",
        address="525 Riverside Ave",
        latitude=44.4893668,
        longitude=-73.2027386,
    )
    assert "error" in result
    assert "not found" in result["error"]


def test_remove_location(client):
    result = unofficial_remove_task_location(task_id="task-2")

    assert result["success"] is True
    assert result["location_verified"] is True
    assert client.last_task_update()["location"] is None
    assert client.tasks["task-2"]["location"] is None
    # The rest of the task survives the read-merge-write.
    assert client.last_task_update()["title"] == LOCATED_TASK["title"]


def test_remove_location_when_there_is_none(client):
    result = unofficial_remove_task_location(task_id="task-1")
    assert result["success"] is True
    assert not client.posts_to("/api/v2/batch/task")


# ==================== inline on create / update ====================


def test_create_task_with_location(client):
    result = unofficial_create_task(
        title="Drop off mattress",
        project_id="proj-1",
        location=VERIFIED_LOCATION,
    )

    assert result["success"] is True
    assert result["location_verified"] is True
    assert client.last_task_add()["location"]["loc"] == VERIFIED_LOCATION["loc"]


def test_update_task_with_location(client):
    result = unofficial_update_task(task_id="task-1", location=VERIFIED_LOCATION)

    assert result["success"] is True
    assert result["location_verified"] is True
    assert client.last_task_update()["location"]["loc"] == VERIFIED_LOCATION["loc"]


def test_update_without_location_leaves_it_alone(client):
    unofficial_update_task(task_id="task-2", title="Renamed")
    assert client.last_task_update()["location"]["loc"] == VERIFIED_LOCATION["loc"]


# ==================== normalization of discarded shapes ====================


def test_normalize_keeps_the_verified_shape():
    assert _normalize_location(VERIFIED_LOCATION) == VERIFIED_LOCATION


def test_normalize_rewrites_top_level_coordinates():
    """Top-level lat/lng are accepted by the API and then dropped — rewrite them."""
    normalized = _normalize_location(
        {
            "address": "525 Riverside Ave, Burlington, VT 05401",
            "latitude": 44.4893668,
            "longitude": -73.2027386,
        }
    )

    assert normalized["loc"] == {"latitude": 44.4893668, "longitude": -73.2027386}
    assert "latitude" not in normalized and "longitude" not in normalized
    assert normalized["radius"] == 300
    assert normalized["transitionType"] == 1


def test_normalize_rewrites_geojson():
    """GeoJSON is also silently discarded; note coordinates are [lng, lat]."""
    normalized = _normalize_location(
        {
            "address": "525 Riverside Ave",
            "loc": {"type": "Point", "coordinates": [-73.2027386, 44.4893668]},
        }
    )
    assert normalized["loc"] == {"latitude": 44.4893668, "longitude": -73.2027386}


def test_normalize_rewrites_top_level_geojson():
    normalized = _normalize_location(
        {
            "address": "525 Riverside Ave",
            "type": "Point",
            "coordinates": [-73.2027386, 44.4893668],
        }
    )
    assert normalized["loc"] == {"latitude": 44.4893668, "longitude": -73.2027386}
    assert "type" not in normalized and "coordinates" not in normalized


def test_normalize_drops_the_server_managed_removed_field():
    normalized = _normalize_location({**VERIFIED_LOCATION, "removed": False})
    assert "removed" not in normalized


def test_normalize_requires_coordinates():
    with pytest.raises(ValueError, match="latitude"):
        _normalize_location({"address": "525 Riverside Ave"})


def test_create_with_flat_coordinates_is_repaired(client):
    """The trap: this shape would 200 and store loc: null if passed through."""
    result = unofficial_create_task(
        title="Drop off mattress",
        project_id="proj-1",
        location={
            "address": "525 Riverside Ave, Burlington, VT 05401",
            "latitude": 44.4893668,
            "longitude": -73.2027386,
        },
    )

    assert result["location_verified"] is True
    sent = client.last_task_add()["location"]
    assert sent["loc"] == {"latitude": 44.4893668, "longitude": -73.2027386}
    assert "latitude" not in sent
