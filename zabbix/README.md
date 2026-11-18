# Template Zabbix 7.0: Ciena MCP by HTTP

`template_ciena_mcp_http.yaml` monitorea la red Ciena a través de la API REST del
MCP con items HTTP agent y `Authorization: Bearer {$CIENA.MCP.TOKEN}`. No hace
falta un agente ni acceso SNMP a los NEs: Zabbix solo habla con el MCP.

Contiene dos templates:

| Template | Se vincula a | Qué hace |
|---|---|---|
| **Ciena MCP by HTTP** | un host que representa al MCP | Chequea la API y el token, cuenta network constructs y descubre cada NE como un host nuevo |
| **Ciena MCP NE by HTTP** | los hosts NE (lo vincula el descubrimiento) | Estado del NE, inventario (NE y equipment) y performance metrics |

## Qué se monitorea

**Host MCP**
- `MCP: API HTTP status`: código HTTP de `GET /tron/api/v1/current-user`. Triggers: token rechazado (401), status inesperado, API sin respuesta.
- Network constructs: total, sin sincronizar, sin conexión, con inventario desincronizado.
- **Network elements discovery**: un host por NE (`ciena-<id>`, nombre visible = displayName del NE), en el grupo `Ciena/Network elements` y en `Ciena/Sites/<sitio>`.

**Cada host NE**
- Estado: association state (`Connected`) y sync state (`synchronized`), con triggers.
- Inventario del NE: versión de SW, serie, modelo, MAC, sitio, latitud/longitud. Llena el **inventario de host** de Zabbix (sirve para el widget Geomap).
- **Equipment discovery**: por tarjeta/módulo: estado, número de serie, part number y tipo. Triggers: tarjeta fuera de servicio, tarjeta reemplazada (cambió la serie), equipment retirado, specification mismatch.
- **PM series discovery**: último bin de 15 min de cada serie (facility + parámetro). Por defecto: `RX_OPTICAL_POWER`, `TX_OPTICAL_POWER`, factor Q (`QUALITY_FACTOR_AVG`, `QUALITY_FACTOR_MIN`) y `PREFEC_BER`. Triggers: condition OOR (pérdida de señal), umbral bajo/alto y desviación respecto del promedio del último día.

Llamadas al MCP: una por NE cada 5 min (estado), una por NE cada 15 min (PM),
una por NE por hora (equipment) y dos del host MCP por minuto o cada 5 min.

## Instalación

1. **Importar** `template_ciena_mcp_http.yaml` en *Data collection → Templates → Import*.
2. **Macros** (*Administration → Macros*, globales):
   - `{$CIENA.MCP.URL}` = `https://<MCP_SERVER_IP>` (sin `/` final)
   - `{$CIENA.MCP.TOKEN}` = token del MCP, tipo **Secret text** (ver abajo)

   También pueden definirse en el host MCP y los NE descubiertos las heredan,
   pero un token nuevo les llega recién en la siguiente corrida del LLD (hasta 1 h).
   Con macros globales el cambio se aplica de inmediato.
3. **Crear un host** (p.ej. `Ciena MCP`, sin interfaces) y vincularle **Ciena MCP by HTTP**.
   No vincular **Ciena MCP NE by HTTP** a mano: los hosts NE los crea el descubrimiento.
   Si el Zabbix server no llega al MCP, elegir en *Monitored by* un proxy que sí llegue;
   los hosts NE descubiertos usan el mismo proxy.
4. Para empezar con un solo NE, ver la sección siguiente.

## Probar con un solo NE

En el host MCP (*Macros* del host, no del template) agregar:

```
{$CIENA.NE.NAME.MATCHES} = ^LURIN$
```

- Es una regex y se compara contra el `name` del NE y contra su `displayName`
  (en algunos NEs difieren, p.ej. `C65bSJNREP` / `C65bSJNREP_REDSUR`); sirve cualquiera.
  Usar `^...$` para que `LURIN` no coincida también con `LURIN2`.
- Para descubrir todos los NEs: **borrar la macro del host** (vuelve al default
  del template, `.*`) o **dejarla vacía**. Las dos opciones descubren todo.
