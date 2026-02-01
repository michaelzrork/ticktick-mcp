"""
MCP Tools for unofficial TickTick API features.

These tools use the unofficial v2 API for features not available in OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs
- Legacy get tools using ticktick-py's state sync

Requires TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables.
"""

import logging
from typing import Any, Literal

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp import config
from ticktick_mcp import unofficial_client
from ticktick_mcp.unofficial_client import TickTickClientSingleton

logger = logging.getLogger(__name__)


@mcp.tool()
def ticktick_get_task_activity(task_id: str) -> dict[str, Any] | list[dict]:
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
    logger.info(f"ticktick_get_task_activity called for task: {task_id}")

    client = config.get_unofficial_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        activities = unofficial_client.get_task_activity(task_id)
        logger.info(f"Got {len(activities)} activity entries")
        return activities
    except Exception as e:
        logger.error(f"Failed to get task activity: {e}")
        return {"error": str(e)}


@mcp.tool()
def ticktick_pin_task(task_id: str) -> dict[str, Any]:
    """
    Pin a task to the top of the list.

    Pinned tasks appear at the top of the Today view and project lists.

    Args:
        task_id: The task ID to pin

    Returns:
        Success message or error
    """
    logger.info(f"ticktick_pin_task called for task: {task_id}")

    client = config.get_unofficial_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        unofficial_client.pin_task(task_id)
        logger.info(f"Successfully pinned task {task_id}")
        return {"success": True, "message": f"Task {task_id} pinned"}
    except Exception as e:
        logger.error(f"Failed to pin task: {e}")
        return {"error": str(e)}


@mcp.tool()
def ticktick_unpin_task(task_id: str) -> dict[str, Any]:
    """
    Unpin a task (remove from pinned list).

    Args:
        task_id: The task ID to unpin

    Returns:
        Success message or error
    """
    logger.info(f"ticktick_unpin_task called for task: {task_id}")

    client = config.get_unofficial_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        unofficial_client.unpin_task(task_id)
        logger.info(f"Successfully unpinned task {task_id}")
        return {"success": True, "message": f"Task {task_id} unpinned"}
    except Exception as e:
        logger.error(f"Failed to unpin task: {e}")
        return {"error": str(e)}


@mcp.tool()
def ticktick_set_repeat_from(
    task_id: str,
    project_id: str,
    repeat_from: str
) -> dict[str, Any]:
    """
    Set whether a repeating task repeats from due date or completion date.

    This is only relevant for recurring tasks with a repeatFlag set.

    Args:
        task_id: The task ID
        project_id: The project ID containing the task
        repeat_from: "due_date" or "completion_date"
            - "due_date": Next occurrence calculated from the original due date
            - "completion_date": Next occurrence calculated from when task was completed

    Returns:
        Success message or error
    """
    logger.info(f"ticktick_set_repeat_from called: task={task_id}, repeat_from={repeat_from}")

    client = config.get_unofficial_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    # Map user-friendly values to API values
    repeat_from_map = {
        "due_date": "0",
        "completion_date": "1"
    }

    api_value = repeat_from_map.get(repeat_from.lower().replace(" ", "_"))
    if not api_value:
        return {"error": f"repeat_from must be 'due_date' or 'completion_date', got '{repeat_from}'"}

    try:
        unofficial_client.set_repeat_from(task_id, project_id, api_value)
        logger.info(f"Successfully set repeat_from for task {task_id}")
        return {"success": True, "message": f"Task {task_id} set to repeat from {repeat_from}"}
    except Exception as e:
        logger.error(f"Failed to set repeat_from: {e}")
        return {"error": str(e)}


# ==================== Legacy Get Tools ====================
# These tools use ticktick-py's state sync for retrieving objects


@mcp.tool()
def legacy_ticktick_get_by_id(obj_id: str) -> dict[str, Any]:
    """
    Get any TickTick object by its ID using the legacy ticktick-py client.

    This searches through all synced state (tasks, projects, tags) to find
    the object with the matching ID.

    Args:
        obj_id: The ID of the object to retrieve

    Returns:
        The object if found, or error dict
    """
    logger.info(f"legacy_ticktick_get_by_id called for: {obj_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        result = client.get_by_id(obj_id)
        if result:
            logger.info(f"Found object with ID: {obj_id}")
            return result
        else:
            logger.info(f"No object found with ID: {obj_id}")
            return {"error": f"No object found with ID: {obj_id}"}
    except Exception as e:
        logger.error(f"Failed to get object by ID: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_get_all(
    obj_type: Literal["tasks", "projects", "tags"]
) -> dict[str, Any] | list[dict]:
    """
    Get all objects of a specific type using the legacy ticktick-py client.

    This returns all synced objects from ticktick-py's state.

    Args:
        obj_type: Type of objects to retrieve - "tasks", "projects", or "tags"

    Returns:
        List of objects or error dict
    """
    logger.info(f"legacy_ticktick_get_all called for type: {obj_type}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        if obj_type == "tasks":
            result = list(client.state["tasks"].values())
        elif obj_type == "projects":
            result = list(client.state["projects"].values())
        elif obj_type == "tags":
            result = list(client.state["tags"].values())
        else:
            return {"error": f"Unknown object type: {obj_type}"}

        logger.info(f"Retrieved {len(result)} {obj_type}")
        return result
    except Exception as e:
        logger.error(f"Failed to get all {obj_type}: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_get_tasks_from_project(project_id: str) -> dict[str, Any] | list[dict]:
    """
    Get all uncompleted tasks from a specific project using the legacy ticktick-py client.

    Args:
        project_id: The project ID to get tasks from

    Returns:
        List of tasks or error dict
    """
    logger.info(f"legacy_ticktick_get_tasks_from_project called for project: {project_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        tasks = client.get_by_fields(
            search="tasks",
            projectId=project_id,
            status=0  # 0 = uncompleted
        )
        logger.info(f"Retrieved {len(tasks)} tasks from project {project_id}")
        return tasks
    except Exception as e:
        logger.error(f"Failed to get tasks from project: {e}")
        return {"error": str(e)}
