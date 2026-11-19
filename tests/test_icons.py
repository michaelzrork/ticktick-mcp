"""The connector icon: served as favicons, and named in MCP serverInfo."""

from starlette.testclient import TestClient

from ticktick_mcp.mcp_instance import mcp


def _app():
    import main
    # No `with`: the lifespan (MCP session manager) is not needed for static files,
    # and it may be started only once per process (test_oauth_flow does that).
    return TestClient(main.build_app(), base_url="https://tt.example.com")


def test_favicon_routes():
    c = _app()
    expected = {
        "/favicon.ico": "image/x-icon",
        "/favicon.svg": "image/svg+xml",
        "/apple-touch-icon.png": "image/png",
        "/icon.png": "image/png",
    }
    for path, media in expected.items():
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith(media), path
        assert "max-age" in r.headers["cache-control"]
        assert c.head(path).status_code == 200
    assert c.get("/icon.png").content[:8] == b"\x89PNG\r\n\x1a\n"
    assert c.get("/favicon.ico").content[:4] == b"\x00\x00\x01\x00"
    assert b"<svg" in c.get("/favicon.svg").content


def test_png_is_256_square():
    import struct
    data = _app().get("/icon.png").content
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (256, 256)


def test_icons_need_no_sign_in():
    # The connector list shows the icon before anyone has signed in.
    assert _app().get("/favicon.svg", headers={}).status_code == 200


def test_server_info_names_the_icons():
    opts = mcp._mcp_server.create_initialization_options()
    srcs = {i.src for i in opts.icons}
    assert any(s.endswith("/favicon.svg") for s in srcs)
    assert any(s.endswith("/icon.png") for s in srcs)
    assert opts.website_url
