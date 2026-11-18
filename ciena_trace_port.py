#!/usr/bin/env python3
"""Traza un puerto cliente (tributario) de un NE Ciena 6500 de extremo a extremo.

Dado un NE y un puerto cliente (p.ej. NODE-A 1-5-1), recorre las capas que
el MCP modela como FREs (Forwarding Relationship Entities):

  1. Servicio cliente   DSR / DSR_ETHERNET (p.ej. 100GE)  NE-A:1-5-1  <-> NE-Z:1-5-1
  2. Contenedor ODU     ODUFLEX / ODU4                    cómo se mapea el cliente en la línea
  3. Línea OTN          ODUCn / OTUCn                     puerto de línea (modem) en cada extremo
  4. Capa fotónica      PHY / OTS / OMS                   modem -> CMD (mux) -> ... -> fibra entre sitios
                                                          -> amplificadores intermedios -> NE-Z

Además genera un diagrama de bloques (equipo/puerto -> equipo/puerto) en Mermaid:
  trace_<NE>_<puerto>.mmd   texto Mermaid (se pega en mermaid.live, Confluence, GitLab, VS Code...)
  trace_<NE>_<puerto>.html  se abre en el navegador y dibuja el diagrama

Ejemplos:
    python ciena_trace_port.py NODE-A 1-5-1
    python ciena_trace_port.py NODE-A 1-5-1 --diagram /ruta/mi_traza
    python ciena_trace_port.py NODE-A 1-5-1 --no-diagram
"""

import argparse
import html
import re
import sys
from collections import deque

from mcp_client import MCPClient, NSIService

CLIENT_LAYERS = ("DSR_ETHERNET", "DSR", "ETHERNET", "OTU4", "OTU2", "STM", "FC")
TPE_RE = re.compile(r"EQPT_(\d+)_(\d+)-(\d+)(?:-(.*))?$")


