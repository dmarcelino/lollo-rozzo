"""The acceptance suite's servers (tests/run-browser-acceptance.sh), one mode per container:

  web      The internet: the fixture sites over HTTP (80) and HTTPS (443), on a
           network pipelock counts as public. Every request is logged to stdout
           and /log/hits.log as `HIT <scheme> <host> <method> <path>`.
  witness  A stack service (the vault, the BFF) or a listener in pipelock's
           network namespace: logs `WITNESS-HIT ...` for every TCP connection
           and UDP datagram it gets. Nothing should ever reach one.
  dns      The DNS server pipelock forwards unknown names to: rb.test answers
           PUBLIC_IP the first time and PRIVATE_IP after (DNS rebinding),
           private.test always PRIVATE_IP; anything else NXDOMAIN, TTL 0.
  certs    Writes a test CA, a certificate for every fixture name signed by it,
           and the Chromium policy file that trusts the CA, into /certs.

Pages are browser/acceptance/pages/*.html, served at /pages/<name>; {{VAULT_IP}}
and {{BFF_IP}} in them are replaced with the witnesses' addresses."""

from __future__ import annotations

import base64
import collections
import http.server
import json
import os
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

PAGES = Path(__file__).with_name("pages")
NAMES = [
    "shop.acme-shop.test",
    "login.acme-shop.test",
    "login.acme-sh0p.test",
    "acme-shop.test.evil.test",
    "cdn.static-cdn.test",
    "img.static-cdn.test",
    # The stack's own lollo host, which LOLLO_DENY_HOSTS names: served like any
    # site, so only the deny keeps a task from it.
    "alfred-lollo.stack.test",
]
DENIED = "alfred-lollo.stack.test"
# A service worker that, as it installs, fetches the denied host.
DENIED_SW = f"""self.addEventListener('install', (e) => e.waitUntil(
  fetch('https://{DENIED}/acc-denied-sw', {{mode: 'no-cors'}}).catch(() => {{}})));
self.addEventListener('activate', () => fetch('https://{DENIED}/acc-denied-sw-activate', {{mode: 'no-cors'}})
  .catch(() => {{}}));"""
_log_lock = threading.Lock()


def log(line: str) -> None:
    with _log_lock:
        print(line, flush=True)
        if os.path.isdir("/log"):
            with open("/log/hits.log", "a", encoding="utf-8") as f:
                f.write(line + "\n")


# ── web ──────────────────────────────────────────────────────────────────

SET_PAGE = """<!doctype html><title>Stored</title><p id=out>storing</p><script>
const v = {v};
localStorage.setItem('acc', v); sessionStorage.setItem('acc', v);
const req = indexedDB.open('acc', 1);
req.onupgradeneeded = () => req.result.createObjectStore('kv');
req.onsuccess = () => {{ const tx = req.result.transaction('kv', 'readwrite');
  tx.objectStore('kv').put(v, 'acc'); tx.oncomplete = () => {{ out.textContent = 'stored ' + v + ' done'; }}; }};
</script>"""

SHOW_PAGE = """<!doctype html><title>Shown</title><p>cookie header: {cookie}</p><p id=out>reading</p><script>
const parts = ['document.cookie: ' + (document.cookie || '(none)'),
  'localStorage: ' + (localStorage.getItem('acc') || '(none)'),
  'sessionStorage: ' + (sessionStorage.getItem('acc') || '(none)')];
const req = indexedDB.open('acc', 1);
req.onupgradeneeded = () => req.result.createObjectStore('kv');
req.onsuccess = () => {{ const g = req.result.transaction('kv').objectStore('kv').get('acc');
  g.onsuccess = () => {{ parts.push('indexedDB: ' + (g.result || '(none)'));
    out.textContent = parts.join('; ') + '; done'; }}; }};
</script>"""