- El cambio se aplica en la siguiente consulta de network constructs
  (`{$CIENA.NC.INTERVAL}`, 5 min). Para no esperar: *Execute now* sobre el item
  `MCP: Get network constructs`.
- Si se vuelve a acotar el filtro, los hosts que ya no coinciden quedan como
  "no descubiertos" y se borran a los 7 días (lifetime de la regla).

También se puede acotar por IP (`{$CIENA.NE.IP.MATCHES}`, p.ej. `^172\.21\.`)
o por sitio (`{$CIENA.NE.SITE.MATCHES}`); todos los filtros se combinan con AND.

## Token

El MCP entrega el token en `POST /tron/api/v1/tokens` y acepta un parámetro
`timeout` (segundos); en este MCP el default es 30 días. Las cuentas tienen
un máximo de sesiones concurrentes, así que conviene un **usuario dedicado a Zabbix**.

`ciena_mcp_token_refresh.py` genera un token y lo escribe en la macro vía la API de Zabbix:

```bash
export ZABBIX_URL=https://zabbix.example.com
export ZABBIX_API_TOKEN=...            # User settings → API tokens
export ZABBIX_MCP_USER=zabbix-ro ZABBIX_MCP_PASSWORD=...   # opcional; si no, usa MCP_USER/MCP_PASSWORD
python zabbix/ciena_mcp_token_refresh.py                      # macro global
python zabbix/ciena_mcp_token_refresh.py --host "Ciena MCP"   # macro del host MCP
```

Programarlo con cron con una frecuencia menor que la vigencia del token, p.ej. semanal:

```
0 6 * * 1  cd /ruta/ciena && .venv/bin/python zabbix/ciena_mcp_token_refresh.py
```

El token anterior se revoca en la corrida siguiente, no en la misma, para que
ningún item quede usando un token ya revocado. Por eso el usuario del MCP
necesita `concurrentSessionMax` ≥ 2. Los tokens emitidos se guardan en
`zabbix/.mcp_zabbix_token_state.json` (permisos 600, en `.gitignore`).

Si el token vence o se revoca, el trigger **Ciena MCP: API token rejected** lo avisa.

## Ajustes por macro

| Macro | Default | Uso |
|---|---|---|
| `{$CIENA.NE.NAME.MATCHES}` / `NOT_MATCHES` | `.*` / `CHANGE_IF_NEEDED` | NEs a descubrir por name o displayName |
| `{$CIENA.NE.IP.MATCHES}`, `{$CIENA.NE.SITE.MATCHES}` | `.*` | NEs a descubrir por IP o sitio |
| `{$CIENA.NC.TYPE}` | `networkElement` | `networkConstructType` a descubrir |
| `{$CIENA.PM.FILTER}` | RX/TX power, Q, pre-FEC BER | Condición JSON del filtro PM del MCP (ver abajo) |
| `{$CIENA.PM.FACILITY.MATCHES}` / `NOT_MATCHES` | `.*` / `CHANGE_IF_NEEDED` | Facilities PM a descubrir, p.ej. `^PTP-` |
| `{$CIENA.PM.CONDITION.DISCOVER.NOT_MATCHES}` | `^OOR$` | No descubre series sin señal (puertos sin uso, -99 dBm) |
| `{$CIENA.PM.LOW}` / `{$CIENA.PM.HIGH}` | deshabilitados | Umbrales PM, con contexto `"PARAMETRO:FACILITY"` |
| `{$CIENA.PM.DELTA.MAX}` | 3 dB RX/TX power, 1 dBQ factor Q | Desviación máxima respecto del promedio de `{$CIENA.PM.BASELINE.PERIOD}` (1d) |
| `{$CIENA.EQPT.NAME.NOT_MATCHES}` | `^FILLER` | Equipment a excluir |
| `{$CIENA.EQPT.STATE.OK.MATCHES}` | `^(IS\|)$` | Estados de equipment considerados OK |
| `{$CIENA.NE.STATUS.INTERVAL}` / `{$CIENA.PM.INTERVAL}` / `{$CIENA.EQPT.INTERVAL}` | 5m / 15m / 1h | Frecuencias de consulta |

Ejemplos de umbrales PM (macros con contexto, en el template, el host NE o globales):

