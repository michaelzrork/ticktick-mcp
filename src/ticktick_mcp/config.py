"""
Configuration for TickTick MCP Server.
Handles dual-client authentication:
1. Official API (OAuth)
2. Unofficial API (Username/Password + Token Cache)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ticktick_mcp.ticktick_client import init_ticktick_client, TickTickClient
from ticktick_mcp.unofficial_client import (
    TickTickUnofficialClient,
    init_unofficial_client,
    get_unofficial_client as _get_unofficial_client
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# --- Initial Argument Parsing ---
parser = argparse.ArgumentParser(description="TickTick MCP server configuration.")
parser.add_argument(
    "--dotenv-dir",
    type=str,
    help="Directory for .env file. Defaults to '~/.config/ticktick-mcp'.",
    default="~/.config/ticktick-mcp"
)
args, _ = parser.parse_known_args()

# Base configuration directory
CONFIG_DIR = Path(args.dotenv_dir).expanduser()

# --- Module Globals ---
CLIENT_ID: str | None = None
CLIENT_SECRET: str | None = None
REDIRECT_URI: str | None = None
ACCESS_TOKEN: str | None = None
USER_ID: str | None = None
USERNAME: str | None = None
PASSWORD: str | None = None

IS_CLOUD_DEPLOYMENT: bool = False
UNOFFICIAL_TOKEN_CACHE_PATH: Path = CONFIG_DIR / ".token-oauth"
_unofficial_client_initialized: bool = False


def _load_env_vars() -> None:
    """
    Logic: 
    1. Check Shell/Cloud Env vars first.
    2. Fallback to .env if local.
    3. If Cloud detected via TICKTICK_OAUTH_TOKEN, reroute unofficial cache to /tmp.
    """
    global CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, ACCESS_TOKEN, USER_ID
    global USERNAME, PASSWORD, IS_CLOUD_DEPLOYMENT, UNOFFICIAL_TOKEN_CACHE_PATH

    # 1. Grab what is currently in the environment
    CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
    CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
    USERNAME = os.getenv("TICKTICK_USERNAME")
    PASSWORD = os.getenv("TICKTICK_PASSWORD")
    ACCESS_TOKEN = os.getenv("TICKTICK_ACCESS_TOKEN")

    # 2. Local Fallback: Load .env if official credentials missing
    if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
        dotenv_path = CONFIG_DIR / ".env"
        if dotenv_path.is_file():
            logger.info(f"Loading environment from {dotenv_path}")
            load_dotenv(override=True, dotenv_path=dotenv_path)
            # Re-read
            CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
            CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
            REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
            USERNAME = os.getenv("TICKTICK_USERNAME")
            PASSWORD = os.getenv("TICKTICK_PASSWORD")
            if not ACCESS_TOKEN:
                ACCESS_TOKEN = os.getenv("TICKTICK_ACCESS_TOKEN")

    # 3. Cloud Detection & Unofficial Cache Rerouting
    # This matches your original logic: Only the unofficial cache needs /tmp in cloud.
    oauth_token_json = os.getenv("TICKTICK_OAUTH_TOKEN")
    
    if oauth_token_json:
        IS_CLOUD_DEPLOYMENT = True
        UNOFFICIAL_TOKEN_CACHE_PATH = Path("/tmp") / ".token-oauth"
        logger.info(f"Cloud detected. Rerouting unofficial cache to: {UNOFFICIAL_TOKEN_CACHE_PATH}")

        try:
            token_data = json.loads(oauth_token_json)
            # Inject expire_time for ticktick-py compatibility
            token_data['expire_time'] = int(time.time()) + token_data.get('expires_in', 15551999)
            
            # Write to /tmp so the unofficial client can authenticate
            UNOFFICIAL_TOKEN_CACHE_PATH.write_text(json.dumps(token_data))
            
            # Sync the access token for the official client if not already set
            if not ACCESS_TOKEN:
                ACCESS_TOKEN = token_data.get("access_token")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to setup cloud cache for unofficial client: {e}")
    else:
        # Local mode: unofficial cache stays in config dir
        UNOFFICIAL_TOKEN_CACHE_PATH = CONFIG_DIR / ".token-oauth"

    USER_ID = os.getenv("TICKTICK_USER_ID")

    # Final Validation for Official Client
    if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI]):
        logger.error("Missing required OAuth credentials (CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)")
        sys.exit(1)


def _save_token_cache(access_token: str, refresh_token: str | None = None, expires_in: int | None = None) -> None:
    """Saves official OAuth token to local cache."""
    token_file = CONFIG_DIR / ".token-cache.json"
    token_data = {"access_token": access_token}
    if refresh_token: token_data["refresh_token"] = refresh_token
    if expires_in:
        token_data["expires_in"] = str(expires_in)
        token_data["expire_time"] = str(int(time.time()) + expires_in)

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        token_file.write_text(json.dumps(token_data, indent=2))
        logger.info(f"Saved official token to: {token_file}")
    except IOError as e:
        logger.warning(f"Failed to save official token cache: {e}")


# Initialize vars immediately on import
_load_env_vars()


def get_ticktick_client() -> TickTickClient | None:
    """Returns official client. Returns None if ACCESS_TOKEN is missing."""
    if not ACCESS_TOKEN:
        return None
    return init_ticktick_client(access_token=ACCESS_TOKEN, user_id=USER_ID)


def save_tokens(access_token: str, refresh_token: str | None = None, expires_in: int | None = None) -> None:
    """Called after successful OAuth callback."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = access_token
    _save_token_cache(access_token, refresh_token, expires_in)
    init_ticktick_client(access_token=access_token, user_id=USER_ID)


async def get_unofficial_client() -> TickTickUnofficialClient | None:
    """Lazy initialization of the unofficial client using the calculated cache path."""
    global _unofficial_client_initialized

    if not USERNAME or not PASSWORD:
        logger.info("Unofficial credentials not set; skipping unofficial client.")
        return None

    # Check for existing singleton
    existing = _get_unofficial_client()
    if existing and existing.is_authenticated:
        return existing

    if not _unofficial_client_initialized:
        try:
            client = await init_unofficial_client(
                username=USERNAME,
                password=PASSWORD,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                access_token=ACCESS_TOKEN,
                token_cache_path=UNOFFICIAL_TOKEN_CACHE_PATH
            )
            _unofficial_client_initialized = True
            return client
        except Exception as e:
            logger.error(f"Unofficial client login failed: {e}")
            return None

    return _get_unofficial_client()