class Tracer:
    def __init__(self, debug=False):
        self.client = MCPClient()
        self.nsi = NSIService(self.client)
        self.debug = debug
        self.ncs = {}
        for nc in self.nsi.network_constructs():
            a = nc["attributes"]
            self.ncs[nc["id"]] = a.get("name") or a.get("displayData", {}).get("displayName") or nc["id"][:8]
        self._fres_by_nc = {}
        self._fre_cache = {}
        self._equipment = {}
        self._tpes = {}
        self.dg = None  # datos recolectados para el diagrama

    # ---------- helpers de lookup ----------
    def find_nc(self, name):
        name_l = name.lower()
        for nc_id, n in self.ncs.items():
            if n.lower() == name_l:
                return nc_id
        sys.exit(f"No encontré el NE '{name}' en el MCP.")

    def nc_name(self, nc_id):
        return self.ncs.get(nc_id, nc_id[:8])

    def card(self, nc_id, shelf, slot):
        """Nombre y tipo de la tarjeta en shelf-slot (p.ej. 'TRM-1-5', 'WL5e MOTR ...')."""
        if nc_id not in self._equipment:
            cards = {}
            for eq in self.nsi.equipment(nc_id):
                a = eq["attributes"]
                disp = a.get("displayData", {}).get("displayName", "")
                m = re.match(r"^([A-Z0-9]+)-(\d+)-(\d+)$", disp)
                if m:
                    cards[(m.group(2), m.group(3))] = (disp, a.get("installedSpec", {}).get("type", ""))
            self._equipment[nc_id] = cards
        return self._equipment[nc_id].get((shelf, slot), (f"?-{shelf}-{slot}", ""))

    @staticmethod
    def parse_tpe(tpe_id):
        """'<nc>::TPE_<nc>::EQPT_1_5-11-CTPServerToClient-CNP1-FP52' -> (nc, '1','5','11', 'CNP1-FP52')."""
        nc_id = tpe_id.split("::")[0]
        m = TPE_RE.search(tpe_id)
        if not m:
            return nc_id, None, None, None, ""
        extra = m.group(4) or ""
        extra = re.sub(r"^(PTP|CTPServerToClient|CTPClientToClient)-?", "", extra)
        extra = re.sub(r"^-?1$", "", extra) if extra in ("1", "-1") else extra
        return nc_id, m.group(1), m.group(2), m.group(3), extra

    def ep_label(self, ep, with_card=False):
        nc_id, sh, sl, pt, extra = ep
        if sh is None:
            return self.nc_name(nc_id)
        port = f"{sh}-{sl}-{pt}" + (f" [{extra}]" if extra else "")
        txt = f"{self.nc_name(nc_id)}:{port}"
        if with_card:
            name, typ = self.card(nc_id, sh, sl)
            txt += f"  ({name}: {typ})" if typ else f"  ({name})"
        return txt

    # ---------- FREs ----------
    def fres_of(self, nc_id):
        if nc_id not in self._fres_by_nc:
            self._fres_by_nc[nc_id] = [f["id"] for f in self.nsi.facility_resources(nc_id)]
        return self._fres_by_nc[nc_id]

    def fre(self, fre_id):
        if fre_id in self._fre_cache:
            return self._fre_cache[fre_id]
        r = self.client.get(f"/nsi/api/v7/fres/{fre_id}")
        a, rel = r["data"]["attributes"], r["data"].get("relationships", {})
        eps = []
        for inc in r.get("included", []):
            if inc["type"] == "endPoints":
                for t in inc["relationships"].get("tpes", {}).get("data", []):
                    eps.append(self.parse_tpe(t["id"]))
        d = {
            "id": fre_id,
            "layer": a.get("layerRate"),
            "qual": a.get("layerRateQualifier") or "",
            "role": a.get("networkRole"),
            "label": a.get("userLabel") or "",
            "link_label": a.get("linkLabel") or a.get("serviceLabel") or "",
            "note": (a.get("note") or {}).get("noteMsg", ""),
            "op": a.get("operationState", ""),
            "eps": eps,
            "supported_by": [x["id"] for x in rel.get("supportedByServices", {}).get("data", [])],
            "partitions": [x["id"] for x in rel.get("partitionFres", {}).get("data", [])],
        }
        self._fre_cache[fre_id] = d
        return d

    def node_fres(self, nc_id, layers=None):
        out = []
        for fid in self.fres_of(nc_id):
            if "resiliency" in fid:
                continue
            d = self.fre(fid)
            if layers is None or d["layer"] in layers:
                out.append(d)
        return out

    # ---------- potencia del puerto ----------
    def tpes_of(self, nc_id):
        if nc_id not in self._tpes:
            self._tpes[nc_id] = self.nsi.tpes(nc_id)
        return self._tpes[nc_id]

    def lldp_neighbor(self, nc_id, sh, sl, pt):
        """Equipo cliente visto por LLDP en el puerto (systemName, remotePortId), si existe."""
        for t in self.tpes_of(nc_id):
            a = t["attributes"]
            if a.get("nativeName") == f"ETTP-{sh}-{sl}-{pt}":
                for lt in a.get("layerTerminations", []):
                    aa = lt.get("additionalAttributes") or {}
                    if aa.get("systemName") or aa.get("remotePortId"):
                        return aa.get("systemName", ""), aa.get("remotePortId", "")
        return None, None

    def port_power(self, nc_id, sh, sl, pt):
        for t in self.tpes_of(nc_id):
            a = t["attributes"]
            if a.get("nativeName") == f"PTP-{sh}-{sl}-{pt}":
                for lt in a.get("layerTerminations", []):
                    pw = lt.get("powerPackage") or {}
                    if pw.get("rxActualPower") or pw.get("txActualPower"):
                        return pw.get("txActualPower"), pw.get("rxActualPower"), a.get("userLabel") or ""
                return None, None, a.get("userLabel") or ""
        return None, None, ""

    # ---------- recorrido ----------
    def trace(self, ne_name, port):
        sh, sl, pt = port.split("-")
        a_nc = self.find_nc(ne_name)

        def touches(d, nc_id, shelf, slot, prt):
            return any(e[0] == nc_id and e[1:4] == (shelf, slot, prt) for e in d["eps"])

        print(f"\n### Trazando {ne_name}:{port}\n")
        self.dg = dg = {"title": f"{ne_name}:{port}", "a_nc": a_nc, "svc": None, "clients": [], "maps": {},
                        "otn": [], "line_eps": [], "local": {}, "freq": None, "path": [], "fibers": [], "amps": {}}

        # 1) Servicio cliente
        services = [d for d in self.node_fres(a_nc)
                    if d["layer"] in CLIENT_LAYERS and touches(d, a_nc, sh, sl, pt)]
        if not services:
            sys.exit("No encontré un servicio cliente (DSR/Ethernet/OTU) que use ese puerto.")
        svc = sorted(services, key=lambda d: d["role"] != "IFRE")[0]
        tx, rx, plabel = self.port_power(a_nc, sh, sl, pt)

        print("1) SERVICIO CLIENTE")
        print(f"   Nombre : {svc['label'] or '-'}   ({svc['layer']} {svc['qual']}, {svc['op']})")
        if svc["note"]:
            print(f"   Nota   : {svc['note']}")
        if plabel:
            print(f"   Label puerto: {plabel}")
        for e in sorted(svc["eps"], key=lambda e: e[0] != a_nc):
            print(f"   Extremo {'A' if e[0] == a_nc else 'Z'}: {self.ep_label(e, with_card=True)}")
        if tx or rx:
            print(f"   Potencia cliente en {ne_name}:{port}: Tx {tx} dBm / Rx {rx} dBm")

        dg["svc"] = svc
        for e in sorted(svc["eps"], key=lambda e: e[0] != a_nc):
            dg["clients"].append((e, self.lldp_neighbor(e[0], *e[1:4])))

        # 2) y 3) Capas OTN que soportan el servicio
        print("\n2) CAPAS OTN (de arriba hacia abajo)")
        seen, queue, line_eps = set(), deque([svc]), []
        for pid in svc["partitions"]:
            queue.append(self.fre(pid))
        while queue:
            d = queue.popleft()
            if d["id"] in seen:
                continue
            seen.add(d["id"])
            if d is not svc:
                ends = "  <->  ".join(self.ep_label(e) for e in sorted(d["eps"], key=lambda e: e[0] != a_nc))
                kind = "dentro del NE (mapeo/cross-connect)" if len({e[0] for e in d["eps"]}) == 1 else "entre NEs"
                print(f"   {d['layer']:<8} {d['qual']:<6} {ends}   [{kind}]")
                if len({e[0] for e in d["eps"]}) == 1 and len(d["eps"]) == 2:
                    dg["maps"][d["eps"][0][0]] = sorted(d["eps"], key=lambda e: (e[3] or "").zfill(3))
                elif d["layer"] and (d["layer"].startswith("OTU") or d["layer"].startswith("ODUC")):
                    dg["otn"].append(f"{d['layer']} {d['qual']}".strip())
                if d["layer"] in ("OTUCn", "OTU4", "OTUC2", "OTUC3", "OTUC4"):
                    line_eps = d["eps"]
            for nid in d["supported_by"] + d["partitions"]:
                queue.append(self.fre(nid))
        if not line_eps:
            # buscar OTU sobre el mismo puerto de línea del ODUCn si no vino encadenado
            for d in self._fre_cache.values():
                if d["layer"] and d["layer"].startswith("OTU") and d["id"] in seen:
                    line_eps = d["eps"]

        if not line_eps:
            print("\n(No se encontró la capa OTU de línea; no puedo seguir a la capa fotónica.)")
            return

        line_eps = sorted(line_eps, key=lambda e: e[0] != a_nc)
        dg["line_eps"] = line_eps
        print("\n   Puertos de línea (modem/transponder):")
        for e in line_eps:
            print(f"   - {self.ep_label(e, with_card=True)}")

        # 4) Capa fotónica en cada extremo: modem -> CMD
        print("\n3) CAPA FOTÓNICA LOCAL (modem -> multiplexor)")
        end_ncs = []
        for e in line_eps:
            nc_id, esh, esl, ept, _ = e
            end_ncs.append(nc_id)
            hops = [d for d in self.node_fres(nc_id, {"PHY"}) if touches(d, nc_id, esh, esl, ept)]
            for d in hops:
                other = [x for x in d["eps"] if x[1:4] != (esh, esl, ept)]
                for o in other:
                    dg["local"].setdefault(nc_id, []).append(o)
                    print(f"   {self.nc_name(nc_id)}:{esh}-{esl}-{ept}  ->  {self.ep_label(o, with_card=True)}")
        freq = dg["freq"] = self.line_frequency(line_eps[0])
        if freq:
            print(f"   Canal óptico: {freq}")

        # 5) Camino entre sitios por fibras (PHY entre NEs distintos), BFS
        print("\n4) CAMINO ENTRE SITIOS (fibras PHY entre NEs, con amplificadores intermedios)")
        if len(end_ncs) < 2 or end_ncs[0] == end_ncs[1]:
            print("   Ambos extremos están en el mismo NE.")
            return
        path = self.bfs(end_ncs[0], end_ncs[1])
        if not path:
            print("   No encontré un camino de fibras PHY descubierto entre ambos NEs.")
            return
        dg["path"] = path
        for i, nc_id in enumerate(path):
            if 0 < i < len(path) - 1:
                amps = self.node_fres(nc_id, {"OMS"})
                internal = [d for d in amps if len({e[0] for e in d["eps"]}) == 1 and d["role"] == "IFRE"]
                print(f"   [NE intermedio] {self.nc_name(nc_id)}")
                dg["amps"][nc_id] = internal
                for d in internal:
                    ports = " -> ".join(self.ep_label(e, with_card=True) for e in sorted(d["eps"], key=lambda x: x[3] or ""))
                    print(f"       amplificación interna: {ports}")
            if i < len(path) - 1:
                nxt = path[i + 1]
                print(f"   Fibras {self.nc_name(nc_id)}  ->  {self.nc_name(nxt)}:")
                for d in self.links_between(nc_id, nxt):
                    a_ep = next(e for e in d["eps"] if e[0] == nc_id)
                    b_ep = next(e for e in d["eps"] if e[0] == nxt)
                    lbl = f"   \"{d['label']}\"" if d["label"] else ""
                    dg["fibers"].append((a_ep, b_ep, d["label"]))
                    print(f"     {self.ep_label(a_ep, with_card=True)}\n"
                          f"        ==fibra==> {self.ep_label(b_ep, with_card=True)}{lbl}")

        print("\n   Nota: los parches internos del sitio (CMD común -> OPS / amplificador) no siempre")
        print("   están descubiertos en el MCP; si no aparecen arriba es porque no hay FRE para ellos.")

    # ---------- diagrama de bloques (Mermaid) ----------
    @staticmethod
    def _txt(t):
        return str(t).replace('"', "#quot;").replace("<", "‹").replace(">", "›")

    @staticmethod
    def _ports(eps):
        """[(nc,'1','84','83',..),(nc,'1','84','84',..)] -> '1-84-83/84'."""
        if not eps:
            return ""
        by_card = {}
        for e in eps:
            by_card.setdefault((e[1], e[2]), set()).add(e[3])
        return ", ".join(f"{sh}-{sl}-" + "/".join(sorted(ps, key=lambda x: int(x) if x.isdigit() else 0))
                         for (sh, sl), ps in by_card.items())

    @staticmethod
    def _card_class(name):
        n = name.upper()
        if n.startswith(("TRM", "OTR", "WL", "MOTR")):
            return "modem"
        if n.startswith(("CMD", "OMD", "CCMD", "SCMD", "BMD")):
            return "mux"
        if n.startswith("OPS"):
            return "prot"
        if n.startswith(("FGA", "LIM", "MLA", "SRA", "ESAM", "RLA", "XLA", "AMP", "SLA", "LIA")):
            return "amp"
        return "other"

    def build_mermaid(self):
        dg = self.dg
        if not dg or not dg["svc"]:
            return None
        nodes, edges = {}, []          # nodes: id -> (nc, label, cls)
        ne_order = []

        def card_node(nc_id, sh, sl, extra_lines=()):
            nid = "c_" + re.sub(r"\W", "_", f"{self.nc_name(nc_id)}_{sh}_{sl}")
            if nid not in nodes:
                name, typ = self.card(nc_id, sh, sl)
                nodes[nid] = {"nc": nc_id, "lines": [f"<b>{self._txt(name)}</b>", self._txt(typ)], "cls": self._card_class(name)}
            for ln in extra_lines:
                if ln not in nodes[nid]["lines"]:
                    nodes[nid]["lines"].append(ln)
            if nc_id not in ne_order:
                ne_order.append(nc_id)
            return nid

        def edge(a, b, label, kind):
            edges.append((a, b, label, kind))

        svc = dg["svc"]
        clients = dg["clients"]
        line_by_nc = {e[0]: e for e in dg["line_eps"]}
        a_nc = clients[0][0][0]
        z_nc = clients[-1][0][0] if len(clients) > 1 else None

        def modem_node(nc_id, client_ep):
            extra = []
            m = dg["maps"].get(nc_id)
            if m:
                extra.append(self._txt(f"{self._ports([m[0]])} [{m[0][4]}] → {self._ports([m[1]])} [{m[1][4]}]"))
            return card_node(nc_id, client_ep[1], client_ep[2], extra)

        def client_node(idx, ep, lldp):
            sysname, rport = lldp
            nid = f"cli{idx}"
            lines = [f"<b>{self._txt(sysname)}</b>" if sysname else "<b>Equipo cliente</b>"]
            if rport:
                lines.append(self._txt(rport))
            nodes[nid] = {"nc": None, "lines": lines, "cls": "client"}
            return nid, rport

        qual = f"{svc['layer']} {svc['qual']}".strip()

        # --- extremo A: cliente -> modem -> mux
        cli_a, rport_a = client_node(0, clients[0][0], clients[0][1])
        trm_a = modem_node(a_nc, clients[0][0])
        edge(cli_a, trm_a, f"{self._txt(rport_a or '?')} ⇄ {self._ports([clients[0][0]])}<br/>{self._txt(qual)}", "client")

        def mux_cards(nc_id):
            groups = {}
            for o in dg["local"].get(nc_id, []):
                groups.setdefault((o[1], o[2]), []).append(o)
            return groups

        freq = f"<br/>{self._txt(dg['freq'])}" if dg["freq"] else ""
        a_mux = []
        for (sh, sl), eps in mux_cards(a_nc).items():
            nid = card_node(a_nc, sh, sl)
            a_mux.append(nid)
            edge(trm_a, nid, f"{self._ports([line_by_nc[a_nc]])} ⇄ {self._ports(eps)}{freq}", "internal")

        # --- fibras entre sitios
        path = dg["path"]
        entry, exit_ = {}, {}   # nc -> set(node ids) por donde entra/sale la fibra
        grouped = {}
        for a_ep, b_ep, label in dg["fibers"]:
            na = card_node(a_ep[0], a_ep[1], a_ep[2])
            nb = card_node(b_ep[0], b_ep[1], b_ep[2])
            exit_.setdefault(a_ep[0], set()).add(na)
            entry.setdefault(b_ep[0], set()).add(nb)
            g = grouped.setdefault((na, nb, label), ([], []))
            g[0].append(a_ep)
            g[1].append(b_ep)
        for (na, nb, label), (aeps, beps) in grouped.items():
            txt = f"{self._ports(aeps)} ⇄ {self._ports(beps)}"
            if label:
                txt += f"<br/><i>{self._txt(label)}</i>"
            edge(na, nb, txt, "fiber")

        # --- amplificación interna en NEs intermedios
        for nc_id, internal in dg["amps"].items():
            for d in internal:
                eps = sorted(d["eps"], key=lambda x: int(x[3] or 0))
                for e in eps:
                    card_node(nc_id, e[1], e[2], [self._txt("amp " + " → ".join(self._ports([x]) for x in eps))])

        def patch(src_nodes, dst_nodes, txt="parche interno<br/>(no descubierto en MCP)"):
            for s_ in src_nodes:
                for d_ in dst_nodes:
                    if s_ != d_:
                        edge(s_, d_, txt, "patch")

        if path:
            patch(a_mux or [trm_a], sorted(exit_.get(a_nc, [])))
            for nc_id in path[1:-1]:
                patch(sorted(entry.get(nc_id, [])), sorted(exit_.get(nc_id, [])), "interno")

        # --- extremo Z: mux -> modem -> cliente
        if z_nc:
            cli_z, rport_z = client_node(1, clients[-1][0], clients[-1][1])
            trm_z = modem_node(z_nc, clients[-1][0])
            z_mux = []
            for (sh, sl), eps in mux_cards(z_nc).items():
                nid = card_node(z_nc, sh, sl)
                z_mux.append(nid)
                edge(nid, trm_z, f"{self._ports(eps)} ⇄ {self._ports([line_by_nc[z_nc]])}{freq}", "internal")
            if path:
                patch(sorted(entry.get(z_nc, [])), z_mux or [trm_z])
            else:
                edge(trm_a, trm_z, "camino fotónico<br/>no descubierto", "patch")
            edge(trm_z, cli_z, f"{self._ports([clients[-1][0]])} ⇄ {self._txt(rport_z or '?')}<br/>{self._txt(qual)}", "client")

        # --- texto Mermaid
        out = ["flowchart LR"]
        order = path or ne_order
        for nc_id in order + [n for n in ne_order if n not in order]:
            sg = "ne_" + re.sub(r"\W", "_", self.nc_name(nc_id))
            role = "A" if nc_id == a_nc else ("Z" if nc_id == z_nc else "intermedio")
            out.append(f'  subgraph {sg}["NE {self._txt(self.nc_name(nc_id))} ({role})"]')
            out.append("    direction LR")
            for nid, n in nodes.items():
                if n["nc"] == nc_id:
                    out.append(f'    {nid}["{"<br/>".join(n["lines"])}"]')
            out.append("  end")
        for nid, n in nodes.items():
            if n["nc"] is None:
                out.append(f'  {nid}(["{"<br/>".join(n["lines"])}"])')
        arrows = {"client": "---", "internal": "---", "fiber": "===", "patch": "-.-"}
        link_styles = {"fiber": "stroke:#d9480f,stroke-width:3px", "patch": "stroke:#868e96,stroke-dasharray:4 4"}
        for i, (a, b, label, kind) in enumerate(edges):
            out.append(f'  {a} {arrows[kind]}|"{label}"| {b}')
            if kind in link_styles:
                out.append(f"  linkStyle {i} {link_styles[kind]}")
        out += [
            "  classDef client fill:#e7f5ff,stroke:#1c7ed6,color:#0b3d6e",
            "  classDef modem fill:#fff4e6,stroke:#e8590c,color:#5c2a00",
            "  classDef mux fill:#ebfbee,stroke:#2f9e44,color:#0b3d1a",
            "  classDef prot fill:#f3f0ff,stroke:#7048e8,color:#2b1a6e",
            "  classDef amp fill:#fff9db,stroke:#f08c00,color:#5c3d00",
            "  classDef other fill:#f1f3f5,stroke:#868e96,color:#212529",
        ]
        for nid, n in nodes.items():
            out.append(f"  class {nid} {n['cls']}")
        return "\n".join(out)

    def write_diagram(self, base):
        mmd = self.build_mermaid()
        if not mmd:
            print("\n(No hay datos suficientes para el diagrama.)")
            return
        dg, svc = self.dg, self.dg["svc"]
        with open(base + ".mmd", "w", encoding="utf-8") as f:
            f.write(mmd + "\n")
        ends = " ⇄ ".join(self.ep_label(e) for e, _ in dg["clients"])
        info = [
            ("Servicio", f"{svc['label'] or '-'} ({svc['layer']} {svc['qual']}, {svc['op']})"),
            ("Extremos", ends),
            ("Línea OTN", ", ".join(dict.fromkeys(dg["otn"])) or "-"),
            ("Canal óptico", dg["freq"] or "-"),
        ]
        if svc["note"]:
            info.insert(1, ("Nota", svc["note"]))
        rows = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in info)
        page = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Traza {html.escape(dg['title'])}</title>
