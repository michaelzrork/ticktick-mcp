"""
MCP Tools for unofficial TickTick API features.

These tools use the unofficial v2 API for features not available in OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs

Requires TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables.
"""

import logging
import json
from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp import config

logger = logging.getLogger(__name__)


# =====================================
# LEGACY TOOL - Uses old working approach for comparison
# =====================================

@mcp.tool()
async def legacy_ticktick_get_task_activity(task_id: str) -> str:
    """
    [LEGACY/DEBUG] Get task activity using the OLD working approach.

    This uses the ticktick-py client's http session directly,
    bypassing the new unofficial_client.py abstraction.
    Used to compare against the new implementation for debugging.

    Args:
        task_id: The task ID

    Returns:
        JSON string with activity entries or error message
    """
    logger.info("=================================================")
    logger.info("=== LEGACY TOOL: legacy_ticktick_get_task_activity ===")
    logger.info("=================================================")
    logger.info(f"  task_id: {task_id}")

    # Try to get the unofficial client to access its internal ticktick-py client
    logger.info("  Calling config.get_unofficial_client()...")
    client = await config.get_unofficial_client()

    if not client:
        logger.warning("  No unofficial client - not configured")
        return json.dumps({"error": "Unofficial API not configured"})

    logger.info(f"  Got client: {client}")
    logger.info(f"  Client is_authenticated: {client.is_authenticated}")
    logger.info(f"  Client._ticktick_client: {client._ticktick_client}")

    if not client._ticktick_client:
        logger.error("  _ticktick_client is None - not authenticated")
        return json.dumps({"error": "Not authenticated - _ticktick_client is None"})

    # Use the OLD approach - direct http call via ticktick-py's session
    try:
        ticktick_py_client = client._ticktick_client
        logger.info(f"  ticktick_py_client type: {type(ticktick_py_client).__name__}")
        logger.info(f"  ticktick_py_client has 'http': {hasattr(ticktick_py_client, 'http')}")

        # The old code used client.http.get()
        if hasattr(ticktick_py_client, 'http'):
            logger.info(f"  ticktick_py_client.http: {ticktick_py_client.http}")
            logger.info(f"  ticktick_py_client.http type: {type(ticktick_py_client.http).__name__}")

            base_url = "https://api.ticktick.com/api/v1"
            activity_url = f"{base_url}/task/activity/{task_id}"
            logger.info(f"  Making request to: {activity_url}")

            response = ticktick_py_client.http.get(activity_url)
            logger.info(f"  Response status_code: {response.status_code}")
            logger.info(f"  Response headers: {dict(response.headers)}")

            if response.status_code == 200:
                activity_log = response.json()
                logger.info(f"  SUCCESS! Got {len(activity_log)} activity entries")
                return json.dumps(activity_log, indent=2)
            else:
                logger.error(f"  FAILED: HTTP {response.status_code}")
                logger.error(f"  Response text: {response.text[:500]}")
                return json.dumps({"error": f"HTTP {response.status_code}: {response.text}"})
        else:
            logger.warning("  ticktick_py_client has no 'http' attribute")

            # Try http_get method (used by new code)
            logger.info("  Trying http_get method instead...")
            if hasattr(ticktick_py_client, 'http_get'):
                base_url = "https://api.ticktick.com/api/v1"
                activity_url = f"{base_url}/task/activity/{task_id}"
                logger.info(f"  Making request to: {activity_url}")

                result = ticktick_py_client.http_get(activity_url)
                logger.info(f"  http_get returned: {type(result).__name__}")
                logger.info(f"  Result: {str(result)[:500]}")
                return json.dumps(result, indent=2) if result else json.dumps([])
            else:
                logger.error("  No http or http_get method available!")
                return json.dumps({"error": "No http method available on ticktick-py client"})

    except Exception as e:
        logger.error(f"  EXCEPTION: {e}")
        logger.exception("  Full traceback:")
        return json.dumps({"error": f"Exception: {str(e)}"})


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
    logger.info("========================================")
    logger.info("=== TOOL: ticktick_pin_task called ===")
    logger.info("========================================")
    logger.info(f"  task_id: {task_id}")

    logger.info("  Calling config.get_unofficial_client()...")
    client = await config.get_unofficial_client()
    logger.info(f"  Got client: {client}")
    logger.info(f"  Client type: {type(client).__name__ if client else 'None'}")

    if not client:
        logger.warning("  No client returned - unofficial API not configured")
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    logger.info(f"  Client is_authenticated: {client.is_authenticated}")

    try:
        logger.info("  Calling client.pin_task()...")
        await client.pin_task(task_id)
        logger.info("  pin_task completed successfully!")
        return f"Successfully pinned task {task_id}"
    except Exception as e:
        logger.error(f"  pin_task FAILED: {e}")
        logger.exception("  Full traceback:")
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
    logger.info("========================================")
    logger.info("=== TOOL: ticktick_unpin_task called ===")
    logger.info("========================================")
    logger.info(f"  task_id: {task_id}")

    logger.info("  Calling config.get_unofficial_client()...")
    client = await config.get_unofficial_client()
    logger.info(f"  Got client: {client}")

    if not client:
        logger.warning("  No client returned - unofficial API not configured")
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    logger.info(f"  Client is_authenticated: {client.is_authenticated}")

    try:
        logger.info("  Calling client.unpin_task()...")
        await client.unpin_task(task_id)
        logger.info("  unpin_task completed successfully!")
        return f"Successfully unpinned task {task_id}"
    except Exception as e:
        logger.error(f"  unpin_task FAILED: {e}")
        logger.exception("  Full traceback:")
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
    logger.info("==============================================")
    logger.info("=== TOOL: ticktick_set_repeat_from called ===")
    logger.info("==============================================")
    logger.info(f"  task_id: {task_id}")
    logger.info(f"  project_id: {project_id}")
    logger.info(f"  repeat_from: {repeat_from}")

    logger.info("  Calling config.get_unofficial_client()...")
    client = await config.get_unofficial_client()
    logger.info(f"  Got client: {client}")

    if not client:
        logger.warning("  No client returned - unofficial API not configured")
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    logger.info(f"  Client is_authenticated: {client.is_authenticated}")

    # Map user-friendly values to API values
    repeat_from_map = {
        "due_date": "0",
        "completion_date": "1"
    }

    api_value = repeat_from_map.get(repeat_from.lower().replace(" ", "_"))
    logger.info(f"  Mapped repeat_from '{repeat_from}' -> api_value: {api_value}")

    if not api_value:
        logger.error(f"  Invalid repeat_from value: {repeat_from}")
        return f"Error: repeat_from must be 'due_date' or 'completion_date', got '{repeat_from}'"

    try:
        logger.info("  Calling client.set_repeat_from()...")
        await client.set_repeat_from(task_id, project_id, api_value)
        logger.info("  set_repeat_from completed successfully!")
        return f"Successfully set task {task_id} to repeat from {repeat_from}"
    except Exception as e:
        logger.error(f"  set_repeat_from FAILED: {e}")
        logger.exception("  Full traceback:")
        return f"Error setting repeat_from: {str(e)}"


@mcp.tool()
async def ticktick_get_task_activity(
    task_id: str
) -> list[dict] | str:
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
        List of activity entries or error message
    """
    logger.info("================================================")
    logger.info("=== TOOL: ticktick_get_task_activity called ===")
    logger.info("================================================")
    logger.info(f"  task_id: {task_id}")

    logger.info("  Calling config.get_unofficial_client()...")
    client = await config.get_unofficial_client()
    logger.info(f"  Got client: {client}")

    if not client:
        logger.warning("  No client returned - unofficial API not configured")
        return "Error: Unofficial API not configured. Set TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables."

    logger.info(f"  Client is_authenticated: {client.is_authenticated}")

    try:
        logger.info("  Calling client.get_task_activity()...")
        activities = await client.get_task_activity(task_id)
        logger.info(f"  get_task_activity returned: {len(activities) if activities else 0} entries")
        if not activities:
            logger.info("  Returning empty list")
            return []
        logger.info("  Returning activities list")
        return activities
    except Exception as e:
        logger.error(f"  get_task_activity FAILED: {e}")
        logger.exception("  Full traceback:")
        return f"Error getting task activity: {str(e)}"
