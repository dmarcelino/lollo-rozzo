"""The person's page and the live view (specs/browser/live-view), with a fake
VNC server standing in for the engine's display."""

from __future__ import annotations

import json
import os
import re
import socket
import struct
import threading
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lollo_browser.page import Tickets
from lollo_browser.tasks import BrowserError

from .conftest import EMAIL, MCP_URL, ORIGIN, SECRET, VNC_SECRET, build, ref_of, wait_for

NAVIGATE = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-site": "same-origin"}
SCRIPT_FETCH = {"sec-fetch-mode": "cors", "sec-fetch-dest": "empty", "sec-fetch-site": "same-origin"}
WS = {"sec-fetch-mode": "websocket", "sec-fetch-dest": "websocket", "sec-fetch-site": "same-origin", "origin": ORIGIN}
SERVER_INIT = struct.pack(">HH", 1280, 900) + bytes(16) + struct.pack(">I", 4) + b"test"

# ── a fake display ───────────────────────────────────────────────────────────


def _des(password: str, challenge: bytes) -> bytes:
    """VNC authentication, written separately from vnc.py."""
    key = bytearray(password.encode()[:8].ljust(8, b"\0"))
    for i, b in enumerate(key):
        key[i] = sum(((b >> bit) & 1) << (7 - bit) for bit in range(8))
    enc = Cipher(TripleDES(bytes(key) * 3), modes.ECB()).encryptor()
    return enc.update(challenge) + enc.finalize()


