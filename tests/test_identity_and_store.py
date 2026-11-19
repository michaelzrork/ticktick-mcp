"""The owner check and the token store."""

import json
import os
import stat

from ticktick_mcp import oauth
from ticktick_mcp.oauth import identity_matches
from ticktick_mcp.token_store import TokenStore, token_hash

OWNER = {"name": "Me", "avatar": "a", "user_id": None, "project_ids": ["p1", "p2", "p3", "p4"]}


# ==================== identity_matches ====================

def test_first_sign_in_is_accepted_and_locked():
    ok, why = identity_matches(OWNER, None, None)
    assert ok and "first" in why


def test_user_id_decides_when_both_sides_know_it():
    assert identity_matches({**OWNER, "user_id": "1"}, {**OWNER, "user_id": "1"}, None)[0]
    # Same name and projects, different id: refused.
    assert not identity_matches({**OWNER, "user_id": "2"}, {**OWNER, "user_id": "1"}, None)[0]


def test_configured_user_id_beats_everything():
    assert not identity_matches({**OWNER, "user_id": "2"}, None, "1")[0]
    assert identity_matches({**OWNER, "user_id": "1", "name": "Renamed"}, OWNER, "1")[0]


def test_name_and_most_projects_are_needed_without_an_id():
    assert identity_matches({**OWNER, "project_ids": ["p1", "p2", "p9"]}, OWNER, None)[0]
    # One shared project is not enough.
    assert not identity_matches({**OWNER, "project_ids": ["p1", "x", "y"]}, OWNER, None)[0]
    assert not identity_matches({**OWNER, "name": "Someone"}, OWNER, None)[0]


def test_avatar_stands_in_when_there_are_no_projects():
    ref = {**OWNER, "project_ids": []}
    assert identity_matches({**ref}, ref, None)[0]
    assert not identity_matches({**ref, "avatar": "b"}, ref, None)[0]


def test_redirect_uri_reuses_the_variable_only_when_it_is_ours(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://tt.example.com")
    monkeypatch.setenv("TICKTICK_REDIRECT_URI", "http://localhost:8080/callback")
    assert oauth.ticktick_redirect_uri() == "https://tt.example.com/oauth/callback"
    monkeypatch.setenv("TICKTICK_REDIRECT_URI", "https://tt.example.com/oauth/callback/")
    assert oauth.ticktick_redirect_uri() == "https://tt.example.com/oauth/callback/"
    assert oauth.redirect_uri_source() == "TICKTICK_REDIRECT_URI"


def test_public_url_comes_from_railway(monkeypatch):
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "ticktick-mcp.up.railway.app")
    assert oauth.public_url() == "https://ticktick-mcp.up.railway.app"


# ==================== TokenStore ====================

def test_store_survives_a_restart_and_is_private(tmp_path):
    s = TokenStore(str(tmp_path))
    s.set_ticktick_token({"access_token": "t", "expires_at": 1})
    s.set_owner({"name": "Me"})
    s.put("access", token_hash("abc"), {"expires_at": 9e12})
    s.mark_connected()
    mode = stat.S_IMODE(os.stat(s.path).st_mode)
    assert mode == 0o600

    again = TokenStore(str(tmp_path))
    assert again.ticktick_token()["access_token"] == "t"
    assert again.owner() == {"name": "Me"}
    assert again.get("access", token_hash("abc"))
    assert again.connected_at()
    # Tokens are stored by hash, never as themselves.
    assert "abc" not in open(s.path).read()


def test_store_prunes_expired_rows(tmp_path):
    s = TokenStore(str(tmp_path))
    s.put("codes", "old", {"expires_at": 1})
    s.put("codes", "new", {"expires_at": 9e12})
    s.prune()
    assert s.get("codes", "old") is None and s.get("codes", "new")


def test_store_delete_where_and_pop(tmp_path):
    s = TokenStore(str(tmp_path))
    s.put("access", "a1", {"refresh": "r"})
    s.put("access", "a2", {"refresh": "r"})
    s.put("access", "a3", {"refresh": "other"})
    assert s.delete_where("access", refresh="r") == 2
    assert s.pop("access", "a3") == {"refresh": "other"}
    assert s.pop("access", "a3") is None


def test_a_corrupt_store_does_not_take_the_server_down(tmp_path):
    (tmp_path / "mcp_store.json").write_text("{not json")
    s = TokenStore(str(tmp_path))
    assert s.ticktick_token() is None
    s.set_owner({"name": "x"})
    assert json.loads((tmp_path / "mcp_store.json").read_text())["owner"] == {"name": "x"}


def test_persistent_follows_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert TokenStore().persistent()
    assert TokenStore().dir == str(tmp_path)
