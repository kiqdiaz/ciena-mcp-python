#!/usr/bin/env python3
"""Lista todos los Network Elements del Ciena MCP con su Site name (discovered).

"Site name (discovered)" en la UI del MCP corresponde a attributes.siteName
(y attributes.siteId) del network construct: es el sitio que el MCP descubre
del propio NE. Si el NE no lo tiene configurado, la columna sale vacía.

Ejemplos:
    python ciena_ne_sites.py
    python ciena_ne_sites.py --sin-site                 # solo NEs sin Site name (discovered)
    python ciena_ne_sites.py --con-site                 # solo NEs con Site name (discovered)
    python ciena_ne_sites.py --site-contains SITE1
    python ciena_ne_sites.py --ip-contains 10.0. --name-contains NODE
    python ciena_ne_sites.py --csv ne_sites.csv
    python ciena_ne_sites.py --type all --raw
"""

import argparse
import csv
import json
import sys

from ciena_inventario import filter_network_constructs
from mcp_client import MCPClient, NSIService

COLUMNS = [
    ("name", "NAME", 16),
    ("display_name", "DISPLAY_NAME", 22),
    ("ip", "IP", 16),
    ("site_name", "SITE_NAME (DISCOVERED)", 24),
    ("site_id", "SITE_ID", 8),
    ("type_group", "TYPE", 12),
    ("software", "SOFTWARE", 10),
    ("sync_state", "SYNC", 14),
    ("association", "ASSOC", 12),
]


def ne_row(nc):
    attrs = nc["attributes"]
    display = attrs.get("displayData", {})
    return {
        "id": nc["id"],
        "name": attrs.get("name", ""),
        "display_name": display.get("displayName", "") or attrs.get("longName", ""),
        "ip": attrs.get("ipAddress", "") or display.get("displayIpAddress", ""),
        "site_name": (attrs.get("siteName") or "").strip(),
        "site_id": str(attrs.get("siteId") or ""),
        "type_group": attrs.get("typeGroup", "") or attrs.get("resourceType", ""),
        "network_construct_type": attrs.get("networkConstructType", ""),
        "software": attrs.get("softwareVersion", ""),
        "sync_state": attrs.get("syncState", {}).get("state", "") or display.get("displaySyncState", ""),
        "association": display.get("displayAssociationState", "") or attrs.get("associationState", ""),
    }


def print_table(rows):
    header = " ".join(f"{title:<{width}}" for _, title, width in COLUMNS)
    print(header)
    print("-" * len(header))
    for r in rows:
        print(" ".join(f"{(r[key] or '-'):<{width}}" for key, _, width in COLUMNS))

    with_site = sum(1 for r in rows if r["site_name"])
    print(f"\n{len(rows)} NEs: {with_site} con Site name (discovered), {len(rows) - with_site} sin él")


def write_csv(rows, path):
    fields = ["id", "name", "display_name", "ip", "site_name", "site_id", "type_group",
              "network_construct_type", "software", "sync_state", "association"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV escrito en {path} ({len(rows)} filas)", file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Network Elements del Ciena MCP con su Site name (discovered).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ip-contains", metavar="TEXT", help="Filtra NEs cuya IP contenga TEXT.")
    parser.add_argument("--name-contains", metavar="TEXT",
                        help="Filtra NEs cuyo nombre/displayName contenga TEXT (case-insensitive).")
    parser.add_argument("--site-contains", metavar="TEXT",
                        help="Filtra NEs cuyo Site name (discovered) contenga TEXT (case-insensitive).")
    site = parser.add_mutually_exclusive_group()
    site.add_argument("--sin-site", action="store_true", help="Solo NEs SIN Site name (discovered).")
    site.add_argument("--con-site", action="store_true", help="Solo NEs CON Site name (discovered).")
    parser.add_argument("--type", metavar="TYPE", default="networkElement",
                        help="networkConstructType a listar; 'all' para no filtrar (default: networkElement).")
    parser.add_argument("--csv", metavar="FILE", help="Además de la tabla, guarda el resultado en un CSV.")
    parser.add_argument("--raw", action="store_true", help="Muestra el JSON completo de cada NE.")
    return parser


def main():
    args = build_parser().parse_args()

    nsi = NSIService(MCPClient())
    constructs = nsi.network_constructs()

    if args.type.lower() != "all":
        constructs = [
            nc for nc in constructs
            if (nc["attributes"].get("networkConstructType") or "").lower() == args.type.lower()
        ]
    constructs = filter_network_constructs(constructs, args.ip_contains, args.name_contains)

    pairs = [(nc, ne_row(nc)) for nc in constructs]
    if args.site_contains:
        needle = args.site_contains.lower()
        pairs = [(nc, r) for nc, r in pairs if needle in r["site_name"].lower()]
    if args.sin_site:
        pairs = [(nc, r) for nc, r in pairs if not r["site_name"]]
    if args.con_site:
        pairs = [(nc, r) for nc, r in pairs if r["site_name"]]

    pairs.sort(key=lambda p: (p[1]["site_name"].lower(), p[1]["name"].lower()))

    if not pairs:
        print("No se encontraron NEs que coincidan con los filtros.", file=sys.stderr)
        return

    if args.raw:
        print(json.dumps([nc for nc, _ in pairs], indent=2, ensure_ascii=False))
    else:
        print_table([r for _, r in pairs])

    if args.csv:
        write_csv([r for _, r in pairs], args.csv)


if __name__ == "__main__":
    main()
