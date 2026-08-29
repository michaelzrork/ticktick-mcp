"""
TickTick Unofficial API Client.

Direct API access without ticktick-py dependency.
Handles authentication via username/password login and makes fresh API calls for all reads.
NO CACHING - every read fetches fresh data from the API.

This eliminates the stale cache problem that plagued the ticktick-py approach.
"""

import email.utils
import logging
import os
import stat
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import (
    USERNAME,
    PASSWORD,
)

logger = logging.getLogger(__name__)


def _session_cache_path() -> Path:
    """
    Where the TickTick session token is cached between restarts.

    Defaults next to the OAuth token cache (/tmp on a cloud deploy). Point
    TICKTICK_SESSION_CACHE at a mounted volume to keep the session across
    deploys, which is what actually stops the login rate limiting.
    """
    override = os.getenv("TICKTICK_SESSION_CACHE")
    if override:
        return Path(override).expanduser()
    from .config import dotenv_dir_path

    return Path(dotenv_dir_path) / ".ticktick-session"


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header, which is either seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return max(0.0, parsed.timestamp() - time.time())


class LoginRateLimited(RuntimeError):
    """TickTick answered the login endpoint with 429."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


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
    # Upper bound on an honoured Retry-After, so a wild value can't wedge us.
    RETRY_AFTER_MAX_SECONDS = 3600

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
                # When TickTick says how long to wait, believe it over our guess.
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    delay = max(delay, min(retry_after, self.RETRY_AFTER_MAX_SECONDS))
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
            # The cached session is what just got rejected - drop it so the
            # reconnect actually logs in rather than replaying a dead token.
            self._clear_cached_session()
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
        client = httpx.Client(
            headers=self.DEFAULT_HEADERS,
            timeout=30.0,
            follow_redirects=True
        )
        self._client = client

        try:
            # Reuse a cached session when we have one. TickTick rate-limits
            # /user/signon hard, so logging in on every boot is what gets a
            # frequently-redeployed server throttled (429).
            if not self._resume_cached_session():
                self._login()
                self._save_session()
                self._load_settings()

            # Do initial sync to get inbox_id
            self._initial_sync()
        except Exception:
            self._client = None
            client.close()
            raise

    def _resume_cached_session(self) -> bool:
        """
        Try the cached session token. Returns True when it is still valid.

        The validation request doubles as the settings load, so resuming costs
        exactly one request and no login.
        """
        token = self._read_cached_token()
        if not token:
            return False

        self._client.cookies.set("t", token)
        try:
            response = self._client.get(
                self.BASE_URL + "user/preferences/settings",
                params={"includeWeb": True},
            )
        except Exception as e:
            logger.warning(f"Could not validate cached session: {e}")
            self._client.cookies.clear()
            return False

        if response.status_code == 200:
            self._access_token = token
            self._apply_settings(response.json())
            logger.info("Reused cached TickTick session (no login needed)")
            return True

        logger.info(
            f"Cached session rejected ({response.status_code}); logging in again"
        )
        self._client.cookies.clear()
        self._clear_cached_session()
        return False

    def _read_cached_token(self) -> Optional[str]:
        try:
            path = _session_cache_path()
            if not path.is_file():
                return None
            return path.read_text().strip() or None
        except Exception as e:
            logger.warning(f"Could not read session cache: {e}")
            return None

    def _save_session(self) -> None:
        if not self._access_token:
            return
        try:
            path = _session_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._access_token)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # a credential: owner only
            logger.info(f"Cached TickTick session token at {path}")
        except Exception as e:
            logger.warning(f"Could not write session cache: {e}")

    def _clear_cached_session(self) -> None:
        try:
            _session_cache_path().unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Could not clear session cache: {e}")
    
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

        if response.status_code == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise LoginRateLimited(
                f"Login failed: 429 - {response.text[:200]}", retry_after
            )

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
        
        self._apply_settings(response.json())

    def _apply_settings(self, data: dict) -> None:
        """Store the fields we care about from a settings payload."""
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
