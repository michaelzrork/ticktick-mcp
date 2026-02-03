"""
TickTick Unofficial API Client.

Direct API access without ticktick-py dependency.
Handles authentication via username/password login and makes fresh API calls for all reads.
NO CACHING - every read fetches fresh data from the API.

This eliminates the stale cache problem that plagued the ticktick-py approach.
"""

import logging
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

        if method == "GET":
            response = self.client.get(url, params=params)
        elif method == "POST":
            response = self.client.post(url, json=data)
        elif method == "PUT":
            response = self.client.put(url, json=data)
        elif method == "DELETE":
            response = self.client.delete(url)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if response.status_code != 200:
            raise RuntimeError(f"API error {response.status_code}: {response.text[:200]}")

        return response.json() if response.content else {"status": "success"}



# ==================== Module-level convenience functions ====================

def get_client() -> Optional[UnofficialAPIClient]:
    """Get the unofficial API client instance."""
    return UnofficialAPIClient.get_instance()
