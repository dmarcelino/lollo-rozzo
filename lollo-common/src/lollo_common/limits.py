"""A cap on request bodies, applied by every lollo service before any handler
reads one.

Any container on the stack network reaches the lollo services without passing
Access, and a handler that reads a body it was sent (a form, JSON, an MCP call)
would hold all of it in memory: one large post could restart the vault, which
locks it. BodyLimit refuses a body over its route's cap with 413, before the
handler runs when the request declares its length, and as soon as the cap is
passed when it streams one.

It is plain ASGI, given to each app's Starlette as middleware, so it runs before
routing and before a mounted MCP app.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Far above any MCP call, form or internal API request today (a few KiB).
DEFAULT_MAX_BODY = 1024 * 1024

Scope = dict[str, Any]
Message = dict[str, Any]


class _TooLarge(Exception):
    """Raised from `receive` once a streamed body passes the cap."""


class BodyLimit:
    """`default`: the cap in bytes for every route. `routes`: a cap per path
    prefix (the longest match wins); None leaves that prefix uncapped, for a
    route that streams its body on after authenticating (the code editor's
    proxy). Websocket and lifespan scopes pass through untouched."""

    def __init__(self, app: Any, default: int = DEFAULT_MAX_BODY, routes: Mapping[str, int | None] | None = None):
        if default <= 0:
            raise ValueError("the default cap must be positive")
        self.app = app
        self.default = default
        # Longest prefix first, so the first match is the most specific one.
        self.routes = sorted((routes or {}).items(), key=lambda kv: len(kv[0]), reverse=True)

    def cap_for(self, path: str) -> int | None:
        for prefix, cap in self.routes:
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return cap
        return self.default

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cap = self.cap_for(scope.get("path", ""))
        if cap is None:
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > cap:
            await _refuse(send, cap)
            return

        received = 0
        tripped = False  # the body passed the cap
        started = False  # the handler's response started before that
        refused = False  # our 413 was sent

        async def limited_receive() -> Message:
            nonlocal received, tripped
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    tripped = True
                    raise _TooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal started, refused
            if tripped and not started:
                # The handler caught the overrun and answered on its own: the
                # caller gets our 413 instead, once.
                if not refused:
                    refused = True
                    await _refuse(send, cap)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except Exception:
            # _TooLarge, or whatever the handler raised because its body stream
            # broke off. With a response already under way, the server drops
            # the connection.
            if not tripped or started:
                raise
        if tripped and not started and not refused:
            await _refuse(send, cap)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            text = value.decode("latin-1").strip()
            return int(text) if text.isdigit() else None
    return None


async def _refuse(send: Any, cap: int) -> None:
    body = f"request body too large: at most {cap} bytes\n".encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
