#!/usr/bin/env python3
"""Inventario completo de equipment (Ciena MCP): equipo + network construct al que pertenece.

Recorre todos los network constructs y, para cada uno, su equipment (nombre,
serial number, part number). Se puede filtrar por parte del direccionamiento
IP del network construct, parte de su hostname y/o parte del nombre del
equipment; si se pasan varios filtros se aplican en conjunto (AND).

Ejemplos:
    python ciena_inventario.py
    python ciena_inventario.py --ip-contains 10.0.
    python ciena_inventario.py --name-contains SITE1
    python ciena_inventario.py --ip-contains 10.0. --name-contains SITE1
    python ciena_inventario.py --equipment-contains TRM-
    python ciena_inventario.py --ip-contains 10.0. --raw
"""

import argparse
import json
import sys

from mcp_client import MCPClient, NSIService


def nc_name(nc):
    attrs = nc["attributes"]
    return attrs.get("displayData", {}).get("displayName", "") or attrs.get("name", "")


def filter_network_constructs(constructs, ip_contains, name_contains):
    ip_contains = ip_contains.lower() if ip_contains else None
    name_contains = name_contains.lower() if name_contains else None

    result = []
    for nc in constructs:
        attrs = nc["attributes"]

        if ip_contains and ip_contains not in attrs.get("ipAddress", "").lower():
            continue

        if name_contains:
            names = [attrs.get("name", ""), attrs.get("displayData", {}).get("displayName", "")]
            if not any(name_contains in n.lower() for n in names if n):
                continue

        result.append(nc)
    return result


def build_inventory(nsi, constructs):
    """Devuelve una lista de filas {network_construct, equipment} para cada equipo."""
    rows = []
    for nc in constructs:
        for eq in nsi.equipment(nc["id"]):
            rows.append({"network_construct": nc, "equipment": eq})
    return rows


def filter_inventory(rows, equipment_contains):
    if not equipment_contains:
        return rows

    equipment_contains = equipment_contains.lower()
    result = []
    for r in rows:
        eq_attrs = r["equipment"]["attributes"]
        names = [eq_attrs.get("name", ""), eq_attrs.get("displayData", {}).get("displayName", "")]
        if any(equipment_contains in n.lower() for n in names if n):
            result.append(r)
    return result


def print_inventory(rows, raw):
    if raw:
        print(json.dumps(
            [{"network_construct": r["network_construct"], "equipment": r["equipment"]} for r in rows],
            indent=2,
            ensure_ascii=False,
        ))
        return

    header = f"{'NETWORK_CONSTRUCT':<25} {'IP':<16} {'EQUIPMENT':<20} {'SERIAL_NUMBER':<20} {'PART_NUMBER':<20} {'TYPE':<30}"
    print(header)
    print("-" * len(header))
    for r in rows:
        nc_attrs = r["network_construct"]["attributes"]
        eq_attrs = r["equipment"]["attributes"]
        installed_spec = eq_attrs.get("installedSpec", {})

        print(
            f"{nc_name(r['network_construct']):<25} "
            f"{nc_attrs.get('ipAddress', ''):<16} "
            f"{eq_attrs.get('displayData', {}).get('displayName', ''):<20} "
            f"{installed_spec.get('serialNumber', ''):<20} "
            f"{installed_spec.get('partNumber', ''):<20} "
            f"{installed_spec.get('type', ''):<30}"
        )

    print(f"\n{len(rows)} equipos en {len({r['network_construct']['id'] for r in rows})} network constructs")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Inventario de equipment del Ciena MCP, agrupado por network construct.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--ip-contains",
        metavar="TEXT",
        help="Filtra network constructs cuya IP contenga TEXT, p.ej. 10.0.",
    )
    parser.add_argument(
        "--name-contains",
        metavar="TEXT",
        help="Filtra network constructs cuyo nombre/hostname contenga TEXT (case-insensitive), p.ej. SITE1.",
    )
    parser.add_argument(
        "--type",
        metavar="TYPE",
        default="networkElement",
        help="Filtra network constructs por networkConstructType, p.ej. networkElement, shelf, osrpNode, manual. "
             "Usa 'all' para no filtrar (default: networkElement).",
    )
    parser.add_argument(
        "--equipment-contains",
        metavar="TEXT",
        help="Filtra equipment cuyo nombre contenga TEXT (case-insensitive), p.ej. TRM-.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Muestra la respuesta completa en JSON en vez de la tabla resumen.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    client = MCPClient()
    nsi = NSIService(client)

    constructs = nsi.network_constructs()
    if args.type.lower() != "all":
        constructs = [
            nc for nc in constructs
            if (nc["attributes"].get("networkConstructType") or "").lower() == args.type.lower()
        ]
    constructs = filter_network_constructs(constructs, args.ip_contains, args.name_contains)

    if not constructs:
        print("No se encontraron network constructs que coincidan con los filtros.", file=sys.stderr)
        return

    rows = build_inventory(nsi, constructs)
    rows = filter_inventory(rows, args.equipment_contains)
    print_inventory(rows, args.raw)


if __name__ == "__main__":
    main()
