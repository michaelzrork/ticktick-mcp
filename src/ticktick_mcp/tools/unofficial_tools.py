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
        if obj_type not in ("tasks", "projects", "tags"):
            return {"error": f"Unknown object type: {obj_type}"}

        state_data = client.state.get(obj_type, [])

        # Handle both dict and list formats from ticktick-py state
        if isinstance(state_data, dict):
            result = list(state_data.values())
        elif isinstance(state_data, list):
            result = state_data
        else:
            result = []

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


@mcp.tool()
def legacy_ticktick_complete_task(task_id: str) -> dict[str, Any]:
    """
    Mark a task as complete using the legacy ticktick-py client.

    Args:
        task_id: The task ID to complete

    Returns:
        Success message or error
    """
    logger.info(f"legacy_ticktick_complete_task called for task: {task_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        # Get the task first
        task = client.get_by_id(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Complete the task using ticktick-py's method
        client.complete(task)
        logger.info(f"Successfully completed task {task_id}")
        return {"success": True, "message": f"Task {task_id} completed"}
    except Exception as e:
        logger.error(f"Failed to complete task: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_move_task(task_id: str, to_project_id: str) -> dict[str, Any]:
    """
    Move a task to a different project using the legacy ticktick-py client.

    Args:
        task_id: The task ID to move
        to_project_id: The destination project ID

    Returns:
        Updated task or error
    """
    logger.info(f"legacy_ticktick_move_task called: task={task_id}, to_project={to_project_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        # Get the task first
        task = client.get_by_id(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Get the destination project
        project = client.get_by_id(to_project_id)
        if not project:
            return {"error": f"Project not found: {to_project_id}"}

        # Move the task using ticktick-py's method
        result = client.move(task, project)
        logger.info(f"Successfully moved task {task_id} to project {to_project_id}")
        return {"success": True, "task": result, "moved_to": to_project_id}
    except Exception as e:
        logger.error(f"Failed to move task: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_make_subtask(child_task_id: str, parent_task_id: str) -> dict[str, Any]:
    """
    Make one task a subtask of another task using the legacy ticktick-py client.

    Both tasks must be in the same project.

    Args:
        child_task_id: The task ID to become a subtask
        parent_task_id: The task ID that will become the parent

    Returns:
        Success message or error
    """
    logger.info(f"legacy_ticktick_make_subtask called: child={child_task_id}, parent={parent_task_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        # Get both tasks
        child_task = client.get_by_id(child_task_id)
        if not child_task:
            return {"error": f"Child task not found: {child_task_id}"}

        parent_task = client.get_by_id(parent_task_id)
        if not parent_task:
            return {"error": f"Parent task not found: {parent_task_id}"}

        # Check same project
        if child_task.get("projectId") != parent_task.get("projectId"):
            return {
                "error": "Tasks must be in the same project",
                "child_project": child_task.get("projectId"),
                "parent_project": parent_task.get("projectId")
            }

        # Make subtask using ticktick-py's method
        result = client.make_subtask(child_task, parent_task)
        logger.info(f"Successfully made task {child_task_id} a subtask of {parent_task_id}")
        return {
            "success": True,
            "message": f"Task '{child_task.get('title')}' is now a subtask of '{parent_task.get('title')}'",
            "result": result
        }
    except Exception as e:
        logger.error(f"Failed to make subtask: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_delete_task(task_id: str) -> dict[str, Any]:
    """
    Delete a task using the legacy ticktick-py client.

    Args:
        task_id: The task ID to delete

    Returns:
        Success message or error
    """
    logger.info(f"legacy_ticktick_delete_task called for task: {task_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        # Get the task first
        task = client.get_by_id(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Delete the task using ticktick-py's method
        client.delete(task)
        logger.info(f"Successfully deleted task {task_id}")
        return {"success": True, "message": f"Task {task_id} deleted"}
    except Exception as e:
        logger.error(f"Failed to delete task: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_create_task(
    title: str,
    project_id: str,
    content: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    priority: int = 0,
    tags: list[str] | None = None
) -> dict[str, Any]:
    """
    Create a new task using the legacy ticktick-py client.

    Args:
        title: Task title (required)
        project_id: Project ID to create task in (required)
        content: Task description/notes
        start_date: Start date in ISO format (e.g., "2024-01-31T09:00:00")
        due_date: Due date in ISO format
        priority: Priority (0=None, 1=Low, 3=Medium, 5=High)
        tags: List of tag names

    Returns:
        Created task or error
    """
    logger.info(f"legacy_ticktick_create_task called: title={title}, project={project_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        # Build task dict for ticktick-py
        task_dict = {
            "title": title,
            "projectId": project_id,
            "priority": priority,
        }

        if content:
            task_dict["content"] = content
        if start_date:
            task_dict["startDate"] = start_date
        if due_date:
            task_dict["dueDate"] = due_date
        if tags:
            task_dict["tags"] = tags

        # Create using ticktick-py's builder or direct method
        result = client.task.create(title, projectId=project_id)

        # Update with additional fields if needed
        if content or start_date or due_date or priority or tags:
            result.update(task_dict)
            result = client.update(result)

        logger.info(f"Successfully created task: {result.get('id')}")
        return {"success": True, "task": result}
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        return {"error": str(e)}


@mcp.tool()
def legacy_ticktick_update_task(
    task_id: str,
    title: str | None = None,
    content: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None
) -> dict[str, Any]:
    """
    Update an existing task using the legacy ticktick-py client.

    Args:
        task_id: The task ID to update (required)
        title: New task title
        content: New task description/notes
        start_date: New start date in ISO format
        due_date: New due date in ISO format
        priority: New priority (0=None, 1=Low, 3=Medium, 5=High)
        tags: New list of tag names

    Returns:
        Updated task or error
    """
    logger.info(f"legacy_ticktick_update_task called for task: {task_id}")

    client = TickTickClientSingleton.get_client()
    if not client:
        logger.warning("Unofficial client not available")
        return {"error": "Unofficial API not configured. Check TICKTICK_USERNAME and TICKTICK_PASSWORD."}

    try:
        # Get the task first
        task = client.get_by_id(task_id)
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Update fields
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
        if tags is not None:
            task["tags"] = tags

        # Save updates
        result = client.update(task)
        logger.info(f"Successfully updated task {task_id}")
        return {"success": True, "task": result}
    except Exception as e:
        logger.error(f"Failed to update task: {e}")
        return {"error": str(e)}
