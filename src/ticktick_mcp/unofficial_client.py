"""
TickTick Unofficial API Client for v2 endpoints.
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
def _patched_login(self, username: str, password: str) -> None:
    """
    Patched login that uses cached OAuth token when available.

    The OAuth access_token can be used directly as the session cookie 't',
    avoiding the /user/signon call which can fail with 500 errors.
    """
    # Check if OAuth manager already has a valid access token
    if hasattr(self, 'oauth_manager') and self.oauth_manager:
        token_info = getattr(self.oauth_manager, 'access_token_info', None)
        if token_info and 'access_token' in token_info:
            cached_token = token_info['access_token']
            logger.info("  Using cached OAuth token, skipping /user/signon")
            self.access_token = cached_token
            self.cookies['t'] = cached_token
            return

    # Fallback to actual login if no cached token
    logger.info("  No cached token, calling /user/signon")
    import secrets
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
        'x-device': '{"platform":"web","os":"Windows 10","device":"Firefox 123.0","name":"","version":4576,"id":"' + secrets.token_hex(12) + '","channel":"website","campaign":"","websocket":""}'
    }
    url = self.BASE_URL + 'user/signon'
    user_info = {'username': username, 'password': password}
    parameters = {'wc': True, 'remember': True}
    response = self.http_post(url, json=user_info, params=parameters, headers=headers)
    self.access_token = response['token']
    self.cookies['t'] = self.access_token

TickTickClient._login = _patched_login
# ============== End monkey patch ==============

class TickTickUnofficialAPIError(Exception):
    def __init__(self, status_code: int, message: str, response_body: Any = None):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"TickTick Unofficial API Error {status_code}: {message}")

class TickTickUnofficialClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None,
        token_cache_path: Optional[Path] = None
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._access_token = access_token
        self._token_cache_path = token_cache_path or Path.home() / ".config" / "ticktick-mcp" / ".token-oauth"
        self._ticktick_client: Optional[TickTickClient] = None

        # If config.py gave us a token, write it to the cache file immediately
        if access_token:
            self._write_token_cache(access_token)

    @property
    def is_authenticated(self) -> bool:
        return self._ticktick_client is not None

    def _write_token_cache(self, access_token: str) -> None:
        try:
            cache_path = Path(self._token_cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            token_info = {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": 15552000,
                "scope": "tasks:read tasks:write",
                "expire_time": int(time.time()) + 15552000,
            }
            cache_path.write_text(json.dumps(token_info))
            logger.info(f"Wrote token to: {cache_path}")
        except Exception as e:
            logger.error(f"Failed to write token cache: {e}")

    def _create_oauth(self) -> OAuth2:
        return OAuth2(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
            cache_path=str(self._token_cache_path)
        )

    def sync_initialize(self, username, password, max_retries=3):
        """
        Initialize ticktick-py client with retry logic.

        TickTick's /user/signon endpoint can return 500 errors intermittently.
        We retry with exponential backoff to handle transient failures.
        """
        oauth = self._create_oauth()
        oauth.get_access_token()

        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(f"  Login attempt {attempt + 1}/{max_retries}...")
                self._ticktick_client = TickTickClient(username, password, oauth)
                logger.info(f"  Login successful!")
                return self._ticktick_client
            except Exception as e:
                last_error = e
                wait_time = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(f"  Login attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"  Retrying in {wait_time}s...")
                    time.sleep(wait_time)

        raise last_error

    async def login(self, username: str, password: str) -> bool:
        """Async wrapper for the library initialization."""
        try:
            await asyncio.to_thread(self.sync_initialize, username, password)
            return True
        except Exception as e:
            logger.error(f"Unofficial auth failed: {e}")
            raise TickTickUnofficialAPIError(status_code=401, message=str(e))

    async def _request(self, method: str, endpoint: str, json: Optional[dict] = None, params: Optional[dict] = None) -> Any:
        url = f"https://api.ticktick.com/api/v2{endpoint}"
        return await asyncio.to_thread(self._sync_request, method, url, json, params)

    def _sync_request(self, method, url, json_data, params):
        if not self._ticktick_client:
            raise TickTickUnofficialAPIError(401, "Not authenticated")

        # Use _session directly for full control over requests
        # This bypasses http_get/http_post which have limited error handling
        session = self._ticktick_client._session

        logger.info(f"  _sync_request: {method} {url}")
        logger.info(f"  Session cookies: {list(session.cookies.keys())}")

        if method.upper() == "GET":
            response = session.get(url, params=params)
        else:
            response = session.post(url, json=json_data, params=params)

        logger.info(f"  Response status: {response.status_code}")

        if response.status_code != 200:
            logger.error(f"  Response body: {response.text[:500]}")
            raise TickTickUnofficialAPIError(
                response.status_code,
                f"API request failed: {response.text[:200]}"
            )

        return response.json()

    async def get_task_activity(self, task_id: str, skip: int = 0) -> list[dict]:
        url = f"https://api.ticktick.com/api/v1/task/activity/{task_id}"
        params = {"skip": skip} if skip > 0 else None
        return await asyncio.to_thread(self._sync_request, "GET", url, None, params)

# Singleton management
_unofficial_client: Optional[TickTickUnofficialClient] = None

def get_unofficial_client() -> Optional[TickTickUnofficialClient]:
    return _unofficial_client

async def init_unofficial_client(username, password, client_id, client_secret, redirect_uri, access_token=None, token_cache_path=None) -> TickTickUnofficialClient:
    global _unofficial_client
    client = TickTickUnofficialClient(client_id, client_secret, redirect_uri, access_token, token_cache_path)
    await client.login(username, password)
    _unofficial_client = client
    return client