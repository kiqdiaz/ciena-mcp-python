#!/usr/bin/env python3
"""Genera un token del Ciena MCP y lo publica en la macro {$CIENA.MCP.TOKEN} de Zabbix.

Pensado para correr por cron (p.ej. semanal) con un token de MCP de 30 días:
siempre hay un token vigente en Zabbix y nunca vence entre corridas.

El token anterior NO se revoca en la misma corrida: los hosts NE descubiertos
pueden seguir usándolo un rato (caché de configuración del server, o hasta el
próximo LLD si la macro está a nivel de host). Se revoca en la corrida
siguiente, así que el usuario del MCP necesita concurrentSessionMax >= 2.

Variables de entorno (se leen también del .env del proyecto):
    MCP_SERVER_IP                  IP/hostname del MCP
    ZABBIX_MCP_USER / ZABBIX_MCP_PASSWORD
                                   usuario del MCP para Zabbix (si no están, usa MCP_USER / MCP_PASSWORD)
    ZABBIX_URL                     p.ej. https://zabbix.example.com (sin /api_jsonrpc.php)
    ZABBIX_API_TOKEN               token de API de Zabbix con permiso para editar macros

Ejemplos:
    python zabbix/ciena_mcp_token_refresh.py                      # macro global
    python zabbix/ciena_mcp_token_refresh.py --host "Ciena MCP"   # macro del host MCP
    python zabbix/ciena_mcp_token_refresh.py --timeout 604800     # token de 7 días
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TOKEN_MACRO = "{$CIENA.MCP.TOKEN}"
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / ".mcp_zabbix_token_state.json"
MACRO_TYPE_SECRET = 1


def mcp_login(base_url, username, password, timeout, verify_ssl):
    resp = requests.post(
        f"{base_url}/tron/api/v1/tokens",
        json={"username": username, "password": password, "timeout": timeout},
        verify=verify_ssl,
        timeout=15,
    )
    if resp.status_code != 201:
        sys.exit(f"Login al MCP falló ({resp.status_code}): {resp.text}")
    data = resp.json()
    return data["token"], data.get("timeout")


def mcp_revoke(base_url, token, verify_ssl):
    """DELETE /tron/api/v1/tokens/{token}; un token ya vencido/revocado no es error."""
    resp = requests.delete(
        f"{base_url}/tron/api/v1/tokens/{token}",
        headers={"Authorization": f"Bearer {token}"},
        verify=verify_ssl,
        timeout=15,
    )
    return resp.status_code in (200, 202, 204, 401, 404)


class ZabbixAPI:
    def __init__(self, url, api_token, verify_ssl):
        self.url = url.rstrip("/") + "/api_jsonrpc.php"
        self.api_token = api_token
        self.verify_ssl = verify_ssl

    def call(self, method, params):
        resp = requests.post(
            self.url,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            headers={"Authorization": f"Bearer {self.api_token}"},
            verify=self.verify_ssl,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Zabbix API {method}: {body['error'].get('data') or body['error']}")
        return body["result"]

    def set_global_macro(self, macro, value):
        existing = self.call("usermacro.get", {"globalmacro": True, "filter": {"macro": macro}})
        if existing:
            self.call("usermacro.updateglobal", {
                "globalmacroid": existing[0]["globalmacroid"], "value": value, "type": MACRO_TYPE_SECRET,
            })
        else:
            self.call("usermacro.createglobal", {"macro": macro, "value": value, "type": MACRO_TYPE_SECRET})

    def set_host_macro(self, host, macro, value):
        hosts = self.call("host.get", {"filter": {"host": host}, "output": ["hostid"]})
        if not hosts:
            raise RuntimeError(f"No existe el host {host!r} en Zabbix")
        hostid = hosts[0]["hostid"]
        existing = self.call("usermacro.get", {"hostids": hostid, "filter": {"macro": macro}})
        if existing:
            self.call("usermacro.update", {
                "hostmacroid": existing[0]["hostmacroid"], "value": value, "type": MACRO_TYPE_SECRET,
            })
        else:
            self.call("usermacro.create", {"hostid": hostid, "macro": macro, "value": value, "type": MACRO_TYPE_SECRET})


def load_state(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    path.write_text(json.dumps(state))
    path.chmod(0o600)


def required_env(*names):
    for name in names:
        if os.environ.get(name):
            return os.environ[name]
    sys.exit(f"Falta la variable de entorno {' o '.join(names)}")


def build_parser():
    parser = argparse.ArgumentParser(
        description=f"Renueva el token del Ciena MCP y lo guarda en la macro {TOKEN_MACRO} de Zabbix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", metavar="HOST",
                        help="Nombre técnico del host MCP en Zabbix. Sin esta opción se actualiza la macro global.")
    parser.add_argument("--timeout", type=int, default=30 * 24 * 3600,
                        help="Vigencia del token del MCP en segundos (default: 2592000 = 30 días).")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH,
                        help="Archivo donde se guardan los tokens emitidos para revocarlos después.")
    parser.add_argument("--verify-ssl", action="store_true", help="Verificar certificados TLS (MCP y Zabbix).")
    return parser


def main():
    args = build_parser().parse_args()

    mcp_url = f"https://{required_env('MCP_SERVER_IP')}"
    username = required_env("ZABBIX_MCP_USER", "MCP_USER")
    password = required_env("ZABBIX_MCP_PASSWORD", "MCP_PASSWORD")
    zabbix = ZabbixAPI(required_env("ZABBIX_URL"), required_env("ZABBIX_API_TOKEN"), args.verify_ssl)

    state = load_state(args.state_file)

    # Se revoca primero el token de hace dos corridas: libera una sesión antes del login.
    if state.get("previous"):
        if not mcp_revoke(mcp_url, state["previous"], args.verify_ssl):
            print("Aviso: no se pudo revocar el token anterior; vencerá solo.", file=sys.stderr)
        state.pop("previous")
        save_state(args.state_file, state)

    token, granted_timeout = mcp_login(mcp_url, username, password, args.timeout, args.verify_ssl)

    try:
        if args.host:
            zabbix.set_host_macro(args.host, TOKEN_MACRO, token)
            target = f"host {args.host!r}"
        else:
            zabbix.set_global_macro(TOKEN_MACRO, token)
            target = "macro global"
    except Exception:
        mcp_revoke(mcp_url, token, args.verify_ssl)
        raise

    save_state(args.state_file, {"current": token, "previous": state.get("current")})
    print(f"{TOKEN_MACRO} actualizado en {target} (vigencia: {granted_timeout or args.timeout}s)")


if __name__ == "__main__":
    main()
