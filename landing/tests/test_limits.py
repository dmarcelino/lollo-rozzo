"""lollo_common.limits.BodyLimit, which every lollo service puts in front of its
routes, and its use here. The other services each check their own routes."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from lollo_common.limits import DEFAULT_MAX_BODY, BodyLimit

from .conftest import API, AUTH

MiB = 1024 * 1024


class Echo:
    """An ASGI app that reads the whole body and answers with its length; it
    records whether `receive` was ever called."""

    def __init__(self, swallow: bool = False):
        self.received = False
        self.swallow = swallow

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.accept"})
            return
        size = 0
        try:
            while True:
                self.received = True
                message = await receive()
                size += len(message.get("body", b""))
                if not message.get("more_body"):
                    break
        except Exception:
            if not self.swallow:
                raise
            # A handler that catches the broken stream and answers on its own.
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"bad"})
            return
        body = str(size).encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


def call(app: Any, path: str, chunks: list[bytes], declared: int | None = None) -> tuple[int, bytes, int]:
    """(status, body, chunks read) of a POST to `app` with `chunks` as its body."""
    headers = [] if declared is None else [(b"content-length", str(declared).encode())]
    scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    sent: list[dict[str, Any]] = []
    queue = list(chunks) or [b""]
    read = 0

    async def receive() -> dict[str, Any]:
        nonlocal read
        chunk = queue.pop(0) if queue else b""
        read += 1
        return {"type": "http.request", "body": chunk, "more_body": bool(queue)}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    anyio.run(app, scope, receive, send)
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, sent
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return starts[0]["status"], body, read


def test_a_declared_overrun_is_refused_without_reading():
    echo = Echo()
    status, body, read = call(BodyLimit(echo), "/mcp", [], declared=2 * MiB)
    assert status == 413 and b"at most 1048576 bytes" in body
    assert read == 0 and not echo.received


def test_a_streamed_overrun_is_refused_once_the_cap_is_passed():
    chunks = [b"x" * (256 * 1024)] * 8  # 2 MiB in 256 KiB chunks
    status, _, read = call(BodyLimit(Echo()), "/mcp", chunks)
    assert status == 413
    assert read == 5  # four chunks fill the cap; the fifth passes it, and nothing more is read


def test_a_handler_that_catches_the_overrun_still_gives_413():
    status, _, _ = call(BodyLimit(Echo(swallow=True)), "/mcp", [b"x" * (2 * MiB)])
    assert status == 413


def test_a_body_under_the_cap_passes():
    status, body, _ = call(BodyLimit(Echo()), "/mcp", [b"x" * 1000, b"y" * 1000], declared=2000)
    assert (status, body) == (200, b"2000")
    status, body, _ = call(BodyLimit(Echo()), "/mcp", [b"x" * DEFAULT_MAX_BODY])
    assert (status, body) == (200, str(DEFAULT_MAX_BODY).encode())


def test_a_route_can_have_a_larger_cap_or_none():
    app = BodyLimit(Echo(), routes={"/upload": 4 * MiB, "/upload/stream": None, "/editor": None})
    assert call(app, "/upload", [b"x" * (3 * MiB)])[0] == 200
    assert call(app, "/upload/x", [b"x" * (3 * MiB)])[0] == 200
    assert call(app, "/upload", [b"x" * (5 * MiB)])[0] == 413
    assert call(app, "/upload/stream/big", [b"x" * (9 * MiB)])[0] == 200  # the longest prefix wins
    assert call(app, "/editor", [], declared=50 * MiB)[0] == 200
    # A prefix matches whole segments only.
    assert call(app, "/uploads", [b"x" * (3 * MiB)])[0] == 413


def test_websockets_pass_untouched():
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    anyio.run(BodyLimit(Echo()), {"type": "websocket", "path": "/x", "headers": []}, receive, send)
    assert sent == [{"type": "websocket.accept"}]


def test_a_cap_must_be_positive():
    with pytest.raises(ValueError):
        BodyLimit(Echo(), default=0)


def test_the_landing_service_caps_its_internal_api(landing):
    r = landing.api.put(f"{API}/agents", content=b"x" * (2 * MiB), headers={**AUTH, "content-type": "application/json"})
    assert r.status_code == 413
    assert landing.put_agents([{"id": "agent-1", "name": "lucius"}]).status_code == 204
    assert landing.page().status_code == 200