<style>
 body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#212529;background:#fff}}
 h1{{font-size:20px;margin:0 0 12px}}
 dl{{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:0 0 20px;font-size:14px}}
 dt{{font-weight:600;color:#495057}} dd{{margin:0}}
 .diagram{{overflow-x:auto;border:1px solid #dee2e6;border-radius:8px;padding:16px}}
 .legend{{font-size:13px;color:#495057;margin-top:12px;line-height:1.7}}
 .sw{{display:inline-block;width:28px;height:0;vertical-align:middle;margin-right:6px}}
 details{{margin-top:16px;font-size:13px}} pre{{background:#f8f9fa;padding:12px;overflow:auto}}
</style></head><body>
<h1>Traza de {html.escape(dg['title'])}</h1>
<dl>{rows}</dl>
<div class="diagram"><pre class="mermaid">{html.escape(mmd)}</pre></div>
<div class="legend">
 <span class="sw" style="border-top:2px solid #333"></span>conexión cliente / interna descubierta &nbsp;
 <span class="sw" style="border-top:3px solid #d9480f"></span>fibra entre sitios &nbsp;
 <span class="sw" style="border-top:2px dashed #868e96"></span>parche interno no descubierto en el MCP (inferido)
</div>
<details><summary>Código Mermaid</summary><pre>{html.escape(mmd)}</pre></details>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true,securityLevel:'loose',flowchart:{{htmlLabels:true,curve:'basis'}}}});</script>
</body></html>
"""
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(page)
        print(f"\nDiagrama: {base}.html  (ábrelo en el navegador)\n          {base}.mmd   (Mermaid)")

    def line_frequency(self, ep):
        nc_id, sh, sl, pt, _ = ep
        for t in self.tpes_of(nc_id):
            a = t["attributes"]
            if a.get("nativeName") == f"PTP-{sh}-{sl}-{pt}":
                for lt in a.get("layerTerminations", []):
                    if lt.get("layerRate") == "OTSi":
                        s = lt.get("signalIndex") or {}
                        if s.get("frequency"):
                            return f"{s['frequency']} THz, ancho {s.get('width', '?')} GHz"
        return None

    def inter_ne_links(self, nc_id):
        return [d for d in self.node_fres(nc_id, {"PHY"}) if len({e[0] for e in d["eps"]}) == 2]

    def links_between(self, a, b):
        return [d for d in self.inter_ne_links(a) if {e[0] for e in d["eps"]} == {a, b}]

    def bfs(self, src, dst, max_depth=20):
        prev, q = {src: None}, deque([(src, 0)])
        while q:
            cur, depth = q.popleft()
            if cur == dst:
                path = []
                while cur:
                    path.append(cur)
                    cur = prev[cur]
                return path[::-1]
            if depth >= max_depth:
                continue
            for d in self.inter_ne_links(cur):
                for e in d["eps"]:
                    if e[0] not in prev:
                        prev[e[0]] = cur
                        q.append((e[0], depth + 1))
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ne", help="Nombre del NE, p.ej. NODE-A")
    p.add_argument("port", help="Puerto cliente shelf-slot-port, p.ej. 1-5-1")
    p.add_argument("--diagram", metavar="RUTA",
                   help="Ruta base del diagrama sin extensión (default: trace_<NE>_<puerto>).")
    p.add_argument("--no-diagram", action="store_true", help="No generar el diagrama.")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    if not re.match(r"^\d+-\d+-\d+$", args.port):
        sys.exit("El puerto debe tener formato shelf-slot-port, p.ej. 1-5-1")
    tracer = Tracer(debug=args.debug)
    tracer.trace(args.ne, args.port)
    if not args.no_diagram:
        tracer.write_diagram(args.diagram or f"trace_{args.ne}_{args.port}")


if __name__ == "__main__":
    main()
