"""
TickTick Unofficial API Client.

Direct API access without a ticktick-py dependency. Authenticates with
username/password against TickTick's v2 endpoints and makes fresh API calls for
all reads. NO CACHING of task data - every read fetches from the API.

Two pieces of state exist purely to keep TickTick's login endpoint happy, because
it rate-limits (429) hard and a throttled login takes every unofficial_* tool
down with it:

- A per-install DEVICE IDENTITY. TickTick sees an `x-device` header carrying a
  device id. That id used to be a constant copied from ticktick-py, which means
  every deployment running this code presented the same device to TickTick and
  shared one rate-limit bucket. It is now generated once per install and kept.
- A cached SESSION TOKEN. Login asks for `remember=true`, so the session is
  long-lived. Startup connects eagerly, but resumes that session when it exists
  and only logs in when it does not - so a normal deploy costs no login, which
  is what stops a burst of redeploys earning a 429.
"""

import email.utils
import json
import logging
import os
import secrets
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

DEFAULT_STATE_FILENAME = ".ticktick-session.json"


# ==================== Persisted state (device id + session) ====================


def state_path() -> Path:
    """
    Where the device id and session token live between restarts.

    Defaults beside the OAuth token cache, which is /tmp on a cloud deploy -
    enough to survive container restarts but not redeploys. Point
    TICKTICK_SESSION_CACHE at a mounted volume to survive those too, which is
    what actually stops repeated logins.
    """
    override = os.getenv("TICKTICK_SESSION_CACHE")
    if override:
        return Path(override).expanduser()

    from .config import dotenv_dir_path

    return Path(dotenv_dir_path) / DEFAULT_STATE_FILENAME


def _read_state() -> dict:
    """Load persisted state, tolerating a missing or corrupt file."""
    try:
        path = state_path()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Could not read TickTick state file: {e}")
        return {}


def _write_state(state: dict) -> bool:
    """Persist state with owner-only permissions. Returns True on success."""
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # holds a session credential
        return True
    except Exception as e:
        logger.warning(f"Could not write TickTick state file: {e}")
        return False


def _login_error_code(body: str) -> str:
    """Pull TickTick's errorCode out of a login error body, for a clean message."""
    try:
        parsed = json.loads(body)
        return parsed.get("errorCode") or "unknown error"
    except Exception:
        return body[:120]


# The device id every copy of this code used to send, copied from ticktick-py.
# Kept only as the FIRST rung of the login ladder: it is the identity this
# account logged in with for months, so it is worth trying before the
# per-install one. Never used as a default.
LEGACY_SHARED_DEVICE_ID = "674c46cf88bb9f5f73c3068a"


def credentials_shape() -> dict:
    """
    Describe the credentials WITHOUT revealing them.

    "The password is correct" and "the password arrives at the login call
    intact" are different claims. A trailing newline or a stray quote picked up
    from a config UI produces an authentication failure for a password that was
    never changed, and nothing else would show it.

    Lengths and whitespace flags only - never the values, and never on the
    public /status endpoint.
    """
    def describe(value: Optional[str]) -> dict:
        if value is None:
            return {"set": False}
        return {
            "set": True,
            "length": len(value),
            "leading_whitespace": value != value.lstrip(),
            "trailing_whitespace": value != value.rstrip(),
            "contains_newline": "\n" in value or "\r" in value,
            "surrounded_by_quotes": len(value) > 1
            and value[0] == value[-1]
            and value[0] in "\"'",
        }

    return {"username": describe(USERNAME), "password": describe(PASSWORD)}


def _redact(text: str) -> str:
    """Strip the account's own username out of text bound for a public endpoint."""
    if USERNAME and text:
        return text.replace(USERNAME, "<username>")
    return text


