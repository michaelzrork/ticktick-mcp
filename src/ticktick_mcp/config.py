"""
Configuration for TickTick MCP Server.
Handles dual-client authentication:
1. Official API (OAuth) - via ticktick_client.py
2. Unofficial API (direct v2 calls, no caching) - via unofficial_client.py
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="TickTick MCP server configuration.")
parser.add_argument(
    "--dotenv-dir",
    type=str,
    help="Directory for .env file. Defaults to '~/.config/ticktick-mcp'.",
    default="~/.config/ticktick-mcp"
)
args, _ = parser.parse_known_args()

# --- Check environment variables (e.g., from Railway) ---
CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
USERNAME = os.getenv("TICKTICK_USERNAME")
PASSWORD = os.getenv("TICKTICK_PASSWORD")
ACCESS_TOKEN = os.getenv("TICKTICK_ACCESS_TOKEN")
USER_ID = os.getenv("TICKTICK_USER_ID")

# --- Load from .env file if something is missing ---
# Nothing here is fatal any more. The browser sign-in (oauth.py) needs only the
# client id and secret; the username and password are for the optional
# unofficial API, and the server runs without them (those tools then say so).
_WANTED_VARS = {
    "TICKTICK_CLIENT_ID": CLIENT_ID,
    "TICKTICK_CLIENT_SECRET": CLIENT_SECRET,
    "TICKTICK_USERNAME": USERNAME,
    "TICKTICK_PASSWORD": PASSWORD,
}

if not all(_WANTED_VARS.values()):
    dotenv_path = Path(args.dotenv_dir).expanduser() / ".env"
    if dotenv_path.is_file() and load_dotenv(override=False, dotenv_path=dotenv_path):
        logger.info(f"Loaded environment from: {dotenv_path}")
        CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
        CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
        REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
        USERNAME = os.getenv("TICKTICK_USERNAME")
        PASSWORD = os.getenv("TICKTICK_PASSWORD")
        ACCESS_TOKEN = ACCESS_TOKEN or os.getenv("TICKTICK_ACCESS_TOKEN")
        USER_ID = USER_ID or os.getenv("TICKTICK_USER_ID")

for _name, _value in {
    "TICKTICK_CLIENT_ID": CLIENT_ID,
    "TICKTICK_CLIENT_SECRET": CLIENT_SECRET,
}.items():
    if not _value:
        logger.warning(f"{_name} is not set: the TickTick browser sign-in cannot work without it.")
if not (USERNAME and PASSWORD):
    logger.info("TICKTICK_USERNAME / TICKTICK_PASSWORD not set: unofficial_* tools are off.")

# --- Set dotenv_dir_path for token cache ---
# Use /tmp for cloud deployment (writable), otherwise use config dir for local
oauth_token_env = os.getenv("TICKTICK_OAUTH_TOKEN")
if oauth_token_env:
    # Cloud deployment - write token to /tmp cache
    logger.info("Cloud deployment detected (TICKTICK_OAUTH_TOKEN set)")
    dotenv_dir_path = Path("/tmp")
    token_path = dotenv_dir_path / ".token-oauth"

    # Parse token and add expire_time field for ticktick-py compatibility
    token_data = json.loads(oauth_token_env)
    current_time = int(time.time())
    token_data['expire_time'] = current_time + token_data.get('expires_in', 15551999)

    # Write corrected token to cache
    token_path.write_text(json.dumps(token_data))
    logger.info(f"Wrote OAuth token to: {token_path}")

    # Also set ACCESS_TOKEN for official API if not already set
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = token_data.get("access_token")
else:
    # Local mode - use config dir
    dotenv_dir_path = Path(args.dotenv_dir).expanduser()
    logger.info(f"Local mode, token cache dir: {dotenv_dir_path}")


# --- Official API Client Functions ---
# These use the separate ticktick_client.py which uses httpx for the official OpenAPI.
#
# Which token: the one saved by the browser sign-in (token store) while it is
# valid, else TICKTICK_ACCESS_TOKEN. The variable is the fallback that keeps a
# deployment working before anyone has signed in through /mcp.

_official_client = None
_official_client_token = None


def official_token() -> tuple:
    """(token, source) for the official API; (None, None) when there is none."""
    try:
        from ticktick_mcp.token_store import get_store
        saved = get_store().ticktick_token()
    except Exception as e:  # a broken store must not take the env token down with it
        logger.warning(f"Could not read the token store: {e}")
        saved = None
    if saved and saved.get("access_token") and saved.get("expires_at", 0) > time.time():
        return saved["access_token"], "sign-in"
    if ACCESS_TOKEN:
        return ACCESS_TOKEN, "env"
    if saved and saved.get("access_token"):
        return saved["access_token"], "sign-in (expired)"
    return None, None


def official_user_id():
    """TICKTICK_USER_ID, else the user id learned at sign-in (for the Inbox)."""
    if USER_ID:
        return USER_ID
    try:
        from ticktick_mcp.token_store import get_store
        return (get_store().owner() or {}).get("user_id")
    except Exception:
        return None


def get_ticktick_client():
    """Returns the official API client, or None when there is no token at all."""
    global _official_client, _official_client_token
    token, _source = official_token()
    if not token:
        return None
    if _official_client is None or token != _official_client_token:
        from ticktick_mcp.ticktick_client import init_ticktick_client
        _official_client = init_ticktick_client(access_token=token, user_id=official_user_id())
        _official_client_token = token
    return _official_client


def reset_official_client():
    """Forget the cached client (a new token was just saved)."""
    global _official_client, _official_client_token
    _official_client = None
    _official_client_token = None


# --- Unofficial API Client Function ---
# This uses unofficial_client.py with direct API calls

def get_unofficial_client():
    """Returns the unofficial API client for direct v2 API access."""
    from ticktick_mcp.unofficial_client import UnofficialAPIClient
    return UnofficialAPIClient.get_instance()
