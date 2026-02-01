"""
TickTick Unofficial API Client.

Uses ticktick-py only for OAuth2 authentication to get an authenticated session.
All data operations use direct API calls to unofficial v2 endpoints.

This avoids ticktick-py's full data sync on init, providing live API access.
"""

import logging
from typing import Optional, Any
import requests

from ticktick.api import TickTickClient
from ticktick.oauth2 import OAuth2

from .config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD, dotenv_dir_path

logger = logging.getLogger(__name__)


class UnofficialAPIClient:
    """
    Direct access to TickTick's unofficial v2 API.

    Uses ticktick-py's OAuth2 + TickTickClient only to establish an authenticated
    session with cookies. All data operations use direct HTTP calls.
    """
    _instance: Optional["UnofficialAPIClient"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the client with an authenticated session."""
        if UnofficialAPIClient._initialized:
            return

        self._session: Optional[requests.Session] = None
        self._ticktick_client: Optional[TickTickClient] = None

        if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD]):
            logger.error("TickTick credentials not found. Ensure .env file or env vars are set.")
            UnofficialAPIClient._initialized = True
            return

        try:
            cache_path = dotenv_dir_path / ".token-oauth"
            logger.info(f"Initializing OAuth2 with cache path: {cache_path}")

            auth_client = OAuth2(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                cache_path=str(cache_path)
            )

            auth_client.get_access_token()
            logger.info("OAuth2 token loaded from cache")

            logger.info(f"Initializing TickTickClient for session auth: {USERNAME}")
            self._ticktick_client = TickTickClient(USERNAME, PASSWORD, auth_client)
            self._session = self._ticktick_client._session
            logger.info("Unofficial API client initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing unofficial client: {e}", exc_info=True)
            self._session = None
        finally:
            UnofficialAPIClient._initialized = True

    @classmethod
    def get_instance(cls) -> Optional["UnofficialAPIClient"]:
        """Get the singleton instance."""
        if not cls._initialized:
            cls()
        instance = cls._instance
        if instance and instance._session:
            return instance
        return None

    @property
    def session(self) -> requests.Session:
        """Get the authenticated session."""
        if not self._session:
            raise RuntimeError("Unofficial client not initialized")
        return self._session

    # ==================== Sync/Fetch Operations ====================

    def sync_all(self) -> dict[str, Any]:
        """
        Fetch all data from TickTick (tasks, projects, tags, etc.).

        Uses the batch/check endpoint with checkpoint=0 for a full sync.

        Returns:
            Dict containing syncTaskBean, projectProfiles, tags, etc.
        """
        url = "https://api.ticktick.com/api/v2/batch/check/0"
        response = self.session.get(url)

        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(f"Sync failed {response.status_code}: {response.text[:200]}")

    def get_all_tasks(self) -> list[dict]:
        """Get all tasks via sync."""
        data = self.sync_all()
        return data.get("syncTaskBean", {}).get("update", [])

    def get_all_projects(self) -> list[dict]:
        """Get all projects via sync."""
        data = self.sync_all()
        return data.get("projectProfiles", [])

    def get_all_tags(self) -> list[dict]:
        """Get all tags via sync."""
        data = self.sync_all()
        return data.get("tags", [])

    def get_task_by_id(self, task_id: str) -> Optional[dict]:
        """
        Get a specific task by ID.

        Fetches all tasks and finds the matching one.
        For better performance with known project_id, use get_task().
        """
        tasks = self.get_all_tasks()
        for task in tasks:
            if task.get("id") == task_id:
                return task
        return None

    def get_project_by_id(self, project_id: str) -> Optional[dict]:
        """Get a specific project by ID."""
        projects = self.get_all_projects()
        for project in projects:
            if project.get("id") == project_id:
                return project
        return None

    def get_tasks_from_project(self, project_id: str, status: int = 0) -> list[dict]:
        """
        Get tasks from a specific project.

        Args:
            project_id: The project ID
            status: 0=uncompleted, 2=completed (default: 0)
        """
        tasks = self.get_all_tasks()
        return [
            t for t in tasks
            if t.get("projectId") == project_id and t.get("status", 0) == status
        ]

    # ==================== Task CRUD Operations ====================

    def create_task(
        self,
        title: str,
        project_id: str,
        content: Optional[str] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        priority: int = 0,
        tags: Optional[list[str]] = None,
        **kwargs
    ) -> dict:
        """
        Create a new task.

        Args:
            title: Task title
            project_id: Project ID
            content: Task description
            start_date: Start date (ISO format)
            due_date: Due date (ISO format)
            priority: 0=None, 1=Low, 3=Medium, 5=High
            tags: List of tag names
            **kwargs: Additional task fields

        Returns:
            Created task data
        """
        import uuid
        from datetime import datetime, timezone

        task_id = str(uuid.uuid4()).replace("-", "")[:24]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        task = {
            "id": task_id,
            "projectId": project_id,
            "title": title,
            "priority": priority,
            "status": 0,
            "createdTime": now,
            "modifiedTime": now,
            **kwargs
        }

        if content:
            task["content"] = content
        if start_date:
            task["startDate"] = start_date
        if due_date:
            task["dueDate"] = due_date
        if tags:
            task["tags"] = tags

        url = "https://api.ticktick.com/api/v2/batch/task"
        payload = {
            "add": [task],
            "update": [],
            "delete": []
        }

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            result = response.json()
            # Return the created task from the response or our constructed task
            id_map = result.get("id2etag", {})
            if task_id in id_map:
                task["etag"] = id_map[task_id]
            return task
        else:
            raise RuntimeError(f"Create task failed {response.status_code}: {response.text[:200]}")

    def update_task(self, task: dict) -> dict:
        """
        Update an existing task.

        Args:
            task: Task dict with 'id' and 'projectId' plus fields to update

        Returns:
            Updated task data
        """
        if "id" not in task or "projectId" not in task:
            raise ValueError("Task must have 'id' and 'projectId'")

        url = "https://api.ticktick.com/api/v2/batch/task"
        payload = {
            "add": [],
            "update": [task],
            "delete": []
        }

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            result = response.json()
            id_map = result.get("id2etag", {})
            if task["id"] in id_map:
                task["etag"] = id_map[task["id"]]
            return task
        else:
            raise RuntimeError(f"Update task failed {response.status_code}: {response.text[:200]}")

    def delete_task(self, task_id: str, project_id: str) -> bool:
        """
        Delete a task.

        Args:
            task_id: The task ID
            project_id: The project ID containing the task

        Returns:
            True if successful
        """
        url = "https://api.ticktick.com/api/v2/batch/task"
        payload = {
            "add": [],
            "update": [],
            "delete": [{"taskId": task_id, "projectId": project_id}]
        }

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            return True
        else:
            raise RuntimeError(f"Delete task failed {response.status_code}: {response.text[:200]}")

    def complete_task(self, task_id: str, project_id: str) -> bool:
        """
        Mark a task as complete.

        Args:
            task_id: The task ID
            project_id: The project ID containing the task

        Returns:
            True if successful
        """
        url = f"https://api.ticktick.com/api/v2/project/{project_id}/task/{task_id}/complete"
        response = self.session.post(url)

        if response.status_code == 200:
            return True
        else:
            raise RuntimeError(f"Complete task failed {response.status_code}: {response.text[:200]}")

    def move_task(self, task_id: str, from_project_id: str, to_project_id: str) -> dict:
        """
        Move a task to a different project.

        Args:
            task_id: The task ID
            from_project_id: Current project ID
            to_project_id: Destination project ID

        Returns:
            Updated task data
        """
        # First get the task
        task = self.get_task_by_id(task_id)
        if not task:
            raise RuntimeError(f"Task not found: {task_id}")

        # Update the projectId
        task["projectId"] = to_project_id

        # Use the batch endpoint to move
        url = "https://api.ticktick.com/api/v2/batch/task"
        payload = {
            "add": [],
            "update": [task],
            "delete": []
        }

        response = self.session.post(url, json=payload)

        if response.status_code == 200:
            return task
        else:
            raise RuntimeError(f"Move task failed {response.status_code}: {response.text[:200]}")

    def make_subtask(self, child_task_id: str, parent_task_id: str) -> dict:
        """
        Make one task a subtask of another.

        Both tasks must be in the same project.

        Args:
            child_task_id: The task ID to become a subtask
            parent_task_id: The parent task ID

        Returns:
            Updated parent task
        """
        # Get both tasks
        child = self.get_task_by_id(child_task_id)
        parent = self.get_task_by_id(parent_task_id)

        if not child:
            raise RuntimeError(f"Child task not found: {child_task_id}")
        if not parent:
            raise RuntimeError(f"Parent task not found: {parent_task_id}")

        if child.get("projectId") != parent.get("projectId"):
            raise RuntimeError("Tasks must be in the same project")

        # Set parent relationship
        child["parentId"] = parent_task_id

        return self.update_task(child)

    # ==================== Special Operations ====================

    def get_task_activity(self, task_id: str, skip: int = 0) -> list:
        """
        Get task activity log.

        Args:
            task_id: The task ID
            skip: Number of entries to skip (pagination)

        Returns:
            List of activity entries
        """
        url = f"https://api.ticktick.com/api/v1/task/activity/{task_id}"
        params = {"skip": skip} if skip > 0 else None

        response = self.session.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")

    def pin_task(self, task_id: str) -> None:
        """Pin a task to the top of the list."""
        url = "https://api.ticktick.com/api/v2/batch/taskPin"
        payload = {"add": [task_id]}

        response = self.session.post(url, json=payload)

        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")

    def unpin_task(self, task_id: str) -> None:
        """Unpin a task."""
        url = "https://api.ticktick.com/api/v2/batch/taskPin"
        payload = {"delete": [task_id]}

        response = self.session.post(url, json=payload)

        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")

    def set_repeat_from(self, task_id: str, project_id: str, repeat_from: str) -> None:
        """
        Set whether a repeating task repeats from due date or completion date.

        Args:
            task_id: The task ID
            project_id: The project ID
            repeat_from: "0" for due date, "1" for completion date
        """
        url = f"https://api.ticktick.com/api/v2/task/{task_id}"
        payload = {
            "id": task_id,
            "projectId": project_id,
            "repeatFrom": repeat_from
        }

        response = self.session.post(url, json=payload)

        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")


# ==================== Backwards Compatibility ====================
# Keep the old singleton class for any code that still references it

class TickTickClientSingleton:
    """Legacy compatibility wrapper. Use UnofficialAPIClient instead."""

    @classmethod
    def get_client(cls):
        """Returns the ticktick-py client for legacy code."""
        instance = UnofficialAPIClient.get_instance()
        if instance and instance._ticktick_client:
            return instance._ticktick_client
        return None


# ==================== Module-level convenience functions ====================
# These maintain backwards compatibility with existing tool code

def get_client() -> Optional[UnofficialAPIClient]:
    """Get the unofficial API client instance."""
    return UnofficialAPIClient.get_instance()


def get_task_activity(task_id: str, skip: int = 0) -> list:
    """Get task activity log."""
    client = get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")
    return client.get_task_activity(task_id, skip)


def pin_task(task_id: str) -> None:
    """Pin a task."""
    client = get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")
    client.pin_task(task_id)


def unpin_task(task_id: str) -> None:
    """Unpin a task."""
    client = get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")
    client.unpin_task(task_id)


def set_repeat_from(task_id: str, project_id: str, repeat_from: str) -> None:
    """Set repeat from due date or completion date."""
    client = get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")
    client.set_repeat_from(task_id, project_id, repeat_from)
