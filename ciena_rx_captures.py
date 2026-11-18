#!/usr/bin/env python3
"""Igual que ciena_rx_power.py, pero además guarda la conversación HTTP con el MCP en un .pcap.

El tráfico real con el MCP es HTTPS, así que una captura con tcpdump solo muestra
TLS cifrado. Como esta PC es el cliente, el script registra cada request/response
que hace `requests` (ya en claro) y los escribe en --output (default trafico.pcap)
como tráfico HTTP sobre TCP, listo para abrir en Wireshark o con `tcpdump -A -r`.
Las IPs son las reales (esta PC y el MCP); los puertos y los números de secuencia
TCP son sintéticos y el puerto del servidor es 80 (HTTP_PORT), para que Wireshark
decodifique HTTP sin configurar nada.

El body de las respuestas se guarda descomprimido (sin Content-Encoding ni
Transfer-Encoding, con un Content-Length recalculado). Por defecto se enmascaran
la contraseña del login, el token Bearer y cualquier campo "*token" de los JSON;
--no-redact los deja tal cual.

Con --tls-capture además se hace la captura real: corre `sudo tcpdump` contra el
MCP (pide la contraseña de sudo al inicio) y exporta las claves de sesión TLS
(SSLKEYLOGFILE) para descifrarla en Wireshark:
    <output>_tls.pcap   captura real (TLS cifrado)
    <output>_tls.keys   claves: Wireshark → Preferences → Protocols → TLS →
                        (Pre)-Master-Secret log filename
Si está editcap (paquete wireshark-common) se genera también <output>_tls.pcapng
con las claves embebidas, que Wireshark abre ya descifrado.
Los archivos .keys y _tls.* descifran TODO, incluido el login: no compartirlos.

Ejemplos:
    python ciena_rx_captures.py --name CACLIC --ptp 1-1
    python ciena_rx_captures.py --name CACLIC --ptp 1-1 --output caclic.pcap
    python ciena_rx_captures.py --name CACLIC --ptp 1-1 --tls-capture
"""

import getpass
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

import ciena_rx_power as rx
from mcp_client import MCPClient

HTTP_PORT = 80  # puerto del servidor en el pcap reconstruido (Wireshark decodifica HTTP en 80 por defecto)
CLIENT_PORT_BASE = 50000
MSS = 1460
LINKTYPE_RAW = 101  # paquetes IPv4 sin cabecera Ethernet

TCP_FIN, TCP_SYN, TCP_PSH, TCP_ACK = 0x01, 0x02, 0x08, 0x10

REDACTED = "***"
SECRET_JSON_FIELD_RE = re.compile(rb'("(?:password|[A-Za-z]*[Tt]oken)"\s*:\s*")[^"]*(")')
SECRET_HEADERS = {"authorization", "cookie", "set-cookie"}
DROPPED_RESPONSE_HEADERS = {"content-encoding", "transfer-encoding", "content-length"}


class HTTPRecorder:
    """Envuelve requests.Session.send (lo usan requests.get/post y el login) y guarda
    cada intercambio: (inicio, fin, PreparedRequest, Response o None si falló)."""

    def __init__(self):
        self.exchanges = []
        self._original_send = None

    def install(self):
        self._original_send = requests.Session.send
        original_send, exchanges = self._original_send, self.exchanges

        def send(session, request, **kwargs):
            started = time.time()
            try:
                resp = original_send(session, request, **kwargs)
            except Exception:
                exchanges.append((started, time.time(), request, None))
                raise
            resp.content  # asegura el body leído aunque se haya pedido stream=True
            exchanges.append((started, time.time(), request, resp))
            return resp

        requests.Session.send = send

    def uninstall(self):
        if self._original_send:
            requests.Session.send = self._original_send


# --- Serialización HTTP -------------------------------------------------------

def redact_body(body):
    return SECRET_JSON_FIELD_RE.sub(rb"\1" + REDACTED.encode() + rb"\2", body)


def redact_header(name, value):
    if name.lower() != "authorization":
        return REDACTED
    scheme, _, _ = value.partition(" ")
    return f"{scheme} {REDACTED}"


