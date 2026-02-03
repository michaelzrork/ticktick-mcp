"""
MCP Tools for unofficial TickTick API features.

These tools use direct API calls to unofficial v2 endpoints:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs
- Full CRUD operations via unofficial API
- Fresh data fetches (no caching!)

Requires TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp.unofficial_client import UnofficialAPIClient, get_client

logger = logging.getLogger(__name__)


# ==================== API Endpoints ====================

BATCH_CHECK = "/api/v2/batch/check/0"
BATCH_TASK = "/api/v2/batch/task"
BATCH_TASK_PIN = "/api/v2/batch/taskPin"
TASK_ACTIVITY = "/api/v1/task/activity/{task_id}"
TASK_BY_ID = "/api/v2/task/{task_id}"
BATCH_TASK_PROJECT = "/api/v2/batch/taskProject" 


# ==================== Helpers ====================


def _get_api_client() -> UnofficialAPIClient:
    """Get the unofficial API client or raise an error."""
    client = get_client()
    if not client:
        raise RuntimeError("Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD.")
    return client


def _fetch_all_data(client: UnofficialAPIClient) -> dict:
    """Fetch all data from batch/check endpoint."""
    result = client.call_api(BATCH_CHECK)
    assert isinstance(result, dict)
    return result


def _get_task_by_id(client: UnofficialAPIClient, task_id: str) -> dict | None:
    """Fetch a task by ID from the batch/check data."""
    data = _fetch_all_data(client)
    tasks = data.get("syncTaskBean", {}).get("update", [])
    for task in tasks:
        if task.get("id") == task_id:
            return task
    return None


# ==================== Activity & Pin Tools ====================


@mcp.tool()
def unofficial_get_task_activity(task_id: str) -> dict[str, Any] | list[dict]:
    """
    Get the activity log for a specific task.

    Shows history of changes, completions, and modifications including:
    - T_REPEAT: Task repeated/rescheduled
    - T_DUE: Due date changed
    - T_CREATE: Task created
    - T_COMPLETE: Task completed
    - T_UPDATE: Task updated

    Each entry includes timestamps, before/after values, and device info.

    Args:
        task_id: The task ID

    Returns:
        List of activity entries or error dict
    """
    logger.info(f"unofficial_get_task_activity called for task: {task_id}")

    try:
        client = _get_api_client()
        endpoint = TASK_ACTIVITY.format(task_id=task_id)
        activities = client.call_api(endpoint)
        logger.info(f"Got {len(activities)} activity entries")
        return activities
    except Exception as e:
        logger.error(f"Failed to get task activity: {e}")
        return {"error": str(e)}

from datetime import datetime, timezone

@mcp.tool()
def unofficial_pin_task(task_id: str) -> dict[str, Any]:
    """
    Pin a task to the top of the list.

    Pinned tasks appear at the top of the Today view and project lists.

    Args:
        task_id: The task ID to pin

    Returns:
        Success message or error
    """
    logger.info(f"unofficial_pin_task called for task: {task_id}")

    try:
        client = _get_api_client()
        
        # Fetch the task first
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}
        
        # Set pinnedTime to current timestamp
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        task["pinnedTime"] = now
        
        # Update via batch endpoint
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)
        
        if isinstance(result, dict) and result.get("id2error", {}).get(task_id):
            return {"error": f"Pin failed: {result['id2error'][task_id]}"}
        
        logger.info(f"Successfully pinned task {task_id}")
        return {"success": True, "message": f"Task {task_id} pinned", "pinnedTime": now}
    except Exception as e:
        logger.error(f"Failed to pin task: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_unpin_task(task_id: str) -> dict[str, Any]:
    """
    Unpin a task (remove from pinned list).

    Args:
        task_id: The task ID to unpin

    Returns:
        Success message or error
    """
    logger.info(f"unofficial_unpin_task called for task: {task_id}")

    try:
        client = _get_api_client()
        
        # Fetch the task first
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}
        
        # Set pinnedTime to "-1" to unpin
        task["pinnedTime"] = "-1"
        
        # Update via batch endpoint
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)
        
        if isinstance(result, dict) and result.get("id2error", {}).get(task_id):
            return {"error": f"Unpin failed: {result['id2error'][task_id]}"}
        
        logger.info(f"Successfully unpinned task {task_id}")
        return {"success": True, "message": f"Task {task_id} unpinned"}
    except Exception as e:
        logger.error(f"Failed to unpin task: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_set_repeat_from(
    task_id: str,
    repeat_from: str
) -> dict[str, Any]:
    """
    Set whether a repeating task repeats from due date or completion date.

    This is only relevant for recurring tasks with a repeatFlag set.

    Args:
        task_id: The task ID
        repeat_from: "due_date" or "completion_date"
            - "due_date": Next occurrence calculated from the original due date
            - "completion_date": Next occurrence calculated from when task was completed

    Returns:
        Success message or error
    """
    logger.info(f"unofficial_set_repeat_from called: task={task_id}, repeat_from={repeat_from}")

    # Map user-friendly values to API values
    repeat_from_map = {
        "due_date": "0",
        "completion_date": "1"
    }

    api_value = repeat_from_map.get(repeat_from.lower().replace(" ", "_"))
    if not api_value:
        return {"error": f"repeat_from must be 'due_date' or 'completion_date', got '{repeat_from}'"}

    try:
        client = _get_api_client()

        # Fetch full task first to avoid wiping other fields
        task = _get_task_by_id(client, task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}

        task["repeatFrom"] = api_value

        payload = {"add": [], "update": [task], "delete": []}
        client.call_api(BATCH_TASK, method="POST", data=payload)
        logger.info(f"Successfully set repeat_from for task {task_id}")
        return {"success": True, "message": f"Task {task_id} set to repeat from {repeat_from}"}
    except Exception as e:
        logger.error(f"Failed to set repeat_from: {e}")
        return {"error": str(e)}

@mcp.tool()
def unofficial_set_specific_dates(
    task_id: str,
    dates: list[str]
) -> dict[str, Any]:
    """
    Set a task to repeat on specific dates.
    
    This replaces any existing recurrence pattern (RRULE) with specific dates (ERULE).
    
    Args:
        task_id: The task ID
        dates: List of dates in YYYY-MM-DD format (e.g., ["2026-02-05", "2026-02-10"])
    
    Returns:
        Success message or error
    """
    logger.info(f"unofficial_set_specific_dates called: task={task_id}, dates={dates}")

    if not dates:
        return {"error": "At least one date is required"}

    try:
        client = _get_api_client()

        # Fetch full task first
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Convert dates to YYYYMMDD format and sort
        formatted_dates = sorted([d.replace("-", "") for d in dates])
        erule = f"ERULE:NAME=CUSTOM;BYDATE={','.join(formatted_dates)}"
        
        # First date becomes repeatFirstDate
        first_date = dates[0] if dates[0] <= min(dates) else min(dates)
        first_date_iso = f"{first_date}T05:00:00.000+0000"

        task["repeatFlag"] = erule
        task["repeatFirstDate"] = first_date_iso
        task["repeatFrom"] = "0"

        payload = {"add": [], "update": [task], "delete": []}
        client.call_api(BATCH_TASK, method="POST", data=payload)
        
        logger.info(f"Successfully set specific dates for task {task_id}")
        return {"success": True, "message": f"Task {task_id} set to repeat on {len(dates)} specific dates", "dates": dates}
    except Exception as e:
        logger.error(f"Failed to set specific dates: {e}")
        return {"error": str(e)}

# ==================== Data Fetch Tools (FRESH - NO CACHE) ====================


@mcp.tool()
def unofficial_get_all_data() -> dict[str, Any]:
    """
    Get all data from TickTick via the unofficial API.

    ALWAYS returns fresh data - no caching. Each call fetches from the API.

    Returns:
        Dict with tasks, projects, tags counts and data
    """
    logger.info("unofficial_get_all_data called")

    try:
        client = _get_api_client()
        data = _fetch_all_data(client)

        tasks = data.get("syncTaskBean", {}).get("update", [])
        projects = data.get("projectProfiles", [])
        tags = data.get("tags", [])

        logger.info(f"Retrieved {len(tasks)} tasks, {len(projects)} projects, {len(tags)} tags (fresh)")

        return {
            "success": True,
            "tasks": tasks,
            "projects": projects,
            "tags": tags,
            "task_count": len(tasks),
            "project_count": len(projects),
            "tag_count": len(tags)
        }
    except Exception as e:
        logger.error(f"Failed to get data: {e}")
        return {"error": str(e)}

@mcp.tool()
def unofficial_get_task(task_id: str) -> dict[str, Any]:
    """
    Get a TickTick task by ID via the unofficial API.
    
    Unlike ticktick_get_task, this doesn't require the project ID
    and returns full metadata including repeatFrom.

    Args:
        task_id: The task ID to retrieve

    Returns:
        Task data or error dict
    """
    logger.info(f"unofficial_get_task called for: {task_id}")

    try:
        client = _get_api_client()
        task = client.call_api(f"/api/v2/task/{task_id}")
        logger.info(f"Found task: {task_id}")
        return task

    except RuntimeError as e:
        if "task_not_found" in str(e):
            return {"error": f"Task not found: {task_id}"}
        logger.error(f"Failed to get task: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Failed to get task: {e}")
        return {"error": str(e)}
    
# @mcp.tool()
# def unofficial_get_by_id(obj_id: str) -> dict[str, Any]:
#     """
#     Get any TickTick object by its ID via the unofficial API.

