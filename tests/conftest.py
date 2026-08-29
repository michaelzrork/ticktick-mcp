"""
Shared pytest setup.

ticktick_mcp.config validates credentials at import time and sys.exit(1)s when any
are missing, so placeholders are installed here — before any test imports the
package. Real credentials in the environment are left alone, which is what the
integration tests need.
"""

import os

_PLACEHOLDER_ENV = {
    "TICKTICK_CLIENT_ID": "test-client-id",
    "TICKTICK_CLIENT_SECRET": "test-client-secret",
    "TICKTICK_REDIRECT_URI": "http://localhost:8000/oauth/callback",
    "TICKTICK_USERNAME": "test@example.com",
    "TICKTICK_PASSWORD": "test-password",
}

for _key, _value in _PLACEHOLDER_ENV.items():
    os.environ.setdefault(_key, _value)


def has_live_credentials() -> bool:
    """True when real TickTick credentials are configured, not the placeholders."""
    username = os.environ.get("TICKTICK_USERNAME")
    password = os.environ.get("TICKTICK_PASSWORD")
    return bool(
        username
        and password
        and username != _PLACEHOLDER_ENV["TICKTICK_USERNAME"]
        and password != _PLACEHOLDER_ENV["TICKTICK_PASSWORD"]
    )
