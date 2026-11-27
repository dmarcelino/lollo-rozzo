"""The person's page at /browser/ on the lollo host (specs/browser/live-view):
hand-offs, tasks with take-over and give-back, the live view, per-site
settings and kept sign-ins, and the audit log. Notifications are the lollo's
own page, /notifications/ (the notifications service), linked from every page.

Every route needs a verified Access JWT for the lollo's own application
(lollo_common.auth), passes the Fetch Metadata check, and every change needs
the form token and this origin; every response sets COOP. The live view's
WebSocket needs the JWT, a same-origin `websocket` fetch, this Origin, and a
single-use ticket the page fetched with its form token. Agents reach this
port too, but have no Access JWT, so nothing here is theirs to change."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import anyio
from jinja2 import Environment, PackageLoader, select_autoescape
from lollo_common.auth import COOP, FETCH, FORM, PAGE, Authenticator, AuthError, admit
from lollo_common.lollo import stylesheet
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import icons, vnc
from .audit import AuditLog
from .config import PREFIX, TICKET_TTL_SECONDS, PageSettings
from .handoffs import HandoffError, Handoffs
from .sessions import Sessions, SiteError, is_identity_provider
from .tasks import BrowserError, BrowserService

log = logging.getLogger("lollo_browser.page")

# The vendored noVNC client (Dockerfile, stage `novnc`): only its .js files.
NOVNC_DIR = Path(os.environ.get("BROWSER_NOVNC_DIR", "/opt/novnc"))
_NOVNC_PATH = re.compile(r"(?:core|vendor)(?:/[A-Za-z0-9_.-]+)+\.js")

Handler = Callable[[Request, str], Awaitable[Response]]


def security_headers(origin: str) -> dict[str, str]:
    host = urlsplit(origin).netloc
    return {
        # noVNC draws PNG and JPEG rectangles through data: images, and talks
        # to the relay over this host's WebSocket.
        "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
        f"img-src 'self' data: blob:; connect-src 'self' wss://{host}; manifest-src 'self'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'",
        "x-content-type-options": "nosniff",
        # same-origin, not no-referrer: with no-referrer browsers send `Origin: null`
        # on form posts, which check_origin must refuse.
        "referrer-policy": "same-origin",
        "cache-control": "no-store",
        "x-frame-options": "DENY",
        **COOP,
    }


def _fmt_time(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""


def _fmt_age(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 5400:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


@dataclass
class Ticket:
    email: str
    task: str
    tab: str
    expires: float


class Tickets:
    """Single-use, short-lived tickets for the live view's WebSocket."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl: float = TICKET_TTL_SECONDS) -> None:
        self._clock = clock
        self._ttl = ttl
        self._items: dict[str, Ticket] = {}

    def issue(self, email: str, task: str, tab: str) -> str:
        now = self._clock()
        for key in [k for k, t in self._items.items() if t.expires < now]:
            del self._items[key]
        token = secrets.token_urlsafe(32)
        self._items[token] = Ticket(email, task, tab, now + self._ttl)
        return token

    def redeem(self, token: str, email: str) -> Ticket | None:
        ticket = self._items.pop(token, None) if token else None
        if ticket is None or ticket.expires < self._clock() or ticket.email != email:
            return None
        return ticket


