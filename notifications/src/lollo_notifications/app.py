"""The ASGI app: the senders' API at /notify/v1/send (a token per sender, no
Access, never routed by the tunnel), the person's page at /notifications/
(every route needs a verified Access JWT of the lollo's application) and
/healthz.

A sender says only what is its own: its link under /<source>/, its tag starting
with <source>-, and nothing but a title, a body, a link and a tag
(lollo_common.notify.check_payload, which the senders' client also runs). The
service then puts the sender's label in front of the title ("Vault: ",
"Browser: "), so no sender can show a notification that reads as another's. It
answers 202 once a notification is queued, in that sender's own queue, and
delivers it in the background.

The page's script registers the one service worker of the lollo host,
/notifications/sw.js at scope /, and posts this device's subscription with the
page's form token. Script elsewhere on this origin (a code editor webview, say)
can turn a device's notifications off in the browser, but it cannot post here
without that token, and nothing it does can send a notification: a push service
takes only pushes signed with this service's key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

import anyio
from jinja2 import Environment, PackageLoader, select_autoescape
from lollo_common.auth import COOP, FETCH, PAGE, Authenticator, AuthError, admit
from lollo_common.executor import BadRequest, error, guard_sources
from lollo_common.lollo import stylesheet
from lollo_common.notify import MAX_SEND_BODY, MAX_TITLE, NOTIFY_PREFIX, SEND_PATH, PayloadError, check_payload
from lollo_common.text import one_line
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from . import icons
from .config import MAX_QUEUED, OWN_SOURCE, PREFIX, TEST_PER_MINUTE, Settings
from .delivery import Busy, Pusher, Queue, RateLimiter, send_limiter

log = logging.getLogger("lollo_notifications")

SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; form-action 'none'; "
    "base-uri 'none'; object-src 'none'",
    "x-content-type-options": "nosniff",
    # same-origin, not no-referrer: with no-referrer browsers send `Origin: null`
    # on posts, which check_origin must refuse.
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
    "x-frame-options": "DENY",
    **COOP,
}
# The page registers the service worker at the lollo host's root, wider than
# the script's own path: browsers allow that only with this header.
SW_HEADERS = {**SECURITY_HEADERS, "cache-control": "no-cache", "service-worker-allowed": "/"}
# A subscription as browsers give it is well under this.
MAX_PAGE_BODY = 4 * 1024
MAX_ENDPOINT = 2048
_KEY = re.compile(r"[A-Za-z0-9_=-]{1,255}")
_ENDPOINT_CHARS = re.compile(r"[\x21-\x7e]+")
TEST_PAYLOAD = {
    "title": "Test notification",
    "body": "Lollo notifications will arrive like this.",
    "url": PREFIX + "/",
    "tag": f"{OWN_SOURCE}-test",
}

# The label in front of a sender's titles; any other sender's is its name, capitalised.
LABELS = {"vault": "Vault", "browser": "Browser", OWN_SOURCE: "Lollo"}

Handler = Callable[[Request, str], Awaitable[Response]]


def label(source: str) -> str:
    return LABELS.get(source) or source.capitalize()


def with_sender(source: str, payload: dict[str, str]) -> dict[str, str]:
    """The checked payload with the sender's label in front of its title, within
    MAX_TITLE: what is cut is the sender's own text, never the label."""
    return {**payload, "title": one_line(f"{label(source)}: {payload['title']}", MAX_TITLE)}


def device_id(endpoint: str) -> str:
    """What the page shows for a device instead of its endpoint; the page's
    script computes the same from this device's subscription to mark it."""
    return hashlib.sha256(endpoint.encode()).hexdigest()


def _fmt_time(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(ts))


