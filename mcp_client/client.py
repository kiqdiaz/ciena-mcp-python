import os

import requests
import urllib3
from dotenv import load_dotenv

from .auth import MCPAuth

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class MCPClient:
    """Cliente base para la API de Ciena MCP.

    Maneja auth (login/token cacheado) y expone request() para que los
    wrappers de cada servicio (nsi, pm, ...) hagan sus llamadas.
    """

    def __init__(self, base_url=None, username=None, password=None, verify_ssl=False):
        self.base_url = (base_url or f"https://{os.environ['MCP_SERVER_IP']}").rstrip("/")
        self.verify_ssl = verify_ssl
        self.auth = MCPAuth(
            base_url=self.base_url,
            username=username or os.environ["MCP_USER"],
            password=password or os.environ["MCP_PASSWORD"],
            verify_ssl=verify_ssl,
        )

    def request(self, method, path, retry_on_401=True, **kwargs):
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.auth.get_token()}"

        resp = requests.request(method, url, headers=headers, verify=self.verify_ssl, timeout=30, **kwargs)

        if resp.status_code == 401 and retry_on_401:
            self.auth._token = None
            self.auth._expires_at = None
            return self.request(method, path, retry_on_401=False, **kwargs)

        resp.raise_for_status()
        return resp.json() if resp.content else None

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def logout(self):
        self.auth.logout()
