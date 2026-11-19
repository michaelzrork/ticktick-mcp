"""
Persistent storage for the MCP sign-in and the TickTick token.

One small JSON file in DATA_DIR. On Railway a volume is mounted at /data and
DATA_DIR=/data, so a redeploy keeps it. Without a volume the file lives on the
container's disk and is lost on every deploy: Claude then has to reconnect, and
the TickTick token falls back to the TICKTICK_ACCESS_TOKEN variable if one is set.

What it holds:
  clients        OAuth clients that registered themselves (Claude, ChatGPT, ...)
  codes          one-time sign-in codes (minutes)
  access         MCP access tokens, by SHA-256 of the token (never the token itself)
  refresh        MCP refresh tokens, same
  pending        sign-ins waiting on TickTick
  ticktick_token the owner's TickTick OpenAPI token from the browser sign-in
  owner          who the owner's TickTick account is (see oauth.py: the official
                 API has no user id, so this is a fingerprint), locked at the
                 first accepted sign-in
  connected_at   when an AI app first connected through /mcp; the old open /sse
                 endpoint switches itself off from then on
"""

import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional


def data_dir() -> str:
    """Where everything persistent lives: DATA_DIR, else /data if writable, else ./data."""
    env = os.environ.get("DATA_DIR")
    if env:
        return env
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "/data"
    return os.path.join(os.getcwd(), "data")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenStore:
    SECTIONS = ("clients", "codes", "access", "refresh", "pending")
    SINGLES = ("ticktick_token", "owner", "connected_at")

    def __init__(self, directory: Optional[str] = None):
        self.dir = directory or data_dir()
        self.path = os.path.join(self.dir, "mcp_store.json")
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {s: {} for s in self.SECTIONS}
        self._data.update({k: None for k in self.SINGLES})
        self._load()

    # -- file ----------------------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for s in self.SECTIONS:
                self._data[s] = data.get(s) or {}
            for k in self.SINGLES:
                self._data[k] = data.get(k)
        except FileNotFoundError:
            pass
        except Exception as e:  # a corrupt file must not take the server down
            print(f"[token_store] could not read {self.path}: {e}")

    def _save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".mcp_store.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.chmod(tmp, 0o600)  # holds a TickTick token
            os.replace(tmp, self.path)  # atomic: never a half-written file
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def persistent(self) -> bool:
        """True when the file sits on what looks like a mounted volume."""
        return os.path.ismount(self.dir) or bool(os.environ.get("DATA_DIR"))

    # -- generic sections ----------------------------------------------------
    def get(self, section: str, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            v = self._data[section].get(key)
            return dict(v) if isinstance(v, dict) else v

    def put(self, section: str, key: str, value: Dict[str, Any]) -> None:
        with self._lock:
            self._data[section][key] = value
            self._save()

    def pop(self, section: str, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            v = self._data[section].pop(key, None)
            if v is not None:
                self._save()
            return v

    def delete_where(self, section: str, **match: Any) -> int:
        with self._lock:
            keys = [k for k, v in self._data[section].items()
                    if all(v.get(f) == want for f, want in match.items())]
            for k in keys:
                del self._data[section][k]
            if keys:
                self._save()
            return len(keys)

    def prune(self) -> None:
        """Drop expired codes, tokens and pending sign-ins."""
        now = time.time()
        with self._lock:
            changed = False
            for section in ("codes", "access", "refresh", "pending"):
                dead = [k for k, v in self._data[section].items()
                        if v.get("expires_at") and v["expires_at"] < now]
                for k in dead:
                    del self._data[section][k]
                    changed = True
            if changed:
                self._save()

    # -- singles -------------------------------------------------------------
    def _get_single(self, key: str):
        with self._lock:
            v = self._data.get(key)
            return dict(v) if isinstance(v, dict) else v

    def _set_single(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def ticktick_token(self) -> Optional[Dict[str, Any]]:
        return self._get_single("ticktick_token")

    def set_ticktick_token(self, info: Dict[str, Any]) -> None:
        self._set_single("ticktick_token", info)

    def owner(self) -> Optional[Dict[str, Any]]:
        return self._get_single("owner")

    def set_owner(self, identity: Dict[str, Any]) -> None:
        self._set_single("owner", identity)

    def connected_at(self) -> Optional[float]:
        return self._get_single("connected_at")

    def mark_connected(self) -> None:
        with self._lock:
            if not self._data.get("connected_at"):
                self._data["connected_at"] = time.time()
                self._save()


_store: Optional[TokenStore] = None
_store_lock = threading.Lock()


def get_store() -> TokenStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = TokenStore()
        return _store


def reset_store() -> None:
    """Forget the process-wide store (tests point DATA_DIR somewhere new)."""
    global _store
    with _store_lock:
        _store = None
