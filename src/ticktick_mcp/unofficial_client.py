"""
TickTick Unofficial API Client.

Direct API access without ticktick-py dependency.
Handles authentication via username/password login and makes fresh API calls for all reads.
NO CACHING - every read fetches fresh data from the API.

This eliminates the stale cache problem that plagued the ticktick-py approach.
"""

import logging
import threading
import time
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
    - Generic call_api() method for all API operations
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
    _lock = threading.RLock()

    # A failed login must not be permanent. TickTick rate-limits /user/signon
    # (429), so a restart during a busy window used to poison the process for its
    # whole lifetime: the old code latched an "initialized" flag in a finally
    # block and never retried, and every tool then reported "not configured".
    RETRY_BASE_SECONDS = 30
    RETRY_MAX_SECONDS = 900

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Set up state and make a first connection attempt."""
        with UnofficialAPIClient._lock:
            if getattr(self, "_constructed", False):
                return
            self._constructed = True

            self._client: Optional[httpx.Client] = None
            self._access_token: Optional[str] = None
            self._inbox_id: Optional[str] = None
            self._time_zone: Optional[str] = None
            self._profile_id: Optional[str] = None

            self._last_error: Optional[str] = None
            self._failed_attempts = 0
            self._next_retry_at = 0.0

        self.ensure_connected()

    # ==================== Connection lifecycle ====================

    @staticmethod
    def credentials_configured() -> bool:
        """Whether a username and password were supplied at all."""
        return bool(USERNAME and PASSWORD)

    def ensure_connected(self) -> bool:
        """
        Connect if not already connected, honouring the retry backoff.

        Returns True when the client is usable. Safe to call on every request:
        once connected it is just a None check, and while backing off it does not
        touch the network.
        """
        with UnofficialAPIClient._lock:
            if self._client is not None:
                return True

            if not self.credentials_configured():
                self._last_error = (
                    "TICKTICK_USERNAME and TICKTICK_PASSWORD are not set"
                )
                return False

            if time.monotonic() < self._next_retry_at:
                return False

            try:
                self._initialize_client()
            except Exception as e:
                self._client = None
                self._failed_attempts += 1
                delay = min(
                    self.RETRY_BASE_SECONDS * (2 ** (self._failed_attempts - 1)),
                    self.RETRY_MAX_SECONDS,
                )
                self._next_retry_at = time.monotonic() + delay
                self._last_error = str(e)
                logger.error(
                    f"Unofficial API login failed (attempt {self._failed_attempts}): "
                    f"{e}. Retrying in {delay}s."
                )
                return False

            self._failed_attempts = 0
            self._next_retry_at = 0.0
            self._last_error = None
            logger.info("Unofficial API client connected (no-cache mode)")
            return True

    def reconnect(self) -> bool:
        """Drop the current session and log in again (used when it expires)."""
        with UnofficialAPIClient._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            self._access_token = None
            # An explicit reconnect should not be blocked by an earlier backoff.
            self._next_retry_at = 0.0
        return self.ensure_connected()

    def status(self) -> dict:
        """A description of why the unofficial API is or isn't usable."""
        with UnofficialAPIClient._lock:
            connected = self._client is not None
            retry_in = 0.0
            if not connected and self._next_retry_at:
                retry_in = max(0.0, self._next_retry_at - time.monotonic())
            return {
                "credentials_configured": self.credentials_configured(),
                "connected": connected,
                "last_error": self._last_error,
                "failed_attempts": self._failed_attempts,
                "retry_in_seconds": round(retry_in),
            }

    def unavailable_reason(self) -> str:
        """A message that says what actually went wrong, not a guess."""
        state = self.status()
        if not state["credentials_configured"]:
            return (
                "Unofficial API not configured: TICKTICK_USERNAME and "
                "TICKTICK_PASSWORD are not set."
            )
        message = f"Unofficial API unavailable: {state['last_error']}"
        if "429" in (state["last_error"] or ""):
            message += (
                " (TickTick is rate-limiting the login endpoint; this usually clears"
                " on its own)"
            )
        if state["retry_in_seconds"]:
            message += f". Retrying in {state['retry_in_seconds']}s."
        return message

    
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
        """The singleton, or None when it cannot currently reach TickTick."""
        instance = cls._instance or cls()
        return instance if instance.ensure_connected() else None

    @classmethod
    def peek(cls) -> Optional["UnofficialAPIClient"]:
        """The singleton without triggering a connection attempt (for status)."""
        return cls._instance
    
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
    
    # ==================== Generic API Call ====================

    def call_api(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | list | None = None,
        params: dict | None = None
    ) -> dict | list:
        """
        Make a generic API call to TickTick.

        Args:
            endpoint: API endpoint path (e.g., "/api/v2/batch/task")
            method: HTTP method (GET, POST, PUT, DELETE)
            data: Request body as JSON (for POST/PUT)
            params: Query string parameters

        Returns:
            Response JSON
        """
        url = f"https://api.ticktick.com{endpoint}"

        response = self._send(method, url, data, params)

        # The session token from /user/signon expires. Without this the whole
        # deployment stays broken until someone redeploys it.
        if response.status_code == 401:
            logger.info("Unofficial API session rejected (401); re-authenticating")
            if self.reconnect():
                response = self._send(method, url, data, params)

        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")

        return response.json() if response.content else {"status": "success"}

    def _send(
        self,
        method: str,
        url: str,
        data: dict | list | None,
        params: dict | None,
    ) -> httpx.Response:
        """Issue one HTTP request with the authenticated client."""
        if method == "GET":
            return self.client.get(url, params=params)
        elif method == "POST":
            return self.client.post(url, json=data)
        elif method == "PUT":
            return self.client.put(url, json=data)
        elif method == "DELETE":
            return self.client.delete(url)
        raise ValueError(f"Unsupported method: {method}")



# ==================== Module-level convenience functions ====================

def get_client() -> Optional[UnofficialAPIClient]:
    """Get the unofficial API client instance, or None if it can't connect."""
    return UnofficialAPIClient.get_instance()


def client_status() -> dict:
    """
    Report the unofficial client's state without forcing a connection attempt.

    Used by the /status endpoint so a failing login can be diagnosed from a
    browser instead of by reading deploy logs.
    """
    instance = UnofficialAPIClient.peek()
    if instance is None:
        return {
            "credentials_configured": UnofficialAPIClient.credentials_configured(),
            "connected": False,
            "last_error": "client not constructed yet",
            "failed_attempts": 0,
            "retry_in_seconds": 0,
        }
    return instance.status()


def unavailable_reason() -> str:
    """Why the unofficial API isn't usable right now."""
    instance = UnofficialAPIClient.peek()
    if instance is None:
        return "Unofficial API client has not been constructed."
    return instance.unavailable_reason()