#     ALWAYS returns fresh data - no caching.
#     Searches through tasks, then projects to find the object.

#     Args:
#         obj_id: The ID of the object to retrieve

#     Returns:
#         The object if found, or error dict
#     """
#     logger.info(f"unofficial_get_by_id called for: {obj_id}")

#     try:
#         client = _get_api_client()
#         data = _fetch_all_data(client)

#         # Try task first
#         tasks = data.get("syncTaskBean", {}).get("update", [])
#         for task in tasks:
#             if task.get("id") == obj_id:
#                 logger.info(f"Found task with ID: {obj_id}")
#                 return {"type": "task", "data": task}

#         # Try project
#         projects = data.get("projectProfiles", [])
#         for project in projects:
#             if project.get("id") == obj_id:
#                 logger.info(f"Found project with ID: {obj_id}")
#                 return {"type": "project", "data": project}

#         logger.info(f"No object found with ID: {obj_id}")
#         return {"error": f"No object found with ID: {obj_id}"}
#     except Exception as e:
#         logger.error(f"Failed to get object by ID: {e}")
#         return {"error": str(e)}


@mcp.tool()
def unofficial_get_all(
    obj_type: Literal["tasks", "projects", "tags"]
) -> dict[str, Any] | list[dict]:
    """
    Get all objects of a specific type via the unofficial API.

    ALWAYS returns fresh data - no caching.

    Args:
        obj_type: Type of objects to retrieve - "tasks", "projects", or "tags"

    Returns:
        List of objects or error dict
    """
    logger.info(f"unofficial_get_all called for type: {obj_type}")

    try:
        client = _get_api_client()
        data = _fetch_all_data(client)

        if obj_type == "tasks":
            result = data.get("syncTaskBean", {}).get("update", [])
        elif obj_type == "projects":
            result = data.get("projectProfiles", [])
        elif obj_type == "tags":
            result = data.get("tags", [])
        else:
            return {"error": f"Unknown object type: {obj_type}"}

        logger.info(f"Retrieved {len(result)} {obj_type} (fresh)")
        return result
    except Exception as e:
        logger.error(f"Failed to get all {obj_type}: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_get_tasks_from_project(
    project_id: str,
    include_completed: bool = False
) -> dict[str, Any] | list[dict]:
    """
    Get tasks from a specific project via the unofficial API.

    ALWAYS returns fresh data - no caching.

    Args:
        project_id: The project ID to get tasks from
        include_completed: If True, also return completed tasks (default: False)

    Returns:
        List of tasks or error dict
    """
    logger.info(f"unofficial_get_tasks_from_project called for project: {project_id}")

    try:
        client = _get_api_client()
        data = _fetch_all_data(client)
        all_tasks = data.get("syncTaskBean", {}).get("update", [])

        # Filter to this project, uncompleted (status=0)
        tasks = [t for t in all_tasks if t.get("projectId") == project_id and t.get("status") == 0]

        # Optionally include completed (status=2)
        if include_completed:
            completed = [t for t in all_tasks if t.get("projectId") == project_id and t.get("status") == 2]
            tasks.extend(completed)

        logger.info(f"Retrieved {len(tasks)} tasks from project {project_id} (fresh)")
        return tasks
    except Exception as e:
        logger.error(f"Failed to get tasks from project: {e}")
        return {"error": str(e)}


