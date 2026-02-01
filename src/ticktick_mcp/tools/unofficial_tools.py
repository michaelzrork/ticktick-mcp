"""
MCP Tools for unofficial TickTick API features.

These tools use the unofficial v2 API for features not available in OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs

Requires TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables.
"""

import logging
from typing import Any

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp import config
from ticktick_mcp import unofficial_client

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
