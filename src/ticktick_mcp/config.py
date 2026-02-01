"""
Configuration for TickTick MCP Server.
Handles dual-client authentication:
1. Official API (OAuth) - via ticktick_client.py
2. Unofficial API (ticktick-py) - via unofficial_client.py
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

# --- Load from .env file if not all vars are set ---
if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD]):
    logger.info("Environment variables not fully set, loading from .env file...")

    dotenv_dir_path = Path(args.dotenv_dir).expanduser()

    try:
        dotenv_dir_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured directory exists: {dotenv_dir_path}")
    except OSError as e:
        logger.error(f"Error creating directory {dotenv_dir_path}: {e}")
        sys.exit(1)

    dotenv_path = dotenv_dir_path / ".env"

    if not dotenv_path.is_file():
        logger.error(f"Required .env file not found at {dotenv_path}")
        logger.error("Please create the .env file with your TickTick credentials.")
        sys.exit(1)

    loaded = load_dotenv(override=True, dotenv_path=dotenv_path)
    if loaded:
        logger.info(f"Loaded environment from: {dotenv_path}")
    else:
        logger.error(f"Failed to load from {dotenv_path}")
        sys.exit(1)

    # Reload variables after dotenv
    CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
    CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
    USERNAME = os.getenv("TICKTICK_USERNAME")
    PASSWORD = os.getenv("TICKTICK_PASSWORD")
    if not ACCESS_TOKEN:
        ACCESS_TOKEN = os.getenv("TICKTICK_ACCESS_TOKEN")
    if not USER_ID:
        USER_ID = os.getenv("TICKTICK_USER_ID")
else:
    logger.info("Using environment variables provided by hosting platform")

# Final validation
if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD]):
    logger.error("Missing required environment variables")
    sys.exit(1)

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
# These use the separate ticktick_client.py which uses httpx for the official OpenAPI

def get_ticktick_client():
    """Returns the official API client. Returns None if ACCESS_TOKEN is missing."""
    if not ACCESS_TOKEN:
        return None
    from ticktick_mcp.ticktick_client import init_ticktick_client
    return init_ticktick_client(access_token=ACCESS_TOKEN, user_id=USER_ID)


def save_tokens(access_token: str, refresh_token: str = None, expires_in: int = None):
    """Called after successful OAuth callback to save tokens."""
    global ACCESS_TOKEN
    ACCESS_TOKEN = access_token

    # Save to local cache file
    token_file = dotenv_dir_path / ".token-cache.json"
    token_data = {"access_token": access_token}
    if refresh_token:
        token_data["refresh_token"] = refresh_token
    if expires_in:
        token_data["expires_in"] = str(expires_in)
        token_data["expire_time"] = str(int(time.time()) + expires_in)

    try:
        dotenv_dir_path.mkdir(parents=True, exist_ok=True)
        token_file.write_text(json.dumps(token_data, indent=2))
        logger.info(f"Saved official token to: {token_file}")
    except IOError as e:
        logger.warning(f"Failed to save token cache: {e}")

    from ticktick_mcp.ticktick_client import init_ticktick_client
    init_ticktick_client(access_token=access_token, user_id=USER_ID)


# --- Unofficial API Client Function ---
# This uses unofficial_client.py with direct API calls

def get_unofficial_client():
    """Returns the unofficial API client for direct v2 API access."""
    from ticktick_mcp.unofficial_client import UnofficialAPIClient
    return UnofficialAPIClient.get_instance()
