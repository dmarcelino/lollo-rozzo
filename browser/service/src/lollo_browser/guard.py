"""The guard in front of every route.

1. Nothing on the engine's network gets an answer (design decision 2): a
   compromised engine must not reach /mcp or the page. A request is refused
   when its peer address is in the engine's network, or when it arrived on
   this container's own address on that network. The network is
   BROWSER_ENGINE_NETWORK_CIDR when set, and otherwise detected: the address
   the engine's name resolves to, the local address that routes to it, and
   that interface's netmask. Until it is known, only loopback is served.
2. /mcp (everything outside /healthz and /browser/) is for agents on the stack
   network: the `lollo-browser` host only, no Origin, none of Cloudflare's
   tunnel headers. The MCP SDK's own host and Origin check stays on behind it.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import logging
import re
import socket
import struct
from collections.abc import Callable, Iterable

from lollo_common.executor import TUNNEL_HEADERS
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import INTERNAL_NAME

log = logging.getLogger("lollo_browser.guard")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
INTERNAL_HOST = re.compile(rf"{re.escape(INTERNAL_NAME)}(:\d+)?")
PAGE_PREFIX = "/browser"

_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B


def _ip(value: object) -> IPAddress | None:
    try:
        ip = ipaddress.ip_address(str(value))
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)})


def route_source(ip: str) -> str:
    """The local address the kernel picks to reach ip (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((ip, 9))
        return s.getsockname()[0]


def ipv4_interfaces() -> Iterable[tuple[str, str]]:
    """(address, netmask) of every IPv4 interface (Linux ioctls)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _, name in socket.if_nameindex():
            req = struct.pack("256s", name.encode()[:15])
            try:
                addr = socket.inet_ntoa(fcntl.ioctl(s.fileno(), _SIOCGIFADDR, req)[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), _SIOCGIFNETMASK, req)[20:24])
            except OSError:
                continue
            yield addr, mask


class EngineNetwork:
    """Which addresses belong to the engine's network."""

    def __init__(
        self,
        explicit: IPNetwork | None,
        engine_host: str,
        resolve: Callable[[str], list[str]] = resolve,
        route_source: Callable[[str], str] = route_source,
        interfaces: Callable[[], Iterable[tuple[str, str]]] = ipv4_interfaces,
    ) -> None:
        self.networks: set[IPNetwork] = {explicit} if explicit else set()
        self.local: set[IPAddress] = set()
        self._auto = explicit is None
        self._host = engine_host
        self._resolve = resolve
        self._route_source = route_source
        self._interfaces = interfaces

    @property
    def known(self) -> bool:
        return bool(self.networks)

    def detect(self) -> bool:
        """Learn the network from the engine's address. Returns whether it is known."""
        if not self._auto:
            return True
        try:
            addresses = self._resolve(self._host)
            masks = dict(self._interfaces())
            for engine_ip in addresses:
                local = self._route_source(engine_ip)
                if local not in masks or local == engine_ip:
                    continue
                network = ipaddress.ip_network(f"{local}/{masks[local]}", strict=False)
                if ipaddress.ip_address(engine_ip) in network and network.prefixlen > 0:
                    if network not in self.networks:
                        log.info("engine network detected: %s", network)
                    self.networks.add(network)
                    self.local.add(ipaddress.ip_address(local))
        except OSError as e:
            log.info("engine network not detected yet: %s", e.strerror or type(e).__name__)
        return self.known

    def refuses(self, peer: IPAddress, local: IPAddress | None) -> bool:
        if local is not None and local in self.local:
            return True
        return any(peer.version == n.version and peer in n for n in self.networks)


async def _refuse(send: Send, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class Guard:
    def __init__(self, app: ASGIApp, network: EngineNetwork) -> None:
        self.app = app
        self.network = network

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        reason = self.refusal(scope)
        if reason is not None:
            status, message = reason
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await _refuse(send, status, message)
            return
        await self.app(scope, receive, send)

    def refusal(self, scope: Scope) -> tuple[int, str] | None:
        peer = _ip((scope.get("client") or ("",))[0])
        local = _ip((scope.get("server") or ("",))[0])
        if peer is None:
            return 403, "refused"
        if not peer.is_loopback:
            if not self.network.known and not self.network.detect():
                return 503, "starting: the engine's network is not known yet"
            if self.network.refuses(peer, local):
                log.warning("refused a request from the engine's network")
                return 403, "refused"
        path = scope.get("path", "")
        if path == "/healthz" or path == PAGE_PREFIX or path.startswith(PAGE_PREFIX + "/"):
            return None
        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}
        if not INTERNAL_HOST.fullmatch(headers.get("host", b"").decode("latin-1")):
            return 403, "internal only"
        if "origin" in headers or any(h in headers for h in TUNNEL_HEADERS):
            return 403, "internal only"
        return None