# ==================== Task CRUD Tools (Direct API) ====================


# @mcp.tool()
# def unofficial_create_task(
#     title: str,
#     project_id: str,
#     content: str | None = None,
#     start_date: str | None = None,
#     due_date: str | None = None,
#     priority: int = 0,
#     tags: list[str] | None = None
# ) -> dict[str, Any]:
#     """
#     Create a new task via the unofficial API.

#     Args:
#         title: Task title (required)
#         project_id: Project ID to create task in (required)
#         content: Task description/notes
#         start_date: Start date in ISO format (e.g., "2024-01-31T09:00:00")
#         due_date: Due date in ISO format
#         priority: Priority (0=None, 1=Low, 3=Medium, 5=High)
#         tags: List of tag names

#     Returns:
#         Created task or error
#     """
#     logger.info(f"unofficial_create_task called: title={title}, project={project_id}")

#     try:
#         client = _get_api_client()

#         task_id = uuid.uuid4().hex[:24]
#         now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

#         task = {
#             "id": task_id,
#             "projectId": project_id,
#             "title": title,
#             "priority": priority,
#             "status": 0,
#             "createdTime": now,
#             "modifiedTime": now,
#         }
#         if content:
#             task["content"] = content
#         if start_date:
#             task["startDate"] = start_date
#         if due_date:
#             task["dueDate"] = due_date
#         if tags:
#             task["tags"] = tags