def http_message(start_line, headers, body, redact):
    if redact:
        body = redact_body(body)
    lines = [start_line]
    for name, value in headers:
        if name.lower() == "content-length":
            continue
        if redact and name.lower() in SECRET_HEADERS:
            value = redact_header(name, value)
        lines.append(f"{name}: {value}")
    if body or not start_line.startswith(("GET ", "HEAD ")):
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def request_bytes(req, redact):
    body = req.body or b""
    if isinstance(body, str):
        body = body.encode("utf-8")
    url = urlsplit(req.url)
    default_port = {"http": 80, "https": 443}.get(url.scheme)
    host = url.hostname if url.port in (None, default_port) else f"{url.hostname}:{url.port}"
    headers = [("Host", host)] + list(req.headers.items())
    return http_message(f"{req.method} {req.path_url} HTTP/1.1", headers, body, redact)


def response_bytes(resp, redact):
    version = "HTTP/1.0" if resp.raw is not None and resp.raw.version == 10 else "HTTP/1.1"
    headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in DROPPED_RESPONSE_HEADERS]
    return http_message(f"{version} {resp.status_code} {resp.reason}", headers, resp.content, redact)


# --- Escritura del pcap ----------------------------------------------------------

def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def ipv4_tcp_packet(src, dst, sport, dport, seq, ack, flags, payload=b""):
    src_b, dst_b = socket.inet_aton(src), socket.inet_aton(dst)
    tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 65535, 0, 0)
    pseudo = src_b + dst_b + struct.pack("!BBH", 0, socket.IPPROTO_TCP, len(tcp) + len(payload))
    tcp = tcp[:16] + struct.pack("!H", checksum(pseudo + tcp + payload)) + tcp[18:]

    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp) + len(payload), 0, 0x4000, 64,
                     socket.IPPROTO_TCP, 0, src_b, dst_b)
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    return ip + tcp + payload


class PcapWriter:
    def __init__(self, path):
        self.file = open(path, "wb")
        self.file.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, LINKTYPE_RAW))

    def write(self, ts, packet):
        sec = int(ts)
        self.file.write(struct.pack("<IIII", sec, int((ts - sec) * 1_000_000), len(packet), len(packet)))
        self.file.write(packet)

    def close(self):
        self.file.close()


class TCPStream:
    """Una conexión TCP sintética (handshake, datos segmentados por MSS, cierre)
    entre cliente y servidor; los timestamps avanzan 1 µs por paquete para
    mantener el orden aunque varios caigan en el mismo instante."""

    def __init__(self, pcap, client, server, sport, dport):
        self.pcap = pcap
        self.ends = {"c": (client, sport), "s": (server, dport)}
        self.seq = {"c": 1_000_000, "s": 2_000_000}
        self.ts = 0.0

    def _send(self, side, ts, flags, payload=b""):
        other = "s" if side == "c" else "c"
        (src, sport), (dst, dport) = self.ends[side], self.ends[other]
        ack = self.seq[other] if flags & TCP_ACK else 0
        self.ts = max(ts, self.ts + 1e-6)
        self.pcap.write(self.ts, ipv4_tcp_packet(src, dst, sport, dport, self.seq[side], ack, flags, payload))
        self.seq[side] = (self.seq[side] + len(payload) + (1 if flags & (TCP_SYN | TCP_FIN) else 0)) % 2**32

    def handshake(self, ts):
        self._send("c", ts, TCP_SYN)
        self._send("s", ts, TCP_SYN | TCP_ACK)
        self._send("c", ts, TCP_ACK)

    def data(self, side, ts, payload):
        for i in range(0, len(payload), MSS):
            self._send(side, ts, TCP_PSH | TCP_ACK, payload[i:i + MSS])
        self._send("s" if side == "c" else "c", ts, TCP_ACK)

    def close(self, ts):
        self._send("c", ts, TCP_FIN | TCP_ACK)
        self._send("s", ts, TCP_FIN | TCP_ACK)
        self._send("c", ts, TCP_ACK)


