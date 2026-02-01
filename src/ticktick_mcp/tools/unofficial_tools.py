"""
MCP Tools for unofficial TickTick API features.

These tools use the unofficial v2 API for features not available in OpenAPI v1:
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