#         payload = {"add": [task], "update": [], "delete": []}
#         result = client.call_api(BATCH_TASK, method="POST", data=payload)

#         # Extract etag from response
#         if isinstance(result, dict):
#             id_map = result.get("id2etag", {})
#             if task_id in id_map:
#                 task["etag"] = id_map[task_id]

#         logger.info(f"Successfully created task: {task_id}")
#         return {"success": True, "task": task}
#     except Exception as e:
#         logger.error(f"Failed to create task: {e}")
#         return {"error": str(e)}


# @mcp.tool()
# def unofficial_update_task(
#     task_id: str,
#     title: str | None = None,
#     content: str | None = None,
#     start_date: str | None = None,
#     due_date: str | None = None,
#     priority: int | None = None,
#     tags: list[str] | None = None
# ) -> dict[str, Any]:
#     """
#     Update an existing task via the unofficial API.

#     Args:
#         task_id: The task ID to update (required)
#         title: New task title
#         content: New task description/notes
#         start_date: New start date in ISO format
#         due_date: New due date in ISO format
#         priority: New priority (0=None, 1=Low, 3=Medium, 5=High)
#         tags: New list of tag names

#     Returns:
#         Updated task or error
#     """
#     logger.info(f"unofficial_update_task called for task: {task_id}")

#     try:
#         client = _get_api_client()

#         # Get the task first (fresh from API)
#         task = _get_task_by_id(client, task_id)
#         if not task:
#             return {"error": f"Task not found: {task_id}"}

#         # Update fields
#         if title is not None:
#             task["title"] = title
#         if content is not None:
#             task["content"] = content
#         if start_date is not None:
#             task["startDate"] = start_date
#         if due_date is not None:
#             task["dueDate"] = due_date
#         if priority is not None:
#             task["priority"] = priority
#         if tags is not None:
#             task["tags"] = tags

#         # Save updates
#         payload = {"add": [], "update": [task], "delete": []}
#         result = client.call_api(BATCH_TASK, method="POST", data=payload)

