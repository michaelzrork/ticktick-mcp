"""
TickTick Unofficial API Client for v2 endpoints.

This client handles features not available in the official OpenAPI v1:
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)
- Task activity logs

Uses ticktick-py library for authentication (which works reliably).
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, Any

from ticktick.oauth2 import OAuth2
from ticktick.api import TickTickClient

logger = logging.getLogger(__name__)


# ============== Monkey patch for ticktick-py ==============
# TickTick changed their API endpoint from /user/signin to /user/signon
# See: https://github.com/lazeroffmichael/ticktick-py/issues/56
def _patched_login(self, username: str, password: str) -> None:
    """Patched login method using updated endpoint and headers."""
    import secrets

    # Updated headers matching current browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
        'x-device': '{"platform":"web","os":"Windows 10","device":"Firefox 123.0","name":"","version":4576,"id":"' + secrets.token_hex(12) + '","channel":"website","campaign":"","websocket":""}'
    }

    url = self.BASE_URL + 'user/signon'  # Changed from signin to signon
    user_info = {'username': username, 'password': password}
    parameters = {'wc': True, 'remember': True}

    response = self.http_post(url, json=user_info, params=parameters, headers=headers)
    self.access_token = response['token']
    self.cookies['t'] = self.access_token


# Apply the monkey patch
TickTickClient._login = _patched_login
# ============== End monkey patch ==============


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
        access_token: Optional[str] = None,
        token_cache_path: Optional[Path] = None
    ):
        """
        Initialize the unofficial client.

        Args:
            client_id: OAuth client ID
            client_secret: OAuth client secret
            redirect_uri: OAuth redirect URI
            access_token: Pre-obtained OAuth access token (for cloud deployment)
            token_cache_path: Path to cache OAuth tokens (optional)
        """
        logger.info("=== TickTickUnofficialClient.__init__() ===")
        logger.info(f"  client_id: {client_id[:8]}*** (length: {len(client_id)})")
        logger.info(f"  client_secret: ***{client_secret[-4:] if client_secret else 'None'}")
        logger.info(f"  redirect_uri: {redirect_uri}")
        logger.info(f"  access_token provided: {bool(access_token)} (length: {len(access_token) if access_token else 0})")
        logger.info(f"  token_cache_path provided: {token_cache_path}")

        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._access_token = access_token
        self._token_cache_path = token_cache_path or Path.home() / ".config" / "ticktick-mcp" / ".token-oauth"
        self._ticktick_client: Optional[TickTickClient] = None

        logger.info(f"  Final token_cache_path: {self._token_cache_path}")
        logger.info(f"  Token cache path exists: {self._token_cache_path.exists()}")
        if self._token_cache_path.exists():
            try:
                cache_content = self._token_cache_path.read_text()
                cache_data = json.loads(cache_content)
                logger.info(f"  Existing cache keys: {list(cache_data.keys())}")
                logger.info(f"  Cache has access_token: {bool(cache_data.get('access_token'))}")
                logger.info(f"  Cache expire_time: {cache_data.get('expire_time')}")
            except Exception as e:
                logger.warning(f"  Failed to read existing cache: {e}")

        # Pre-populate the cache file if access_token is provided
        # This prevents ticktick-py from triggering interactive OAuth flow
        if access_token:
            logger.info("  Pre-populating token cache with provided access_token...")
            self._write_token_cache(access_token)
        else:
            logger.info("  No access_token provided, skipping cache pre-population")

    @property
    def is_authenticated(self) -> bool:
        """Check if the client is authenticated."""
        return self._ticktick_client is not None

    def _write_token_cache(self, access_token: str) -> None:
        """
        Write the OAuth token to cache file for ticktick-py to use.

        This prevents the interactive OAuth flow from triggering.
        """
        logger.info("=== _write_token_cache() ===")
        logger.info(f"  access_token length: {len(access_token)}")
        logger.info(f"  access_token preview: {access_token[:20]}***")

        try:
            cache_path = Path(self._token_cache_path)
            logger.info(f"  cache_path: {cache_path}")
            logger.info(f"  cache_path.parent: {cache_path.parent}")
            logger.info(f"  cache_path.parent.exists(): {cache_path.parent.exists()}")

            cache_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"  Created parent directory (or already exists)")

            # Token format expected by ticktick-py
            token_info = {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": 15552000,  # ~6 months
                "scope": "tasks:read tasks:write",
                "expire_time": int(time.time()) + 15552000,
            }

            logger.info(f"  Token info keys: {list(token_info.keys())}")
            logger.info(f"  Token expire_time: {token_info['expire_time']}")

            cache_path.write_text(json.dumps(token_info))
            logger.info(f"  Successfully wrote OAuth token to cache: {cache_path}")

            # Verify write
            if cache_path.exists():
                logger.info(f"  Verified: cache file exists, size: {cache_path.stat().st_size} bytes")
            else:
                logger.error(f"  ERROR: cache file does not exist after write!")
        except Exception as e:
            logger.error(f"  Failed to write token cache: {e}")
            logger.exception("  Full traceback:")

    def _create_oauth(self) -> OAuth2:
        """Create the OAuth2 session object."""
        logger.info("=== _create_oauth() ===")
        logger.info(f"  client_id: {self._client_id[:8]}***")
        logger.info(f"  client_secret: ***{self._client_secret[-4:]}")
        logger.info(f"  redirect_uri: {self._redirect_uri}")
        logger.info(f"  cache_path: {self._token_cache_path}")

        oauth = OAuth2(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            cache_path=str(self._token_cache_path)
        )
        logger.info(f"  OAuth2 object created: {oauth}")
        return oauth

    def _sync_login(self, username: str, password: str) -> None:
        """Synchronous login using ticktick-py."""
        logger.info("=== _sync_login() ===")
        logger.info(f"  username: {username[:3]}***")
        logger.info(f"  password length: {len(password)}")

        logger.info("  Step 1: Creating OAuth2 object...")
        oauth = self._create_oauth()

        logger.info("  Step 2: Calling oauth.get_access_token()...")
        try:
            token = oauth.get_access_token()
            logger.info(f"  Step 2 complete: got token (type: {type(token).__name__})")
            if token:
                logger.info(f"  Token preview: {str(token)[:30]}***")
        except Exception as e:
            logger.error(f"  Step 2 FAILED: oauth.get_access_token() raised: {e}")
            logger.exception("  Full traceback:")
            raise

        logger.info("  Step 3: Creating TickTickClient...")
        try:
            self._ticktick_client = TickTickClient(username, password, oauth)
            logger.info(f"  Step 3 complete: TickTickClient created")
            logger.info(f"  _ticktick_client type: {type(self._ticktick_client).__name__}")
            logger.info(f"  _ticktick_client has access_token: {hasattr(self._ticktick_client, 'access_token')}")
            if hasattr(self._ticktick_client, 'access_token'):
                logger.info(f"  access_token set: {bool(self._ticktick_client.access_token)}")
        except Exception as e:
            logger.error(f"  Step 3 FAILED: TickTickClient() raised: {e}")
            logger.exception("  Full traceback:")
            raise

        logger.info("  Successfully authenticated with ticktick-py!")

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
        logger.info("=== login() [async] ===")
        logger.info(f"  username: {username[:3]}*** (full length: {len(username)})")
        logger.info(f"  password length: {len(password)}")

        try:
            logger.info("  Calling asyncio.to_thread(_sync_login)...")
            await asyncio.to_thread(self._sync_login, username, password)
            logger.info("  asyncio.to_thread completed successfully!")
            logger.info(f"  is_authenticated: {self.is_authenticated}")
            return True
        except Exception as e:
            logger.error(f"  Login FAILED: {e}")
            logger.exception("  Full traceback:")
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
        logger.info("=== _sync_request() ===")
        logger.info(f"  method: {method}")
        logger.info(f"  url: {url}")
        logger.info(f"  json: {json}")
        logger.info(f"  params: {params}")
        logger.info(f"  _ticktick_client: {self._ticktick_client}")

        if not self._ticktick_client:
            logger.error("  ERROR: Not authenticated - _ticktick_client is None")
            raise TickTickUnofficialAPIError(
                status_code=401,
                message="Not authenticated. Call login() first."
            )

        logger.info(f"  Using _ticktick_client: {type(self._ticktick_client).__name__}")
        logger.info(f"  _ticktick_client has http_get: {hasattr(self._ticktick_client, 'http_get')}")
        logger.info(f"  _ticktick_client has http_post: {hasattr(self._ticktick_client, 'http_post')}")

        try:
            if method.upper() == "GET":
                logger.info("  Calling http_get...")
                result = self._ticktick_client.http_get(url, params=params)
                logger.info(f"  http_get returned: {type(result).__name__}")
                logger.info(f"  Result preview: {str(result)[:200]}...")
                return result
            elif method.upper() == "POST":
                logger.info("  Calling http_post...")
                result = self._ticktick_client.http_post(url, json=json, params=params)
                logger.info(f"  http_post returned: {type(result).__name__}")
                logger.info(f"  Result preview: {str(result)[:200]}...")
                return result
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
        except Exception as e:
            logger.error(f"  Request FAILED: {e}")
            logger.exception("  Full traceback:")
            raise

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
        logger.info("=== _request() [async] ===")
        logger.info(f"  method: {method}")
        logger.info(f"  endpoint: {endpoint}")
        logger.info(f"  json: {json}")
        logger.info(f"  params: {params}")

        url = f"{self.BASE_URL}{endpoint}"
        logger.info(f"  Full URL: {url}")

        try:
            logger.info("  Calling asyncio.to_thread(_sync_request)...")
            result = await asyncio.to_thread(
                self._sync_request, method, url, json, params
            )
            logger.info(f"  Request completed successfully!")
            logger.info(f"  Result type: {type(result).__name__}")
            return result
        except Exception as e:
            logger.error(f"  Request FAILED: {e}")
            logger.exception("  Full traceback:")
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
        logger.info("=== pin_task() ===")
        logger.info(f"  task_id: {task_id}")
        logger.info(f"  is_authenticated: {self.is_authenticated}")

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
        logger.info(f"  Request body: {body}")

        result = await self._request("POST", "/batch/order", json=body)
        logger.info(f"  pin_task completed successfully! Result: {result}")
        return True

    async def unpin_task(self, task_id: str) -> bool:
        """
        Unpin a task.

        Args:
            task_id: Task ID to unpin

        Returns:
            True if successful
        """
        logger.info("=== unpin_task() ===")
        logger.info(f"  task_id: {task_id}")
        logger.info(f"  is_authenticated: {self.is_authenticated}")

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
        logger.info(f"  Request body: {body}")

        result = await self._request("POST", "/batch/order", json=body)
        logger.info(f"  unpin_task completed successfully! Result: {result}")
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
        logger.info("=== batch_update_task() ===")
        logger.info(f"  task_id: {task_id}")
        logger.info(f"  project_id: {project_id}")
        logger.info(f"  updates: {updates}")
        logger.info(f"  is_authenticated: {self.is_authenticated}")

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
        logger.info(f"  Request body: {body}")

        result = await self._request("POST", "/batch/task", json=body)
        logger.info(f"  batch_update_task completed! Result: {result}")
        return result

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
        logger.info("=== set_repeat_from() ===")
        logger.info(f"  task_id: {task_id}")
        logger.info(f"  project_id: {project_id}")
        logger.info(f"  repeat_from: {repeat_from}")

        if repeat_from not in ("0", "1"):
            logger.error(f"  Invalid repeat_from value: {repeat_from}")
            raise ValueError("repeat_from must be '0' (due date) or '1' (completion date)")

        result = await self.batch_update_task(
            task_id=task_id,
            project_id=project_id,
            updates={"repeatFrom": repeat_from}
        )
        logger.info(f"  set_repeat_from completed! Result: {result}")
        return result

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
        logger.info("=== get_task_activity() ===")
        logger.info(f"  task_id: {task_id}")
        logger.info(f"  skip: {skip}")
        logger.info(f"  is_authenticated: {self.is_authenticated}")

        if not self.is_authenticated:
            logger.error("  ERROR: Not authenticated")
            raise TickTickUnofficialAPIError(
                status_code=401,
                message="Not authenticated. Call login() first."
            )

        # Activity log uses /api/v1/ not /api/v2/
        url = f"https://api.ticktick.com/api/v1/task/activity/{task_id}"
        params = {}
        if skip > 0:
            params["skip"] = skip

        logger.info(f"  Full URL: {url}")
        logger.info(f"  params: {params}")

        try:
            logger.info("  Calling asyncio.to_thread(_sync_request)...")
            result = await asyncio.to_thread(
                self._sync_request, "GET", url, None, params if params else None
            )
            logger.info(f"  Request completed! Result type: {type(result).__name__}")
            logger.info(f"  Result count: {len(result) if result else 0}")
            return result if result else []
        except Exception as e:
            logger.error(f"  Request FAILED: {e}")
            logger.exception("  Full traceback:")
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
    access_token: Optional[str] = None,
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
        access_token: Pre-obtained OAuth access token (for cloud deployment)
        token_cache_path: Path to cache OAuth tokens (optional)

    Returns:
        Authenticated TickTickUnofficialClient instance
    """
    logger.info("=== init_unofficial_client() ===")
    logger.info(f"  username: {username[:3]}***")
    logger.info(f"  password length: {len(password)}")
    logger.info(f"  client_id: {client_id[:8]}***")
    logger.info(f"  client_secret: ***{client_secret[-4:]}")
    logger.info(f"  redirect_uri: {redirect_uri}")
    logger.info(f"  access_token provided: {bool(access_token)}")
    logger.info(f"  token_cache_path: {token_cache_path}")

    global _unofficial_client
    logger.info("  Creating TickTickUnofficialClient...")
    _unofficial_client = TickTickUnofficialClient(
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
        redirect_uri=redirect_uri,
        token_cache_path=token_cache_path
    )
    logger.info("  TickTickUnofficialClient created, now calling login...")
    await _unofficial_client.login(username, password)
    logger.info("  Login complete! Returning client.")
    logger.info(f"  Client is_authenticated: {_unofficial_client.is_authenticated}")
    return _unofficial_client
