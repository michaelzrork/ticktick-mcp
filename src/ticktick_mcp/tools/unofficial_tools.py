"""
MCP Tools for unofficial TickTick API features.

These tools use direct API calls to unofficial v2 endpoints:
- Pin/unpin tasks
- Recurrence patterns (RRULE, ERULE for specific dates, repeatFrom)
- Task activity logs
- Full CRUD operations via unofficial API
- Fresh data fetches (no caching!)
- Proper subtask relationships (parentId/childIds via batch/taskParent)
- Checklist item management (items[] array - add, update, remove, convert)

CHECKLIST ITEMS vs SUBTASKS:
- Checklist items: Embedded in task's items[] array. Simple checkbox list.
  Use unofficial_add_checklist_item, unofficial_update_checklist_item, etc.
- Subtasks: Separate tasks with parentId/childIds. True task hierarchy.
  Use unofficial_make_subtask, unofficial_remove_subtask.

Requires TICKTICK_USERNAME and TICKTICK_PASSWORD environment variables.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from ticktick_mcp.mcp_instance import mcp
from ticktick_mcp.unofficial_client import UnofficialAPIClient, get_client

logger = logging.getLogger(__name__)


# ==================== API Endpoints ====================

BATCH_CHECK = "/api/v2/batch/check/0"
BATCH_TASK = "/api/v2/batch/task"
BATCH_TASK_PROJECT = "/api/v2/batch/taskProject"
BATCH_TASK_PARENT = "/api/v2/batch/taskParent"
TASK_ACTIVITY = "/api/v1/task/activity/{task_id}"
TASK_BY_ID = "/api/v2/task/{task_id}"


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


def _normalize_repeat_from(value: str | None) -> str | None:
    """Convert friendly repeat_from names to API values."""
    if value is None:
        return None
    normalized = value.lower().replace(" ", "_").replace("-", "_")
    if normalized in ("completion_date", "completion", "1"):
        return "1"
    elif normalized in ("due_date", "due", "0"):
        return "0"
    return value  # Pass through if already "0" or "1"


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

        # Fetch the FULL task first (critical - partial updates strip fields!)
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Set pinnedTime to current timestamp
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")
        task["pinnedTime"] = now

        # Send the FULL task back
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

        # Fetch the FULL task first (critical - partial updates strip fields!)
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Set pinnedTime to "-1" to unpin
        task["pinnedTime"] = "-1"

        # Send the FULL task back
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict) and result.get("id2error", {}).get(task_id):
            return {"error": f"Unpin failed: {result['id2error'][task_id]}"}

        logger.info(f"Successfully unpinned task {task_id}")
        return {"success": True, "message": f"Task {task_id} unpinned"}
    except Exception as e:
        logger.error(f"Failed to unpin task: {e}")
        return {"error": str(e)}


# ==================== Data Fetch Tools (FRESH - NO CACHE) ====================


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


@mcp.tool()
def unofficial_get_all(
    obj_type: Literal["projects", "tags"]
) -> dict[str, Any] | list[dict]:
    """
    Get all projects or tags via the unofficial API.

    ALWAYS returns fresh data - no caching.

    For tasks, use unofficial_filter_tasks() instead — it supports filtering
    by status, project, tag, date range, priority, and title search to avoid
    returning overwhelming amounts of data.

    Args:
        obj_type: Type of objects to retrieve - "projects" or "tags"

    Returns:
        List of objects or error dict
    """
    logger.info(f"unofficial_get_all called for type: {obj_type}")

    try:
        client = _get_api_client()
        data = _fetch_all_data(client)

        if obj_type == "projects":
            result = data.get("projectProfiles", [])
        elif obj_type == "tags":
            result = data.get("tags", [])
        else:
            return {"error": f"Unknown object type: {obj_type}. Use unofficial_filter_tasks() for tasks."}

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


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string to datetime for comparison."""
    if not date_str:
        return None
    try:
        clean = date_str.replace(".000", "")
        if len(clean) > 5 and clean[-5] in "+-" and ":" not in clean[-5:]:
            clean = clean[:-2] + ":" + clean[-2:]
        return datetime.fromisoformat(clean)
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(date_str[:10])
        except (ValueError, TypeError):
            return None