#         # Extract etag from response
#         if isinstance(result, dict):
#             id_map = result.get("id2etag", {})
#             if task_id in id_map:
#                 task["etag"] = id_map[task_id]

#         logger.info(f"Successfully updated task {task_id}")
#         return {"success": True, "task": task}
#     except Exception as e:
#         logger.error(f"Failed to update task: {e}")
#         return {"error": str(e)}

@mcp.tool()
def unofficial_update_task(
    task_id: str,
    title: str | None = None,
    content: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    priority: int | None = None,
    status: int | None = None,           # NEW: 0=incomplete, 2=complete
    tags: list[str] | None = None,
    repeat_flag: str | None = None,      # NEW: RRULE string
    repeat_from: str | None = None,      # NEW: "0"=due date, "1"=completion date
) -> dict[str, Any]:
    """
    Update an existing task via the unofficial API.
    Works for both completed and incomplete tasks.
    """
    logger.info(f"unofficial_update_task called for task: {task_id}")

    try:
        client = _get_api_client()

        # Direct fetch - works for completed AND incomplete tasks
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Update fields (only if provided)
        if title is not None:
            task["title"] = title
        if content is not None:
            task["content"] = content
        if start_date is not None:
            task["startDate"] = start_date
        if due_date is not None:
            task["dueDate"] = due_date
        if priority is not None:
            task["priority"] = priority
        if status is not None:
            task["status"] = status
            if status == 0:
                task["completedTime"] = None
        if tags is not None:
            task["tags"] = tags
        if repeat_flag is not None:
            task["repeatFlag"] = repeat_flag
        if repeat_from is not None:
            task["repeatFrom"] = repeat_from

        # Save via batch endpoint
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict) and task_id in result.get("id2etag", {}):
            task["etag"] = result["id2etag"][task_id]

        return {"success": True, "task": task}
    except Exception as e:
        logger.error(f"Failed to update task: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_create_task(
    title: str,
    project_id: str,
    content: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    priority: int = 0,
    tags: list[str] | None = None,
    is_all_day: bool = True,             # NEW: explicit control
    repeat_flag: str | None = None,      # NEW: RRULE string
    repeat_from: str | None = None,      # NEW: "0"=due date, "1"=completion date
    time_zone: str = "America/New_York",
) -> dict[str, Any]:
    """
    Create a new task via the unofficial API.
    Supports all fields including repeatFrom.
    """
    logger.info(f"unofficial_create_task: {title}")

    try:
        client = _get_api_client()

        task = {
            "title": title,
            "projectId": project_id,
            "priority": priority,
            "status": 0,
            "timeZone": time_zone,
            "isAllDay": is_all_day,
        }

        if content:
            task["content"] = content
        if start_date:
            task["startDate"] = start_date
        if due_date:
            task["dueDate"] = due_date
        if tags:
            task["tags"] = tags
        if repeat_flag:
            task["repeatFlag"] = repeat_flag
        if repeat_from:
            task["repeatFrom"] = repeat_from

        payload = {"add": [task], "update": [], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict):
            id2etag = result.get("id2etag", {})
            if id2etag:
                task_id = list(id2etag.keys())[0]
                task["id"] = task_id
                task["etag"] = id2etag[task_id]

        return {"success": True, "task": task}
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        return {"error": str(e)}

