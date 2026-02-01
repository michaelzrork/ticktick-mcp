"""
TickTick API Client using Official OpenAPI v1 endpoints.

Base URL: https://api.ticktick.com/open/v1/
Documentation: https://developer.ticktick.com/docs#/openapi
"""

import httpx
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


class TickTickAPIError(Exception):
    """Exception raised for TickTick API errors."""

    def __init__(self, status_code: int, message: str, response_body: Any = None):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"TickTick API Error {status_code}: {message}")


class TickTickClient:
    """
    Client for the official TickTick OpenAPI v1.

    All methods are async and use httpx for HTTP requests.

    Endpoints implemented:
    - Tasks: get, create, update, complete, delete
    - Projects: list, get, get_data (with tasks), create, update, delete
    """

    BASE_URL = "https://api.ticktick.com/open/v1"

    def __init__(self, access_token: str, user_id: Optional[str] = None):
        """
        Initialize the TickTick client.

        Args:
            access_token: OAuth2 access token
            user_id: User ID (needed for Inbox access as inbox{userId})
        """
        self.access_token = access_token
        self.user_id = user_id
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def inbox_id(self) -> Optional[str]:
        """Get the Inbox project ID (inbox{userId})."""
        if self.user_id:
            return f"inbox{self.user_id}"
        return None

    def _headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers=self._headers(),
                timeout=30.0
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None
    ) -> Any:
        """
        Make an API request.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint (without base URL)
            json: JSON body for POST requests
            params: Query parameters

        Returns:
            Response JSON or None for empty responses

        Raises:
            TickTickAPIError: If the API returns an error
        """
        client = await self._get_client()

        try:
            response = await client.request(
                method=method,
                url=endpoint,
                json=json,
                params=params
            )

            # Log the request/response for debugging
            logger.debug(f"{method} {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                raise TickTickAPIError(
                    status_code=response.status_code,
                    message=f"API request failed: {endpoint}",
                    response_body=body
                )

            # Some endpoints return empty response (204 No Content, etc.)
            if response.status_code == 204 or not response.content:
                return None

            return response.json()

        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise TickTickAPIError(
                status_code=0,
                message=f"Request failed: {str(e)}"
            )

    # ==================== Project Operations ====================

    async def get_projects(self) -> list[dict]:
        """
        Get all projects (excludes Inbox).

        Returns:
            List of project objects
        """
        result = await self._request("GET", "/project")
        return result if result else []

    async def get_project(self, project_id: str) -> dict:
        """
        Get a project by ID.

        Args:
            project_id: Project ID

        Returns:
            Project object
        """
        return await self._request("GET", f"/project/{project_id}")

    async def get_project_with_data(self, project_id: str) -> dict:
        """
        Get a project with all its tasks.

        This is the recommended way to bulk-fetch tasks for a project.

        Args:
            project_id: Project ID (use inbox{userId} for Inbox)

        Returns:
            Project object with 'tasks' array
        """
        return await self._request("GET", f"/project/{project_id}/data")

    async def create_project(
        self,
        name: str,
        color: Optional[str] = None,
        view_mode: Optional[str] = None,
        kind: Optional[str] = None,
        sort_order: Optional[int] = None
    ) -> dict:
        """
        Create a new project.

        Args:
            name: Project name (required)
            color: Color hex code (e.g., "#F18181")
            view_mode: View mode ("list", "kanban", "timeline")
            kind: Project kind ("TASK" or "NOTE")
            sort_order: Sort order value

        Returns:
            Created project object
        """
        body = {"name": name}
        if color:
            body["color"] = color
        if view_mode:
            body["viewMode"] = view_mode
        if kind:
            body["kind"] = kind
        if sort_order is not None:
            body["sortOrder"] = sort_order

        return await self._request("POST", "/project", json=body)

    async def update_project(
        self,
        project_id: str,
        name: Optional[str] = None,
        color: Optional[str] = None,
        view_mode: Optional[str] = None,
        kind: Optional[str] = None,
        sort_order: Optional[int] = None
    ) -> dict:
        """
        Update a project.

        Args:
            project_id: Project ID
            name: New project name
            color: New color hex code
            view_mode: New view mode
            kind: New project kind
            sort_order: New sort order

        Returns:
            Updated project object
        """
        body = {}
        if name:
            body["name"] = name
        if color:
            body["color"] = color
        if view_mode:
            body["viewMode"] = view_mode
        if kind:
            body["kind"] = kind
        if sort_order is not None:
            body["sortOrder"] = sort_order

        return await self._request("POST", f"/project/{project_id}", json=body)

    async def delete_project(self, project_id: str) -> bool:
        """
        Delete a project.

        Args:
            project_id: Project ID

        Returns:
            True if successful
        """
        await self._request("DELETE", f"/project/{project_id}")
        return True

    # ==================== Task Operations ====================

    async def get_task(self, project_id: str, task_id: str) -> dict:
        """
        Get a task by ID.

        Args:
            project_id: Project ID containing the task
            task_id: Task ID

        Returns:
            Task object
        """
        return await self._request("GET", f"/project/{project_id}/task/{task_id}")

    async def create_task(
        self,
        title: str,
        project_id: str,
        content: Optional[str] = None,
        desc: Optional[str] = None,
        is_all_day: Optional[bool] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        time_zone: Optional[str] = None,
        reminders: Optional[list[str]] = None,
        repeat_flag: Optional[str] = None,
        priority: Optional[int] = None,
        sort_order: Optional[int] = None,
        items: Optional[list[dict]] = None,
        tags: Optional[list[str]] = None
    ) -> dict:
        """
        Create a new task.

        Args:
            title: Task title (required)
            project_id: Project ID (required, use inbox{userId} for Inbox)
            content: Task content/notes
            desc: Description for checklist
            is_all_day: Whether it's an all-day task
            start_date: Start date in "yyyy-MM-dd'T'HH:mm:ssZ" format
            due_date: Due date in "yyyy-MM-dd'T'HH:mm:ssZ" format
            time_zone: Timezone (e.g., "America/New_York")
            reminders: List of reminders (e.g., ["TRIGGER:PT0S", "TRIGGER:-PT30M"])
            repeat_flag: Recurrence rule (e.g., "RRULE:FREQ=DAILY;INTERVAL=1")
            priority: Priority (0=None, 1=Low, 3=Medium, 5=High)
            sort_order: Sort order value
            items: List of subtasks with {title, startDate, isAllDay, etc.}
            tags: List of tags

        Returns:
            Created task object
        """
        body: dict[str, Any] = {
            "title": title,
            "projectId": project_id
        }

        if content is not None:
            body["content"] = content
        if desc is not None:
            body["desc"] = desc
        if is_all_day is not None:
            body["isAllDay"] = is_all_day
        if start_date is not None:
            body["startDate"] = start_date
        if due_date is not None:
            body["dueDate"] = due_date
        if time_zone is not None:
            body["timeZone"] = time_zone
        if reminders is not None:
            body["reminders"] = reminders
        if repeat_flag is not None:
            body["repeatFlag"] = repeat_flag
        if priority is not None:
            body["priority"] = priority
        if sort_order is not None:
            body["sortOrder"] = sort_order
        if items is not None:
            body["items"] = items
        if tags is not None:
            body["tags"] = tags

        return await self._request("POST", "/task", json=body)

    async def update_task(
        self,
        task_id: str,
        project_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        desc: Optional[str] = None,
        is_all_day: Optional[bool] = None,
        start_date: Optional[str] = None,
        due_date: Optional[str] = None,
        time_zone: Optional[str] = None,
        reminders: Optional[list[str]] = None,
        repeat_flag: Optional[str] = None,
        priority: Optional[int] = None,
        sort_order: Optional[int] = None,
        items: Optional[list[dict]] = None,
        tags: Optional[list[str]] = None
    ) -> dict:
        """
        Update an existing task.

        Note: The request body MUST include both 'id' and 'projectId'.

        Args:
            task_id: Task ID (required)
            project_id: Project ID (required)
            title: New task title
            content: New task content
            desc: New description
            is_all_day: Whether it's an all-day task
            start_date: New start date
            due_date: New due date
            time_zone: New timezone
            reminders: New reminders list
            repeat_flag: New recurrence rule
            priority: New priority
            sort_order: New sort order
            items: New subtasks list
            tags: New tags list

        Returns:
            Updated task object
        """
        body: dict[str, Any] = {
            "id": task_id,
            "projectId": project_id
        }

        if title is not None:
            body["title"] = title
        if content is not None:
            body["content"] = content
        if desc is not None:
            body["desc"] = desc
        if is_all_day is not None:
            body["isAllDay"] = is_all_day
        if start_date is not None:
            body["startDate"] = start_date
        if due_date is not None:
            body["dueDate"] = due_date
        if time_zone is not None:
            body["timeZone"] = time_zone
        if reminders is not None:
            body["reminders"] = reminders
        if repeat_flag is not None:
            body["repeatFlag"] = repeat_flag
        if priority is not None:
            body["priority"] = priority
        if sort_order is not None:
            body["sortOrder"] = sort_order
        if items is not None:
            body["items"] = items
        if tags is not None:
            body["tags"] = tags

        return await self._request("POST", f"/task/{task_id}", json=body)

    async def complete_task(self, project_id: str, task_id: str) -> bool:
        """
        Mark a task as complete.

        Args:
            project_id: Project ID containing the task
            task_id: Task ID

        Returns:
            True if successful
        """
        await self._request("POST", f"/project/{project_id}/task/{task_id}/complete")
        return True

    async def delete_task(self, project_id: str, task_id: str) -> bool:
        """
        Delete a task.

        Args:
            project_id: Project ID containing the task
            task_id: Task ID

        Returns:
            True if successful
        """
        await self._request("DELETE", f"/project/{project_id}/task/{task_id}")
        return True

    # ==================== Convenience Methods ====================

    async def get_inbox_data(self) -> dict:
        """
        Get all Inbox tasks.

        Requires user_id to be set during initialization.

        Returns:
            Inbox project data with tasks

        Raises:
            ValueError: If user_id is not set
        """
        if not self.inbox_id:
            raise ValueError("user_id must be set to access Inbox")
        return await self.get_project_with_data(self.inbox_id)

    async def get_all_tasks(self) -> list[dict]:
        """
        Get all tasks from all projects (including Inbox if user_id is set).

        Returns:
            List of all tasks across all projects
        """
        all_tasks = []

        # Get tasks from Inbox if user_id is set
        if self.inbox_id:
            try:
                inbox_data = await self.get_project_with_data(self.inbox_id)
                all_tasks.extend(inbox_data.get("tasks", []))
            except TickTickAPIError as e:
                logger.warning(f"Failed to get Inbox tasks: {e}")

        # Get all other projects and their tasks
        projects = await self.get_projects()
        for project in projects:
            try:
                project_data = await self.get_project_with_data(project["id"])
                all_tasks.extend(project_data.get("tasks", []))
            except TickTickAPIError as e:
                logger.warning(f"Failed to get tasks for project {project['id']}: {e}")

        return all_tasks


# Singleton instance
_client: Optional[TickTickClient] = None


def get_ticktick_client() -> Optional[TickTickClient]:
    """Get the global TickTick client instance."""
    return _client


def init_ticktick_client(access_token: str, user_id: Optional[str] = None) -> TickTickClient:
    """
    Initialize the global TickTick client.

    Args:
        access_token: OAuth2 access token
        user_id: User ID for Inbox access

    Returns:
        Initialized TickTickClient instance
    """
    global _client
    _client = TickTickClient(access_token=access_token, user_id=user_id)
    return _client