```
{$CIENA.PM.LOW:regex:"^RX_OPTICAL_POWER:PTP-"}     = -18
{$CIENA.PM.LOW:"RX_OPTICAL_POWER:AMP-1-1-4"}       = -25
{$CIENA.PM.DELTA.MAX:"RX_OPTICAL_POWER:PTP-1-5-11"} = 2
```

## Factor Q

El MCP reporta el factor Q de forma nativa en los puertos coherentes (facilities
OTSI, p.ej. `PTP-1-5-11`): `QUALITY_FACTOR_AVG`, `QUALITY_FACTOR_MIN`,
`QUALITY_FACTOR_MAX` y `QUALITY_FACTOR__STANDARD_DEVIATION`, en dBQ.

Además, por cada serie `PREFEC_BER` / `PREFEC_BER_MAX`, el template calcula
`Q_FROM_PREFEC_BER` / `Q_FROM_PREFEC_BER_MAX` (dBQ) a partir del BER pre-FEC:

```
Q(dB) = 20·log10( √2 · erfcinv(2·BER) )
```

Sirve para tarjetas que reportan BER pero no Q, y como contraste del valor
nativo. En LURIN `PTP-1-5-11`: BER 1.3e-3 → 9.58 dBQ calculado vs 9.57 dBQ
nativo. El MCP redondea el BER a 2 cifras, así que el Q calculado tiene una
resolución de unos ±0.05 dB. Con BER = 0 no hay Q finito y no se guarda valor.

El factor Q solo existe en puertos de línea coherentes. En amplificadores,
OPTMON o puertos cliente no hay series de Q/BER, así que no se crean items.

Alertas de Q: por defecto, desviación de más de 1 dBQ respecto del promedio
del último día. El umbral absoluto depende del modem y de la FEC, así que va
por macro, p.ej.:

```
{$CIENA.PM.LOW:regex:"^(QUALITY_FACTOR_MIN|Q_FROM_PREFEC_BER):"} = 7
```

## Otros parámetros PM

Para agregar otros parámetros PM, ampliar el filtro. Los nombres de parámetro
disponibles salen del propio MCP (p.ej. `ERRORED_SECONDS`, `UNAVAILABLE_SECONDS`,
`RX_OPTICAL_POWER_MIN`):

```
{$CIENA.PM.FILTER} = ["or",["=","parameter","RX_OPTICAL_POWER"],["=","parameter","TX_OPTICAL_POWER"],["=","parameter","QUALITY_FACTOR_AVG"],["=","parameter","QUALITY_FACTOR_MIN"],["=","parameter","PREFEC_BER"],["=","parameter","ERRORED_SECONDS"]]
```

El filtro PM del MCP no acepta el operador `in`: hay que usar `["or", ...]`.

## Problemas comunes

**`Cannot perform request: Connection timed out after 30001 milliseconds`**:
el Zabbix server (o el proxy del host) no logra abrir la conexión TCP a
`{$CIENA.MCP.URL}`. No es un problema de token ni del template, sino de red o
de URL. Se puede diagnosticar sin acceso SSH, desde la interfaz web:

1. Revisar que la macro sea exactamente `https://<IP_MCP>` (sin `/` final).
2. En cualquier host, *Items → Create item*, tipo **Simple check**, key
   `net.tcp.service[tcp,<IP_MCP>,443]`, y usar **Test → Get value from host**
   (no hace falta guardarlo). `1` = hay conectividad; `0` = firewall o falta una ruta.
   Si el host tiene proxy, la prueba sale desde ese proxy.
3. Si da `0`, elegir otro *Monitored by* (server o proxy) en el host MCP y repetir.

## Notas

- Los items "Get ..." son los maestros HTTP (sin historial); el resto son
  dependientes y se calculan con preprocesamiento, sin llamadas extra.
- Las muestras PM con condition `OOR` no guardan valor (el MCP reporta `-99`),
  así no ensucian gráficos ni promedios; el trigger de condition las cubre.
- Validado en Zabbix 7.0 contra el MCP real: import sin errores, filtro de
  descubrimiento (por name, por displayName y vacío), inventario, equipment,
  PM y factor Q (nativo y calculado) con valores reales.
