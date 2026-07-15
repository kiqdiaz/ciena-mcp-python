#!/usr/bin/env python3
"""CLI para explorar la API del Ciena MCP usando mcp_client.

Ejemplos:
    python ciena_mcp.py --network-constructs
    python ciena_mcp.py --network-constructs --ip-startswith 10.0
    python ciena_mcp.py --network-constructs --type all
    python ciena_mcp.py --network-constructs --name-contains NODE --name-endswith A1
    python ciena_mcp.py --equipment NODE-A
    python ciena_mcp.py --equipment NODE-A --name-contains 1-1-5
    python ciena_mcp.py --facility-resources NODE-A --raw
    python ciena_mcp.py --facility-resources NODE-A --link-label-contains LINK1
    python ciena_mcp.py --facility-resources NODE-A --user-label-contains SITE1
    python ciena_mcp.py --facility-resources NODE-A --layer-rate PHY
    python ciena_mcp.py --tpes NODE-A
    python ciena_mcp.py --tpes NODE-A --structure-type all
    python ciena_mcp.py --metrics --ne-name NODE-A --facility PTP-1-3-1 --parameter RX_OPTICAL_POWER
    python ciena_mcp.py --logout
"""

import argparse
import json
import sys

from mcp_client import MCPClient, NSIService, PMService


def resolve_network_construct(nsi, id_or_name):
    """Busca un network construct por id o por nombre/displayName."""
    constructs = nsi.network_constructs()

    for nc in constructs:
        if nc["id"] == id_or_name:
            return nc

    for nc in constructs:
        attrs = nc["attributes"]
        display_name = attrs.get("displayData", {}).get("displayName", "")
        if id_or_name in (attrs.get("name"), display_name):
            return nc

    print(f"Error: no se encontró ningún network construct que coincida con {id_or_name!r}", file=sys.stderr)
    sys.exit(1)


def print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def apply_filters(items, args, name_fn, ip_fn=None):
    """Filtra items por --name-contains/--name-startswith/--name-endswith (y --ip-startswith si ip_fn).

    name_fn(item) debe devolver una lista de strings candidatos (p.ej. name y
    displayName); un item matchea si CUALQUIERA de los candidatos cumple el filtro.
    """
    name_contains = args.name_contains.lower() if args.name_contains else None
    name_startswith = args.name_startswith.lower() if args.name_startswith else None
    name_endswith = args.name_endswith.lower() if args.name_endswith else None

    result = []
    for item in items:
        names = [n.lower() for n in name_fn(item) if n]

        if name_contains and not any(name_contains in n for n in names):
            continue
        if name_startswith and not any(n.startswith(name_startswith) for n in names):
            continue
        if name_endswith and not any(n.endswith(name_endswith) for n in names):
            continue
        if ip_fn and args.ip_startswith:
            ip = ip_fn(item) or ""
            if not ip.startswith(args.ip_startswith):
                continue

        result.append(item)
    return result


def nc_names(nc):
    attrs = nc["attributes"]
    return [attrs.get("name", ""), attrs.get("displayData", {}).get("displayName", "")]


def nc_ip(nc):
    return nc["attributes"].get("ipAddress", "")


def eq_names(eq):
    attrs = eq["attributes"]
    return [attrs.get("displayData", {}).get("displayName", ""), attrs.get("nativeName", "")]


def fre_note_msg(attrs):
    """note puede venir como string plano o como {"noteMsg": ..., "lastUpdatedBy": ..., ...}."""
    note = attrs.get("note", "")
    return note.get("noteMsg", "") if isinstance(note, dict) else note


def fre_names(fre):
    attrs = fre["attributes"]
    return [attrs.get("userLabel", ""), fre_note_msg(attrs)]


def tpe_names(tpe):
    attrs = tpe["attributes"]
    return [attrs.get("nativeName", "")]


def apply_tpe_filters(tpes, args):
    """Filtro específico de tpes: structureType (igualdad, case-insensitive)."""
    structure_type = args.structure_type.lower() if args.structure_type else None
    if structure_type is None or structure_type == "all":
        return tpes
    return [t for t in tpes if t["attributes"].get("structureType", "").lower() == structure_type]


