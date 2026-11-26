"""The guard: /mcp only for agents on the stack network (host, no Origin, no
tunnel headers), and nothing at all for the engine's network."""

from __future__ import annotations

import ipaddress

import httpx2 as httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from lollo_browser.guard import EngineNetwork, Guard

from .conftest import ENGINE_CIDR, MCP_URL


def inner_app() -> Starlette:
    async def ok(request):
        return PlainTextResponse("served")

    return Starlette(routes=[Route("/{path:path}", ok, methods=["GET", "POST"])])


def client(network: EngineNetwork, peer=("10.20.0.7", 40000)) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=Guard(inner_app(), network), client=peer)
    return httpx.AsyncClient(transport=transport, base_url="http://lollo-browser:8000")


def explicit() -> EngineNetwork:
    return EngineNetwork(ipaddress.ip_network(ENGINE_CIDR), "lollo-browser-engine")


@pytest.mark.anyio
async def test_agent_on_the_stack_network_is_served():
    async with client(explicit()) as c:
        r = await c.post("/mcp", json={})
        assert r.status_code == 200 and r.text == "served"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://alfred-lollo.example.com"},
        {"origin": "null"},
        {"cf-access-jwt-assertion": "x"},
        {"cf-access-jwt-assertion": ""},
        {"cf-connecting-ip": "192.0.2.1"},
        {"cf-ray": "1-AMS"},
        {"host": "alfred-lollo.example.com"},
        {"host": "lollo-browser.evil.test"},
        {"host": "127.0.0.1:8000"},
    ],
)
async def test_mcp_refusals(headers):
    async with client(explicit()) as c:
        r = await c.post("/mcp", json={}, headers=headers)
        assert r.status_code == 403 and "served" not in r.text


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/mcp", "/healthz", "/browser/", "/browser/live"])
async def test_engine_network_gets_nothing(path):
    async with client(explicit(), peer=("10.99.0.5", 1234)) as c:
        assert (await c.get(path)).status_code == 403


@pytest.mark.anyio
async def test_page_routes_skip_the_mcp_host_rule():
    """/browser/ comes through the tunnel with the public host; its own checks
    (lollo_common.auth, group 6) apply there."""
    async with client(explicit()) as c:
        r = await c.get("/browser/", headers={"host": "alfred-lollo.example.com", "cf-ray": "1"})
        assert r.status_code == 200


def detected(resolve, source="10.99.0.2", interfaces=(("10.20.0.3", "255.255.0.0"), ("10.99.0.2", "255.255.255.0"))):
    return EngineNetwork(
        None,
        "lollo-browser-engine",
        resolve=resolve,
        route_source=lambda ip: source,
        interfaces=lambda: list(interfaces),
    )


def test_detect_from_the_interface_that_routes_to_the_engine():
    net = detected(lambda host: ["10.99.0.4"])
    assert net.detect() and net.networks == {ipaddress.ip_network("10.99.0.0/24")}
    assert net.refuses(ipaddress.ip_address("10.99.0.200"), None)
    assert net.refuses(ipaddress.ip_address("10.20.0.7"), ipaddress.ip_address("10.99.0.2"))
    assert not net.refuses(ipaddress.ip_address("10.20.0.7"), ipaddress.ip_address("10.20.0.3"))


def test_detect_fails_closed():
    def unresolvable(host):
        raise OSError(-2, "Name or service not known")

    net = detected(unresolvable)
    assert not net.detect() and not net.known


@pytest.mark.anyio
async def test_the_service_caps_bodies_before_the_guard_and_mcp(tmp_path, limits):
    from .conftest import build

    parts = build(tmp_path, limits)
    transport = httpx.ASGITransport(app=parts.guard, client=("10.20.0.7", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://lollo-browser:8000") as c:
        big = b"{" + b" " * (2 * 1024 * 1024) + b"}"
        r = await c.post(MCP_URL, content=big, headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert (await c.get("/healthz")).status_code == 200


@pytest.mark.anyio
async def test_unknown_network_serves_loopback_only():
    def unresolvable(host):
        raise OSError(-2, "Name or service not known")

    net = detected(unresolvable)
    async with client(net) as c:
        assert (await c.post("/mcp", json={})).status_code == 503
    async with client(net, peer=("127.0.0.1", 5555)) as c:
        assert (await c.get("/healthz")).status_code == 200


@pytest.mark.anyio
async def test_full_app_refusals(env):
    assert (await env.rpc("tools/list")).status_code == 200
    for headers in ({"origin": "https://alfred-lollo.example.com"}, {"cf-ray": "1"}):
        r = await env.rpc("tools/list", headers=headers)
        assert r.status_code == 403
    r = await env.client.post(
        MCP_URL.replace("lollo-browser", "alfred-lollo.example.com"),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 403