@mcp.tool()
def unofficial_delete_task(task_id: str) -> dict[str, Any]:
    """
    Delete a task via the unofficial API.

    Args:
        task_id: The task ID to delete

    Returns:
        Success message or error
    """
    logger.info(f"unofficial_delete_task called for task: {task_id}")

    try:
        client = _get_api_client()

        # Get the task first to find its project (fresh from API)
        task = _get_task_by_id(client, task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}

        project_id = task.get("projectId")
        if not project_id:
            return {"error": f"Task has no projectId: {task_id}"}

        payload = {"add": [], "update": [], "delete": [{"taskId": task_id, "projectId": project_id}]}
        client.call_api(BATCH_TASK, method="POST", data=payload)
        logger.info(f"Successfully deleted task {task_id}")
        return {"success": True, "message": f"Task {task_id} deleted"}
    except Exception as e:
        logger.error(f"Failed to delete task: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_move_task(task_id: str, to_project_id: str) -> dict[str, Any]:
    """
    Move a task to a different project via the unofficial API.

    Args:
        task_id: The task ID to move
        to_project_id: The destination project ID

    Returns:
        Updated task or error
    """
    logger.info(f"unofficial_move_task called: task={task_id}, to_project={to_project_id}")

    try:
        client = _get_api_client()

        # Get the task first to find its current project
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        from_project_id = task.get("projectId")

        # Use the dedicated move endpoint (discovered from HAR analysis)
        move_payload = [{
            "taskId": task_id,
            "fromProjectId": from_project_id,
            "toProjectId": to_project_id
        }]
        result = client.call_api(BATCH_TASK_PROJECT, method="POST", data=move_payload)

        # Verify the move worked
        if isinstance(result, dict) and result.get("id2error", {}).get(task_id):
            return {"error": f"Move failed: {result['id2error'][task_id]}"}

        # Fetch updated task to return
        updated_task = client.call_api(f"/api/v2/task/{task_id}")

        logger.info(f"Successfully moved task {task_id} from {from_project_id} to {to_project_id}")
        return {
            "success": True,
            "task": updated_task,
            "moved_from": from_project_id,
            "moved_to": to_project_id
        }
    except Exception as e:
        logger.error(f"Failed to move task: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_make_subtask(child_task_id: str, parent_task_id: str) -> dict[str, Any]:
    """
    Make one task a subtask of another via the unofficial API.

    Both tasks must be in the same project.

    Args:
        child_task_id: The task ID to become a subtask
        parent_task_id: The task ID that will become the parent

    Returns:
        Success message or error
    """
    logger.info(f"unofficial_make_subtask called: child={child_task_id}, parent={parent_task_id}")

    try:
        client = _get_api_client()

        # Get both tasks fresh from API
        child = _get_task_by_id(client, child_task_id)
        if not child:
            return {"error": f"Child task not found: {child_task_id}"}

        parent = _get_task_by_id(client, parent_task_id)
        if not parent:
            return {"error": f"Parent task not found: {parent_task_id}"}

        if child.get("projectId") != parent.get("projectId"):
            return {"error": "Tasks must be in the same project"}

        # Set parent relationship and save
        child["parentId"] = parent_task_id
        payload = {"add": [], "update": [child], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        # Extract etag from response
        if isinstance(result, dict):
            id_map = result.get("id2etag", {})
            if child_task_id in id_map:
                child["etag"] = id_map[child_task_id]

        logger.info(f"Successfully made task {child_task_id} a subtask of {parent_task_id}")
        return {
            "success": True,
            "message": "Task is now a subtask",
            "task": child
        }
    except Exception as e:
        logger.error(f"Failed to make subtask: {e}")
        return {"error": str(e)}


# ==================== Experimental API Tool ====================


@mcp.tool()
def unofficial_experimental_api_call(
    endpoint: str,
    method: str = "GET",
    data: dict | None = None,  # Changed from str | None
    params: dict | None = None  # Changed from str | None
) -> dict[str, Any] | list[dict]:
    """
    Make a raw API call to TickTick's unofficial API for experimentation.

    Args:
        endpoint: API path (e.g., "/api/v2/batch/task")
        method: HTTP method - GET, POST, PUT, or DELETE
        data: JSON object for the request body (POST/PUT)
        params: JSON object for query parameters

    Returns:
        Raw API response or error dict
    """
    logger.info(f"unofficial_experimental_api_call: {method} {endpoint}")

    try:
        client = _get_api_client()
        
        # No need to parse - already dicts
        result = client.call_api(endpoint, method=method, data=data, params=params)
        logger.info(f"unofficial_experimental_api_call succeeded: {method} {endpoint}")
        return result
    except Exception as e:
        logger.error(f"unofficial_experimental_api_call failed: {e}")
        return {"error": str(e)}
