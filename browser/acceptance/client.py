"""The agent's side of the acceptance suite (tests/run-browser-acceptance.sh).

Drives lollo-browser through its real /mcp endpoint, from the stack's default
network, as an agent's MCP client would, and checks what the results show.
Every tool result's text is saved to /out/results.jsonl, and the values typed
through the secret tools to /out/values, for the runner's checks of logs.
Reads the fixture's request log (/log/hits.log, read-only) to count the
requests of each page load.

Prints `ok    <case>` or `FAIL  <case>: <why>` per check; exits 1 on any FAIL."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from PIL import Image

MCP = "http://lollo-browser:8000/mcp"
SECRET = os.environ["ACC_SECRET"]
CARD = os.environ["ACC_CARD"]
TOTP_KEY = os.environ["ACC_TOTP_KEY"]
SITE = "https://login.acme-shop.test"
SECRET_PAGE = "https://login.acme-shop.test/pages/secret.html"
# The fields' boxes on secret.html (CSS pixels; the window has a device scale of 1).
BOXES = {"Password": (40, 120), "Card": (40, 200), "Note": (40, 280), "Code": (40, 360)}
BOX_SIZE = (322, 38)

failed = False
results = open("/out/results.jsonl", "a", encoding="utf-8")
typed_codes: set[str] = set()
_id = 0


def check(name: str, ok: bool, why: str = "") -> bool:
    global failed
    if ok:
        print(f"ok    {name}", flush=True)
    else:
        failed = True
        print(f"FAIL  {name}: {why[:900]}", flush=True)
    return ok


def info(text: str) -> None:
    print(f"info  {text}", flush=True)


class ToolError(Exception):
    pass


def rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
    global _id
    _id += 1
    req = urllib.request.Request(
        MCP,
        data=json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params}).encode(),
        headers={
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": "2025-06-18",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def call(name: str, **args: Any) -> dict[str, Any]:
    """The tool's structured result; raises ToolError with the error text."""
    reply = rpc("tools/call", {"name": name, "arguments": args})
    if "error" in reply:
        raise ToolError(json.dumps(reply["error"]))
    result = reply["result"]
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    record = {"tool": name, "isError": result.get("isError", False), "texts": texts}
    if "structuredContent" in result:
        record["structured"] = result["structuredContent"]
    results.write(json.dumps(record) + "\n")
    results.flush()
    if result.get("isError"):
        raise ToolError(" ".join(texts))
    if name == "screenshot":
        return result
    return result.get("structuredContent") or json.loads(texts[0])


def error_of(name: str, **args: Any) -> str:
    try:
        call(name, **args)
    except ToolError as e:
        return str(e)
    return ""


def snapshot(task: str, tab: str) -> str:
    return call("snapshot", task=task, tab=tab)["untrusted_page_data"]["snapshot"]


def wait_text(task: str, tab: str, text: str, seconds: float = 20) -> str:
    end = time.time() + seconds
    snap = ""
    while time.time() < end:
        snap = snapshot(task, tab)
        if text in snap:
            return snap
        time.sleep(0.5)
    return snap


def ref_of(snap: str, role: str, name: str) -> str:
    m = re.search(rf'- {role} "{re.escape(name)}"[^\n]*?\[ref=([a-z0-9]+)\]', snap)
    if not m:
        raise AssertionError(f'no {role} "{name}" in snapshot:\n{snap[:1500]}')
    return m.group(1)


