#!/usr/bin/env python3
"""Últimas muestras de potencia RX óptica (Ciena MCP) de los puertos ópticos de uno o varios NEs.

Recorre los network constructs (hosts), filtrables por IP y/o nombre, y para
cada uno consulta sus tpes cuyo nativeName tiene la forma PREFIJO-shelf-slot-port
con un prefijo que reporta RX (PTP-, OPTMON-, RAMAN-, AMP-; opcionalmente
acotado a un shelf-slot con --ptp). Para cada puerto consulta el parámetro PM
RX_OPTICAL_POWER y muestra las últimas --count muestras, junto con la
descripción del fre relacionado (si existe).

Ejemplos:
    python ciena_rx_power.py --ip-contains 172.21. --name SJN
    python ciena_rx_power.py --name SJN --ptp 1-3
    python ciena_rx_power.py --ip-contains 172.21. --count 5
    python ciena_rx_power.py --name SJN --raw
    python ciena_rx_power.py --name Celendin --ptp 1-1 --hours 168
"""

import argparse
import json
import re
import sys

from mcp_client import MCPClient, NSIService, PMService

PM_WINDOW_HOURS = 72  # ventana de consulta PM por defecto; el MCP puede ir >24 h atrasado en la recolección
PREFERRED_GRANULARITY = "15_MINUTE"  # el PM devuelve series 15_MINUTE y 24_HOUR para el mismo parámetro

# Prefijos de nativeName que reportan RX_OPTICAL_POWER: PTP- en tarjetas de línea/cliente,
# OPTMON-/RAMAN-/AMP- en equipos fotónicos (p.ej. OPTMON-1-1-8, RAMAN-1-3-8).
RX_PREFIXES = ("PTP", "OPTMON", "RAMAN", "AMP")
PORT_NAME_RE = re.compile(r"^([A-Z]+)-(\d+-\d+-\d+)$")


def nc_name(nc):
    attrs = nc["attributes"]
    return attrs.get("displayData", {}).get("displayName", "") or attrs.get("name", "")


def filter_hosts(constructs, ip_contains, name):
    ip_contains = ip_contains.lower() if ip_contains else None
    name = name.lower() if name else None

    result = []
    for nc in constructs:
        attrs = nc["attributes"]

        if ip_contains and ip_contains not in attrs.get("ipAddress", "").lower():
            continue

        if name:
            names = [attrs.get("name", ""), attrs.get("displayData", {}).get("displayName", "")]
            if not any(name in n.lower() for n in names if n):
                continue

        result.append(nc)
    return result


def ptp_matches(native_name, ptp_filter):
    """nativeName debe ser PREFIJO-shelf-slot-port con PREFIJO en RX_PREFIXES; si hay
    ptp_filter (p.ej. '1-3'), debe coincidir con el shelf-slot (p.ej. PTP-1-3-1, RAMAN-1-3-8)."""
    match = PORT_NAME_RE.match(native_name or "")
    if not match or match.group(1) not in RX_PREFIXES:
        return False
    if not ptp_filter:
        return True
    location = match.group(2)
    return location == ptp_filter or location.startswith(ptp_filter + "-")


def fre_note_msg(attrs):
    """note puede venir como string plano o como {"noteMsg": ..., "lastUpdatedBy": ..., ...}."""
    note = attrs.get("note", "")
    return note.get("noteMsg", "") if isinstance(note, dict) else (note or "")


def find_description(fres, ptp_location):
    """Busca, entre los fres del NE, uno cuyo linkLabel referencie el shelf-slot-port
    del PTP (formato 'NEName:shelf-slot-port') y devuelve su userLabel o note."""
    key = f"{ptp_location.get('neName', '')}:{ptp_location.get('shelf', '')}-{ptp_location.get('slot', '')}-{ptp_location.get('port', '')}"
    for fre in fres:
        attrs = fre["attributes"]
        link_label = attrs.get("linkLabel") or ""
        if key in link_label:
            desc = attrs.get("userLabel") or fre_note_msg(attrs)
            if desc:
                return desc
    return ""