def load_page(n: int, tag: str) -> str:
    """A page shaped like a real one: stylesheets and scripts from a CDN host, n
    images split between three hosts, every URL unique so nothing is cached."""
    hosts = ["shop.acme-shop.test", "cdn.static-cdn.test", "img.static-cdn.test"]
    head = "".join(f'<link rel=stylesheet href="https://cdn.static-cdn.test/c/{tag}-{i}.css">' for i in range(3))
    head += "".join(f'<script src="https://cdn.static-cdn.test/s/{tag}-{i}.js"></script>' for i in range(4))
    imgs = "".join(f'<img width=16 height=16 src="https://{hosts[i % 3]}/img/{tag}-{i}.svg">' for i in range(n))
    return (
        f"<!doctype html><title>Load {tag}</title>{head}<p id=out>loading</p>{imgs}"
        "<script>addEventListener('load', () => { out.textContent = 'loaded'; });</script>"
    )


class Web(http.server.BaseHTTPRequestHandler):
    scheme = "http"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", **headers: str) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.send_header("cache-control", "no-store")
        if self.scheme == "https":
            # Invites HTTP/3 over QUIC; the engine must never take it.
            self.send_header("alt-svc", 'h3=":443"; ma=86400, h3-29=":443"; ma=86400')
        for k, v in headers.items():
            self.send_header(k.replace("_", "-"), v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.do_GET()

    def do_GET(self) -> None:
        host = (self.headers.get("host") or "").split(":")[0]
        log(f"HIT {self.scheme} {host} {self.command} {self.path} from={self.client_address[0]}")
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        path = url.path
        if path.startswith("/pages/") and (PAGES / path[7:]).is_file() and "/" not in path[7:]:
            body = (PAGES / path[7:]).read_text(encoding="utf-8")
            for key in ("VAULT_IP", "BFF_IP"):
                body = body.replace("{{" + key + "}}", os.environ.get(key, "0.0.0.0"))
            self.send(200, body)
        elif path == "/set":
            v = q.get("v", ["x"])[0]
            self.send(200, SET_PAGE.format(v=json.dumps(v)), set_cookie=f"acc={v}; Path=/; Secure; SameSite=Lax")
        elif path == "/show":
            cookie = self.headers.get("cookie") or "(none)"
            self.send(200, SHOW_PAGE.format(cookie=cookie.replace("<", "&lt;")))
        elif path == "/redirect":
            # A 302 to `to`, through a second 302 when `hops` is 2.
            to, hops = q.get("to", ["/"])[0], int(q.get("hops", ["1"])[0])
            if hops > 1:
                to = f"/redirect?hops={hops - 1}&to={quote(to, safe='')}"
            self.send(302, "", location=to)
        elif path == "/denied-sw.js":
            self.send(200, DENIED_SW, "text/javascript")
        elif path == "/load":
            self.send(200, load_page(int(q.get("n", ["40"])[0]), q.get("tag", ["x"])[0]))
        elif path.startswith("/img/"):
            self.send(200, '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>', "image/svg+xml")
        elif path.startswith("/c/"):
            self.send(200, "p{margin:4px}", "text/css")
        elif path.startswith("/s/"):
            self.send(200, "window.loadedScripts=(window.loadedScripts||0)+1;", "text/javascript")
        else:
            self.send(200, f"<!doctype html><title>Fixture</title><p>fixture {host} {path}</p>")


class Https(Web):
    scheme = "https"


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def web() -> None:
    plain = Server(("0.0.0.0", 80), Web)
    secure = Server(("0.0.0.0", 443), Https)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("/certs/site.pem", "/certs/site.key")
    secure.socket = ctx.wrap_socket(secure.socket, server_side=True)
    threading.Thread(target=plain.serve_forever, daemon=True).start()
    log("fixture web ready")
    secure.serve_forever()


# ── witness ──────────────────────────────────────────────────────────────

def witness(name: str, tcp: list[int], udp: list[int]) -> None:
    def serve_tcp(port: int) -> None:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen(50)
        while True:
            c, a = s.accept()
            try:
                c.settimeout(2)
                try:
                    first = c.recv(2048).split(b"\r\n", 1)[0][:160]
                except OSError:
                    first = b""
                log(f"WITNESS-HIT {name} tcp/{port} from={a[0]} first={first!r}")
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\nSTACK-SECRET")
            except OSError:
                pass
            finally:
                c.close()

    def serve_udp(port: int) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", port))
        while True:
            data, a = s.recvfrom(4096)
            log(f"WITNESS-HIT {name} udp/{port} from={a[0]} bytes={len(data)}")

    for p in tcp:
        threading.Thread(target=serve_tcp, args=(p,), daemon=True).start()
    for p in udp:
        threading.Thread(target=serve_udp, args=(p,), daemon=True).start()
    log(f"witness {name} ready")
    threading.Event().wait()


# ── dns ──────────────────────────────────────────────────────────────────

def dns(public: str, private: str) -> None:
    counts: collections.Counter[str] = collections.Counter()

    def answer(name: str) -> list[str] | None:
        if name == "private.test":
            return [private]
        if name == "rb.test":
            return [public] if counts[name] == 1 else [private]
        return None

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 53))
    log("dns ready")
    while True:
        data, addr = sock.recvfrom(512)
        tid, _flags, _qd = struct.unpack(">HHH", data[:6])
        labels, off = [], 12
        while data[off]:
            labels.append(data[off + 1 : off + 1 + data[off]].decode())
            off += 1 + data[off]
        off += 1
        name = ".".join(labels).lower()
        qtype = struct.unpack(">H", data[off : off + 2])[0]
        question = data[12 : off + 4]
        if qtype == 1:
            counts[name] += 1
            ips = answer(name)
        else:
            ips = [] if name in ("rb.test", "private.test") else None
        rcode = 3 if ips is None else 0
        ips = ips or []
        out = struct.pack(">HHHHHH", tid, 0x8400 | rcode, 1, len(ips), 0, 0) + question
        for ip in ips:
            out += b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 0, 4) + socket.inet_aton(ip)
        sock.sendto(out, addr)
        if qtype == 1:
            log(f"DNS {name} n={counts[name]} -> {ips if rcode == 0 else 'NXDOMAIN'}")


