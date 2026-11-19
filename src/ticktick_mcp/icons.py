"""
The connector's icon: an original checkmark in a rounded square (not TickTick's
logo). Claude finds it two ways, so both are served:
  - MCP serverInfo.icons (the initialize response), pointing at the files below;
  - the site's favicon (/favicon.ico, /favicon.svg, /apple-touch-icon.png).
The PNG is 256x256, rendered from favicon.svg's 64-unit grid.
"""

from pathlib import Path

from mcp.types import Icon
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

STATIC = Path(__file__).parent / "static"
CACHE = "public, max-age=86400"

_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/icon.svg": ("favicon.svg", "image/svg+xml"),
    "/apple-touch-icon.png": ("icon.png", "image/png"),
    "/icon.png": ("icon.png", "image/png"),
}


def server_icons(base_url: str) -> list[Icon]:
    return [
        Icon(src=f"{base_url}/favicon.svg", mimeType="image/svg+xml", sizes=["any"]),
        Icon(src=f"{base_url}/icon.png", mimeType="image/png", sizes=["256x256"]),
    ]


def _endpoint(filename: str, media_type: str):
    data = (STATIC / filename).read_bytes()

    async def serve(request: Request) -> Response:
        return Response(data, media_type=media_type, headers={"Cache-Control": CACHE})

    return serve


def icon_routes() -> list[Route]:
    return [Route(path, endpoint=_endpoint(name, mt), methods=["GET", "HEAD"])
            for path, (name, mt) in _FILES.items()]
