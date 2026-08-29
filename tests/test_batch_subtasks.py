"""Tests for unofficial_batch_make_subtasks (/api/v2/batch/taskParent)."""

import pytest

from ticktick_mcp.tools import unofficial_tools
from ticktick_mcp.tools.unofficial_tools import unofficial_batch_make_subtasks

from fake_client import FakeAPIClient

TASKS = [
    {"id": "parent-1", "projectId": "proj-1", "title": "Parent"},
    {"id": "child-1", "projectId": "proj-1", "title": "Child 1"},
    {"id": "child-2", "projectId": "proj-1", "title": "Child 2"},
    {"id": "other-project-task", "projectId": "proj-2", "title": "Elsewhere"},
]


@pytest.fixture
def client(monkeypatch):
    fake = FakeAPIClient(TASKS)
    monkeypatch.setattr(unofficial_tools, "_get_api_client", lambda: fake)
    return fake


def test_links_many_pairs_in_one_request(client):
    result = unofficial_batch_make_subtasks(
        [
            {"child_task_id": "child-1", "parent_task_id": "parent-1"},
            {"child_task_id": "child-2", "parent_task_id": "parent-1"},
        ]
    )

    assert result["success"] is True
    assert result["linked_count"] == 2

    posts = client.posts_to("/api/v2/batch/taskParent")
    assert len(posts) == 1
    # The body is a plain array, not the add/update/delete envelope.
    assert posts[0] == [
        {"taskId": "child-1", "parentId": "parent-1", "projectId": "proj-1"},
        {"taskId": "child-2", "parentId": "parent-1", "projectId": "proj-1"},
    ]
    assert client.tasks["child-1"]["parentId"] == "parent-1"


def test_accepts_the_native_api_keys(client):
    result = unofficial_batch_make_subtasks(
        [{"taskId": "child-1", "parentId": "parent-1", "projectId": "proj-1"}]
    )

    assert result["success"] is True
    assert client.posts_to("/api/v2/batch/taskParent")[0] == [
        {"taskId": "child-1", "parentId": "parent-1", "projectId": "proj-1"}
    ]


def test_explicit_project_id_skips_the_lookup(client):
    unofficial_batch_make_subtasks(
        [{"child_task_id": "child-1", "parent_task_id": "parent-1"}],
        project_id="proj-1",
    )

    assert not [c for c in client.calls if c["endpoint"] == "/api/v2/batch/check/0"]


def test_project_ids_are_looked_up_once(client):
    unofficial_batch_make_subtasks(
        [
            {"child_task_id": "child-1", "parent_task_id": "parent-1"},
            {"child_task_id": "child-2", "parent_task_id": "parent-1"},
        ]
    )

    lookups = [c for c in client.calls if c["endpoint"] == "/api/v2/batch/check/0"]
    assert len(lookups) == 1


def test_rejects_a_cross_project_pair(client):
    result = unofficial_batch_make_subtasks(
        [{"child_task_id": "other-project-task", "parent_task_id": "parent-1"}]
    )

    assert "error" in result
    assert "same project" in result["error"]
    assert not client.posts_to("/api/v2/batch/taskParent")


def test_rejects_an_unknown_task(client):
    result = unofficial_batch_make_subtasks(
        [{"child_task_id": "missing", "parent_task_id": "parent-1"}]
    )
    assert "error" in result
    assert not client.posts_to("/api/v2/batch/taskParent")


def test_rejects_an_incomplete_pair(client):
    result = unofficial_batch_make_subtasks([{"child_task_id": "child-1"}])
    assert "error" in result
    assert not client.posts_to("/api/v2/batch/taskParent")


def test_rejects_an_empty_list(client):
    assert "error" in unofficial_batch_make_subtasks([])


def test_large_batches_are_chunked(client):
    children = [
        {"id": f"c{i}", "projectId": "proj-1", "title": f"Child {i}"} for i in range(120)
    ]
    for child in children:
        client.tasks[child["id"]] = child

    result = unofficial_batch_make_subtasks(
        [{"child_task_id": c["id"], "parent_task_id": "parent-1"} for c in children],
        project_id="proj-1",
    )

    posts = client.posts_to("/api/v2/batch/taskParent")
    assert [len(p) for p in posts] == [50, 50, 20]
    assert result["linked_count"] == 120
