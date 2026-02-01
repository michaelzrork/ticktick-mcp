"""
Configuration for TickTick MCP Server.

This module handles:
- Loading credentials from environment variables or .env file
- Initializing the TickTick API clients (official and unofficial)

Required environment variables:
- TICKTICK_CLIENT_ID: OAuth client ID from developer.ticktick.com
- TICKTICK_CLIENT_SECRET: OAuth client secret
- TICKTICK_REDIRECT_URI: OAuth redirect URI

For cloud deployment (Railway, etc.):
- TICKTICK_ACCESS_TOKEN: OAuth access token (get via /oauth/start flow)
- TICKTICK_USER_ID: User ID for Inbox access (format: numeric ID like "115085635")

For unofficial API features (pins, repeatFrom, activity logs):
- TICKTICK_USERNAME: TickTick account email
- TICKTICK_PASSWORD: TickTick account password

For local development:
- Tokens cached in ~/.config/ticktick-mcp/.token-cache.json
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

# Setup argument parser
parser = argparse.ArgumentParser(
    description="Run the TickTick MCP server, specifying the directory for the .env file."
)
parser.add_argument(
    "--dotenv-dir",
    type=str,
    help="Path to the directory containing the .env file. Defaults to '~/.config/ticktick-mcp'.",
    default="~/.config/ticktick-mcp"
)

# Parse arguments (happens on import for standalone script use)
args, _ = parser.parse_known_args()

# Configuration directory path
CONFIG_DIR = Path(args.dotenv_dir).expanduser()

# Module-level configuration (set during _load_env_vars)
CLIENT_ID: str = ""
CLIENT_SECRET: str = ""
REDIRECT_URI: str = ""
ACCESS_TOKEN: str | None = None
USER_ID: str | None = None

# Unofficial API credentials (for pins, repeatFrom, activity logs)
USERNAME: str | None = None
PASSWORD: str | None = None
_unofficial_client_initialized: bool = False


def _load_env_vars() -> None:
    """Load environment variables from hosting platform or .env file."""
    global CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, ACCESS_TOKEN, USER_ID, USERNAME, PASSWORD

    # Check if OAuth credentials are already set (e.g., by Railway)
    client_id = os.getenv("TICKTICK_CLIENT_ID")
    client_secret = os.getenv("TICKTICK_CLIENT_SECRET")
    redirect_uri = os.getenv("TICKTICK_REDIRECT_URI")

    # If not set, try to load from .env file
    if not all([client_id, client_secret, redirect_uri]):
        logger.info("Environment variables not fully set, attempting to load from .env file...")

        # Create config directory if it doesn't exist
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"Ensured directory exists: {CONFIG_DIR}")
        except OSError as e:
            logger.error(f"Error creating directory {CONFIG_DIR}: {e}")
            sys.exit(1)

        # Check for .env file
        dotenv_path = CONFIG_DIR / ".env"
        if not dotenv_path.is_file():
            logger.error(f"Required .env file not found at {dotenv_path}")
            logger.error("Please create the .env file with your TickTick credentials.")
            logger.error("Expected content:")
            logger.error("  TICKTICK_CLIENT_ID=your_client_id")
            logger.error("  TICKTICK_CLIENT_SECRET=your_client_secret")
            logger.error("  TICKTICK_REDIRECT_URI=your_redirect_uri")
            logger.error("  TICKTICK_ACCESS_TOKEN=your_access_token (optional, can use OAuth flow)")
            logger.error("  TICKTICK_USER_ID=your_user_id (optional, needed for Inbox)")
            sys.exit(1)

        # Load .env file
        loaded = load_dotenv(override=True, dotenv_path=dotenv_path)
        if loaded:
            logger.info(f"Successfully loaded environment variables from: {dotenv_path}")
        else:
            logger.error(f"Failed to load environment variables from {dotenv_path}")
            sys.exit(1)

        # Reload after dotenv
        client_id = os.getenv("TICKTICK_CLIENT_ID")
        client_secret = os.getenv("TICKTICK_CLIENT_SECRET")
        redirect_uri = os.getenv("TICKTICK_REDIRECT_URI")
    else:
        logger.info("Using environment variables provided by hosting platform")

    # Validate OAuth credentials
    if not client_id or not client_secret or not redirect_uri:
        logger.error("Missing required OAuth environment variables (CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)")
        sys.exit(1)

    # Set module-level credentials
    CLIENT_ID = client_id
    CLIENT_SECRET = client_secret
    REDIRECT_URI = redirect_uri

    # Load access token
    access_token = os.getenv("TICKTICK_ACCESS_TOKEN")

    # Check for legacy TICKTICK_OAUTH_TOKEN format (full JSON object)
    oauth_token_json = os.getenv("TICKTICK_OAUTH_TOKEN")
    if oauth_token_json and not access_token:
        try:
            token_data = json.loads(oauth_token_json)
            access_token = token_data.get("access_token")
            logger.info("Extracted access_token from TICKTICK_OAUTH_TOKEN JSON")
        except json.JSONDecodeError:
            logger.warning("TICKTICK_OAUTH_TOKEN is not valid JSON")

    # Try loading from cached token file
    if not access_token:
        token_file = CONFIG_DIR / ".token-cache.json"
        if token_file.is_file():
            try:
                token_data = json.loads(token_file.read_text())
                access_token = token_data.get("access_token")
                logger.info(f"Loaded access token from cache: {token_file}")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to read token cache: {e}")

    ACCESS_TOKEN = access_token

    # Load user ID
    USER_ID = os.getenv("TICKTICK_USER_ID")

    # Load unofficial API credentials (for pins, repeatFrom, activity logs)
    USERNAME = os.getenv("TICKTICK_USERNAME")
    PASSWORD = os.getenv("TICKTICK_PASSWORD")

    if USERNAME and PASSWORD:
        logger.info("Unofficial API credentials found (pins, repeatFrom, activity logs enabled)")
    else:
        logger.info("Unofficial API credentials not set (pins, repeatFrom, activity logs disabled)")


def _save_token_cache(
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int | None = None
) -> None:
    """Save token to cache file."""
    token_file = CONFIG_DIR / ".token-cache.json"
    token_data: dict[str, Any] = {
        "access_token": access_token
    }
    if refresh_token:
        token_data["refresh_token"] = refresh_token
    if expires_in:
        token_data["expires_in"] = expires_in
        token_data["expire_time"] = int(time.time()) + expires_in

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        token_file.write_text(json.dumps(token_data, indent=2))
        logger.info(f"Saved token to cache: {token_file}")
    except IOError as e:
        logger.warning(f"Failed to save token cache: {e}")


# Initialize environment variables on module load
_load_env_vars()


def get_ticktick_client() -> TickTickClient | None:
    """
    Get the initialized TickTick client.

    Returns None if ACCESS_TOKEN is not available (user needs to complete OAuth flow).
    """
    if not ACCESS_TOKEN:
        logger.warning("No access token available. Complete OAuth flow at /oauth/start")
        return None

    return init_ticktick_client(access_token=ACCESS_TOKEN, user_id=USER_ID)


def save_tokens(
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int | None = None
) -> None:
    """
    Save OAuth tokens after completing OAuth flow.

    This should be called from the OAuth callback handler.
    """
    global ACCESS_TOKEN
    ACCESS_TOKEN = access_token
    _save_token_cache(access_token, refresh_token, expires_in)

    # Reinitialize client with new token
    init_ticktick_client(access_token=access_token, user_id=USER_ID)


async def get_unofficial_client() -> TickTickUnofficialClient | None:
    """
    Get the initialized unofficial TickTick client.

    Returns None if USERNAME/PASSWORD are not available.
    Initializes on first call (lazy initialization).
    """
    global _unofficial_client_initialized

    if not USERNAME or not PASSWORD:
        logger.debug("Unofficial API credentials not configured")
        return None

    # Check if already initialized
    existing = _get_unofficial_client()
    if existing and existing.is_authenticated:
        return existing

    # Initialize on first use
    if not _unofficial_client_initialized:
        try:
            client = await init_unofficial_client(
                username=USERNAME,
                password=PASSWORD,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                access_token=ACCESS_TOKEN,
                token_cache_path=CONFIG_DIR / ".token-oauth"
            )
            _unofficial_client_initialized = True
            logger.info("Unofficial client initialized successfully")
            return client
        except Exception as e:
            logger.error(f"Failed to initialize unofficial client: {e}")
            return None

    return _get_unofficial_client()
