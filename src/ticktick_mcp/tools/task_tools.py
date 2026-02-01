"""
MCP Tools for TickTick Task operations.

Uses the official TickTick OpenAPI v1 endpoints.

Date format: "yyyy-MM-dd'T'HH:mm:ssZ" (e.g., "2019-11-13T03:00:00+0000")
Priority values: 0=None, 1=Low, 3=Medium, 5=High
Reminder format: RFC 5545 TRIGGER (e.g., "TRIGGER:PT0S", "TRIGGER:-PT30M")
Recurrence format: RFC 5545 RRULE (e.g., "RRULE:FREQ=DAILY;INTERVAL=1")
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp.config import get_ticktick_client
from ticktick_mcp.ticktick_client import TickTickAPIError

logger = logging.getLogger(__name__)


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string to datetime for comparison."""
    if not date_str:
        return None
    try:
        # Handle TickTick format: "2024-07-26T10:00:00+0000" or "2024-07-26T10:00:00.000+0000"
        clean = date_str.replace(".000", "")
        # Handle timezone offset format (+0000 vs +00:00)
        if len(clean) > 5 and clean[-5] in "+-" and ":" not in clean[-5:]:
            clean = clean[:-2] + ":" + clean[-2:]
        return datetime.fromisoformat(clean)
    except (ValueError, TypeError):
        try:
            # Try date-only format
            return datetime.fromisoformat(date_str[:10])
        except (ValueError, TypeError):
            return None


def _matches_filter(task: dict, filters: dict) -> bool:
    """Check if a task matches the given filter criteria."""
    # Status filter (0=uncompleted, 2=completed)
    status = filters.get("status", "uncompleted")
    task_status = task.get("status", 0)
    if status == "uncompleted" and task_status != 0:
        return False
    if status == "completed" and task_status != 2:
        return False

    # Project filter
    project_id = filters.get("project_id")
    if project_id and task.get("projectId") != project_id:
        return False

    # Tag filter
    tag_label = filters.get("tag_label")
    if tag_label:
        task_tags = task.get("tags") or []
        if tag_label not in task_tags:
            return False

    # Priority filter
    priority = filters.get("priority")
    if priority is not None and task.get("priority") != priority:
        return False

    # Due date range filter (for uncompleted tasks)
    if status == "uncompleted":
        due_start = filters.get("due_start_date")
        due_end = filters.get("due_end_date")
        if due_start or due_end:
            task_due = _parse_date(task.get("dueDate"))
            if not task_due:
                return False  # No due date but filter requires one
            if due_start:
                filter_start = _parse_date(due_start)
                if filter_start and task_due.date() < filter_start.date():
                    return False
            if due_end:
                filter_end = _parse_date(due_end)
                if filter_end and task_due.date() > filter_end.date():
                    return False

    # Completion date range filter (for completed tasks)
    if status == "completed":
        comp_start = filters.get("completion_start_date")
        comp_end = filters.get("completion_end_date")
        if comp_start or comp_end:
            task_completed = _parse_date(task.get("completedTime"))
            if not task_completed:
                return False
            if comp_start:
                filter_start = _parse_date(comp_start)
                if filter_start and task_completed.date() < filter_start.date():
                    return False
            if comp_end:
                filter_end = _parse_date(comp_end)
                if filter_end and task_completed.date() > filter_end.date():
                    return False

    return True


def _format_task(task: dict) -> dict[str, Any]:
    """Format a task object for display."""
    return {
        "id": task.get("id"),
        "projectId": task.get("projectId"),
        "title": task.get("title"),
        "content": task.get("content"),
        "desc": task.get("desc"),
        "isAllDay": task.get("isAllDay"),
        "startDate": task.get("startDate"),
        "dueDate": task.get("dueDate"),
        "timeZone": task.get("timeZone"),
        "reminders": task.get("reminders"),
        "repeatFlag": task.get("repeatFlag"),
        "priority": task.get("priority"),
        "status": task.get("status"),
        "completedTime": task.get("completedTime"),
        "sortOrder": task.get("sortOrder"),
        "items": task.get("items"),  # Subtasks
        "tags": task.get("tags"),
        "kind": task.get("kind"),
    }