def _matches_filter(task: dict, filters: dict) -> bool:
    """Check if a task matches the given filter criteria."""
    status = filters.get("status", "uncompleted")
    task_status = task.get("status", 0)
    if status == "uncompleted" and task_status != 0:
        return False
    if status == "completed" and task_status != 2:
        return False

    title_contains = filters.get("title_contains")
    if title_contains:
        task_title = task.get("title") or ""
        if title_contains.lower() not in task_title.lower():
            return False

    project_id = filters.get("project_id")
    if project_id and task.get("projectId") != project_id:
        return False

    tag_label = filters.get("tag_label")
    if tag_label:
        task_tags = task.get("tags") or []
        if tag_label not in task_tags:
            return False

    priority = filters.get("priority")
    if priority is not None and task.get("priority") != priority:
        return False

    due_start = filters.get("due_start_date")
    due_end = filters.get("due_end_date")
    if due_start or due_end:
        task_due = _parse_date(task.get("dueDate"))
        if not task_due:
            return False
        if due_start:
            filter_start = _parse_date(due_start)
            if filter_start and task_due.date() < filter_start.date():
                return False
        if due_end:
            filter_end = _parse_date(due_end)
            if filter_end and task_due.date() > filter_end.date():
                return False

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


@mcp.tool()
def unofficial_filter_tasks(
    status: str = "uncompleted",
    project_id: str | None = None,
    tag_label: str | None = None,
    title_contains: str | None = None,
    priority: int | None = None,
    due_start_date: str | None = None,
    due_end_date: str | None = None,
    completion_start_date: str | None = None,
    completion_end_date: str | None = None,
    sort_by_priority: bool = False
) -> dict[str, Any]:
    """
    PRIMARY TOOL FOR FINDING AND LISTING TASKS via the unofficial API.
    Use this instead of fetching all data.

    Searches across all projects and returns only tasks matching your filters.
    Always returns fresh data (no caching). By default, only returns uncompleted tasks.

    IMPORTANT: Use this tool whenever you need to find, list, or browse tasks.
    Do NOT use get-all tools to retrieve tasks — use this with appropriate filters.

    Args:
        status: "uncompleted" (default), "completed", or "all"
        project_id: Filter by specific project ID
        tag_label: Filter by tag name (exact match, e.g., "errands", "work")
        title_contains: Search for tasks whose title contains this text (case-insensitive)
        priority: Filter by priority level (0=None, 1=Low, 3=Medium, 5=High)
        due_start_date: Only tasks due on or after this date (ISO format, e.g., "2026-02-01")
        due_end_date: Only tasks due on or before this date (ISO format)
        completion_start_date: Only tasks completed on or after this date (for completed/all)
        completion_end_date: Only tasks completed on or before this date (for completed/all)
        sort_by_priority: Sort results by priority (highest first)

    Returns:
        Dict with filtered tasks, total_count, and filters_applied

    Examples:
        Get all incomplete tasks (default):
            unofficial_filter_tasks()

        Search by title:
            unofficial_filter_tasks(title_contains="groceries")

        Get tasks from a specific project:
            unofficial_filter_tasks(project_id="abc123")

        Get tasks with a specific tag:
            unofficial_filter_tasks(tag_label="errands")

        Get high-priority tasks:
            unofficial_filter_tasks(priority=5, sort_by_priority=True)

        Get tasks due this week:
            unofficial_filter_tasks(due_start_date="2026-02-03", due_end_date="2026-02-09")

        Get tasks due today or later:
            unofficial_filter_tasks(due_start_date="2026-02-03")

        Get completed tasks from a project:
            unofficial_filter_tasks(status="completed", project_id="abc123")

        Get completed tasks from last week:
            unofficial_filter_tasks(status="completed", completion_start_date="2026-01-27", completion_end_date="2026-02-02")

        Get all tasks (completed + uncompleted) with a tag:
            unofficial_filter_tasks(status="all", tag_label="work")
    """
    logger.info("unofficial_filter_tasks called")

    try:
        client = _get_api_client()
        data = _fetch_all_data(client)
        all_tasks = data.get("syncTaskBean", {}).get("update", [])

        filters = {
            "status": status,
            "project_id": project_id,
            "tag_label": tag_label,
            "title_contains": title_contains,
            "priority": priority,
            "due_start_date": due_start_date,
            "due_end_date": due_end_date,
            "completion_start_date": completion_start_date,
            "completion_end_date": completion_end_date,
        }

        filtered_tasks = [t for t in all_tasks if _matches_filter(t, filters)]

        if sort_by_priority:
            filtered_tasks.sort(key=lambda t: t.get("priority", 0), reverse=True)

        logger.info(f"Filtered to {len(filtered_tasks)} tasks from {len(all_tasks)} total (fresh)")
        return {
            "tasks": filtered_tasks,
            "total_count": len(filtered_tasks),
            "filters_applied": {k: v for k, v in filters.items() if v is not None}
        }
    except Exception as e:
        logger.error(f"Failed to filter tasks: {e}")
        return {"error": str(e)}