def apply_fre_filters(fres, args):
    """Filtros específicos de facility resources: linkLabel/userLabel (substring) y layerRate (igualdad)."""
    link_label_contains = args.link_label_contains.lower() if args.link_label_contains else None
    user_label_contains = args.user_label_contains.lower() if args.user_label_contains else None
    layer_rate = args.layer_rate.lower() if args.layer_rate else None

    result = []
    for fre in fres:
        attrs = fre["attributes"]

        if link_label_contains and link_label_contains not in attrs.get("linkLabel", "").lower():
            continue
        if user_label_contains and user_label_contains not in attrs.get("userLabel", "").lower():
            continue
        if layer_rate and attrs.get("layerRate", "").lower() != layer_rate:
            continue

        result.append(fre)
    return result


def print_network_constructs(constructs, raw):
    if raw:
        print_json(constructs)
        return
    for nc in constructs:
        attrs = nc["attributes"]
        location = attrs.get('userData',{}).get('latitudeLongitudeString', '')
        display_name = attrs.get("displayData", {}).get("displayName", "")
        print(f"- name={attrs.get('name', ''):<25} displayName={display_name:<20} type={nc['type']:<20} ipv4={attrs.get('ipAddress', ''):<20} location={location:<20} id={nc['id']:<20}")


def print_equipment(equipment, raw):
    if raw:
        print_json(equipment)
        return
    for eq in equipment:
        attrs = eq["attributes"]
        display_name = attrs.get("displayData", {}).get("displayName", "")
        print(f"{display_name:<20} {attrs.get('installedSpec', {}).get('serialNumber', ''):<20} {attrs.get('installedSpec', {}).get('partNumber', ''):<20} {attrs.get('installedSpec', {}).get('type', ''):<40} {eq['id']:<20} ")


def print_facility_resources(fres, raw):
    if raw:
        print_json(fres)
        return
    for fre in fres:
        attrs = fre["attributes"]
        print(f"- linkLabel={attrs.get('linkLabel', ''):<25} userLabel={attrs.get('userLabel', ''):<20} layerRate={attrs.get('layerRate', ''):<20} Nota={fre_note_msg(attrs):<20} id={fre['id']:<20}")


def print_tpes(tpes, raw):
    if raw:
        print_json(tpes)
        return
    for tpe in tpes:
        attrs = tpe["attributes"]
        location = attrs.get("locations", [{}])[0]
        shelf_slot_port = f"{location.get('shelf', '')}-{location.get('slot', '')}-{location.get('port', '')}"
        print(f"- nativeName={attrs.get('nativeName', ''):<25} structureType={attrs.get('structureType', ''):<20} state={attrs.get('state', ''):<6} location={shelf_slot_port:<15} id={tpe['id']:<20}")