def rx_samples(pm, ne_name, facility_name, count, hours):
    metrics = pm.query_metrics(
        network_element_name=ne_name,
        facility_name_native=facility_name,
        parameter="RX_OPTICAL_POWER",
        range_unit="HOURS",
        range_value=hours,
    )
    if not metrics:
        return []

    metric = next(
        (m for m in metrics if m["attributes"].get("tags", {}).get("granularity") == PREFERRED_GRANULARITY),
        metrics[0],
    )
    unit = metric["attributes"].get("tags", {}).get("unit", "")
    values = metric["attributes"].get("values", {})
    timestamps = sorted(values.keys(), reverse=True)[:count]
    return [
        {"timestamp": ts, "value": values[ts].get("value"), "condition": values[ts].get("condition"), "unit": unit}
        for ts in timestamps
    ]


def build_report(nsi, pm, hosts, ptp_filter, count, hours):
    rows = []
    for nc in hosts:
        ne_name = nc["attributes"].get("name", "")
        tpes = nsi.tpes(nc["id"])
        ptps = [t for t in tpes if ptp_matches(t["attributes"].get("nativeName", ""), ptp_filter)]
        if not ptps:
            continue

        fres = nsi.facility_resources(nc["id"])

        for tpe in ptps:
            attrs = tpe["attributes"]
            native_name = attrs.get("nativeName", "")
            location = attrs.get("locations", [{}])[0]
            description = find_description(fres, location)

            samples = rx_samples(pm, ne_name, native_name, count, hours)
            if not samples:
                rows.append({
                    "ne": ne_name, "ptp": native_name, "description": description,
                    "timestamp": "", "rx": "sin datos",
                })
                continue

            for s in samples:
                rx = f"{s['value']} {s['unit']}".strip() if s["value"] is not None else (s["condition"] or "sin datos")
                rows.append({
                    "ne": ne_name, "ptp": native_name, "description": description,
                    "timestamp": s["timestamp"], "rx": rx,
                })
    return rows


def print_report(rows, raw):
    if raw:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    header = f"{'NE':<20} {'PTP':<15} {'DESCRIPTION':<30} {'TIMESTAMP':<22} {'RX':<12}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r['ne']:<20} {r['ptp']:<15} {r['description']:<30} {r['timestamp']:<22} {r['rx']:<12}")

    print(f"\n{len(rows)} muestras")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Últimas muestras de potencia RX óptica de los puertos ópticos del Ciena MCP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ip-contains", metavar="TEXT", help="Filtra hosts cuya IP contenga TEXT, p.ej. 172.21.")
    parser.add_argument("--name", metavar="TEXT", help="Filtra hosts cuyo nombre contenga TEXT (case-insensitive), p.ej. SJN.")
    parser.add_argument("--ptp", metavar="SHELF-SLOT", help="Filtra los puertos por shelf-slot, p.ej. 1-3 (matchea PTP-1-3-1, RAMAN-1-3-8, ...). Sin filtro: todos.")
    parser.add_argument("--count", type=int, default=3, help="Número de muestras más recientes por PTP (default: 3).")
    parser.add_argument("--hours", type=int, default=PM_WINDOW_HOURS, help=f"Ventana de consulta PM en horas (default: {PM_WINDOW_HOURS}).")
    parser.add_argument("--raw", action="store_true", help="Muestra la respuesta completa en JSON en vez de la tabla resumen.")
    return parser


def run(args, client=None):
    client = client or MCPClient()
    nsi = NSIService(client)
    pm = PMService(client)

    constructs = nsi.network_constructs()
    constructs = [
        nc for nc in constructs
        if (nc["attributes"].get("networkConstructType") or "").lower() == "networkelement"
    ]
    hosts = filter_hosts(constructs, args.ip_contains, args.name)

    if not hosts:
        print("No se encontraron hosts que coincidan con los filtros.", file=sys.stderr)
        return

    rows = build_report(nsi, pm, hosts, args.ptp, args.count, args.hours)
    print_report(rows, args.raw)


def main():
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