def totp_codes(key: str, at: float) -> set[str]:
    """RFC 6238 codes (SHA1, 6 digits, 30 s) around `at`."""
    secret = base64.b32decode(key.upper() + "=" * (-len(key) % 8))
    out = set()
    for step in (-1, 0, 1):
        counter = int(at // 30) + step
        digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
        o = digest[-1] & 15
        out.add(f"{(struct.unpack('>I', digest[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}")
    return out


def hits() -> list[str]:
    try:
        with open("/log/hits.log", encoding="utf-8") as f:
            return [line for line in f if line.startswith("HIT ")]
    except FileNotFoundError:
        return []


def leaks(text: str) -> list[str]:
    """Which typed values (in any case, with or without separators) occur in text."""
    flat = re.sub(r"[\s\-‐-―]", "", text).lower()
    found = []
    for label, value in (("password", SECRET), ("card", CARD), ("totp key", TOTP_KEY)):
        if value.lower() in text.lower() or re.sub(r"[\s\-]", "", value).lower() in flat:
            found.append(label)
    for code in typed_codes:
        if code in text:
            found.append(f"one-time code {code}")
    return found


# ── cases ────────────────────────────────────────────────────────────────


def case_isolation() -> None:
    """5. Two tasks share no cookies or storage."""
    a = call("start_task", purpose="acceptance: isolation A")["task"]
    b = call("start_task", purpose="acceptance: isolation B")["task"]
    ta = call("open_tab", task=a, url="https://shop.acme-shop.test/set?v=task-a-marker")["tab"]
    wait_text(a, ta, "done")
    tb = call("open_tab", task=b, url="https://shop.acme-shop.test/set?v=task-b-marker")["tab"]
    wait_text(b, tb, "done")
    a2 = call("open_tab", task=a, url="https://shop.acme-shop.test/show")["tab"]
    snap_a = wait_text(a, a2, "done")
    b2 = call("open_tab", task=b, url="https://shop.acme-shop.test/show")["tab"]
    snap_b = wait_text(b, b2, "done")
    check(
        "isolation: control: a task sees its own cookie, localStorage and IndexedDB in a new tab",
        snap_a.count("task-a-marker") >= 3 and "task-b-marker" not in snap_a,
        snap_a,
    )
    check(
        "isolation: a second task, open at the same time, sees none of the first's cookies or storage",
        "task-a-marker" not in snap_b and snap_b.count("task-b-marker") >= 3,
        snap_b,
    )
    call("end_task", task=a)
    call("end_task", task=b)
    c = call("start_task", purpose="acceptance: isolation C")["task"]
    tc = call("open_tab", task=c, url="https://shop.acme-shop.test/show")["tab"]
    snap_c = wait_text(c, tc, "done")
    check(
        "isolation: a task started after both ended starts signed out",
        "marker" not in snap_c and "cookie header: (none)" in snap_c and "indexedDB: (none)" in snap_c,
        snap_c,
    )
    call("end_task", task=c)


def mask_stats(img: Image.Image, label: str) -> tuple[float, tuple[float, float, float]]:
    x, y = BOXES[label]
    # The box's inside, a few pixels in from the border JPEG blurs.
    region = img.crop((x + 4, y + 4, x + BOX_SIZE[0] - 4, y + BOX_SIZE[1] - 4)).convert("RGB")
    px = list(region.getdata())
    n = len(px)
    mean = tuple(sum(p[i] for p in px) / n for i in range(3))
    spread = max((sum((p[i] - mean[i]) ** 2 for p in px) / n) ** 0.5 for i in range(3))
    return spread, mean


def case_secrets() -> None:
    """3. Typed secrets come back in no result, and screenshots mask the fields."""
    global typed_codes
    task = call("start_task", purpose="acceptance: secrets")["task"]
    tab = call("open_tab", task=task, url=SECRET_PAGE)["tab"]
    snap = wait_text(task, tab, "echo:")
    refs = {label: ref_of(snap, "textbox", label) for label in BOXES}
    r1 = call("type_secret", task=task, tab=tab, ref=refs["Password"], text=SECRET, expect_site=SITE)
    check("secrets: type_secret types into a password field on the right site", r1.get("site") == "acme-shop.test", str(r1))
    snap = snapshot(task, tab)
    call("type_secret", task=task, tab=tab, ref=ref_of(snap, "textbox", "Card"), text=CARD, expect_site=SITE)
    snap = snapshot(task, tab)
    call("type_text", task=task, tab=tab, ref=ref_of(snap, "textbox", "Note"), text="WWWWWWWWWWWW")
    snap = snapshot(task, tab)
    typed_codes = totp_codes(TOTP_KEY, time.time())
    call("type_totp", task=task, tab=tab, ref=ref_of(snap, "textbox", "Code"), key=TOTP_KEY, expect_site=SITE)
    typed_codes |= totp_codes(TOTP_KEY, time.time())
    with open("/out/values", "w", encoding="utf-8") as f:
        f.write("\n".join([SECRET, CARD, TOTP_KEY, *sorted(typed_codes)]) + "\n")
    time.sleep(1)
    snap = snapshot(task, tab)
    check(
        "secrets: the hostile page copied the values (title, text, label, URL), and the snapshot shows [masked]",
        snap.count("[masked]") >= 4 and ("Sign in [masked]" in snap or "echo: [masked]" in snap),
        snap,
    )
    shot = call("screenshot", task=task, tab=tab)
    image = next(c for c in shot["content"] if c["type"] == "image")
    raw = base64.b64decode(image["data"])
    with open("/out/secrets.jpg", "wb") as f:
        f.write(raw)
    img = Image.open(io.BytesIO(raw))
    info(f"screenshot {img.size[0]}x{img.size[1]}")
    for label in ("Password", "Card", "Code"):
        spread, mean = mask_stats(img, label)
        info(f"screenshot, {label} field: pixel spread {spread:.1f}, mean colour {tuple(round(v) for v in mean)}")
        check(
            f"secrets: the screenshot covers the {label} field with a uniform mask",
            spread < 12,
            f"pixel spread {spread:.1f}, mean {tuple(round(v) for v in mean)}",
        )
    spread, mean = mask_stats(img, "Note")
    info(f"screenshot, Note field: pixel spread {spread:.1f}, mean colour {tuple(round(v) for v in mean)}")
    check(
        "secrets: control: the Note field, typed with type_text, is not masked (shows its text)",
        spread > 25,
        f"pixel spread {spread:.1f}, mean {tuple(round(v) for v in mean)}",
    )
    # Results so far, this task's included, must not carry a value in any form.
    call("end_task", task=task)
    results.flush()
    with open("/out/results.jsonl", encoding="utf-8") as f:
        text = f.read()
    found = leaks(text)
    check("secrets: no tool result holds a typed value, the TOTP key or a one-time code", not found, ", ".join(found))


def case_lookalike() -> None:
    """4. The site check refuses look-alikes, plain HTTP and a look-alike frame."""
    task = call("start_task", purpose="acceptance: look-alike sites")["task"]
    cases = [
        ("a look-alike domain (acme-sh0p.test)", "https://login.acme-sh0p.test/pages/secret.html", None),
        ("the site's name under another domain (acme-shop.test.evil.test)",
         "https://acme-shop.test.evil.test/pages/secret.html", None),
        ("the right site over plain HTTP", "http://login.acme-shop.test/pages/secret.html", None),
        ("a field in a frame from a look-alike, on the right site", "https://login.acme-shop.test/pages/frame.html",
         "frame"),
    ]
    for label, url, kind in cases:
        tab = call("open_tab", task=task, url=url)["tab"]
        snap = wait_text(task, tab, "echo:")
        ref = ref_of(snap, "textbox", "Password")
        if kind == "frame" and not ref.startswith("f"):
            check(f"look-alike: {label}", False, f"the field's ref {ref} is not in a frame")
            continue
        err = error_of("type_secret", task=task, tab=tab, ref=ref, text=SECRET, expect_site=SITE)
        after = snapshot(task, tab)
        check(
            f"look-alike: type_secret refuses {label}, typing nothing",
            "nothing was typed" in err and "echo: (nothing)" in after,
            f"error={err!r} snapshot={after[:600]}",
        )
        call("close_tab", task=task, tab=tab)
    call("end_task", task=task)


def case_load() -> None:
    """7. Page loads whose pipelock receipts the runner counts once pipelock has
    stopped and written them all. Pipelock writes a tunnel's receipt when the
    tunnel closes, and a task keeps its tunnels open between pages, so each
    load is a task of its own, ended inside the load's time window, which goes
    to /out/loads.json with the requests the fixture sites got."""
    loads = []
    for i in range(3):
        before = len(hits())
        start = time.time()
        task = call("start_task", purpose=f"acceptance: page load {i + 1}")["task"]
        tab = call("open_tab", task=task, url=f"https://shop.acme-shop.test/load?n=60&tag=load{i}")["tab"]
        snap = wait_text(task, tab, "loaded", 30)
        call("end_task", task=task)
        time.sleep(1.5)
        loads.append({"start": start, "end": time.time(), "requests": len(hits()) - before,
                      "loaded": "loaded" in snap})
    with open("/out/loads.json", "w", encoding="utf-8") as f:
        json.dump(loads, f)
    check("load: three pages of 68 resources from 3 hosts loaded through pipelock, each in a new task",
          all(x["loaded"] and x["requests"] >= 68 for x in loads), json.dumps(loads))


def case_webrtc_quic() -> None:
    """2. WebRTC and QUIC: the page's view (the packet capture is the runner's)."""
    task = call("start_task", purpose="acceptance: webrtc and quic")["task"]
    tab = call("open_tab", task=task, url="https://shop.acme-shop.test/pages/webrtc.html")["tab"]
    snap = wait_text(task, tab, "done", 30)
    m = re.search(r"candidates host=(\d+) srflx=(\d+) relay=(\d+) prflx=(\d+)", snap)
    info(f"webrtc: {m.group(0) if m else 'no result'}")
    check(
        "webrtc: no server-reflexive or relay candidate from STUN/TURN at the engine's network or a stack address",
        bool(m) and m.group(2) == "0" and m.group(3) == "0",
        snap[:600],
    )
    call("end_task", task=task)


PROBE_NAVIGATIONS = [
    "http://lollo-vault:8000/acc-probe-nav-vault",
    "http://bff:3000/acc-probe-nav-bff",
    "http://127.0.0.1:3000/acc-probe-nav-engine",
    "http://localhost:5900/acc-probe-nav-vnc",
    "http://169.254.169.254/acc-probe-nav-metadata",
    "http://private.test/acc-probe-nav-private",
    "http://lollo-browser:8000/acc-probe-nav-service",
    "http://rb.test/acc-probe-nav-rb1",
    "http://rb.test/acc-probe-nav-rb2",
    "http://rb.test/acc-probe-nav-rb3",
]


def case_unreachable() -> None:
    """1. The page's and the agent's attempts; the runner checks no target saw them."""
    task = call("start_task", purpose="acceptance: unreachable addresses")["task"]
    tab = call("open_tab", task=task, url="https://shop.acme-shop.test/pages/probe.html")["tab"]
    snap = wait_text(task, tab, "probes done", 40)
    check("unreachable: the hostile probe page ran", "probes done" in snap, snap[:400])
    loaded = []
    for url in PROBE_NAVIGATIONS:
        try:
            r = call("navigate", task=task, tab=tab, url=url)
            text = snapshot(task, tab)
            if "STACK-SECRET" in text or "Running" in text:
                loaded.append(url)
            info(f"navigate {url}: page from pipelock ({r['url']}): {' '.join(text.split())[:120]}")
        except ToolError as e:
            info(f"navigate {url}: {str(e)[:120]}")
    check("unreachable: no navigation to a stack, loopback or private address loaded its content", not loaded,
          str(loaded))
    call("end_task", task=task)


DENIED_NAVIGATIONS = [
    "https://alfred-lollo.stack.test/acc-denied-nav",
    "https://ALFRED-LOLLO.stack.test./acc-denied-dot",
    "https://alfred%2Dlollo.stack.test/acc-denied-pct",
    "https://shop.acme-shop.test/redirect?hops=2&to=https%3A%2F%2Falfred-lollo.stack.test%2Facc-denied-302",
]


def case_denied() -> None:
    """The stack's own host (LOLLO_DENY_HOSTS) by any route; the runner checks
    the fixture got no request for it."""
    task = call("start_task", purpose="acceptance: the stack's own host")["task"]
    tab = call("open_tab", task=task, url="https://shop.acme-shop.test/acc-denied-control")["tab"]
    check("denied: control: a fixture site loads", "fixture shop.acme-shop.test" in wait_text(
        task, tab, "fixture shop.acme-shop.test"), "the control page did not load")
    loaded = []
    for url in DENIED_NAVIGATIONS:
        try:
            r = call("navigate", task=task, tab=tab, url=url)
            text = snapshot(task, tab)
            if "fixture alfred-lollo.stack.test" in text:
                loaded.append(url)
            info(f"navigate {url}: {r.get('url')}: {' '.join(text.split())[:120]}")
        except ToolError as e:
            info(f"navigate {url}: {str(e)[:120]}")
    check("denied: no navigation to the stack's own host loaded it", not loaded, str(loaded))
    page = call("open_tab", task=task, url="https://shop.acme-shop.test/pages/denied.html")["tab"]
    snap = wait_text(task, page, "probes done", 30)
    check("denied: the page with a frame, fetches and a service worker ran", "probes done" in snap, snap[:400])
    call("end_task", task=task)


def main() -> None:
    reply = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "acceptance", "version": "1"}})
    check("mcp: the agent's client reaches /mcp from the stack network", "result" in reply, str(reply))
    tools = {t["name"] for t in rpc("tools/list", {})["result"]["tools"]}
    info(f"tools: {', '.join(sorted(tools))}")
    for case in (case_isolation, case_secrets, case_lookalike, case_load, case_webrtc_quic, case_unreachable,
                 case_denied):
        try:
            case()
        except Exception as e:  # a crash in one case fails it, the others still run
            check(f"{case.__name__} ran to the end", False, f"{type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