class FakeVnc:
    def __init__(self, password: str) -> None:
        self.password = password
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.received = bytearray()
        self.auth: bool | None = None
        self.client_init: bytes | None = None
        self.closed = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _exactly(conn: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    def _serve(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.sendall(b"RFB 003.008\n")
                self._exactly(conn, 12)
                conn.sendall(b"\x01\x02")
                assert self._exactly(conn, 1) == b"\x02"
                challenge = os.urandom(16)
                conn.sendall(challenge)
                self.auth = self._exactly(conn, 16) == _des(self.password, challenge)
                conn.sendall(struct.pack(">I", 0 if self.auth else 1))
                if not self.auth:
                    return
                self.client_init = self._exactly(conn, 1)
                conn.sendall(SERVER_INIT)
                while data := conn.recv(4096):
                    self.received += data
            except ConnectionError, OSError, AssertionError:
                pass
            finally:
                self.closed.set()


def wait_until(predicate: Any, seconds: float = 5) -> None:
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("timed out")
        time.sleep(0.02)


# ── the page ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_jwt(key: Any, **over: Any) -> str:
    now = int(time.time())
    claims = {
        "aud": ["aud1"],
        "iss": "https://team1.cloudflareaccess.com",
        "email": EMAIL,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256")


class Page:
    def __init__(self, client: TestClient, parts: Any, key: Any, vnc: FakeVnc) -> None:
        self.client, self.parts, self.key, self.vnc = client, parts, key, vnc
        self.service = parts.service
        self.token = make_jwt(key)

    def get(self, path: str, **headers: str):
        return self.client.get(path, headers={**NAVIGATE, "cf-access-jwt-assertion": self.token, **headers})

    def csrf(self) -> str:
        return re.search(r'name="csrf" content="([0-9a-f]+)"', self.get("/browser/notifications").text).group(1)

    def post(self, path: str, data: dict[str, Any] | None = None, **headers: str):
        form = {"csrf": self.csrf(), **(data or {})}
        h = {**NAVIGATE, "cf-access-jwt-assertion": self.token, "origin": ORIGIN, **headers}
        return self.client.post(path, data=form, headers=h, follow_redirects=False)

    def post_json(self, path: str, body: dict[str, Any]):
        h = {**SCRIPT_FETCH, "cf-access-jwt-assertion": self.token, "origin": ORIGIN, "x-csrf-token": self.csrf()}
        return self.client.post(path, json=body, headers=h)

    def mcp(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
            headers={"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"},
        )
        assert r.status_code == 200, r.text
        return r.json()["result"]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = self.mcp(name, args)
        assert not result.get("isError"), result
        return result["structuredContent"]

    def error(self, name: str, args: dict[str, Any]) -> str:
        result = self.mcp(name, args)
        assert result.get("isError"), result
        return result["content"][0]["text"]

    def task_with_tab(self, url: str, purpose: str = "compare kettle prices") -> tuple[str, str]:
        task = self.call("start_task", {"purpose": purpose})["task"]
        return task, self.call("open_tab", {"task": task, "url": url})["tab"]

    def audit(self) -> list[dict[str, Any]]:
        path = self.parts.data / "audit.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def page(tmp_path, limits, sites, signing_key):
    vnc = FakeVnc(VNC_SECRET[:8])
    parts = build(tmp_path, limits, signing_key, vnc.port)
    with TestClient(parts.guard, base_url=ORIGIN, client=("10.20.0.8", 41000)) as client:
        wait_until(lambda: parts.service.connected, 30)
        yield Page(client, parts, signing_key, vnc)
    vnc.server.close()


# ── access ───────────────────────────────────────────────────────────────────


def test_not_signed_in_or_wrong_application_is_refused(page, signing_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for token in (
        "",
        "not-a-jwt",
        make_jwt(other),
        make_jwt(signing_key, aud=["lettuce-aud"]),
        make_jwt(signing_key, email="mallory@example.com"),
    ):
        for path in ("/browser/", "/browser/sites", "/browser/audit", "/browser/live"):
            r = page.client.get(path, headers={**NAVIGATE, "cf-access-jwt-assertion": token})
            assert r.status_code == 403, (path, token[:10])
            assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert page.get("/browser/").status_code == 200


def test_every_response_sets_coop_and_csp(page):
    for path in ("/browser/", "/browser/sites", "/browser/audit", "/browser/live", "/browser/notifications"):
        r = page.get(path)
        assert r.status_code == 200, path
        assert r.headers["cross-origin-opener-policy"] == "same-origin"
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert "wss://alfred-lollo.example.com" in r.headers["content-security-policy"]


def test_changes_need_the_form_token_and_this_origin(page):
    form = {"site": "example.com", "keep_signed_in": "on"}
    assert page.post("/browser/sites", form, origin="https://evil.example").status_code == 403
    cross = {"sec-fetch-site": "cross-site"}
    assert page.post("/browser/sites", form, **cross).status_code == 403
    h = {**NAVIGATE, "cf-access-jwt-assertion": page.token, "origin": ORIGIN}
    assert page.client.post("/browser/sites", data=form, headers=h).status_code == 403  # no token
    assert page.service.sessions.sites() == []
    assert page.post("/browser/sites", form).status_code == 303
    assert page.service.sessions.setting("example.com").keep_signed_in


def test_agents_cannot_change_settings_through_the_page(page):
    """An agent reaches the port, but has no Access JWT."""
    r = page.client.post(
        "/browser/sites", data={"site": "example.com", "may_checkout": "on"}, headers={**NAVIGATE, "origin": ORIGIN}
    )
    assert r.status_code == 403 and page.service.sessions.sites() == []


def test_novnc_is_served_from_this_origin(page):
    r = page.client.get("/browser/novnc/core/rfb.js", headers={"cf-access-jwt-assertion": page.token})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert "export default class RFB" in r.text
    for bad in (
        "/browser/novnc/core/%2e%2e/%2e%2e/etc/passwd",
        "/browser/novnc/core%2F..%2Frfb.js",
        "/browser/novnc/package.json",
        "/browser/novnc/LICENSE.txt",
    ):
        assert page.client.get(bad, headers={"cf-access-jwt-assertion": page.token}).status_code == 404
    assert page.client.get("/browser/novnc/core/rfb.js").status_code == 403
    live = page.get("/browser/live").text
    assert "/browser/live.js" in live and "https://" not in re.sub(r'content="[^"]*"', "", live.split("<body>")[0])


# ── lists and controls ───────────────────────────────────────────────────────


def test_overview_lists_hand_offs_and_tasks_and_ends_them(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com", "/login"), "renew the parking permit")
    h = page.call("request_handoff", {"task": task, "tab": tab, "summary": "2 items, 34.90 EUR, delivery Tuesday"})
    other, _ = page.task_with_tab(sites.https("other.test"), "check the weather")
    html = page.get("/browser/").text
    assert "2 items, 34.90 EUR, delivery Tuesday" in html and "renew the parking permit" in html
    assert "check the weather" in html and "example.com" in html and "guess" in html
    detail = page.get(f"/browser/h/{h['handoff']}").text
    assert "Open the live view" in detail and f"tab={tab}" in detail
    assert page.post(f"/browser/tasks/{task}/end").status_code == 303
    assert "ended by the person" in page.error("snapshot", {"task": task, "tab": tab})
    assert page.post("/browser/tasks/end-all").status_code == 303
    assert "ended by the person" in page.error("snapshot", {"task": other, "tab": "t1"})
    assert page.service.tasks() == []


def test_answering_a_hand_off_on_the_page(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com", "/login"))
    h = page.call("request_handoff", {"task": task, "tab": tab, "summary": "please sign in"})
    assert page.post(f"/browser/h/{h['handoff']}/answer", {"action": "done"}).status_code == 303
    assert page.call("wait_handoff", {"task": task, "handoff": h["handoff"]})["status"] == "done"
    detail = page.get(f"/browser/h/{h['handoff']}").text
    assert "Answered: done" in detail and 'value="done"' not in detail
    assert "please sign in" not in page.get("/browser/").text  # no longer waiting
    answered = [e for e in page.audit() if e["event"] == "handoff_answered"]
    assert answered and answered[0]["by"] == EMAIL and answered[0]["status"] == "done"


def test_take_over_refuses_agents_and_give_back_restores_them(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com"))
    snap = page.call("snapshot", {"task": task, "tab": tab})
    r = page.post(f"/browser/tasks/{task}/tabs/{tab}/take")
    assert r.status_code == 303 and r.headers["location"] == f"/browser/live?task={task}&tab={tab}"
    assert "the person has control of this tab" in page.error(
        "click", {"task": task, "tab": tab, "ref": ref_of(snap, "next")}
    )
    assert "You have control" in page.get(f"/browser/live?task={task}&tab={tab}").text
    page.post(f"/browser/tasks/{task}/tabs/{tab}/give")
    snap = page.call("snapshot", {"task": task, "tab": tab})
    page.call("click", {"task": task, "tab": tab, "ref": ref_of(snap, "next")})
    events = [e["event"] for e in page.audit()]
    assert "taken_over" in events and "given_back" in events


def test_sites_settings_and_kept_sign_ins(page):
    assert (
        page.post("/browser/sites", {"site": "https://shop.example.com/x", "keep_signed_in": "on"}).status_code == 303
    )
    page.post("/browser/sites", {"site": "other.test", "may_checkout": "on"})
    html = page.get("/browser/sites").text
    assert "example.com" in html and "other.test" in html and "every agent of this stack" in html
    sessions = page.service.sessions
    assert sessions.setting("example.com").keep_signed_in and sessions.may_checkout("https://pay.other.test/")
    sessions.save(
        {
            "cookies": [
                {
                    "name": "sid",
                    "value": "v",
                    "domain": "example.com",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        },
        {"example.com"},
    )
    assert page.post("/browser/sites/forget", {"site": "example.com"}).status_code == 303
    assert not sessions.setting("example.com").keep_signed_in and sessions.initial_state() == (None, frozenset())
    page.post("/browser/sites", {"site": "example.com", "keep_signed_in": "on"})
    assert page.post("/browser/sites/forget-all").status_code == 303
    assert not sessions.setting("example.com").keep_signed_in
    # Unchecking "may finish checkout" takes effect at once.
    page.post("/browser/sites", {"site": "other.test"})
    assert not sessions.may_checkout("https://other.test/")
    assert page.post("/browser/sites", {"site": "co.uk", "keep_signed_in": "on"}).status_code == 400
    events = [e["event"] for e in page.audit()]
    assert events.count("setting_changed") >= 3 and "signin_forgotten" in events


def test_audit_page_filters_and_never_shows_a_secret(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com", "/login"))
    snap = page.call("snapshot", {"task": task, "tab": tab})
    page.call(
        "type_secret",
        {"task": task, "tab": tab, "ref": ref_of(snap, "Password"), "text": SECRET, "expect_site": "example.com"},
    )
    other, _ = page.task_with_tab(sites.https("other.test"), "check the weather")
    html = page.get("/browser/audit").text
    assert "secret typed" in html and "textbox &#34;Password&#34;" in html and SECRET not in html
    assert html.index("check the weather") < html.index("compare kettle prices")  # newest first
    only = page.get(f"/browser/audit?task={task}").text
    assert "check the weather" not in only and "secret typed" in only
    by_site = page.get("/browser/audit?site=other.test").text
    assert "secret typed" not in by_site and "navigated" in by_site


def test_audit_page_names_a_payment_provider(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com", "/frame-provider"))
    snap = page.call("snapshot", {"task": task, "tab": tab})
    page.call(
        "type_secret",
        {"task": task, "tab": tab, "ref": ref_of(snap, "Password"), "text": SECRET, "expect_site": "example.com"},
    )
    html = page.get(f"/browser/audit?task={task}").text
    assert "in stripe.com's frame" in html and SECRET not in html


def test_push_subscriptions_and_test(page):
    sub = {"endpoint": "https://push.example/device-2", "keys": {"p256dh": "k", "auth": "a"}}
    assert page.post_json("/browser/push/subscribe", sub).json() == {"ok": True}
    assert any(s["endpoint"] == sub["endpoint"] for s in page.parts.service.pusher.subscriptions.all())
    assert page.post_json("/browser/push/subscribe", {"endpoint": "http://x", "keys": {}}).status_code == 400
    assert page.post_json("/browser/push/test", {}).json() == {"delivered": 2}
    page.post_json("/browser/push/unsubscribe", {"endpoint": sub["endpoint"]})
    assert len(page.parts.service.pusher.subscriptions.all()) == 1


# ── the live view ────────────────────────────────────────────────────────────


def ticket(page: Page, task: str = "", tab: str = "") -> str:
    r = page.post_json("/browser/live/ticket", {"task": task, "tab": tab})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


class Rfb:
    """The person's side, as noVNC speaks it."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.buffer = b""

    def read(self, n: int) -> bytes:
        while len(self.buffer) < n:
            self.buffer += self.ws.receive_bytes()
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def handshake(self) -> None:
        assert self.read(12) == b"RFB 003.008\n"
        self.ws.send_bytes(b"RFB 003.008\n")
        assert self.read(2) == b"\x01\x01"  # security type None: the password stays in the relay
        self.ws.send_bytes(b"\x01")
        assert self.read(4) == b"\0\0\0\0"
        self.ws.send_bytes(b"\x00")  # not shared; the relay asks for shared anyway
        assert self.read(len(SERVER_INIT)) == SERVER_INIT


KEY = struct.pack(">BBxxI", 4, 1, 0x61)
POINTER = struct.pack(">BBHH", 5, 1, 10, 10)
UPDATE = struct.pack(">BBHHHH", 3, 1, 0, 0, 1280, 900)


def test_live_view_relays_read_only_until_take_over(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com"))
    vnc = page.vnc
    with page.client.websocket_connect(
        f"/browser/live/ws?ticket={ticket(page, task, tab)}", headers={**WS, "cf-access-jwt-assertion": page.token}
    ) as ws:
        rfb = Rfb(ws)
        rfb.handshake()
        assert vnc.auth is True and vnc.client_init == b"\x01"
        encodings = [7, 0, -258, -308, -312, -313, struct.unpack(">i", b"\xc0\xa1\xe5\xce")[0], -316, -239]
        ws.send_bytes(struct.pack(f">BxH{len(encodings)}i", 2, len(encodings), *encodings))
        ws.send_bytes(KEY + POINTER + UPDATE)
        wait_until(lambda: UPDATE in vnc.received)
        assert vnc.received.startswith(struct.pack(">BxH3i", 2, 3, 7, 0, -239))
        assert KEY not in vnc.received and POINTER not in vnc.received
        # Take over: input reaches the display.
        page.post(f"/browser/tasks/{task}/tabs/{tab}/take")
        ws.send_bytes(KEY + UPDATE)
        wait_until(lambda: KEY in vnc.received)
        # Give back: dropped again.
        page.post(f"/browser/tasks/{task}/tabs/{tab}/give")
        before = len(vnc.received)
        ws.send_bytes(POINTER + UPDATE)
        wait_until(lambda: len(vnc.received) >= before + len(UPDATE))
        assert POINTER not in bytes(vnc.received[before:])
        # A message outside the set closes the connection.
        ws.send_bytes(b"\xfa\x00\x00\x00")
        assert vnc.closed.wait(5)


def test_live_view_opens_without_fetch_metadata_as_chrome_sends_it(page):
    # Chrome's WebSocket handshake carries Origin but no Sec-Fetch-* header.
    headers = {"origin": ORIGIN, "cf-access-jwt-assertion": page.token}
    with page.client.websocket_connect(f"/browser/live/ws?ticket={ticket(page)}", headers=headers) as ws:
        Rfb(ws).handshake()
        assert page.vnc.auth is True


def test_live_view_without_a_tab_never_passes_input(page):
    with page.client.websocket_connect(
        f"/browser/live/ws?ticket={ticket(page)}", headers={**WS, "cf-access-jwt-assertion": page.token}
    ) as ws:
        Rfb(ws).handshake()
        ws.send_bytes(KEY + UPDATE)
        wait_until(lambda: UPDATE in page.vnc.received)
        assert KEY not in page.vnc.received


@pytest.mark.parametrize(
    "change",
    [
        "no token",
        "lettuce token",
        "cross-site",
        "other origin",
        "no origin",
        "no origin nor fetch metadata",
        "other origin nor fetch metadata",
        "no ticket",
        "reused ticket",
        "not ws",
    ],
)
def test_live_view_refusals(page, signing_key, change):
    headers = {**WS, "cf-access-jwt-assertion": page.token}
    t = ticket(page)
    if change.endswith("nor fetch metadata"):
        for k in ("sec-fetch-mode", "sec-fetch-dest", "sec-fetch-site"):
            headers.pop(k)
        if change.startswith("no origin"):
            headers.pop("origin")
        else:
            headers["origin"] = "https://evil.example"
    elif change == "no token":
        headers.pop("cf-access-jwt-assertion")
    elif change == "lettuce token":
        headers["cf-access-jwt-assertion"] = make_jwt(signing_key, aud=["lettuce-aud"])
    elif change == "cross-site":
        headers["sec-fetch-site"] = "cross-site"
    elif change == "other origin":
        headers["origin"] = "https://evil.example"
    elif change == "no origin":
        headers.pop("origin")
    elif change == "no ticket":
        t = ""
    elif change == "reused ticket":
        with page.client.websocket_connect(f"/browser/live/ws?ticket={t}", headers=headers) as ws:
            Rfb(ws).handshake()
    elif change == "not ws":
        headers["sec-fetch-mode"] = "navigate"
    with pytest.raises(WebSocketDisconnect):
        with page.client.websocket_connect(f"/browser/live/ws?ticket={t}", headers=headers) as ws:
            ws.receive_bytes()


def test_tickets_are_single_use_short_lived_and_personal():
    now = [0.0]
    tickets = Tickets(clock=lambda: now[0], ttl=30)
    a = tickets.issue(EMAIL, "task-1", "t1")
    assert tickets.redeem(a, "hal@example.com") is None  # and it is gone
    b = tickets.issue(EMAIL, "task-1", "t1")
    now[0] = 31
    assert tickets.redeem(b, EMAIL) is None
    c = tickets.issue(EMAIL, "task-1", "t1")
    assert tickets.redeem(c, EMAIL).tab == "t1" and tickets.redeem(c, EMAIL) is None


def test_wrong_display_password_closes_the_view(tmp_path, limits, sites, signing_key):
    vnc = FakeVnc("another1")
    parts = build(tmp_path, limits, signing_key, vnc.port)
    with TestClient(parts.guard, base_url=ORIGIN, client=("10.20.0.8", 41000)) as client:
        p = Page(client, parts, signing_key, vnc)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/browser/live/ws?ticket={ticket(p)}", headers={**WS, "cf-access-jwt-assertion": p.token}
            ) as ws:
                ws.receive_bytes()
        assert vnc.auth is False
    vnc.server.close()


# ── one controlled tab, kept in front ────────────────────────────────────────


@pytest.mark.anyio
async def test_one_tab_at_a_time_is_controlled(env, sites):
    a = await env.task("task A")
    a1 = await env.tab(a, sites.https("shop.example.com"))
    b = await env.task("task B")
    b1 = await env.tab(b, sites.https("other.test"))
    assert await env.service.take_over(a, a1, EMAIL)
    with pytest.raises(BrowserError, match="give it back first"):
        await env.service.take_over(b, b1, EMAIL)
    assert not env.service.find_tab(b, b1)[1].controlled
    assert await env.service.take_over(a, a1, EMAIL)  # the same tab again is fine
    assert await env.service.give_back(a, a1, EMAIL)
    assert await env.service.take_over(b, b1, EMAIL)


@pytest.mark.anyio
async def test_the_controlled_tab_stays_in_front(env, sites):
    """Every tab shares one display: the person's input passes only while the
    tab they control is the one the service last brought to the front."""
    service = env.service
    a = await env.task("task A")
    a1 = await env.tab(a, sites.https("shop.example.com"))
    b = await env.task("task B")
    b1 = await env.tab(b, sites.https("shop.example.com", "/popups"))
    assert await service.show(b, b1)
    assert not service.controls(a, a1)  # not controlled yet
    assert await service.take_over(a, a1, EMAIL)
    assert service.controls(a, a1)
    # Watching another tab does not bring it to the front.
    assert not await service.show(b, b1)
    assert service.controls(a, a1)
    # An agent opens a tab in another task: input is dropped until the
    # controlled tab is in front again.
    await env.tab(b, sites.https("other.test"))
    assert not service.controls(a, a1)
    await wait_for(lambda: service.controls(a, a1))
    # So with a page's popup.
    await env.service.end_task_by_person(b)
    c = await env.task("task C")
    c1 = await env.tab(c, sites.https("shop.example.com", "/popups"))
    await wait_for(lambda: service.controls(a, a1))
    snap = await env.snap(c, c1)
    await env.call("click", {"task": c, "tab": c1, "ref": ref_of(snap, "open")})
    await wait_for(lambda: not service.controls(a, a1), 2)
    await wait_for(lambda: service.controls(a, a1))
    # Given back, watching works again and input stops.
    assert await service.give_back(a, a1, EMAIL)
    assert not service.controls(a, a1)
    assert await service.show(c, c1)


def test_watching_another_tab_while_in_control_says_so(page, sites):
    task, tab = page.task_with_tab(sites.https("shop.example.com"))
    other, other_tab = page.task_with_tab(sites.https("other.test"), "check the weather")
    page.post(f"/browser/tasks/{task}/tabs/{tab}/take")
    html = page.get(f"/browser/live?task={other}&tab={other_tab}").text
    assert "You have control of another tab" in html
    assert page.service.controls(task, tab)
    r = page.post(f"/browser/tasks/{other}/tabs/{other_tab}/take")
    assert r.status_code == 409 and "give it back first" in r.text


def test_adding_a_site_already_listed_changes_nothing(page):
    sessions = page.service.sessions
    assert page.post("/browser/sites", {"site": "example.com", "keep_signed_in": "on", "add": "1"}).status_code == 303
    cookie = {"name": "sid", "value": "v", "domain": "example.com", "path": "/", "expires": -1}
    sessions.save({"cookies": [cookie], "origins": []}, {"example.com"})
    assert 'name="add"' in page.get("/browser/sites").text  # the add form says it is one
    # The add form, used again for a listed site with only "may finish checkouts".
    r = page.post("/browser/sites", {"site": "https://shop.example.com/", "may_checkout": "on", "add": "1"})
    assert r.status_code == 400 and "already listed" in r.text
    setting = sessions.setting("example.com")
    assert setting.keep_signed_in and not setting.may_checkout and setting.saved_at is not None
    assert "signin_forgotten" not in [e["event"] for e in page.audit()]
    # Its own row still changes it.
    page.post("/browser/sites", {"site": "example.com", "keep_signed_in": "on", "may_checkout": "on"})
    assert sessions.setting("example.com").may_checkout
