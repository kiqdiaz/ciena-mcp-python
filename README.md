# Ciena MCP Client

Aplicación en Python para autenticarse y consultar la API del Ciena MCP
(Manage, Control and Plan): inventario de red (`nsi`) y métricas de
performance monitoring (`pm`).

## Requisitos

- Python 3.10+
- Acceso de red al MCP (`MCP_SERVER_IP` en `.env`)
- Un usuario válido en el MCP con rol suficiente para consultar inventario y métricas

## Instalación

```bash
cd /home/sandbox/ciena
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuración

Las credenciales se leen del archivo `.env` (no se versiona, ver `.gitignore`):

```
KEY_ID=...
KEY_SECRET=...
MCP_SERVER_IP=10.0.0.1
MCP_USER=...
MCP_PASSWORD=...
```

`MCP_SERVER_IP`, `MCP_USER` y `MCP_PASSWORD` son los que usa `MCPClient` para
loguearse. `KEY_ID`/`KEY_SECRET` quedan reservados para un futuro flujo de API
key (el grant `client_credentials` no está habilitado actualmente en este MCP).

## Uso

Con el entorno virtual activado:

```bash
source .venv/bin/activate
python main.py
```

`main.py` es un ejemplo que:
1. Lista los network constructs (`nsi.network_constructs()`).
2. Toma el primer NE y lista su equipment, filtrando por `"TRM"` en el nombre.
3. Consulta la métrica `RX_OPTICAL_POWER` de la última hora para la facilidad
   `PTP-1-3-1` de ese NE (`pm.query_metrics(...)`).

### Notebook tutorial: `ciena_mcp_tutorial.ipynb`

Si los conceptos de la API (network construct, equipment, facility, PTP,
parameter...) no te resultan familiares, abre `ciena_mcp_tutorial.ipynb`: es
una guía paso a paso, con llamadas reales a la API y su salida ya ejecutada,
que explica cada objeto a medida que lo va obteniendo.

Para poder abrirlo/re-ejecutarlo instala las dependencias extra de notebook:

```bash
source .venv/bin/activate
pip install -r requirements-notebook.txt
jupyter notebook ciena_mcp_tutorial.ipynb
```

(También se abre directamente desde el explorador de notebooks de VS Code si
seleccionas el intérprete de `.venv` como kernel.)

### Usar el cliente en tu propio script

```python
from mcp_client import MCPClient, NSIService, PMService

client = MCPClient()
nsi = NSIService(client)
pm = PMService(client)

constructs = nsi.network_constructs()
ne = constructs[0]

equipment = nsi.equipment(ne["id"])

metrics = pm.query_metrics(
    network_element_name=ne["attributes"]["name"],
    facility_name_native="PTP-1-3-1",
    parameter="RX_OPTICAL_POWER",
    range_unit="HOURS",
    range_value=1,
)
```

## Manejo de sesiones/token

El MCP limita el número de sesiones concurrentes por usuario
(`concurrentSessionMax`, visible en `/tron/api/v1/current-user`). Por eso:

- `MCPAuth` cachea el token obtenido en `.mcp_token_cache.json` (gitignored) y
  lo reutiliza mientras siga vigente, en vez de loguearse en cada ejecución.
- Si el token cacheado expiró o el servidor responde `401`, `MCPClient` hace
  login de nuevo automáticamente.
- Si ves el error `"Maximum number of active sessions reached"` al correr la
  app, significa que ya tienes las sesiones máximas abiertas (por ejemplo,
  una sesión web activa). Cierra sesiones desde la consola web del MCP
  (usuario → sesiones activas) y vuelve a intentar.
- Para forzar un logout limpio desde código: `client.logout()` (llama a
  `POST /tron/api/v1/logout` y borra el cache local).

## Estructura del proyecto

```
mcp_client/
  auth.py     login (/tron/api/v1/tokens), cache de token, logout
  client.py   cliente HTTP base: agrega el Bearer token, reintenta en 401
  nsi.py      wrapper de /nsi/api/v7 (network constructs, equipment)
  pm.py       wrapper de /pm/api/v3 (query de métricas)
main.py       script de ejemplo
```

## Endpoints del MCP descubiertos

- Auth/sesiones: `/tron/api/v1/*` (`tokens`, `logout`, `current-user`,
  `sessions`, `users`, `api-keys`, `oauth2/tokens`, ...)
- Inventario de red: `/nsi/api/v7/*` (`networkConstructs`, `equipment`, ...)
- Métricas PM: `/pm/api/v3/query/metrics` (POST, filtros tipo JSON:API)

`GET /tron/api/v1/` (sin autenticación) devuelve el mapa completo de recursos
disponibles del servicio de seguridad/auth si necesitas explorar más.
