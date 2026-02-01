"""
MCP Tools for unofficial TickTick API features.

These tools use the unofficial v2 API for features not available in OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs

Requires TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables.
"""

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp import config


@mcp.tool()
async def ticktick_pin_task(task_id: str) -> str:
    """
    Pin a task to the top of the list.

    Pinned tasks appear at the top of the Today view and project lists.

    Args:
        task_id: The task ID to pin

    Returns:
        Success message or error
    """
    client = await config.get_unofficial_client()
    if not client:
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    try:
        await client.pin_task(task_id)
        return f"Successfully pinned task {task_id}"
    except Exception as e:
        return f"Error pinning task: {str(e)}"


@mcp.tool()
async def ticktick_unpin_task(task_id: str) -> str:
    """
    Unpin a task (remove from pinned list).

    Args:
        task_id: The task ID to unpin

    Returns:
        Success message or error
    """
    client = await config.get_unofficial_client()
    if not client:
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    try:
        await client.unpin_task(task_id)
        return f"Successfully unpinned task {task_id}"
    except Exception as e:
        return f"Error unpinning task: {str(e)}"


@mcp.tool()
async def ticktick_set_repeat_from(
    task_id: str,
    project_id: str,
    repeat_from: str
) -> str:
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
    client = await config.get_unofficial_client()
    if not client:
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    # Map user-friendly values to API values
    repeat_from_map = {
        "due_date": "0",
        "completion_date": "1"
    }

    api_value = repeat_from_map.get(repeat_from.lower().replace(" ", "_"))
    if not api_value:
        return f"Error: repeat_from must be 'due_date' or 'completion_date', got '{repeat_from}'"

    try:
        await client.set_repeat_from(task_id, project_id, api_value)
        return f"Successfully set task {task_id} to repeat from {repeat_from}"
    except Exception as e:
        return f"Error setting repeat_from: {str(e)}"


@mcp.tool()
async def ticktick_get_task_activity(
    project_id: str,
    task_id: str,
    limit: int = 50
) -> list[dict] | str:
    """
    Get the activity log for a specific task.

    Shows history of changes, completions, and modifications.

    Args:
        project_id: The project ID containing the task
        task_id: The task ID
        limit: Maximum number of activities to return (default: 50)

    Returns:
        List of activity entries or error message
    """
    client = await config.get_unofficial_client()
    if not client:
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    try:
        activities = await client.get_task_activity(project_id, task_id, limit)
        if not activities:
            return []
        return activities
    except Exception as e:
        return f"Error getting task activity: {str(e)}"


@mcp.tool()
async def ticktick_get_project_activity(
    project_id: str,
    limit: int = 50
) -> list[dict] | str:
    """
    Get the activity log for a project.

    Shows history of task changes, completions, and modifications in the project.

    Args:
        project_id: The project ID
        limit: Maximum number of activities to return (default: 50)

    Returns:
        List of activity entries or error message
    """
    client = await config.get_unofficial_client()
    if not client:
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    try:
        activities = await client.get_project_activity(project_id, limit)
        if not activities:
            return []
        return activities
    except Exception as e:
        return f"Error getting project activity: {str(e)}"
