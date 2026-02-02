"""
TickTick Unofficial API Client.

Direct API access without ticktick-py dependency.
Handles authentication via username/password login and makes fresh API calls for all reads.
NO CACHING - every read fetches fresh data from the API.

This eliminates the stale cache problem that plagued the ticktick-py approach.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import (
    USERNAME,
    PASSWORD,
)

logger = logging.getLogger(__name__)


class UnofficialAPIClient:
    """
    Direct access to TickTick's unofficial v2 API.
    
    Key differences from the old ticktick-py based approach:
    - No caching: Every read makes a fresh API call
    - Self-contained auth: No ticktick-py dependency
    - Fresh data: get_all_tasks() always returns current state
    """
    
    BASE_URL = "https://api.ticktick.com/api/v2/"
    BATCH_CHECK_URL = BASE_URL + "batch/check/0"
    
    # Headers that mimic the web app - copied exactly from ticktick-py
    USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
    X_DEVICE = '{"platform":"web","os":"macOS 10.15.7","device":"Chrome 135.0.0.0","name":"","version":6260,"id":"674c46cf88bb9f5f73c3068a","channel":"website","campaign":"","websocket":""}'
    
    DEFAULT_HEADERS = {
        'origin': 'https://ticktick.com',
        'sec-ch-ua': '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'user-agent': USER_AGENT,
        'x-device': X_DEVICE,
    }
    
    _instance: Optional["UnofficialAPIClient"] = None
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize the client with authentication."""
        if UnofficialAPIClient._initialized:
            return
        
        self._client: Optional[httpx.Client] = None
        self._access_token: Optional[str] = None
        self._inbox_id: Optional[str] = None
        self._time_zone: Optional[str] = None
        self._profile_id: Optional[str] = None
        
        if not all([USERNAME, PASSWORD]):
            logger.error("TickTick credentials not found. Set TICKTICK_USERNAME and TICKTICK_PASSWORD.")
            UnofficialAPIClient._initialized = True
            return
        
        try:
            self._initialize_client()
            logger.info("Unofficial API client initialized successfully (no-cache mode)")
        except Exception as e:
            logger.error(f"Error initializing unofficial client: {e}", exc_info=True)
            self._client = None
        finally:
            UnofficialAPIClient._initialized = True
    
    def _initialize_client(self):
        """
        Set up authenticated httpx client.
        
        IMPORTANT: The OAuth2 token in .token-oauth is for the OFFICIAL API only.
        The unofficial API requires a SESSION token from /user/signon.
        We ALWAYS call _login() with username/password to get the session token.
        """
        # Create httpx client with default headers
        self._client = httpx.Client(
            headers=self.DEFAULT_HEADERS,
            timeout=30.0,
            follow_redirects=True
        )
        
        # Always do username/password login to get session token
        # The OAuth2 token in cache is for the official API, NOT the unofficial API
        self._login()
        
        # Load user settings (timezone, profile_id)
        self._load_settings()
        
        # Do initial sync to get inbox_id
        self._initial_sync()
    
    def _login(self):
        """Authenticate with username/password to get session token."""
        url = self.BASE_URL + "user/signon"
        params = {"wc": True, "remember": True}
        payload = {
            "username": USERNAME,
            "password": PASSWORD
        }
        
        logger.info(f"Logging in as {USERNAME}")
        response = self._client.post(url, json=payload, params=params)
        
        if response.status_code != 200:
            raise RuntimeError(f"Login failed: {response.status_code} - {response.text[:200]}")
        
        data = response.json()
        self._access_token = data.get("token")
        
        if not self._access_token:
            raise RuntimeError("Login response missing token")
        
        # Set the cookie for subsequent requests
        self._client.cookies.set("t", self._access_token)
        logger.info("Login successful, session token obtained")
    
    def _load_settings(self):
        """Load user settings (timezone, profile_id)."""
        url = self.BASE_URL + "user/preferences/settings"
        params = {"includeWeb": True}
        
        response = self._client.get(url, params=params)
        
        if response.status_code != 200:
            logger.warning(f"Failed to load settings: {response.status_code}")
            return
        
        data = response.json()
        self._time_zone = data.get("timeZone", "America/New_York")
        self._profile_id = data.get("id")
        logger.info(f"Loaded settings: timezone={self._time_zone}")
    
    def _initial_sync(self):
        """Do initial batch sync to get inbox_id and validate connection."""
        try:
            data = self._fetch_batch_check()
            self._inbox_id = data.get("inboxId")
            logger.info(f"Initial sync complete, inbox_id={self._inbox_id}")
        except Exception as e:
            logger.warning(f"Initial sync failed: {e}")
    
    def _fetch_batch_check(self) -> dict:
        """
        Fetch all data from the batch/check endpoint.
        
        This is the core sync endpoint that returns:
        - inboxId
        - projectProfiles (projects)
        - projectGroups (folders)
        - syncTaskBean.update (tasks)
        - tags
        """
        response = self._client.get(self.BATCH_CHECK_URL)
        
        if response.status_code != 200:
            raise RuntimeError(f"Batch check failed: {response.status_code} - {response.text[:200]}")
        
        return response.json()
    
    @classmethod
    def get_instance(cls) -> Optional["UnofficialAPIClient"]:
        """Get the singleton instance."""
        if not cls._initialized:
            cls()
        instance = cls._instance
        if instance and instance._client:
            return instance
        return None
    
    @property
    def client(self) -> httpx.Client:
        """Get the authenticated HTTP client."""
        if not self._client:
            raise RuntimeError("Unofficial client not initialized")
        return self._client
    
    @property
    def inbox_id(self) -> Optional[str]:
        """Get the inbox project ID."""
        return self._inbox_id
    
    # ==================== Data Fetch Operations (FRESH - NO CACHE) ====================
    
    def sync(self) -> dict:
        """
        Fetch fresh data from TickTick.
        
        Unlike the old ticktick-py approach, this doesn't populate a cache.
        It just returns the fresh data. Use get_all_tasks() etc. instead.
        
        Returns:
            Raw response from batch/check endpoint
        """
        return self._fetch_batch_check()
    
    def get_all_tasks(self) -> list[dict]:
        """
        Get all tasks - FRESH from API, no caching.
        
        Returns:
            List of task dicts
        """
        data = self._fetch_batch_check()
        tasks = data.get("syncTaskBean", {}).get("update", [])
        logger.debug(f"Fetched {len(tasks)} tasks (fresh)")
        return tasks
    
    def get_all_projects(self) -> list[dict]:
        """
        Get all projects - FRESH from API, no caching.
        
        Returns:
            List of project dicts
        """
        data = self._fetch_batch_check()
        projects = data.get("projectProfiles", [])
        logger.debug(f"Fetched {len(projects)} projects (fresh)")
        return projects
    
    def get_all_tags(self) -> list[dict]:
        """
        Get all tags - FRESH from API, no caching.
        
        Returns:
            List of tag dicts
        """
        data = self._fetch_batch_check()
        tags = data.get("tags", [])
        logger.debug(f"Fetched {len(tags)} tags (fresh)")
        return tags
    
    def get_task_by_id(self, task_id: str) -> Optional[dict]:
        """
        Get a specific task by ID - FRESH from API.
        
        Args:
            task_id: The task ID
            
        Returns:
            Task dict or None if not found
        """
        tasks = self.get_all_tasks()
        for task in tasks:
            if task.get("id") == task_id:
                return task
        return None
    
    def get_project_by_id(self, project_id: str) -> Optional[dict]:
        """
        Get a specific project by ID - FRESH from API.
        
        Args:
            project_id: The project ID
            
        Returns:
            Project dict or None if not found
        """
        projects = self.get_all_projects()
        for project in projects:
            if project.get("id") == project_id:
                return project
        return None
    
    def get_tasks_from_project(self, project_id: str, status: int = 0) -> list[dict]:
        """
        Get tasks from a specific project - FRESH from API.
        
        Args:
            project_id: The project ID
            status: 0=uncompleted, 2=completed (default: 0)
            
        Returns:
            List of matching task dicts
        """
        tasks = self.get_all_tasks()
        return [
            t for t in tasks
            if t.get("projectId") == project_id and t.get("status") == status
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
        task_id = uuid.uuid4().hex[:24]
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
        
        response = self.client.post(url, json=payload)
        
        if response.status_code == 200:
            result = response.json()
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
        
        response = self.client.post(url, json=payload)
        
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
        
        response = self.client.post(url, json=payload)
        
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
        response = self.client.post(url)
        
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
        # Get fresh task data
        task = self.get_task_by_id(task_id)
        if not task:
            raise RuntimeError(f"Task not found: {task_id}")
        
        # Update the projectId
        task["projectId"] = to_project_id
        
        return self.update_task(task)
    
    def make_subtask(self, child_task_id: str, parent_task_id: str) -> dict:
        """
        Make one task a subtask of another.
        
        Both tasks must be in the same project.
        
        Args:
            child_task_id: The task ID to become a subtask
            parent_task_id: The parent task ID
            
        Returns:
            Updated child task
        """
        # Get fresh data for both tasks
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
        
        response = self.client.get(url, params=params)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")
    
    def pin_task(self, task_id: str) -> None:
        """Pin a task to the top of the list."""
        url = "https://api.ticktick.com/api/v2/batch/taskPin"
        payload = {"add": [task_id]}
        
        response = self.client.post(url, json=payload)
        
        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")
    
    def unpin_task(self, task_id: str) -> None:
        """Unpin a task."""
        url = "https://api.ticktick.com/api/v2/batch/taskPin"
        payload = {"delete": [task_id]}
        
        response = self.client.post(url, json=payload)
        
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
        
        response = self.client.post(url, json=payload)
        
        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")


# ==================== Module-level convenience functions ====================

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