def print_metrics(metrics, raw):
    if raw:
        print_json(metrics)
        return
    for m in metrics:
        print(m)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Explora la API del Ciena MCP (inventario de red y métricas PM).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--network-constructs",
        action="store_true",
        help="Lista todos los network constructs (nodos de red).",
    )
    parser.add_argument(
        "--equipment",
        metavar="NETWORK_CONSTRUCT",
        help="Lista el equipment de un network construct (id o nombre).",
    )
    parser.add_argument(
        "--facility-resources",
        metavar="NETWORK_CONSTRUCT",
        help="Lista los facility resources (fres/enlaces) de un network construct (id o nombre).",
    )
    parser.add_argument(
        "--tpes",
        metavar="NETWORK_CONSTRUCT",
        help="Lista los tpes (termination points) de un network construct (id o nombre). "
             "Por defecto muestra sólo los de structureType=PTP: su nativeName (p.ej. PTP-1-3-1) "
             "es el valor a usar en --facility para --metrics.",
    )
    parser.add_argument(
        "--structure-type",
        metavar="TYPE",
        default="PTP",
        help="Filtra tpes por structureType, p.ej. PTP, CTPServerToClient, CTPClientToClient. "
             "Usa 'all' para no filtrar (sólo --tpes, default: PTP).",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="Consulta métricas PM (requiere --ne-name, --facility y --parameter).",
    )
    parser.add_argument("--ne-name", metavar="NAME", help="Nombre del network element (para --metrics).")
    parser.add_argument("--facility", metavar="NAME", help="facilityNameNative (para --metrics).")
    parser.add_argument("--parameter", metavar="PARAM", help="Parámetro PM, p.ej. RX_OPTICAL_POWER (para --metrics).")
    parser.add_argument(
        "--range-unit",
        default="HOURS",
        help="Unidad del rango relativo para --metrics (default: HOURS).",
    )
    parser.add_argument(
        "--range-value",
        type=int,
        default=1,
        help="Valor del rango relativo para --metrics (default: 1).",
    )

    parser.add_argument(
        "--name-contains",
        metavar="TEXT",
        help="Filtra resultados cuyo nombre contenga TEXT (case-insensitive). "
             "Aplica a --network-constructs, --equipment, --facility-resources y --tpes.",
    )
    parser.add_argument(
        "--name-startswith",
        metavar="TEXT",
        help="Filtra resultados cuyo nombre empiece con TEXT (case-insensitive).",
    )
    parser.add_argument(
        "--name-endswith",
        metavar="TEXT",
        help="Filtra resultados cuyo nombre termine con TEXT (case-insensitive), p.ej. A1.",
    )
    parser.add_argument(
        "--ip-startswith",
        metavar="PREFIX",
        help="Filtra network constructs cuya IP empiece con PREFIX, p.ej. 10.0 (sólo --network-constructs).",
    )
    parser.add_argument(
        "--type",
        metavar="TYPE",
        default="networkElement",
        help="Filtra network constructs por networkConstructType, p.ej. networkElement, shelf, osrpNode, manual. "
             "Usa 'all' para no filtrar (sólo --network-constructs, default: networkElement).",
    )
    parser.add_argument(
        "--link-label-contains",
        metavar="TEXT",
        help="Filtra fres cuyo linkLabel contenga TEXT, p.ej. LINK1 (sólo --facility-resources).",
    )
    parser.add_argument(
        "--user-label-contains",
        metavar="TEXT",
        help="Filtra fres cuyo userLabel contenga TEXT, p.ej. SITE1 (sólo --facility-resources).",
    )
    parser.add_argument(
        "--layer-rate",
        metavar="VALUE",
        help="Filtra fres cuyo layerRate sea exactamente VALUE (case-insensitive), p.ej. PHY (sólo --facility-resources).",
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help="Muestra la respuesta completa de la API en JSON en vez del resumen.",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Cierra la sesión actual en el MCP y borra el token cacheado.",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    actions = [args.network_constructs, bool(args.equipment), bool(args.facility_resources), bool(args.tpes), args.metrics, args.logout]
    if not any(actions):
        parser.print_help()
        return

    client = MCPClient()

    if args.logout:
        client.logout()
        print("Sesión cerrada.")
        return

    nsi = NSIService(client)

    if args.network_constructs:
        constructs = nsi.network_constructs()
        if args.type.lower() != "all":
            constructs = [
                nc for nc in constructs
                if (nc["attributes"].get("networkConstructType") or "").lower() == args.type.lower()
            ]
        constructs = apply_filters(constructs, args, nc_names, nc_ip)
        print_network_constructs(constructs, args.raw)

    if args.equipment:
        nc = resolve_network_construct(nsi, args.equipment)
        equipment = apply_filters(nsi.equipment(nc["id"]), args, eq_names)
        print_equipment(equipment, args.raw)

    if args.facility_resources:
        nc = resolve_network_construct(nsi, args.facility_resources)
        fres = apply_filters(nsi.facility_resources(nc["id"]), args, fre_names)
        fres = apply_fre_filters(fres, args)
        print_facility_resources(fres, args.raw)

    if args.tpes:
        nc = resolve_network_construct(nsi, args.tpes)
        tpes = apply_filters(nsi.tpes(nc["id"]), args, tpe_names)
        tpes = apply_tpe_filters(tpes, args)
        print_tpes(tpes, args.raw)

    if args.metrics:
        missing = [name for name, value in (("--ne-name", args.ne_name), ("--facility", args.facility), ("--parameter", args.parameter)) if not value]
        if missing:
            parser.error(f"--metrics requiere {', '.join(missing)}")

        pm = PMService(client)
        metrics = pm.query_metrics(
            network_element_name=args.ne_name,
            facility_name_native=args.facility,
            parameter=args.parameter,
            range_unit=args.range_unit,
            range_value=args.range_value,
        )
        print_metrics(metrics, args.raw)


if __name__ == "__main__":
    main()
