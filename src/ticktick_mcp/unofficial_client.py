"""
TickTick Unofficial API Client for v2 endpoints.

This client handles features not available in the official OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs

Base URL: https://api.ticktick.com/api/v2/
Authentication: Session-based with username/password login
"""

import secrets
import httpx
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)


class TickTickUnofficialAPIError(Exception):
    """Exception raised for unofficial TickTick API errors."""

    def __init__(self, status_code: int, message: str, response_body: Any = None):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"TickTick Unofficial API Error {status_code}: {message}")


class TickTickUnofficialClient:
    """
    Client for the unofficial TickTick API v2.

    This is used for features not available in the official API:
    - Pin/unpin tasks via /api/v2/batch/order
    - Set repeatFrom via /api/v2/batch/task
    - Get task activity logs

    Authentication is session-based using username/password.
    """

    BASE_URL = "https://api.ticktick.com/api/v2"

    def __init__(self):
        """Initialize the unofficial client (not yet authenticated)."""
        self._client: Optional[httpx.AsyncClient] = None
        self._access_token: Optional[str] = None
        self._device_id = secrets.token_hex(10)

    @property
    def is_authenticated(self) -> bool:
        """Check if the client is authenticated."""
        return self._access_token is not None

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        # Match ticktick-py format exactly
        import json
        x_device = {
            "platform": "web",
            "os": "OS X",
            "device": "Firefox 95.0",
            "name": "unofficial api!",
            "version": 4531,
            "id": "6490" + self._device_id,
            "channel": "website",
            "campaign": "",
            "websocket": ""
        }
        return {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:95.0) Gecko/20100101 Firefox/95.0",
            "x-device": json.dumps(x_device),
        }

    def _get_cookies(self) -> dict[str, str]:
        """Get cookies for authenticated requests."""
        if self._access_token:
            return {"t": self._access_token}
        return {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._get_headers(),
                cookies=self._get_cookies(),
                timeout=30.0
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def login(self, username: str, password: str) -> bool:
        """
        Authenticate with TickTick using username and password.

        Args:
            username: TickTick account email
            password: TickTick account password

        Returns:
            True if login successful

        Raises:
            TickTickUnofficialAPIError: If login fails
        """
        client = await self._get_client()

        try:
            # Log the request (without exposing password)
            logger.info(f"Attempting login for user: {username} (password length: {len(password)})")

            response = await client.post(
                f"{self.BASE_URL}/user/signon",
                params={"wc": True, "remember": True},
                json={
                    "username": username,
                    "password": password
                }
            )

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                logger.error(f"Login failed with status {response.status_code}: {body}")
                raise TickTickUnofficialAPIError(
                    status_code=response.status_code,
                    message=f"Login failed: {body}",
                    response_body=body
                )

            data = response.json()
            self._access_token = data.get("token")

            if not self._access_token:
                raise TickTickUnofficialAPIError(
                    status_code=response.status_code,
                    message="No token in login response",
                    response_body=data
                )

            # Close and recreate client with auth cookies
            await self.close()
            logger.info("Successfully authenticated with unofficial API")
            return True

        except httpx.RequestError as e:
            logger.error(f"Login request error: {e}")
            raise TickTickUnofficialAPIError(
                status_code=0,
                message=f"Login request failed: {str(e)}"
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None
    ) -> Any:
        """
        Make an authenticated API request.

        Args:
            method: HTTP method (GET, POST)
            endpoint: API endpoint (without base URL)
            json: JSON body for POST requests
            params: Query parameters

        Returns:
            Response JSON or None for empty responses

        Raises:
            TickTickUnofficialAPIError: If the API returns an error
        """
        if not self.is_authenticated:
            raise TickTickUnofficialAPIError(
                status_code=401,
                message="Not authenticated. Call login() first."
            )

        client = await self._get_client()

        try:
            response = await client.request(
                method=method,
                url=f"{self.BASE_URL}{endpoint}",
                json=json,
                params=params,
                cookies=self._get_cookies()
            )

            logger.debug(f"{method} {endpoint} -> {response.status_code}")

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                raise TickTickUnofficialAPIError(
                    status_code=response.status_code,
                    message=f"API request failed: {endpoint}",
                    response_body=body
                )

            if response.status_code == 204 or not response.content:
                return None

            return response.json()

        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise TickTickUnofficialAPIError(
                status_code=0,
                message=f"Request failed: {str(e)}"
            )

    # ==================== Pin/Unpin Operations ====================

    async def pin_task(self, task_id: str) -> bool:
        """
        Pin a task to the top.

        Args:
            task_id: Task ID to pin

        Returns:
            True if successful
        """
        # Pin uses a large negative order value
        order_value = -36352603324416  # Standard pin order from HAR

        body = {
            "orderByType": {
                "taskPinned": {
                    "all": {
                        "changed": [{
                            "id": task_id,
                            "type": 1,  # 1 = add to pinned
                            "order": order_value
                        }],
                        "deleted": []
                    }
                }
            }
        }

        await self._request("POST", "/batch/order", json=body)
        return True

    async def unpin_task(self, task_id: str) -> bool:
        """
        Unpin a task.

        Args:
            task_id: Task ID to unpin

        Returns:
            True if successful
        """
        body = {
            "orderByType": {
                "taskPinned": {
                    "all": {
                        "changed": [],
                        "deleted": [task_id]
                    }
                }
            }
        }

        await self._request("POST", "/batch/order", json=body)
        return True

    # ==================== Batch Task Operations ====================

    async def batch_update_task(
        self,
        task_id: str,
        project_id: str,
        updates: dict
    ) -> dict:
        """
        Update a task using the batch endpoint.

        This endpoint supports fields not available in official API:
        - repeatFrom: "0" = from due date, "1" = from completion date
        - pinnedTime: pin time or "-1" for unpinned

        Args:
            task_id: Task ID
            project_id: Project ID
            updates: Dictionary of fields to update

        Returns:
            Response containing id2etag mapping
        """
        task_data = {
            "id": task_id,
            "projectId": project_id,
            **updates
        }

        body = {
            "add": [],
            "update": [task_data],
            "delete": [],
            "addAttachments": [],
            "updateAttachments": [],
            "deleteAttachments": []
        }

        return await self._request("POST", "/batch/task", json=body)

    async def set_repeat_from(
        self,
        task_id: str,
        project_id: str,
        repeat_from: str
    ) -> dict:
        """
        Set whether a repeating task repeats from due date or completion date.

        Args:
            task_id: Task ID
            project_id: Project ID
            repeat_from: "0" = from due date, "1" = from completion date

        Returns:
            Response containing id2etag mapping
        """
        if repeat_from not in ("0", "1"):
            raise ValueError("repeat_from must be '0' (due date) or '1' (completion date)")

        return await self.batch_update_task(
            task_id=task_id,
            project_id=project_id,
            updates={"repeatFrom": repeat_from}
        )

    # ==================== Activity Log Operations ====================

    async def get_task_activity(
        self,
        task_id: str,
        skip: int = 0
    ) -> list[dict]:
        """
        Get activity log for a specific task.

        Endpoint: GET /api/v1/task/activity/{taskId}

        Args:
            task_id: Task ID
            skip: Number of entries to skip (for pagination)

        Returns:
            List of activity log entries with fields like:
            - id: Activity ID
            - action: Action type (T_REPEAT, T_DUE, T_CREATE, etc.)
            - when: Timestamp
            - deviceChannel: Device used (web, android, ios)
            - startDate/startDateBefore: Date changes
            - dueDate/dueDateBefore: Due date changes
            - whoProfile: User info
        """
        if not self.is_authenticated:
            raise TickTickUnofficialAPIError(
                status_code=401,
                message="Not authenticated. Call login() first."
            )

        client = await self._get_client()

        # Activity log uses /api/v1/ not /api/v2/
        url = f"https://api.ticktick.com/api/v1/task/activity/{task_id}"
        params = {}
        if skip > 0:
            params["skip"] = skip

        try:
            response = await client.request(
                method="GET",
                url=url,
                params=params if params else None,
                cookies=self._get_cookies()
            )

            logger.debug(f"GET {url} -> {response.status_code}")

            if response.status_code >= 400:
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                raise TickTickUnofficialAPIError(
                    status_code=response.status_code,
                    message=f"API request failed: {url}",
                    response_body=body
                )

            if response.status_code == 204 or not response.content:
                return []

            return response.json()

        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise TickTickUnofficialAPIError(
                status_code=0,
                message=f"Request failed: {str(e)}"
            )


# Singleton instance
_unofficial_client: Optional[TickTickUnofficialClient] = None


def get_unofficial_client() -> Optional[TickTickUnofficialClient]:
    """Get the global unofficial client instance."""
    return _unofficial_client


async def init_unofficial_client(username: str, password: str) -> TickTickUnofficialClient:
    """
    Initialize and authenticate the unofficial client.

    Args:
        username: TickTick account email
        password: TickTick account password

    Returns:
        Authenticated TickTickUnofficialClient instance
    """
    global _unofficial_client
    _unofficial_client = TickTickUnofficialClient()
    await _unofficial_client.login(username, password)
    return _unofficial_client