# ==================== Task CRUD Tools (Direct API) ====================


@mcp.tool()
def unofficial_create_task(
    title: str,
    project_id: str,
    content: str | None = None,
    desc: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    priority: int = 0,
    tags: list[str] | None = None,
    reminders: list[str] | None = None,
    is_all_day: bool = True,
    repeat_flag: str | None = None,
    repeat_from: str | None = None,
    specific_dates: list[str] | None = None,
    time_zone: str = "America/New_York",
) -> dict[str, Any]:
    """
    Create a new task via the unofficial API.

    Supports all fields including recurrence patterns that the official API cannot set.

    CRITICAL — DATE/TIME RULES:

    All-day tasks (DEFAULT — is_all_day defaults to True):
        start_date="2026-02-06"    (date-only, NO time component)
        due_date="2026-02-06"      (date-only, NO time component)
        time_zone="America/New_York"
        Do NOT append T00:00:00 or any time to all-day dates.

    Timed tasks (only when user specifies a clock time):
        start_date="2026-02-06T14:00:00"    (include time)
        due_date="2026-02-06T14:00:00"      (include time)
        is_all_day=False                     (must explicitly set)
        time_zone="America/New_York"

    ALWAYS:
        - Set BOTH start_date AND due_date to the same value
        - Include time_zone with every date (defaults to America/New_York)
        - Omitting one date can create unintended date ranges

    Args:
        title: Task title (required)
        project_id: Project ID (required). Use "inbox{userId}" for Inbox.
        content: Task content/notes
        desc: User-visible description shown as subtitle in TickTick list view preview. Highest
            display priority — always shows when set, overriding both checklist items and content
            in the preview. Works on all task types. When opening the full task, all fields are visible.
        start_date: Start date. Use "2026-02-06" for all-day, "2026-02-06T14:00:00" for timed.
        due_date: Due date. Use "2026-02-06" for all-day, "2026-02-06T14:00:00" for timed.
            Required for recurring tasks.
        priority: Priority level (0=None, 1=Low, 3=Medium, 5=High)
        tags: List of tags
        reminders: List of reminder triggers in RFC 5545 format. Examples:
            - ["TRIGGER:PT0S"] = At time of event
            - ["TRIGGER:-PT30M"] = 30 minutes before
            - ["TRIGGER:-PT1H"] = 1 hour before
            - ["TRIGGER:-P1D"] = 1 day before
            Multiple: ["TRIGGER:PT0S", "TRIGGER:-PT30M"]
        is_all_day: Whether it's an all-day task (default: True). Set to False for timed tasks.
        repeat_flag: Recurrence rule (RRULE format). Examples:
            - "RRULE:FREQ=DAILY;INTERVAL=1" = Every day
            - "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR" = Mon/Wed/Fri
            - "RRULE:FREQ=MONTHLY;BYMONTHDAY=15" = 15th of each month
        repeat_from: When to calculate next occurrence (only for recurring tasks):
            - "due_date" or "0" = From the due date (default)
            - "completion_date" or "1" = From when task was completed
        specific_dates: List of specific dates in YYYY-MM-DD format.
            If provided, creates an ERULE instead of RRULE (overrides repeat_flag).
            Example: ["2026-02-05", "2026-02-10", "2026-02-15"]
        time_zone: Timezone (e.g., "America/New_York"). Always included when dates are set.

    Returns:
        Created task details

    Examples:
        # All-day task (default)
        unofficial_create_task(
            title="Buy groceries",
            project_id="abc123",
            start_date="2026-02-06",
            due_date="2026-02-06",
            time_zone="America/New_York"
        )

        # Timed task at 2pm
        unofficial_create_task(
            title="Meeting",
            project_id="abc123",
            start_date="2026-02-06T14:00:00",
            due_date="2026-02-06T14:00:00",
            is_all_day=False,
            time_zone="America/New_York"
        )

        # Recurring task that repeats from completion date
        unofficial_create_task(
            title="Water plants",
            project_id="abc123",
            start_date="2026-02-01",
            due_date="2026-02-01",
            repeat_flag="RRULE:FREQ=DAILY;INTERVAL=3",
            repeat_from="completion_date"
        )

        # Task on specific dates
        unofficial_create_task(
            title="Take medication",
            project_id="abc123",
            specific_dates=["2026-02-05", "2026-02-10", "2026-02-15"]
        )
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
        if desc is not None:
            task["desc"] = desc
        if reminders is not None:
            task["reminder"] = reminders[0]
            if len(reminders) > 1:
                task["reminders"] = [{"trigger": r} for r in reminders]

        # Handle specific dates (ERULE) - takes precedence over repeat_flag
        if specific_dates:
            formatted_dates = sorted([d.replace("-", "") for d in specific_dates])
            task["repeatFlag"] = f"ERULE:NAME=CUSTOM;BYDATE={','.join(formatted_dates)}"
            first_date = min(specific_dates)
            task["repeatFirstDate"] = f"{first_date}T05:00:00.000+0000"
            task["repeatFrom"] = "0"
        elif repeat_flag:
            task["repeatFlag"] = repeat_flag
            if repeat_from:
                task["repeatFrom"] = _normalize_repeat_from(repeat_from)

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
def unofficial_update_task(
    task_id: str,
    title: str | None = None,
    content: str | None = None,
    desc: str | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    priority: int | None = None,
    status: int | None = None,
    tags: list[str] | None = None,
    reminders: list[str] | None = None,
    is_all_day: bool | None = None,
    repeat_flag: str | None = None,
    repeat_from: str | None = None,
    specific_dates: list[str] | None = None,
    time_zone: str = "America/New_York",
) -> dict[str, Any]:
    """
    Update an existing task via the unofficial API.

    Works for both completed and incomplete tasks. Can update fields the official
    API cannot, including recurrence patterns and task status.

    CRITICAL — DATE/TIME RULES:

    Moving an all-day task to a new date:
        start_date="2026-02-08"    (date-only, NO time component)
        due_date="2026-02-08"      (date-only, NO time component)
        time_zone="America/New_York"
        is_all_day is optional here but safe to include as True

    Moving a timed task to a new time:
        start_date="2026-02-08T14:00:00"    (include time)
        due_date="2026-02-08T14:00:00"      (include time)
        time_zone="America/New_York"
        is_all_day is optional here but safe to include as False

    Converting timed → all-day:
        is_all_day=True                     (REQUIRED — explicit flag)
        start_date="2026-02-08"             (REQUIRED — date-only)
        due_date="2026-02-08"               (REQUIRED — date-only)
        time_zone="America/New_York"
        WARNING: Sending is_all_day without dates WIPES the dates entirely

    Converting all-day → timed:
        is_all_day=False                    (REQUIRED — explicit flag)
        start_date="2026-02-08T14:00:00"    (REQUIRED — with time)
        due_date="2026-02-08T14:00:00"      (REQUIRED — with time)
        time_zone="America/New_York"

    ALWAYS:
        - Set BOTH start_date AND due_date even if only one is changing
        - Include time_zone with every date update (defaults to America/New_York)
        - Omitting start_date leaves old value, creating unintended date ranges
        - Do NOT append T00:00:00 or any time to all-day dates
        - Do NOT strip time from timed task dates

    Args:
        task_id: Task ID to update (required)
        title: New task title
        content: New task content/notes
        desc: User-visible description shown as subtitle in TickTick list view preview. Highest
            display priority — always shows when set, overriding both checklist items and content
            in the preview. Works on all task types. When opening the full task, all fields are visible.
        start_date: New start date. Use "2026-02-08" for all-day, "2026-02-08T14:00:00" for timed.
        due_date: New due date. Use "2026-02-08" for all-day, "2026-02-08T14:00:00" for timed.
        priority: New priority (0=None, 1=Low, 3=Medium, 5=High)
        status: Task status:
            - 0 = Incomplete (use this to UN-COMPLETE a completed task)
            - 2 = Complete
        tags: New tags list (replaces existing tags)
        reminders: List of reminder triggers in RFC 5545 format. Examples:
            - ["TRIGGER:PT0S"] = At time of event
            - ["TRIGGER:-PT30M"] = 30 minutes before
            - ["TRIGGER:-PT1H"] = 1 hour before
            - ["TRIGGER:-P1D"] = 1 day before
            Multiple: ["TRIGGER:PT0S", "TRIGGER:-PT30M"]
        is_all_day: Whether this is an all-day task. Required when converting between
            timed and all-day tasks.
        repeat_flag: New recurrence rule (RRULE format). Examples:
            - "RRULE:FREQ=DAILY;INTERVAL=1" = Every day
            - "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR" = Mon/Wed/Fri
            - "RRULE:FREQ=MONTHLY;BYMONTHDAY=15" = 15th of each month
        repeat_from: When to calculate next occurrence:
            - "due_date" or "0" = From the due date
            - "completion_date" or "1" = From when task was completed
        specific_dates: List of specific dates in YYYY-MM-DD format.
            If provided, replaces any existing recurrence with an ERULE.
            Example: ["2026-02-05", "2026-02-10", "2026-02-15"]
        time_zone: Timezone (e.g., "America/New_York"). Always included when dates are updated.

    Returns:
        Updated task details

    Examples:
        # Move an all-day task to a new date
        unofficial_update_task(
            task_id="abc123",
            start_date="2026-02-08",
            due_date="2026-02-08",
            time_zone="America/New_York"
        )

        # Convert timed task to all-day
        unofficial_update_task(
            task_id="abc123",
            is_all_day=True,
            start_date="2026-02-08",
            due_date="2026-02-08",
            time_zone="America/New_York"
        )

        # Convert all-day task to timed
        unofficial_update_task(
            task_id="abc123",
            is_all_day=False,
            start_date="2026-02-08T14:00:00",
            due_date="2026-02-08T14:00:00",
            time_zone="America/New_York"
        )

        # Un-complete a task
        unofficial_update_task(task_id="abc123", status=0)

        # Change recurrence to repeat from completion date
        unofficial_update_task(task_id="abc123", repeat_from="completion_date")

        # Set specific dates recurrence
        unofficial_update_task(task_id="abc123", specific_dates=["2026-03-01", "2026-03-15"])
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
        if tags is not None:
            task["tags"] = tags
        if is_all_day is not None:
            task["isAllDay"] = is_all_day
        if desc is not None:
            task["desc"] = desc
        if reminders is not None:
            task["reminder"] = reminders[0]
            if len(reminders) > 1:
                task["reminders"] = [{"trigger": r} for r in reminders]
            else:
                task["reminders"] = [{"trigger": reminders[0]}]

        # Always update timeZone when any date field is being updated
        if start_date is not None or due_date is not None:
            task["timeZone"] = time_zone

        # Handle status change (including un-completing)
        if status is not None:
            task["status"] = status
            if status == 0:
                task["completedTime"] = None

        # Handle specific dates (ERULE) - takes precedence
        if specific_dates is not None:
            formatted_dates = sorted([d.replace("-", "") for d in specific_dates])
            task["repeatFlag"] = f"ERULE:NAME=CUSTOM;BYDATE={','.join(formatted_dates)}"
            first_date = min(specific_dates)
            task["repeatFirstDate"] = f"{first_date}T05:00:00.000+0000"
            task["repeatFrom"] = "0"
        else:
            # Handle repeat_flag update
            if repeat_flag is not None:
                task["repeatFlag"] = repeat_flag

            # Handle repeat_from with friendly names
            if repeat_from is not None:
                task["repeatFrom"] = _normalize_repeat_from(repeat_from)

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

    Creates a proper parent-child relationship where:
    - Parent task gets childIds[] array updated
    - Child task gets parentId set

    This creates TRUE subtasks (separate tasks with hierarchy), not checklist
    items (embedded in items[] array). Both tasks must be in the same project.

    Args:
        child_task_id: The task ID to become a subtask
        parent_task_id: The task ID that will become the parent

    Returns:
        Dict with success status and updated parent/child info from API response

    Example:
        # Create parent and child tasks first
        parent = unofficial_create_task(title="Main Task", project_id="...")
        child = unofficial_create_task(title="Step 1", project_id="...")

        # Make child a subtask of parent
        result = unofficial_make_subtask(child["task"]["id"], parent["task"]["id"])
        # Parent now has childIds: [child_id]
        # Child now has parentId: parent_id
    """
    logger.info(f"unofficial_make_subtask called: child={child_task_id}, parent={parent_task_id}")

    try:
        client = _get_api_client()

        # Get both tasks to verify they exist and are in same project
        child = _get_task_by_id(client, child_task_id)
        if not child:
            return {"error": f"Child task not found: {child_task_id}"}

        parent = _get_task_by_id(client, parent_task_id)
        if not parent:
            return {"error": f"Parent task not found: {parent_task_id}"}

        project_id = child.get("projectId")
        if project_id != parent.get("projectId"):
            return {"error": "Tasks must be in the same project"}

        # Use the dedicated taskParent endpoint (discovered from ticktick-py)
        # This properly updates BOTH parent.childIds AND child.parentId
        subtask_payload = [{
            "parentId": parent_task_id,
            "projectId": project_id,
            "taskId": child_task_id
        }]
        result = client.call_api(BATCH_TASK_PARENT, method="POST", data=subtask_payload)

        # Check for errors
        if isinstance(result, dict) and result.get("id2error", {}).get(child_task_id):
            return {"error": f"Make subtask failed: {result['id2error'][child_task_id]}"}

        # Extract updated info from response
        id2etag = result.get("id2etag", {}) if isinstance(result, dict) else {}

        logger.info(f"Successfully made task {child_task_id} a subtask of {parent_task_id}")
        return {
            "success": True,
            "message": f"Task is now a subtask",
            "parent": id2etag.get(parent_task_id, {}),
            "child": id2etag.get(child_task_id, {})
        }
    except Exception as e:
        logger.error(f"Failed to make subtask: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_remove_subtask(child_task_id: str) -> dict[str, Any]:
    """
    Remove a subtask relationship, making the child a standalone task.

    The child task remains in the same project but is no longer nested
    under its parent. Both tasks continue to exist.

    Args:
        child_task_id: The subtask ID to un-nest

    Returns:
        Dict with success status and updated task info
    """
    logger.info(f"unofficial_remove_subtask called: child={child_task_id}")

    try:
        client = _get_api_client()

        # Get the child task
        child = _get_task_by_id(client, child_task_id)
        if not child:
            return {"error": f"Child task not found: {child_task_id}"}

        if not child.get("parentId"):
            return {"error": "Task is not a subtask (no parentId)"}

        project_id = child.get("projectId")

        # Use taskParent endpoint with null parentId to remove relationship
        subtask_payload = [{
            "parentId": None,
            "projectId": project_id,
            "taskId": child_task_id
        }]
        result = client.call_api(BATCH_TASK_PARENT, method="POST", data=subtask_payload)

        # Check for errors
        if isinstance(result, dict) and result.get("id2error", {}).get(child_task_id):
            return {"error": f"Remove subtask failed: {result['id2error'][child_task_id]}"}

        logger.info(f"Successfully removed subtask relationship for {child_task_id}")
        return {
            "success": True,
            "message": "Task is no longer a subtask",
            "task_id": child_task_id
        }
    except Exception as e:
        logger.error(f"Failed to remove subtask: {e}")
        return {"error": str(e)}


# ==================== Checklist Item Tools ====================


@mcp.tool()
def unofficial_add_checklist_item(
    task_id: str,
    title: str
) -> dict[str, Any]:
    """
    Add a checklist item to an existing task via the unofficial API.

    Checklist items are embedded in the task's items[] array. This is different
    from subtasks, which are separate tasks with parentId/childIds relationships.

    Args:
        task_id: The task ID to add the checklist item to
        title: The title of the new checklist item

    Returns:
        Updated task with the new checklist item

    Example:
        unofficial_add_checklist_item(
            task_id="abc123",
            title="New step"
        )
    """
    logger.info(f"unofficial_add_checklist_item called for task: {task_id}")

    try:
        client = _get_api_client()

        # Get the full task
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        # Get existing items or start with empty list
        existing_items = task.get("items") or []

        # Add new item (API will assign an ID)
        new_item = {"title": title, "status": 0}
        task["items"] = existing_items + [new_item]

        # Update via batch endpoint
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict) and task_id in result.get("id2etag", {}):
            task["etag"] = result["id2etag"][task_id]

        logger.info(f"Successfully added checklist item to task {task_id}")
        return {
            "success": True,
            "task": task,
            "added_item": new_item
        }
    except Exception as e:
        logger.error(f"Failed to add checklist item: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_update_checklist_item(
    task_id: str,
    item_id: str,
    title: str | None = None,
    status: int | None = None
) -> dict[str, Any]:
    """
    Update a checklist item within a task via the unofficial API.

    Args:
        task_id: The task ID containing the checklist item
        item_id: The checklist item ID to update
        title: New title for the item (optional)
        status: New status (0=uncompleted, 2=completed) (optional)

    Returns:
        Updated task with modified checklist item

    Example:
        # Complete a checklist item
        unofficial_update_checklist_item(
            task_id="abc123",
            item_id="item789",
            status=2
        )

        # Rename a checklist item
        unofficial_update_checklist_item(
            task_id="abc123",
            item_id="item789",
            title="Updated step name"
        )
    """
    logger.info(f"unofficial_update_checklist_item called for task: {task_id}, item: {item_id}")

    try:
        client = _get_api_client()

        # Get the full task
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        existing_items = task.get("items") or []
        if not existing_items:
            return {"error": "Task has no checklist items"}

        # Find and update the item
        item_found = False
        for item in existing_items:
            if item.get("id") == item_id:
                item_found = True
                if title is not None:
                    item["title"] = title
                if status is not None:
                    item["status"] = status
                break

        if not item_found:
            return {"error": f"Checklist item not found: {item_id}"}

        task["items"] = existing_items

        # Update via batch endpoint
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict) and task_id in result.get("id2etag", {}):
            task["etag"] = result["id2etag"][task_id]

        logger.info(f"Successfully updated checklist item {item_id} in task {task_id}")
        return {
            "success": True,
            "task": task
        }
    except Exception as e:
        logger.error(f"Failed to update checklist item: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_remove_checklist_item(
    task_id: str,
    item_id: str
) -> dict[str, Any]:
    """
    Remove a checklist item from a task via the unofficial API.

    Args:
        task_id: The task ID containing the checklist item
        item_id: The checklist item ID to remove

    Returns:
        Updated task without the removed checklist item

    Example:
        unofficial_remove_checklist_item(
            task_id="abc123",
            item_id="item789"
        )
    """
    logger.info(f"unofficial_remove_checklist_item called for task: {task_id}, item: {item_id}")

    try:
        client = _get_api_client()

        # Get the full task
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        existing_items = task.get("items") or []
        if not existing_items:
            return {"error": "Task has no checklist items"}

        # Filter out the item to remove
        original_count = len(existing_items)
        updated_items = [item for item in existing_items if item.get("id") != item_id]

        if len(updated_items) == original_count:
            return {"error": f"Checklist item not found: {item_id}"}

        task["items"] = updated_items

        # Update via batch endpoint
        payload = {"add": [], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict) and task_id in result.get("id2etag", {}):
            task["etag"] = result["id2etag"][task_id]

        logger.info(f"Successfully removed checklist item {item_id} from task {task_id}")
        return {
            "success": True,
            "task": task,
            "removed_item_id": item_id
        }
    except Exception as e:
        logger.error(f"Failed to remove checklist item: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_convert_checklist_item_to_task(
    task_id: str,
    item_id: str
) -> dict[str, Any]:
    """
    Convert a checklist item into a standalone task via the unofficial API.

    The checklist item is removed from the parent task and a new task
    is created with the same title in the same project.

    Args:
        task_id: The task ID containing the checklist item
        item_id: The checklist item ID to convert

    Returns:
        Dict with the updated parent task and the new standalone task

    Example:
        result = unofficial_convert_checklist_item_to_task(
            task_id="abc123",
            item_id="item789"
        )
        # result["new_task"] is the new standalone task
        # result["parent_task"] is the updated parent (without the item)
    """
    logger.info(f"unofficial_convert_checklist_item_to_task called for task: {task_id}, item: {item_id}")

    try:
        client = _get_api_client()

        # Get the full task
        task = client.call_api(f"/api/v2/task/{task_id}")
        if not task:
            return {"error": f"Task not found: {task_id}"}

        existing_items = task.get("items") or []
        if not existing_items:
            return {"error": "Task has no checklist items"}

        # Find the item to convert
        item_to_convert = None
        updated_items = []
        for item in existing_items:
            if item.get("id") == item_id:
                item_to_convert = item
            else:
                updated_items.append(item)

        if not item_to_convert:
            return {"error": f"Checklist item not found: {item_id}"}

        project_id = task.get("projectId")

        # Create new task from the item
        new_task = {
            "title": item_to_convert.get("title", "Untitled"),
            "projectId": project_id,
            "priority": 0,
            "status": item_to_convert.get("status", 0),
            "timeZone": task.get("timeZone", "America/New_York"),
            "isAllDay": True,
        }

        # Update parent task to remove the item
        task["items"] = updated_items

        # Batch update: add new task, update parent
        payload = {"add": [new_task], "update": [task], "delete": []}
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        # Extract IDs from result
        if isinstance(result, dict):
            id2etag = result.get("id2etag", {})
            for task_id_result, etag_info in id2etag.items():
                if task_id_result == task_id:
                    task["etag"] = etag_info if isinstance(etag_info, str) else etag_info.get("etag")
                else:
                    # This is the new task
                    new_task["id"] = task_id_result
                    new_task["etag"] = etag_info if isinstance(etag_info, str) else etag_info.get("etag")

        logger.info(f"Successfully converted checklist item {item_id} to task")
        return {
            "success": True,
            "new_task": new_task,
            "parent_task": task,
            "converted_from_item": item_to_convert
        }
    except Exception as e:
        logger.error(f"Failed to convert checklist item to task: {e}")
        return {"error": str(e)}


@mcp.tool()
def unofficial_convert_task_to_checklist_item(
    child_task_id: str,
    parent_task_id: str
) -> dict[str, Any]:
    """
    Convert a task into a checklist item of another task via the unofficial API.

    The child task is deleted and added as an item in the parent task's
    items[] array. This is NOT the same as creating a subtask hierarchy.

    For true subtasks (separate tasks with parent/child relationship),
    use unofficial_make_subtask instead.

    Args:
        child_task_id: The task ID to convert into a checklist item
        parent_task_id: The task ID that will contain the new checklist item

    Returns:
        Updated parent task with the new checklist item

    Note:
        The original child task is DELETED and becomes embedded in the parent.
    """
    logger.info(f"unofficial_convert_task_to_checklist_item called: child={child_task_id}, parent={parent_task_id}")

    try:
        client = _get_api_client()

        # Get both tasks
        child_task = client.call_api(f"/api/v2/task/{child_task_id}")
        if not child_task:
            return {"error": f"Child task not found: {child_task_id}"}

        parent_task = client.call_api(f"/api/v2/task/{parent_task_id}")
        if not parent_task:
            return {"error": f"Parent task not found: {parent_task_id}"}

        # Verify same project
        if child_task.get("projectId") != parent_task.get("projectId"):
            return {
                "error": "Tasks must be in the same project to convert to checklist item",
                "child_project": child_task.get("projectId"),
                "parent_project": parent_task.get("projectId")
            }

        # Get existing checklist items from parent
        existing_items = parent_task.get("items") or []

        # Create new checklist item from child task
        new_item = {
            "title": child_task.get("title"),
            "status": child_task.get("status", 0),
        }
        if child_task.get("startDate"):
            new_item["startDate"] = child_task.get("startDate")

        # Add to parent's items
        parent_task["items"] = existing_items + [new_item]

        # Batch: update parent, delete child
        payload = {
            "add": [],
            "update": [parent_task],
            "delete": [{"taskId": child_task_id, "projectId": child_task.get("projectId")}]
        }
        result = client.call_api(BATCH_TASK, method="POST", data=payload)

        if isinstance(result, dict) and parent_task_id in result.get("id2etag", {}):
            parent_task["etag"] = result["id2etag"][parent_task_id]

        logger.info(f"Successfully converted task {child_task_id} to checklist item of {parent_task_id}")
        return {
            "success": True,
            "message": f"Task '{child_task.get('title')}' is now a checklist item of '{parent_task.get('title')}'",
            "parent_task": parent_task,
            "note": "Original task was deleted and converted to a checklist item"
        }
    except Exception as e:
        logger.error(f"Failed to convert task to checklist item: {e}")
        return {"error": str(e)}


# ==================== Experimental API Tool ====================


@mcp.tool()
def unofficial_experimental_api_call(
    endpoint: str,
    method: str = "GET",
    data: dict | list | None = None,
    params: dict | None = None
) -> dict[str, Any] | list[dict]:
    """
    Make a raw API call to TickTick's unofficial API for experimentation.

    Args:
        endpoint: API path (e.g., "/api/v2/batch/task")
        method: HTTP method - GET, POST, PUT, or DELETE
        data: JSON object or array for the request body (POST/PUT)
        params: JSON object for query parameters

    Returns:
        Raw API response or error dict
    """
    logger.info(f"unofficial_experimental_api_call: {method} {endpoint}")

    try:
        client = _get_api_client()

        result = client.call_api(endpoint, method=method, data=data, params=params)
        logger.info(f"unofficial_experimental_api_call succeeded: {method} {endpoint}")
        return result
    except Exception as e:
        logger.error(f"unofficial_experimental_api_call failed: {e}")
        return {"error": str(e)}