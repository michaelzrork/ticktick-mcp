"""
TickTick Unofficial API Client using ticktick-py library.

This provides access to unofficial v2 API features:
- Task activity logs
- Pin/unpin tasks
- Set repeatFrom (repeat from due date vs completion date)

Uses the singleton pattern to ensure only one client instance exists.
"""

import logging
from typing import Optional

from ticktick.api import TickTickClient
from ticktick.oauth2 import OAuth2

# Import config variables and the token cache path
from .config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD, dotenv_dir_path

logger = logging.getLogger(__name__)


class TickTickClientSingleton:
    """Singleton class to manage the TickTickClient instance."""
    _instance: Optional[TickTickClient] = None
    _initialized: bool = False

    def __new__(cls):
        return super(TickTickClientSingleton, cls).__new__(cls)

    def __init__(self):
        """Initializes the TickTick client, ensuring it runs only once."""
        if TickTickClientSingleton._initialized:
            return  # Already initialized

        if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD]):
            logger.error("TickTick credentials not found. Ensure .env file or env vars are set.")
            TickTickClientSingleton._instance = None
            TickTickClientSingleton._initialized = True
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

            # This loads the token from cache if available
            auth_client.get_access_token()
            logger.info("OAuth2 token loaded from cache")

            logger.info(f"Initializing TickTickClient with username: {USERNAME}")
            client = TickTickClient(USERNAME, PASSWORD, auth_client)
            logger.info("TickTick client initialized successfully")

            TickTickClientSingleton._instance = client

        except Exception as e:
            logger.error(f"Error initializing TickTick client: {e}", exc_info=True)
            TickTickClientSingleton._instance = None
        finally:
            TickTickClientSingleton._initialized = True

    @classmethod
    def get_client(cls) -> Optional[TickTickClient]:
        """Returns the initialized TickTick client instance."""
        if not cls._initialized:
            cls()  # Trigger initialization
        if cls._instance is None:
            logger.warning("get_client() called, but TickTick client failed to initialize.")
        return cls._instance


# --- Convenience functions for API calls ---

def get_task_activity(task_id: str, skip: int = 0) -> list:
    """
    Get task activity log.

    Args:
        task_id: The task ID
        skip: Number of entries to skip (for pagination)

    Returns:
        List of activity entries
    """
    client = TickTickClientSingleton.get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")

    url = f"https://api.ticktick.com/api/v1/task/activity/{task_id}"
    params = {"skip": skip} if skip > 0 else None

    response = client._session.get(url, params=params)

    if response.status_code == 200:
        return response.json()
    else:
        raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")


def pin_task(task_id: str) -> None:
    """
    Pin a task to the top of the list.

    Args:
        task_id: The task ID to pin
    """
    client = TickTickClientSingleton.get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")

    url = "https://api.ticktick.com/api/v2/batch/taskPin"
    payload = {"add": [task_id]}

    response = client._session.post(url, json=payload)

    if response.status_code != 200:
        raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")


def unpin_task(task_id: str) -> None:
    """
    Unpin a task.

    Args:
        task_id: The task ID to unpin
    """
    client = TickTickClientSingleton.get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")

    url = "https://api.ticktick.com/api/v2/batch/taskPin"
    payload = {"delete": [task_id]}

    response = client._session.post(url, json=payload)

    if response.status_code != 200:
        raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")


def set_repeat_from(task_id: str, project_id: str, repeat_from: str) -> None:
    """
    Set whether a repeating task repeats from due date or completion date.

    Args:
        task_id: The task ID
        project_id: The project ID containing the task
        repeat_from: "0" for due date, "1" for completion date
    """
    client = TickTickClientSingleton.get_client()
    if not client:
        raise RuntimeError("Unofficial client not initialized")

    url = f"https://api.ticktick.com/api/v2/task/{task_id}"
    payload = {
        "id": task_id,
        "projectId": project_id,
        "repeatFrom": repeat_from
    }

    response = client._session.post(url, json=payload)

    if response.status_code != 200:
        raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")
