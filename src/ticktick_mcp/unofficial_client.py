"""
TickTick Unofficial API Client for v2 endpoints.

This client handles features not available in the official OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs

Uses ticktick-py library for authentication (which works reliably).
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Any

from ticktick.oauth2 import OAuth2
from ticktick.api import TickTickClient

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

    Uses ticktick-py library for authentication.
    """

    BASE_URL = "https://api.ticktick.com/api/v2"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_cache_path: Optional[Path] = None
    ):
        """
        Initialize the unofficial client.

        Args:
            client_id: OAuth client ID
            client_secret: OAuth client secret
            redirect_uri: OAuth redirect URI
            token_cache_path: Path to cache OAuth tokens (optional)
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_cache_path = token_cache_path or Path.home() / ".config" / "ticktick-mcp" / ".token-oauth"
        self._ticktick_client: Optional[TickTickClient] = None

    @property
    def is_authenticated(self) -> bool:
        """Check if the client is authenticated."""
        return self._ticktick_client is not None

    def _create_oauth(self) -> OAuth2:
        """Create the OAuth2 session object."""
        return OAuth2(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            cache_path=str(self._token_cache_path)
        )

    def _sync_login(self, username: str, password: str) -> None:
        """Synchronous login using ticktick-py."""
        oauth = self._create_oauth()
        # ticktick-py handles the login internally
        self._ticktick_client = TickTickClient(username, password, oauth)
        logger.info("Successfully authenticated with ticktick-py")

    async def login(self, username: str, password: str) -> bool:
        """
        Authenticate with TickTick using username and password.

        Uses ticktick-py library for reliable authentication.

        Args:
            username: TickTick account email
            password: TickTick account password

        Returns:
            True if login successful

        Raises:
            TickTickUnofficialAPIError: If login fails
        """
        logger.info(f"Attempting login for user: {username} (password length: {len(password)})")

        try:
            # Run sync ticktick-py login in thread pool
            await asyncio.to_thread(self._sync_login, username, password)
            return True
        except Exception as e:
            logger.error(f"Login failed: {e}")
            raise TickTickUnofficialAPIError(
                status_code=401,
                message=f"Login failed: {str(e)}"
            )

    def _sync_request(
        self,
        method: str,
        url: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None
    ) -> Any:
        """Make a synchronous authenticated API request using ticktick-py's session."""
        if not self._ticktick_client:
            raise TickTickUnofficialAPIError(
                status_code=401,
                message="Not authenticated. Call login() first."
            )

        if method.upper() == "GET":
            return self._ticktick_client.http_get(url, params=params)
        elif method.upper() == "POST":
            return self._ticktick_client.http_post(url, json=json, params=params)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

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
        url = f"{self.BASE_URL}{endpoint}"

        try:
            result = await asyncio.to_thread(
                self._sync_request, method, url, json, params
            )
            logger.debug(f"{method} {endpoint} -> success")
            return result
        except Exception as e:
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

        # Activity log uses /api/v1/ not /api/v2/
        url = f"https://api.ticktick.com/api/v1/task/activity/{task_id}"
        params = {}
        if skip > 0:
            params["skip"] = skip

        try:
            result = await asyncio.to_thread(
                self._sync_request, "GET", url, None, params if params else None
            )
            return result if result else []
        except Exception as e:
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


async def init_unofficial_client(
    username: str,
    password: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    token_cache_path: Optional[Path] = None
) -> TickTickUnofficialClient:
    """
    Initialize and authenticate the unofficial client.

    Args:
        username: TickTick account email
        password: TickTick account password
        client_id: OAuth client ID
        client_secret: OAuth client secret
        redirect_uri: OAuth redirect URI
        token_cache_path: Path to cache OAuth tokens (optional)

    Returns:
        Authenticated TickTickUnofficialClient instance
    """
    global _unofficial_client
    _unofficial_client = TickTickUnofficialClient(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        token_cache_path=token_cache_path
    )
    await _unofficial_client.login(username, password)
    return _unofficial_client
