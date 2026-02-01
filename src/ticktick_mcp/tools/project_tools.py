"""
MCP Tools for TickTick Project operations.

Uses the official TickTick OpenAPI v1 endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp.config import get_ticktick_client
from ticktick_mcp.ticktick_client import TickTickAPIError

logger = logging.getLogger(__name__)


def _format_project(project: dict) -> dict[str, Any]:
    """Format a project object for display."""
    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "color": project.get("color"),
        "viewMode": project.get("viewMode"),
        "kind": project.get("kind"),
        "sortOrder": project.get("sortOrder"),
    }


@mcp.tool()
async def ticktick_list_projects() -> dict[str, Any]:
    """
    List all TickTick projects.

    Note: The Inbox is not included in this list. Use ticktick_get_inbox_tasks
    to access Inbox tasks.

    Returns:
        Dictionary with 'projects' array containing all projects
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        projects = await client.get_projects()
        return {
            "projects": [_format_project(p) for p in projects],
            "count": len(projects)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to list projects: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_get_project(project_id: str) -> dict[str, Any]:
    """
    Get a specific project by ID.

    Args:
        project_id: The project ID

    Returns:
        Project details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        project = await client.get_project(project_id)
        return _format_project(project)
    except TickTickAPIError as e:
        logger.error(f"Failed to get project {project_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_get_project_with_tasks(project_id: str) -> dict[str, Any]:
    """
    Get a project with all its tasks.

    This is the recommended way to fetch all tasks from a project.
    Use "inbox{userId}" as project_id to get Inbox tasks (requires TICKTICK_USER_ID).

    Args:
        project_id: The project ID (or "inbox{userId}" for Inbox)

    Returns:
        Project details with 'tasks' array
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        data = await client.get_project_with_data(project_id)
        return {
            "project": _format_project(data.get("project", data)),
            "tasks": data.get("tasks", []),
            "task_count": len(data.get("tasks", []))
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to get project data {project_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_get_inbox_tasks() -> dict[str, Any]:
    """
    Get all tasks from the Inbox.

    Requires TICKTICK_USER_ID to be set in environment.

    Returns:
        Inbox data with 'tasks' array
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    if not client.inbox_id:
        return {
            "error": "TICKTICK_USER_ID not set. Cannot access Inbox without user ID."
        }

    try:
        data = await client.get_inbox_data()
        return {
            "inbox_id": client.inbox_id,
            "tasks": data.get("tasks", []),
            "task_count": len(data.get("tasks", []))
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to get Inbox tasks: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_create_project(
    name: str,
    color: str | None = None,
    view_mode: str | None = None,
    kind: str | None = None
) -> dict[str, Any]:
    """
    Create a new project.

    Args:
        name: Project name (required)
        color: Color hex code (e.g., "#F18181")
        view_mode: View mode - "list", "kanban", or "timeline"
        kind: Project kind - "TASK" or "NOTE"

    Returns:
        Created project details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        project = await client.create_project(
            name=name,
            color=color,
            view_mode=view_mode,
            kind=kind
        )
        return {
            "success": True,
            "project": _format_project(project)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to create project: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_update_project(
    project_id: str,
    name: str | None = None,
    color: str | None = None,
    view_mode: str | None = None
) -> dict[str, Any]:
    """
    Update an existing project.

    Args:
        project_id: Project ID to update
        name: New project name
        color: New color hex code
        view_mode: New view mode - "list", "kanban", or "timeline"

    Returns:
        Updated project details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        project = await client.update_project(
            project_id=project_id,
            name=name,
            color=color,
            view_mode=view_mode
        )
        return {
            "success": True,
            "project": _format_project(project)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to update project {project_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_delete_project(project_id: str) -> dict[str, Any]:
    """
    Delete a project.

    WARNING: This permanently deletes the project and all its tasks.

    Args:
        project_id: Project ID to delete

    Returns:
        Success status
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        await client.delete_project(project_id)
        return {
            "success": True,
            "message": f"Project {project_id} deleted successfully"
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to delete project {project_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}