def _new_device_id() -> str:
    """
    Generate a device id in the shape TickTick uses (24 lowercase hex chars).

    Unique per install. Do not replace this with a constant - see the module
    docstring for why a shared id is a problem.
    """
    return secrets.token_hex(12)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header: either seconds, or an HTTP date."""
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


class LoginRejected(RuntimeError):
    """
    TickTick answered the login with a credential-rejection error code.

    Do NOT read this as proof the password is wrong. It is what TickTick's
    undocumented endpoint returns, and the same code appears to cover more than
    one condition - a genuinely wrong password, but also anti-abuse states that
    follow a burst of failed or throttled logins. The two are indistinguishable
    from the response alone.

    So it is treated as a long cooldown rather than a permanent stop: retrying
    immediately cannot help and risks a lockout, but a block that lifts on its
    own should still recover without a redeploy.
    """


# Error codes in TickTick's login response that mean "these credentials are
# wrong", as opposed to "try again later".
TERMINAL_LOGIN_ERROR_CODES = (
    "username_password_not_match",
    "user_not_exist",
    "account_not_exist",
)


class UnofficialAPIClient:
    """
    Direct access to TickTick's unofficial v2 API.

    - No caching of task data: every read makes a fresh API call
    - Self-contained auth: no ticktick-py dependency
    - Generic call_api() method for all API operations
    """

    BASE_URL = "https://api.ticktick.com/api/v2/"
    BATCH_CHECK_URL = BASE_URL + "batch/check/0"

    # Headers that mimic the web app. The device id inside x-device is filled in
    # per install by _device_header() - it is deliberately NOT a constant here.
    USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'

    STATIC_HEADERS = {
        'origin': 'https://ticktick.com',
        'sec-ch-ua': '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'user-agent': USER_AGENT,
    }

    # A failed login must never be permanent. TickTick rate-limits /user/signon,
    # so a restart during a throttled window used to poison the process for its
    # whole lifetime and report itself as "not configured".
    RETRY_BASE_SECONDS = 30
    RETRY_MAX_SECONDS = 900
    RETRY_AFTER_MAX_SECONDS = 3600  # cap on an honoured Retry-After
    # A credential rejection may be a wrong password or an anti-abuse block, so
    # wait a long time rather than retrying fast or giving up forever.
    REJECTED_COOLDOWN_SECONDS = 3600

    _instance: Optional["UnofficialAPIClient"] = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Prepare state. Deliberately does NOT log in.

        Connecting is left to ensure_connected(), so importing this module never
        spends a login against a rate-limited endpoint.
        """
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
            self._settings_error: Optional[str] = None
            self._sync_error: Optional[str] = None
            self._failed_attempts = 0
            self._next_retry_at = 0.0
            self._logged_in_this_process = False
            self._credentials_rejected = False
            self._last_login_diagnostics: Optional[dict] = None

            self._device_id, self._device_id_persisted = self._resolve_device_id()
            # Which identity the current session was actually established with.
            self._active_device_id: Optional[str] = None

    # ==================== Device identity ====================

    @staticmethod
    def _resolve_device_id() -> tuple[str, bool]:
        """
        Settle on this install's device id.

        Precedence: TICKTICK_DEVICE_ID, then the persisted one, then a new one
        which we persist. Returns (device_id, persisted).
        """
        override = os.getenv("TICKTICK_DEVICE_ID")
        if override:
            logger.info("Using device id from TICKTICK_DEVICE_ID")
            return override, True

        state = _read_state()
        existing = state.get("device_id")
        if existing:
            return existing, True

        device_id = _new_device_id()
        state["device_id"] = device_id
        persisted = _write_state(state)
        if persisted:
            logger.info(f"Generated a new device id for this install: {device_id}")
        else:
            # Still better than a shared constant, but it changes every restart.
            logger.warning(
                "Generated a device id but could not persist it; it will change "
                "on restart. Set TICKTICK_DEVICE_ID or make the state file "
                "writable to keep it stable."
            )
        return device_id, persisted

    def _login_device_candidates(self) -> list[tuple[str, str]]:
        """
        Device identities to try at login, in order, as (label, device_id).

        The legacy shared id goes first: it is what this account authenticated
        with for months. It is also the id most likely to be rate-limited, since
        every other copy of this code sends it - so a 429 falls through to this
        install's own id, which nobody else is using.
        """
        candidates = [("legacy-shared", LEGACY_SHARED_DEVICE_ID)]
        if self._device_id != LEGACY_SHARED_DEVICE_ID:
            candidates.append(("per-install", self._device_id))
        return candidates

    def _device_header(self, device_id: Optional[str] = None) -> str:
        """The x-device header value, carrying a device id."""
        return json.dumps(
            {
                "platform": "web",
                "os": "macOS 10.15.7",
                "device": "Chrome 135.0.0.0",
                "name": "",
                "version": 6260,
                "id": device_id or self._device_id,
                "channel": "website",
                "campaign": "",
                "websocket": "",
            },
            separators=(",", ":"),
        )

    def _build_headers(self) -> dict:
        return {**self.STATIC_HEADERS, "x-device": self._device_header()}

    @property
    def device_id(self) -> str:
        return self._device_id

    # ==================== Connection lifecycle ====================

    @staticmethod
    def credentials_configured() -> bool:
        """Whether a username and password were supplied at all."""
        return bool(USERNAME and PASSWORD)

    def ensure_connected(self, allow_login: bool = True) -> bool:
        """
        Connect if not already connected. Returns True when usable.

        allow_login=False resumes a cached session but will not spend a fresh
        login - used at startup so booting never costs a login by itself.

        Safe to call on every request: once connected it is a None check, and
        while backing off it does not touch the network.
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
                self._initialize_client(allow_login=allow_login)
            except _LoginDeferred:
                # No cached session and we were told not to log in. Not an error.
                return False
            except LoginRejected as e:
                self._client = None
                self._credentials_rejected = True
                self._last_error = str(e)
                self._next_retry_at = (
                    time.monotonic() + self.REJECTED_COOLDOWN_SECONDS
                )
                logger.error(
                    f"TickTick rejected the login: {e}. Waiting "
                    f"{self.REJECTED_COOLDOWN_SECONDS}s before trying again - "
                    "retrying sooner cannot help and risks a lockout."
                )
                return False
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
                    f"{e}. Retrying in {int(delay)}s."
                )
                return False

            self._failed_attempts = 0
            self._next_retry_at = 0.0
            self._last_error = None
            self._credentials_rejected = False
            self._last_login_diagnostics = None
            return True

    def reconnect(self) -> bool:
        """Drop the current session and authenticate again (used on a 401)."""
        with UnofficialAPIClient._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            self._access_token = None
            # The cached session is what was just rejected - drop it so the
            # reconnect actually logs in rather than replaying a dead token.
            self._forget_session()
            self._next_retry_at = 0.0
        return self.ensure_connected(allow_login=True)

    def status(self) -> dict:
        """A description of why the unofficial API is or isn't usable."""
        with UnofficialAPIClient._lock:
            connected = self._client is not None
            retry_in = 0.0
            if not connected and self._next_retry_at:
                retry_in = max(0.0, self._next_retry_at - time.monotonic())
            return {
                "credentials_configured": self.credentials_configured(),
                "credentials_rejected": self._credentials_rejected,
                "connected": connected,
                "session_source": (
                    "cached" if connected and not self._logged_in_this_process
                    else "login" if connected
                    else None
                ),
                "device_id": self._device_id,
                "device_id_persisted": self._device_id_persisted,
                "active_device_id": self._active_device_id,
                "state_file": str(state_path()),
                "last_error": self._last_error,
                "failed_attempts": self._failed_attempts,
                "retry_in_seconds": round(retry_in),
                # These degrade silently rather than failing the connection, so
                # surface them instead of leaving them only in the logs.
                "last_login_diagnostics": self._last_login_diagnostics,
                "settings_error": self._settings_error,
                "sync_error": self._sync_error,
                "inbox_id": self._inbox_id,
                "time_zone": self._time_zone,
            }

    def unavailable_reason(self) -> str:
        """A message that says what actually went wrong, not a guess."""
        state = self.status()
        if state["connected"]:
            return "Unofficial API is connected."
        if not state["credentials_configured"]:
            return (
                "Unofficial API not configured: TICKTICK_USERNAME and "
                "TICKTICK_PASSWORD are not set."
            )
        if state["credentials_rejected"]:
            message = (
                "Unofficial API unavailable: TickTick answered the login with "
                f"'{(state.get('last_login_diagnostics') or {}).get('error_code')}'. "
                "That code covers a genuinely wrong password AND anti-abuse "
                "blocks that follow repeated failed or throttled logins, so it "
                "is not proof the credentials are wrong. Waiting before the next "
                "attempt rather than retrying, which cannot help and risks a "
                "lockout. See /status for the full response."
            )
            if state["retry_in_seconds"]:
                message += f" Next attempt in {state['retry_in_seconds']}s."
            return message
        if not state["last_error"]:
            return "Unofficial API is not connected yet; it authenticates on first use."
        message = f"Unofficial API unavailable: {state['last_error']}"
        if "429" in (state["last_error"] or ""):
            message += (
                " (TickTick is rate-limiting the login endpoint; this clears on "
                "its own and the client keeps retrying)"
            )
        if state["retry_in_seconds"]:
            message += f". Retrying in {state['retry_in_seconds']}s."
        return message

    # ==================== Setup ====================

    def _initialize_client(self, allow_login: bool = True):
        """
        Set up the authenticated httpx client.

        Prefers an existing session over a login. The OAuth2 token in
        .token-oauth is for the OFFICIAL API only and is never used here - the
        unofficial API needs a SESSION token from /user/signon.
        """
        client = httpx.Client(
            headers=self._build_headers(),
            timeout=30.0,
            follow_redirects=True,
        )
        self._client = client

        try:
            if not self._resume_cached_session():
                if not allow_login:
                    raise _LoginDeferred()
                self._login()
                self._remember_session()
                self._load_settings()

            self._initial_sync()
        except Exception:
            self._client = None
            client.close()
            raise

    def _resume_cached_session(self) -> bool:
        """
        Try the cached session token. True when it is still valid.

        The validation request doubles as the settings load, so resuming costs
        one request and no login.
        """
        token = _read_state().get("session_token")
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
            self._active_device_id = self._device_id
            self._logged_in_this_process = False
            self._apply_settings(response.json())
            logger.info("Reused cached TickTick session (no login needed)")
            return True

        logger.info(
            f"Cached session rejected ({response.status_code}); a login is needed"
        )
        self._client.cookies.clear()
        self._forget_session()
        return False

    def _login(self):
        """
        Authenticate, trying each device identity in turn.

        The legacy shared id is tried first because it is what this account used
        for months. Only a 429 falls through to the next candidate - that is the
        one failure a different device identity can actually fix, since the
        legacy id shares a rate-limit bucket with every other copy of this code.
        Any other outcome (success, or a rejection) is about the account, not the
        device, so it stops the ladder immediately.
        """
        url = self.BASE_URL + "user/signon"
        params = {"wc": True, "remember": True}
        payload = {"username": USERNAME, "password": PASSWORD}

        # The username is an email address; keep it out of INFO-level deploy logs.
        logger.debug(f"Logging in as {USERNAME}")

        candidates = self._login_device_candidates()
        last_rate_limit: Optional[LoginRateLimited] = None

        for label, device_id in candidates:
            logger.info(
                f"Authenticating with TickTick using the {label} device id "
                f"({device_id})"
            )
            response = self._client.post(
                url,
                json=payload,
                params=params,
                headers={"x-device": self._device_header(device_id)},
            )

            if response.status_code == 200:
                self._finish_login(response, label, device_id)
                return

            self._record_login_failure(response, label, device_id)

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                last_rate_limit = LoginRateLimited(
                    f"Login failed: 429 with the {label} device id", retry_after
                )
                logger.info(
                    f"The {label} device id is rate-limited; trying the next one"
                )
                continue

            body = response.text[:500]
            if any(code in body for code in TERMINAL_LOGIN_ERROR_CODES):
                raise LoginRejected(
                    f"TickTick returned {_login_error_code(body)} "
                    f"(HTTP {response.status_code}) with the {label} device id"
                )
            raise RuntimeError(
                f"Login failed: {response.status_code} - {_redact(body[:200])}"
            )

        # Every candidate was rate-limited.
        raise last_rate_limit

    def _finish_login(self, response: httpx.Response, label: str, device_id: str):
        """Store the session from a successful login response."""
        data = response.json()
        self._access_token = data.get("token")

        if not self._access_token:
            raise RuntimeError("Login response missing token")

        # Keep using the identity that actually worked for the rest of the
        # session, so the session and the device stay consistent.
        self._client.headers["x-device"] = self._device_header(device_id)
        self._client.cookies.set("t", self._access_token)
        self._active_device_id = device_id
        self._logged_in_this_process = True
        logger.info(
            f"Login successful using the {label} device id ({device_id})"
        )

    def _record_login_failure(
        self,
        response: httpx.Response,
        device_label: str = "unknown",
        device_id: Optional[str] = None,
    ) -> None:
        """
        Keep everything the failed login told us.

        The body alone cannot distinguish a wrong password from an anti-abuse
        block; a captcha requirement or a throttling hint would show up in the
        headers, which nothing was capturing before. Set-Cookie is excluded so a
        session value never lands in /status.
        """
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() != "set-cookie"
        }
        # /status is unauthenticated, so the account's own email must not ride
        # along in the error body TickTick echoes back.
        self._last_login_diagnostics = {
            "status": response.status_code,
            "error_code": _login_error_code(response.text[:500]),
            "body": _redact(response.text[:500]),
            "headers": headers,
            "device_label": device_label,
            "device_id": device_id or self._device_id,
        }
        logger.error(
            f"Login failed with the {device_label} device id: "
            f"HTTP {response.status_code}; body={response.text[:300]}; "
            f"headers={headers}"
        )

    def _remember_session(self) -> None:
        """Persist the session token so the next restart needs no login."""
        if not self._access_token:
            return
        state = _read_state()
        state["device_id"] = self._device_id
        state["session_token"] = self._access_token
        if _write_state(state):
            self._device_id_persisted = True
            logger.info(f"Cached TickTick session at {state_path()}")

    def _forget_session(self) -> None:
        """Drop the cached session token, keeping the device id."""
        state = _read_state()
        if "session_token" in state:
            state.pop("session_token", None)
            _write_state(state)

    def _load_settings(self):
        """Load user settings (timezone, profile_id)."""
        url = self.BASE_URL + "user/preferences/settings"
        params = {"includeWeb": True}

        response = self._client.get(url, params=params)

        if response.status_code != 200:
            # Non-fatal by design: the client still works without these. Recorded
            # so /status can show it rather than it only existing in the logs.
            self._settings_error = f"settings request returned {response.status_code}"
            logger.warning(f"Failed to load settings: {response.status_code}")
            return

        self._apply_settings(response.json())

    def _apply_settings(self, data: dict) -> None:
        """Store the fields we use from a settings payload."""
        self._time_zone = data.get("timeZone", "America/New_York")
        self._profile_id = data.get("id")
        self._settings_error = None
        logger.info(f"Loaded settings: timezone={self._time_zone}")

    def _initial_sync(self):
        """Batch sync to get inbox_id and confirm the session really works."""
        try:
            data = self._fetch_batch_check()
            self._inbox_id = data.get("inboxId")
            self._sync_error = None
            logger.info(f"Initial sync complete, inbox_id={self._inbox_id}")
        except Exception as e:
            # Non-fatal by design; surfaced through status() rather than silent.
            self._sync_error = str(e)
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
            raise RuntimeError(
                f"Batch check failed: {response.status_code} - {response.text[:200]}"
            )

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

        # The session token expires. Without this the whole deployment stays
        # broken until someone redeploys it.
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


class _LoginDeferred(Exception):
    """Internal: a login was needed but not permitted on this call."""


# ==================== Module-level convenience functions ====================

def get_client() -> Optional[UnofficialAPIClient]:
    """Get the unofficial API client instance, or None if it can't connect."""
    return UnofficialAPIClient.get_instance()


def client_status() -> dict:
    """
    Report the unofficial client's state without forcing a connection attempt.

    Used by /status so a failing login can be diagnosed from a browser instead
    of by reading deploy logs.
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