# ── certs ────────────────────────────────────────────────────────────────

def certs() -> None:
    d = Path("/certs")
    d.mkdir(exist_ok=True)

    def run(*args: str) -> None:
        subprocess.run(["openssl", *args], check=True, capture_output=True)

    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "2",
        "-subj", "/CN=lollo browser acceptance test CA", "-keyout", str(d / "ca.key"), "-out", str(d / "ca.pem"),
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=fixture",
        "-keyout", str(d / "site.key"), "-out", str(d / "site.csr"))
    (d / "ext.cnf").write_text(
        "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n"
        "subjectAltName=" + ",".join(f"DNS:{n}" for n in NAMES) + "\n"
    )
    run("x509", "-req", "-in", str(d / "site.csr"), "-CA", str(d / "ca.pem"), "-CAkey", str(d / "ca.key"),
        "-CAcreateserial", "-days", "2", "-extfile", str(d / "ext.cnf"), "-out", str(d / "site.pem"))
    der = subprocess.run(["openssl", "x509", "-in", str(d / "ca.pem"), "-outform", "DER"],
                         check=True, capture_output=True).stdout
    # Chromium's CACertificates policy: trusts the CA for server authentication.
    (d / "policy.json").write_text(json.dumps({"CACertificates": [base64.b64encode(der).decode()]}))
    (d / "ca.key").unlink()
    for f in d.iterdir():
        f.chmod(0o644)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "web":
        web()
    elif mode == "witness":
        name = sys.argv[2]
        tcp = [int(p) for p in sys.argv[3].split(",") if p]
        udp = [int(p) for p in sys.argv[4].split(",") if p] if len(sys.argv) > 4 else []
        witness(name, tcp, udp)
    elif mode == "dns":
        dns(sys.argv[2], sys.argv[3])
    elif mode == "certs":
        certs()
    else:
        sys.exit(f"unknown mode {mode}")
