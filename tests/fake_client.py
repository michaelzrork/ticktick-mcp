"""
An in-memory stand-in for UnofficialAPIClient.

It records every call so tests can assert on the exact JSON sent to TickTick —
which is where the timezone bug lived: the response echoed back what was sent, so
only the wire payload (or a read-back) shows the truth.
"""

from copy import deepcopy
from itertools import count
from typing import Any


class FakeAPIClient:
    """Minimal simulation of the v2 endpoints the tools touch."""

    def __init__(self, tasks: list[dict] | None = None):
        self.tasks: dict[str, dict] = {t["id"]: deepcopy(t) for t in (tasks or [])}
        self.calls: list[dict[str, Any]] = []
        self._ids = count(1)
        # Reproduce the API's silent-discard failure mode for badly shaped locations.
        self.drop_location_coords = False

    # --- assertions helpers ---

    def posts_to(self, endpoint: str) -> list[Any]:
        return [
            c["data"]
            for c in self.calls
            if c["endpoint"] == endpoint and c["method"] == "POST"
        ]

    def last_task_update(self) -> dict:
        payloads = self.posts_to("/api/v2/batch/task")
        assert payloads, "no POST to /api/v2/batch/task was recorded"
        updates = payloads[-1]["update"]
        assert len(updates) == 1, f"expected one updated task, got {len(updates)}"
        return updates[-1]

    def last_task_add(self) -> dict:
        payloads = self.posts_to("/api/v2/batch/task")
        assert payloads, "no POST to /api/v2/batch/task was recorded"
        adds = payloads[-1]["add"]
        assert len(adds) == 1, f"expected one added task, got {len(adds)}"
        return adds[-1]

    # --- the client surface the tools use ---

    def call_api(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | list | None = None,
        params: dict | None = None,
    ) -> dict | list | None:
        self.calls.append(
            {
                "endpoint": endpoint,
                "method": method,
                "data": deepcopy(data),
                "params": params,
            }
        )

        if method == "GET" and endpoint.startswith("/api/v2/task/"):
            task = self.tasks.get(endpoint.rsplit("/", 1)[1])
            return deepcopy(task) if task else None

        if endpoint == "/api/v2/batch/check/0":
            return {
                "syncTaskBean": {"update": [deepcopy(t) for t in self.tasks.values()]}
            }

        if endpoint == "/api/v2/batch/task" and method == "POST":
            assert isinstance(data, dict)
            id2etag = {}
            for task in data.get("add", []):
                stored = deepcopy(task)
                stored["id"] = f"task-{next(self._ids)}"
                self._store(stored)
                id2etag[stored["id"]] = f"etag-{stored['id']}"
            for task in data.get("update", []):
                stored = deepcopy(task)
                self._store(stored)
                id2etag[stored["id"]] = f"etag-{stored['id']}"
            for task_id in data.get("delete", []):
                self.tasks.pop(
                    task_id if isinstance(task_id, str) else task_id.get("taskId"), None
                )
            return {"id2etag": id2etag, "id2error": {}}

        if endpoint == "/api/v2/batch/taskParent" and method == "POST":
            assert isinstance(data, list)
            response = []
            for entry in data:
                child = self.tasks.get(entry["taskId"])
                if child is not None:
                    child["parentId"] = entry["parentId"]
                response.append(
                    {"taskId": entry["taskId"], "parentId": entry["parentId"]}
                )
            return response

        raise AssertionError(f"unexpected API call: {method} {endpoint}")

    def _store(self, task: dict) -> None:
        location = task.get("location")
        if self.drop_location_coords and location:
            # What the real API does with top-level or GeoJSON coordinates: 200 OK,
            # then loc comes back empty.
            task["location"] = {**location, "loc": None}
        self.tasks[task["id"]] = task