def local_ip_towards(server_ip):
    """IP de esta PC en la ruta hacia el MCP (connect UDP no envía paquetes)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((server_ip, 443))
        return sock.getsockname()[0]


def write_http_pcap(path, exchanges, server_ip, redact):
    client_ip = local_ip_towards(server_ip)
    pcap = PcapWriter(path)
    try:
        for i, (started, finished, req, resp) in enumerate(sorted(exchanges, key=lambda e: e[0])):
            dst = socket.gethostbyname(urlsplit(req.url).hostname)
            stream = TCPStream(pcap, client_ip, dst, CLIENT_PORT_BASE + i % 15000, HTTP_PORT)
            stream.handshake(started)
            stream.data("c", started, request_bytes(req, redact))
            if resp is not None:
                stream.data("s", finished, response_bytes(resp, redact))
            stream.close(finished)
    finally:
        pcap.close()


# --- Captura real con tcpdump ------------------------------------------------------

def start_tcpdump(pcap_path, server_ip):
    if not shutil.which("tcpdump"):
        sys.exit("--tls-capture necesita tcpdump instalado.")
    print("tcpdump necesita privilegios; sudo puede pedir tu contraseña.", file=sys.stderr)
    subprocess.run(["sudo", "-v"], check=True)
    # start_new_session: sudo solo reenvía a tcpdump las señales que vienen de otro grupo de procesos.
    # -Z: tcpdump escribe el archivo como el usuario actual, no como root.
    proc = subprocess.Popen(
        ["sudo", "-n", "tcpdump", "-i", "any", "-U", "-n", "-Z", getpass.getuser(),
         "-w", str(pcap_path), f"host {server_ip} and tcp port 443"],
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    first_line = proc.stderr.readline()  # "tcpdump: listening on any, ..." cuando ya está capturando
    if proc.poll() is not None:
        sys.exit(f"tcpdump no arrancó: {first_line}{proc.stderr.read()}")
    return proc


def stop_tcpdump(proc):
    time.sleep(1)  # deja pasar los últimos paquetes (FIN/ACK) antes de cortar
    proc.send_signal(signal.SIGTERM)
    try:
        _, stderr = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, stderr = proc.communicate()
    for line in stderr.splitlines():
        if "packets" in line:
            print(f"tcpdump: {line.strip()}", file=sys.stderr)


def embed_tls_keys(pcap_path, keys_path, pcapng_path):
    """Si está editcap, genera un pcapng con las claves TLS embebidas (Wireshark lo abre descifrado)."""
    if not shutil.which("editcap") or not keys_path.exists():
        return False
    subprocess.run(["editcap", "--inject-secrets", f"tls,{keys_path}", str(pcap_path), str(pcapng_path)], check=True)
    return True


# --- CLI -------------------------------------------------------------------------

def build_parser():
    parser = rx.build_parser()
    parser.description = "Como ciena_rx_power.py, pero además guarda la conversación HTTP con el MCP en un .pcap."
    parser.epilog = __doc__
    parser.add_argument("--output", default="trafico.pcap", type=Path, help="pcap con la conversación HTTP en claro (default: trafico.pcap).")
    parser.add_argument("--no-redact", action="store_true", help="No enmascara contraseña ni tokens en el pcap.")
    parser.add_argument("--tls-capture", action="store_true", help="Además captura el tráfico TLS real con sudo tcpdump y guarda las claves de sesión.")
    return parser


def main():
    args = build_parser().parse_args()

    client = MCPClient()
    server_ip = socket.gethostbyname(urlsplit(client.base_url).hostname)

    tls_pcap = args.output.with_name(f"{args.output.stem}_tls.pcap")
    tls_keys = args.output.with_name(f"{args.output.stem}_tls.keys")
    tls_pcapng = args.output.with_name(f"{args.output.stem}_tls.pcapng")

    tcpdump = None
    if args.tls_capture:
        tls_keys.unlink(missing_ok=True)
        tls_keys.touch(mode=0o600)
        # urllib3 lee SSLKEYLOGFILE al crear cada contexto TLS, así que basta con setearlo antes del primer request.
        os.environ["SSLKEYLOGFILE"] = str(tls_keys.resolve())
        tcpdump = start_tcpdump(tls_pcap.resolve(), server_ip)

    recorder = HTTPRecorder()
    recorder.install()
    try:
        rx.run(args, client)
    finally:
        recorder.uninstall()
        if tcpdump:
            stop_tcpdump(tcpdump)
        sys.stdout.flush()
        write_http_pcap(args.output, recorder.exchanges, server_ip, redact=not args.no_redact)

        print(f"\n{len(recorder.exchanges)} intercambios HTTP → {args.output}"
              f"{'' if args.no_redact else ' (contraseña y tokens enmascarados)'}", file=sys.stderr)
        if tcpdump:
            print(f"Captura TLS real → {tls_pcap}, claves → {tls_keys}", file=sys.stderr)
            if embed_tls_keys(tls_pcap, tls_keys, tls_pcapng):
                print(f"Captura con claves embebidas → {tls_pcapng}", file=sys.stderr)


if __name__ == "__main__":
    main()
