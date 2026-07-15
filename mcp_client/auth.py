import json
import time
from pathlib import Path

import requests

TOKEN_CACHE_PATH = Path(__file__).resolve().parent.parent / ".mcp_token_cache.json"


class MCPAuthError(Exception):
    pass


class MCPAuth:
    """Maneja el login contra /tron/api/v1/tokens y cachea el token en disco.

    La cuenta tiene concurrentSessionMax limitado (ver /tron/api/v1/current-user),
    por lo que reutilizamos el token cacheado mientras siga vigente en vez de
    loguearnos en cada ejecución.
    """

    def __init__(self, base_url, username, password, verify_ssl=False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._token = None
        self._expires_at = None

    def get_token(self):
        if self._token is None:
            self._load_cached_token()
        if self._token is None or self._is_expired():
            self._login()
        return self._token

    def _is_expired(self):
        if self._expires_at is None:
            return False
        return time.time() >= self._expires_at

    def _load_cached_token(self):
        if not TOKEN_CACHE_PATH.exists():
            return
        try:
            cached = json.loads(TOKEN_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return
        if cached.get("username") != self.username:
            return
        self._token = cached.get("token")
        self._expires_at = cached.get("expires_at")
        if self._is_expired():
            self._token = None
            self._expires_at = None

    def _save_cached_token(self):
        TOKEN_CACHE_PATH.write_text(json.dumps({
            "username": self.username,
            "token": self._token,
            "expires_at": self._expires_at,
        }))
        TOKEN_CACHE_PATH.chmod(0o600)

    def _login(self):
        resp = requests.post(
            f"{self.base_url}/tron/api/v1/tokens",
            json={"username": self.username, "password": self.password},
            verify=self.verify_ssl,
            timeout=15,
        )
        if resp.status_code != 201:
            raise MCPAuthError(f"Login failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        self._token = data["token"]
        timeout = data.get("timeout") or 3600
        self._expires_at = time.time() + timeout
        self._save_cached_token()

    def logout(self):
        if self._token is None:
            return
        requests.post(
            f"{self.base_url}/tron/api/v1/logout",
            headers={"Authorization": f"Bearer {self._token}"},
            verify=self.verify_ssl,
            timeout=15,
        )
        self._token = None
        self._expires_at = None
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