def page_routes(
    service: BrowserService,
    settings: PageSettings,
    authenticator: Authenticator,
    tickets: Tickets | None = None,
    connect_vnc: Callable[[], Awaitable[Any]] | None = None,
) -> list[BaseRoute]:
    env = Environment(loader=PackageLoader("lollo_browser", "templates"), autoescape=select_autoescape(default=True))
    env.filters["time"] = _fmt_time
    env.filters["age"] = _fmt_age
    env.globals["base"] = PREFIX
    static = resources.files("lollo_browser") / "static"
    headers = security_headers(settings.public_origin)
    sessions: Sessions = service.sessions
    audit: AuditLog = service.audit
    handoffs: Handoffs = service.handoffs
    tickets = tickets or Tickets()

    async def default_connect() -> Any:
        return await anyio.connect_tcp(settings.vnc_host, settings.vnc_port)

    connect_vnc = connect_vnc or default_connect

    def render(name: str, email: str, status: int = 200, **ctx: Any) -> HTMLResponse:
        html = env.get_template(name).render(
            email=email,
            csrf=authenticator.csrf_token(email) if email else "",
            pending_count=len(handoffs.pending()) if email else 0,
            **ctx,
        )
        return HTMLResponse(html, status_code=status, headers=headers)

    def error(message: str, status: int, email: str = "") -> HTMLResponse:
        return render("error.html", email, status=status, message=message)

    def back(path: str) -> RedirectResponse:
        return RedirectResponse(PREFIX + path, status_code=303, headers=headers)

    def page(handler: Handler, post: bool = False, asset: bool = False) -> Callable[[Request], Awaitable[Response]]:
        """`asset`: a script, stylesheet or icon; it holds nothing to protect."""

        async def endpoint(request: Request) -> Response:
            is_json = request.headers.get("content-type", "").startswith("application/json")
            try:
                use = None if asset else (FETCH if is_json else FORM) if post else PAGE
                email = await admit(authenticator, request.headers, use)
            except AuthError as e:
                log.info("refused %s %s reason=%s", request.method, request.url.path, e.reason)
                return error(str(e), e.status)
            if post:
                try:
                    authenticator.check_origin(request.headers)
                    token = request.headers.get("x-csrf-token") if is_json else (await request.form()).get("csrf")
                    authenticator.check_csrf(email, token if isinstance(token, str) else None)
                except AuthError as e:
                    return error(str(e), e.status, email)
            try:
                return await handler(request, email)
            except (SiteError, HandoffError) as e:
                return error(str(e), 400, email)

        return endpoint

    # ── overview: hand-offs and tasks ────────────────────────────────────

    async def index(request: Request, email: str) -> Response:
        purposes = {t["task"]: t["purpose"] for t in service.tasks()}
        return render(
            "index.html",
            email,
            handoffs=handoffs.pending(),
            purposes=purposes,
            tasks=service.tasks(),
            connected=service.connected,
        )

    def show_handoff(hid: str, email: str, problem: str = "", status: int = 200) -> Response:
        try:
            h = handoffs.get(hid)
        except HandoffError:
            return error(
                problem or "This hand-off is no longer here: it was answered, its task ended, or the browser restarted.",
                404,
                email,
            )
        purpose = next((t["purpose"] for t in service.tasks() if t["task"] == h.task_id), "")
        return render("handoff.html", email, status=status, h=h, purpose=purpose, problem=problem)

    async def handoff_page(request: Request, email: str) -> Response:
        return show_handoff(request.path_params["hid"], email)

    async def handoff_approve(request: Request, email: str) -> Response:
        """Place a held checkout: the browser performs the agent's held action,
        and with allow=site the site may finish checkouts from then on. Only
        here, behind the form token: a notification only opens this page."""
        form = await request.form()
        allow = form.get("allow", "")
        if allow not in ("", "site"):
            return error("invalid answer", 400, email)
        hid = request.path_params["hid"]
        try:
            await service.approve_checkout(hid, email, allow_site=allow == "site")
        except BrowserError as e:
            return show_handoff(hid, email, problem=str(e), status=409)
        return back("/")

    async def handoff_answer(request: Request, email: str) -> Response:
        form = await request.form()
        action = form.get("action")
        if action not in ("done", "declined"):
            return error("invalid answer", 400, email)
        h = handoffs.answer(request.path_params["hid"], done=action == "done", by=email)
        found = service.find_tab(h.task_id, h.tab_id)
        audit.record("handoff_answered", found[0] if found else None, handoff=h.id, status=h.status, by=email)
        return back("/")

    async def end_task(request: Request, email: str) -> Response:
        await service.end_task_by_person(request.path_params["tid"])
        return back("/")

    async def end_all(request: Request, email: str) -> Response:
        await service.end_all_tasks()
        return back("/")

    def live_target(task: str, tab: str) -> str:
        return "/live?" + urlencode({"task": task, "tab": tab})

    async def take(request: Request, email: str) -> Response:
        tid, tab = request.path_params["tid"], request.path_params["tab"]
        try:
            if not await service.take_over(tid, tab, email):
                return error("This tab is no longer open.", 404, email)
        except BrowserError as e:
            return error(str(e), 409, email)
        return back(live_target(tid, tab))

    async def give(request: Request, email: str) -> Response:
        tid, tab = request.path_params["tid"], request.path_params["tab"]
        await service.give_back(tid, tab, email)
        return back(live_target(tid, tab))

    # ── the live view ────────────────────────────────────────────────────

    async def live(request: Request, email: str) -> Response:
        task, tab = request.query_params.get("task", ""), request.query_params.get("tab", "")
        found = service.find_tab(task, tab) if task and tab else None
        # While the person controls a tab, it stays in front: watching another
        # shows that one, read-only.
        held = None
        if found is not None and not await service.show(task, tab):
            held = service.controlled_tab()
        return render(
            "live.html",
            email,
            task=found[0] if found else None,
            tab=found[1] if found else None,
            held=held,
            connected=service.connected,
        )

    async def ticket(request: Request, email: str) -> Response:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        task, tab = str(body.get("task") or ""), str(body.get("tab") or "")
        return JSONResponse(
            {"ticket": tickets.issue(email, task[:64], tab[:16]), "path": f"{PREFIX}/live/ws"}, headers=headers
        )

    async def live_ws(websocket: WebSocket) -> None:
        try:
            email = await authenticator.email(websocket.headers)
            # Chrome sends no Fetch Metadata on a WebSocket handshake, only Origin,
            # which every browser sends and no page can set: Origin and the ticket
            # (fetched by this page with its form token) are what hold. Headers a
            # browser does send must still say a WebSocket from this site.
            mode, site = websocket.headers.get("sec-fetch-mode"), websocket.headers.get("sec-fetch-site")
            if mode not in (None, "websocket") or site not in (None, "same-origin"):
                raise AuthError("the live view opens only from its own page", reason="fetch")
            if websocket.headers.get("origin") != settings.public_origin:
                raise AuthError("cross-site request refused", reason="fetch")
            granted = tickets.redeem(websocket.query_params.get("ticket", ""), email)
            if granted is None:
                raise AuthError("the live view's ticket is missing, used or expired", reason="ticket")
        except AuthError as e:
            log.info("refused the live view reason=%s", e.reason)
            await websocket.close(code=1008)
            return
        await websocket.accept()

        async def receive() -> bytes:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return b""
            return message.get("bytes") or b""

        async def send(data: bytes) -> None:
            await websocket.send_bytes(data)

        def allow_input() -> bool:
            return bool(granted.task) and service.controls(granted.task, granted.tab)

        try:
            await vnc.relay(receive, send, connect_vnc, settings.vnc_password, allow_input)
        except (vnc.RelayError, OSError, WebSocketDisconnect, anyio.EndOfStream, anyio.BrokenResourceError) as e:
            log.info("live view closed: %s", type(e).__name__)
        finally:
            # The person closing or reloading the page leaves nothing to close.
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass

    # ── sites: settings and kept sign-ins ────────────────────────────────

    async def sites_page(request: Request, email: str) -> Response:
        return render("sites.html", email, sites=sessions.sites())

    async def site_save(request: Request, email: str) -> Response:
        form = await request.form()
        site = str(form.get("site", ""))
        keep, checkout_ok = form.get("keep_signed_in") == "on", form.get("may_checkout") == "on"
        before = sessions.setting(site)
        # The add form has both boxes unticked by default: for a site already
        # listed it would turn off what the row has, and forget its sign-in.
        if form.get("add") and (before.keep_signed_in or before.may_checkout or before.saved_at is not None):
            return error(f"{before.site} is already listed: change it in its own row on the Sites page.", 400, email)
        # Keeping the identity provider's site signs agents in to every site that
        # uses it: the person confirms it first, and nothing is saved until then.
        if (
            keep
            and not before.keep_signed_in
            and is_identity_provider(before.site)
            and form.get("confirm_identity_provider") != "on"
        ):
            confirm = {"site": before.site, "may_checkout": checkout_ok, "add": bool(form.get("add"))}
            return render("sites.html", email, sites=sessions.sites(), confirm_idp=confirm)
        sessions.set_may_checkout(site, checkout_ok)
        after = sessions.set_keep_signed_in(site, keep)
        audit.record(
            "setting_changed",
            site=after.site,
            by=email,
            keep_signed_in=after.keep_signed_in,
            may_checkout=after.may_checkout,
        )
        if before.saved_at is not None and not keep:
            audit.record("signin_forgotten", site=after.site, by=email)
        return back("/sites")

    async def site_forget(request: Request, email: str) -> Response:
        form = await request.form()
        setting = sessions.forget(str(form.get("site", "")))
        audit.record("signin_forgotten", site=setting.site, by=email)
        return back("/sites")

    async def forget_all(request: Request, email: str) -> Response:
        sites = sessions.forget_all()
        audit.record("signin_forgotten", by=email, sites=sites, all=True)
        return back("/sites")

    # ── the audit log ────────────────────────────────────────────────────

    async def audit_page(request: Request, email: str) -> Response:
        task = request.query_params.get("task", "").strip()[:64] or None
        site = request.query_params.get("site", "").strip().lower()[:300] or None
        entries = audit.read(limit=500, task=task, site=site)
        return render("audit.html", email, entries=entries, task=task or "", site=site or "")

    # ── static: behind Access too (fetched with the Access cookie) ───────

    def static_bytes(body: bytes, media_type: str) -> Handler:
        async def serve(request: Request, email: str) -> Response:
            return Response(body, media_type=media_type, headers={**headers, "cache-control": "no-cache"})

        return serve

    def static_file(name: str, media_type: str) -> Handler:
        return static_bytes((static / name).read_bytes(), media_type)

    def icon(size: int) -> Handler:
        async def serve(request: Request, email: str) -> Response:
            return Response(
                icons.png(size), media_type="image/png", headers={**headers, "cache-control": "max-age=86400"}
            )

        return serve

    async def novnc(request: Request, email: str) -> Response:
        path = request.path_params["path"]
        if not _NOVNC_PATH.fullmatch(path) or ".." in path:
            return Response("not found", status_code=404, headers=headers)
        file = NOVNC_DIR / path
        try:
            body = await anyio.Path(file).read_bytes()
        except OSError:
            return Response("not found", status_code=404, headers=headers)
        return Response(body, media_type="text/javascript", headers={**headers, "cache-control": "max-age=3600"})

    async def root(request: Request) -> Response:
        return RedirectResponse(PREFIX + "/", status_code=308)

    asset = {"asset": True}
    p = PREFIX
    return [
        Route(p, root),
        Route(p + "/", page(index)),
        Route(p + "/h/{hid}", page(handoff_page)),
        Route(p + "/h/{hid}/answer", page(handoff_answer, post=True), methods=["POST"]),
        Route(p + "/h/{hid}/approve", page(handoff_approve, post=True), methods=["POST"]),
        Route(p + "/tasks/end-all", page(end_all, post=True), methods=["POST"]),
        Route(p + "/tasks/{tid}/end", page(end_task, post=True), methods=["POST"]),
        Route(p + "/tasks/{tid}/tabs/{tab}/take", page(take, post=True), methods=["POST"]),
        Route(p + "/tasks/{tid}/tabs/{tab}/give", page(give, post=True), methods=["POST"]),
        Route(p + "/live", page(live)),
        Route(p + "/live/ticket", page(ticket, post=True), methods=["POST"]),
        WebSocketRoute(p + "/live/ws", live_ws),
        Route(p + "/sites", page(sites_page)),
        Route(p + "/sites", page(site_save, post=True), methods=["POST"]),
        Route(p + "/sites/forget", page(site_forget, post=True), methods=["POST"]),
        Route(p + "/sites/forget-all", page(forget_all, post=True), methods=["POST"]),
        Route(p + "/audit", page(audit_page)),
        Route(p + "/sw.js", page(static_file("sw.js", "text/javascript"), **asset)),
        Route(p + "/app.js", page(static_file("app.js", "text/javascript"), **asset)),
        Route(p + "/live.js", page(static_file("live.js", "text/javascript"), **asset)),
        Route(p + "/lollo.css", page(static_bytes(stylesheet(), "text/css"), **asset)),
        Route(p + "/app.css", page(static_file("app.css", "text/css"), **asset)),
        Route(p + "/icon-192.png", page(icon(192), **asset)),
        Route(p + "/novnc/{path:path}", page(novnc, **asset)),
    ]