@mcp.tool()
async def ticktick_get_task(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Get a specific task by ID.

    Args:
        project_id: The project ID containing the task
        task_id: The task ID

    Returns:
        Task details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        task = await client.get_task(project_id, task_id)
        return _format_task(task)
    except TickTickAPIError as e:
        logger.error(f"Failed to get task {task_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_create_task(
    title: str,
    project_id: str,
    content: str | None = None,
    desc: str | None = None,
    is_all_day: bool | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    time_zone: str | None = None,
    reminders: list[str] | None = None,
    repeat_flag: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None
) -> dict[str, Any]:
    """
    Create a new task.

    Args:
        title: Task title (required)
        project_id: Project ID (required). Use "inbox{userId}" for Inbox.
        content: Task content/notes
        desc: Description for checklist
        is_all_day: Whether it's an all-day task
        start_date: Start date in "yyyy-MM-dd'T'HH:mm:ssZ" format
        due_date: Due date in "yyyy-MM-dd'T'HH:mm:ssZ" format
        time_zone: Timezone (e.g., "America/New_York")
        reminders: List of reminders. Examples:
            - "TRIGGER:PT0S" = At time of event
            - "TRIGGER:-PT30M" = 30 minutes before
            - "TRIGGER:-PT1H" = 1 hour before
            - "TRIGGER:-P1D" = 1 day before
        repeat_flag: Recurrence rule. Examples:
            - "RRULE:FREQ=DAILY;INTERVAL=1" = Every day
            - "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR" = Mon/Wed/Fri
            - "RRULE:FREQ=MONTHLY;BYMONTHDAY=15" = 15th of each month
        priority: Priority level (0=None, 1=Low, 3=Medium, 5=High)
        tags: List of tags

    Returns:
        Created task details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        task = await client.create_task(
            title=title,
            project_id=project_id,
            content=content,
            desc=desc,
            is_all_day=is_all_day,
            start_date=start_date,
            due_date=due_date,
            time_zone=time_zone,
            reminders=reminders,
            repeat_flag=repeat_flag,
            priority=priority,
            tags=tags
        )
        return {
            "success": True,
            "task": _format_task(task)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to create task: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_create_task_with_subtasks(
    title: str,
    project_id: str,
    subtasks: list[str],
    content: str | None = None,
    due_date: str | None = None,
    time_zone: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None
) -> dict[str, Any]:
    """
    Create a new task with subtasks (checklist items).

    Args:
        title: Task title (required)
        project_id: Project ID (required)
        subtasks: List of subtask titles (required)
        content: Task content/notes
        due_date: Due date in "yyyy-MM-dd'T'HH:mm:ssZ" format
        time_zone: Timezone
        priority: Priority level (0=None, 1=Low, 3=Medium, 5=High)
        tags: List of tags

    Returns:
        Created task with subtasks
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    # Build subtask items
    items = [
        {"title": subtask_title, "status": 0}
        for subtask_title in subtasks
    ]

    try:
        task = await client.create_task(
            title=title,
            project_id=project_id,
            content=content,
            due_date=due_date,
            time_zone=time_zone,
            priority=priority,
            tags=tags,
            items=items
        )
        return {
            "success": True,
            "task": _format_task(task)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to create task with subtasks: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_update_task(
    task_id: str,
    project_id: str,
    title: str | None = None,
    content: str | None = None,
    is_all_day: bool | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    time_zone: str | None = None,
    reminders: list[str] | None = None,
    repeat_flag: str | None = None,
    priority: int | None = None,
    tags: list[str] | None = None
) -> dict[str, Any]:
    """
    Update an existing task.

    Note: Both task_id and project_id are required for updates.

    Args:
        task_id: Task ID to update (required)
        project_id: Project ID containing the task (required)
        title: New task title
        content: New task content
        is_all_day: Whether it's an all-day task
        start_date: New start date
        due_date: New due date
        time_zone: New timezone
        reminders: New reminders list
        repeat_flag: New recurrence rule
        priority: New priority (0=None, 1=Low, 3=Medium, 5=High)
        tags: New tags list

    Returns:
        Updated task details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        task = await client.update_task(
            task_id=task_id,
            project_id=project_id,
            title=title,
            content=content,
            is_all_day=is_all_day,
            start_date=start_date,
            due_date=due_date,
            time_zone=time_zone,
            reminders=reminders,
            repeat_flag=repeat_flag,
            priority=priority,
            tags=tags
        )
        return {
            "success": True,
            "task": _format_task(task)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to update task {task_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_complete_task(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Mark a task as complete.

    Args:
        project_id: Project ID containing the task
        task_id: Task ID to complete

    Returns:
        Success status
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        await client.complete_task(project_id, task_id)
        return {
            "success": True,
            "message": f"Task {task_id} marked as complete"
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to complete task {task_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_delete_task(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Delete a task.

    WARNING: This permanently deletes the task.

    Args:
        project_id: Project ID containing the task
        task_id: Task ID to delete

    Returns:
        Success status
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        await client.delete_task(project_id, task_id)
        return {
            "success": True,
            "message": f"Task {task_id} deleted successfully"
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to delete task {task_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_get_all_tasks() -> dict[str, Any]:
    """
    Get all tasks from all projects (including Inbox if user_id is configured).

    Note: This makes multiple API calls, one per project.

    Returns:
        All tasks grouped by project
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        tasks = await client.get_all_tasks()
        return {
            "tasks": [_format_task(t) for t in tasks],
            "total_count": len(tasks)
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to get all tasks: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_filter_tasks(
    status: str = "uncompleted",
    project_id: str | None = None,
    tag_label: str | None = None,
    priority: int | None = None,
    due_start_date: str | None = None,
    due_end_date: str | None = None,
    completion_start_date: str | None = None,
    completion_end_date: str | None = None,
    sort_by_priority: bool = False
) -> dict[str, Any]:
    """
    Filter tasks based on various criteria.

    Fetches tasks from all projects and applies client-side filtering.

    Args:
        status: Task status - "uncompleted" (default) or "completed"
        project_id: Filter by specific project ID
        tag_label: Filter by tag name (exact match)
        priority: Filter by priority level (0=None, 1=Low, 3=Medium, 5=High)
        due_start_date: Start of due date range (ISO format, e.g., "2024-07-26")
        due_end_date: End of due date range (ISO format)
        completion_start_date: Start of completion date range (for completed tasks)
        completion_end_date: End of completion date range (for completed tasks)
        sort_by_priority: Sort results by priority (highest first)

    Returns:
        Filtered list of tasks

    Examples:
        Get all uncompleted high-priority tasks:
            status="uncompleted", priority=5

        Get tasks due this week:
            status="uncompleted", due_start_date="2024-07-22", due_end_date="2024-07-28"

        Get completed tasks from last week:
            status="completed", completion_start_date="2024-07-15", completion_end_date="2024-07-21"

        Get all tasks with "work" tag:
            tag_label="work"
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    # Build filter criteria
    filters = {
        "status": status,
        "project_id": project_id,
        "tag_label": tag_label,
        "priority": priority,
        "due_start_date": due_start_date,
        "due_end_date": due_end_date,
        "completion_start_date": completion_start_date,
        "completion_end_date": completion_end_date,
    }

    try:
        # Fetch all tasks
        all_tasks = await client.get_all_tasks()

        # Apply filters
        filtered_tasks = [t for t in all_tasks if _matches_filter(t, filters)]

        # Sort by priority if requested (highest first: 5, 3, 1, 0)
        if sort_by_priority:
            filtered_tasks.sort(key=lambda t: t.get("priority", 0), reverse=True)

        return {
            "tasks": [_format_task(t) for t in filtered_tasks],
            "total_count": len(filtered_tasks),
            "filters_applied": {k: v for k, v in filters.items() if v is not None}
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to filter tasks: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_move_task(task_id: str, from_project_id: str, to_project_id: str) -> dict[str, Any]:
    """
    Move a task from one project to another.

    Note: This is done by updating the task's projectId field.

    Args:
        task_id: The task ID to move
        from_project_id: Current project ID (required to fetch the task)
        to_project_id: Destination project ID

    Returns:
        Updated task details
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        # Get the current task
        task = await client.get_task(from_project_id, task_id)

        # Update with new project ID
        # The official API requires sending the full task object for updates
        updated_task = await client.update_task(
            task_id=task_id,
            project_id=to_project_id,  # New project
            title=task.get("title"),
        )

        return {
            "success": True,
            "task": _format_task(updated_task),
            "moved_from": from_project_id,
            "moved_to": to_project_id
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to move task {task_id}: {e}")
        return {"error": str(e), "status_code": e.status_code}


@mcp.tool()
async def ticktick_make_subtask(
    child_task_id: str,
    child_project_id: str,
    parent_task_id: str
) -> dict[str, Any]:
    """
    Make one task a subtask of another task.

    Note: Both tasks must be in the same project. This converts an existing
    task into a subtask (checklist item) of the parent task.

    IMPORTANT: The official API may have limitations with this operation.
    Subtasks created via the items[] array during task creation work reliably.
    Converting existing tasks to subtasks may require the unofficial API.

    Args:
        child_task_id: The task ID to become a subtask
        child_project_id: The project ID containing the child task
        parent_task_id: The task ID that will become the parent

    Returns:
        Result of the operation
    """
    client = get_ticktick_client()
    if not client:
        return {
            "error": "Not authenticated. Please complete OAuth flow at /oauth/start"
        }

    try:
        # Get both tasks
        child_task = await client.get_task(child_project_id, child_task_id)
        parent_task = await client.get_task(child_project_id, parent_task_id)

        # Verify same project
        if child_task.get("projectId") != parent_task.get("projectId"):
            return {
                "error": "Tasks must be in the same project to create a subtask relationship",
                "child_project": child_task.get("projectId"),
                "parent_project": parent_task.get("projectId")
            }

        # Get existing subtasks from parent
        existing_items = parent_task.get("items") or []

        # Create new subtask item from child task
        new_item = {
            "title": child_task.get("title"),
            "status": child_task.get("status", 0),
        }
        if child_task.get("startDate"):
            new_item["startDate"] = child_task.get("startDate")

        # Add to parent's items
        updated_items = existing_items + [new_item]

        # Update parent task with new subtask
        updated_parent = await client.update_task(
            task_id=parent_task_id,
            project_id=child_project_id,
            items=updated_items
        )

        # Delete the original child task (now it's a subtask)
        await client.delete_task(child_project_id, child_task_id)

        return {
            "success": True,
            "message": f"Task '{child_task.get('title')}' is now a subtask of '{parent_task.get('title')}'",
            "parent_task": _format_task(updated_parent),
            "note": "Original task was deleted and converted to a subtask item"
        }
    except TickTickAPIError as e:
        logger.error(f"Failed to make subtask: {e}")
        return {"error": str(e), "status_code": e.status_code}