async def read_body(request: Request, cap: int) -> Any:
    """The body as JSON, read with a size cap (BadRequest otherwise)."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise BadRequest()
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise BadRequest()
        if int(declared) > cap:
            raise BadRequest(413)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > cap:
            raise BadRequest(413)
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as e:
        raise BadRequest() from e


def _endpoint(value: Any) -> str:
    if not isinstance(value, str) or len(value) > MAX_ENDPOINT or not _ENDPOINT_CHARS.fullmatch(value):
        raise BadRequest()
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise BadRequest()
    return value


def create_app(
    settings: Settings,
    authenticator: Authenticator,
    pusher: Pusher,
    clock: Callable[[], float] = time.monotonic,
    queue_size: int = MAX_QUEUED,
) -> Starlette:
    env = Environment(
        loader=PackageLoader("lollo_notifications", "templates"), autoescape=select_autoescape(default=True)
    )
    static = resources.files("lollo_notifications") / "static"
    lollo_css = stylesheet()
    subscriptions = pusher.subscriptions
    queue = Queue(pusher, settings.tokens, queue_size)
    limiter = send_limiter(clock)
    test_limiter = RateLimiter(((TEST_PER_MINUTE, 60),), clock)

    def render(name: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        html = env.get_template(name).render(base=PREFIX, **ctx)
        return HTMLResponse(html, status_code=status, headers=SECURITY_HEADERS)

    def answer(body: dict[str, Any], status: int = 200) -> JSONResponse:
        return JSONResponse(body, status_code=status, headers=SECURITY_HEADERS)

    def refused(message: str, status: int, use: str | None) -> Response:
        # Without the email: a page carrying it also carries the form token.
        if use == FETCH:
            return answer({"error": message}, status)
        return render("error.html", status=status, message=message)

    def page(handler: Handler, use: str | None, post: bool = False) -> Callable[[Request], Awaitable[Response]]:
        """`use`: PAGE for the page, FETCH for its script's calls, None for an asset
        (the script, stylesheet, icon or service worker, which hold nothing to protect).
        A post also needs the lollo's Origin and the page's form token."""

        async def endpoint(request: Request) -> Response:
            try:
                email = await admit(authenticator, request.headers, use)
                if post:
                    authenticator.check_origin(request.headers)
                    authenticator.check_csrf(email, request.headers.get("x-csrf-token"))
            except AuthError as e:
                log.info("refused %s %s reason=%s", request.method, request.url.path, e.reason)
                return refused(str(e), e.status, use)
            try:
                return await handler(request, email)
            except BadRequest as e:
                return answer({"error": "too large" if e.status == 413 else "bad request"}, e.status)

        return endpoint

    # ── the senders' API ─────────────────────────────────────────────────────
    async def send(request: Request) -> Response:
        source = request.state.source
        try:
            payload = check_payload(source, await read_body(request, MAX_SEND_BODY))
        except PayloadError as e:
            log.warning("notification refused source=%s reason=%s", source, e)
            return error(400, str(e))
        tag = payload["tag"]
        if not limiter.check(source):
            log.warning("notification refused source=%s tag=%s reason=rate limit", source, tag)
            return error(429, "too many notifications; try again later")
        try:
            queue.put(source, with_sender(source, payload))
        except Busy:
            log.warning("notification refused source=%s tag=%s reason=busy", source, tag)
            return error(503, "busy; try again later")
        # Counted only once queued: a refusal (503) is not a notification sent.
        limiter.record(source)
        log.info("notification accepted source=%s tag=%s", source, tag)
        return JSONResponse({"queued": True}, status_code=202, headers={"cache-control": "no-store"})

    if settings.tokens:
        send_endpoint = guard_sources(NOTIFY_PREFIX, settings.tokens)(send)
    else:

        async def send_endpoint(request: Request) -> Response:
            # No sender has a token: nobody may send.
            return error(401, "unauthorized")

    # ── the page ─────────────────────────────────────────────────────────────
    async def home(request: Request, email: str) -> Response:
        devices = [
            {"id": device_id(s.endpoint), "added": _fmt_time(s.added_at), "host": urlsplit(s.endpoint).hostname}
            for s in reversed(subscriptions.of(email))
        ]
        return render(
            "notifications.html",
            email=email,
            csrf=authenticator.csrf_token(email),
            vapid_public_key=settings.vapid_public_key,
            devices=devices,
        )

    async def subscribe(request: Request, email: str) -> Response:
        obj = await read_body(request, MAX_PAGE_BODY)
        if not isinstance(obj, dict) or not isinstance(obj.get("keys"), dict):
            raise BadRequest()
        endpoint = _endpoint(obj.get("endpoint"))
        p256dh, auth_key = obj["keys"].get("p256dh"), obj["keys"].get("auth")
        if not all(isinstance(k, str) and _KEY.fullmatch(k) for k in (p256dh, auth_key)):
            raise BadRequest()
        subscriptions.add(email, endpoint, p256dh, auth_key)
        log.info("device enrolled host=%s", urlsplit(endpoint).hostname)
        return answer({"ok": True})

    async def unsubscribe(request: Request, email: str) -> Response:
        obj = await read_body(request, MAX_PAGE_BODY)
        if not isinstance(obj, dict):
            raise BadRequest()
        endpoint = _endpoint(obj.get("endpoint"))
        # Only one's own device: the list never shows anyone else's either.
        if subscriptions.remove(endpoint, email):
            log.info("device removed host=%s", urlsplit(endpoint).hostname)
        return answer({"ok": True})

    async def test(request: Request, email: str) -> Response:
        if not test_limiter.allow(email):
            return answer({"error": "too many tests; wait a minute"}, 429)
        payload = with_sender(OWN_SOURCE, check_payload(OWN_SOURCE, TEST_PAYLOAD))
        reached = await pusher.deliver(OWN_SOURCE, payload, email)
        log.info("notification sent source=%s tag=%s devices=%s", OWN_SOURCE, TEST_PAYLOAD["tag"], reached)
        return answer({"reached": reached})

    # ── assets: behind Access too (fetched with the Access cookie) ───────────
    def asset(body: bytes, media_type: str, headers: dict[str, str] | None = None) -> Handler:
        sent = headers or {**SECURITY_HEADERS, "cache-control": "no-cache"}

        async def serve(request: Request, email: str) -> Response:
            return Response(body, media_type=media_type, headers=sent)

        return serve

    def static_file(name: str) -> bytes:
        return (static / name).read_bytes()

    async def icon(request: Request, email: str) -> Response:
        return Response(
            icons.png(192), media_type="image/png", headers={**SECURITY_HEADERS, "cache-control": "max-age=86400"}
        )

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    async def to_page(request: Request) -> Response:
        return RedirectResponse(PREFIX + "/", status_code=308)

    ui = [
        Route("/", page(home, PAGE)),
        Route("/subscribe", page(subscribe, FETCH, post=True), methods=["POST"]),
        Route("/unsubscribe", page(unsubscribe, FETCH, post=True), methods=["POST"]),
        Route("/test", page(test, FETCH, post=True), methods=["POST"]),
        Route("/sw.js", page(asset(static_file("sw.js"), "text/javascript", SW_HEADERS), None)),
        Route("/app.js", page(asset(static_file("app.js"), "text/javascript"), None)),
        Route("/app.css", page(asset(static_file("app.css"), "text/css"), None)),
        Route("/lollo.css", page(asset(lollo_css, "text/css"), None)),
        Route("/icon-192.png", page(icon, None)),
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with anyio.create_task_group() as tg:
            async with queue.running(tg):
                yield
            tg.cancel_scope.cancel()

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route(SEND_PATH, send_endpoint, methods=["POST"]),
            Route(PREFIX, to_page),
            Mount(PREFIX, routes=ui),
        ],
        lifespan=lifespan,
    )
