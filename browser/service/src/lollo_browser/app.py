"""The ASGI app: the agents' MCP server at /mcp (internal only; the tunnel
forwards /browser/ on the lollo host alone) and /healthz. The person's page at
/browser/ (group 6) mounts in front of /mcp through `page_routes`.

Everything sits behind guard.Guard: nothing from the engine's network is served,
and /mcp takes only the `lollo-browser` host, without Origin or tunnel headers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import anyio
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.types import ASGIApp

from .config import HOUSEKEEPING_SECONDS, INTERNAL_NAME
from .guard import EngineNetwork, Guard
from .mcp_tools import build_server
from .tasks import BrowserService


def create_app(
    service: BrowserService,
    network: EngineNetwork,
    page_routes: Sequence[BaseRoute] = (),
    housekeeping_seconds: float = HOUSEKEEPING_SECONDS,
) -> ASGIApp:
    mcp_app = build_server(service).streamable_http_app(
        stateless_http=True,
        json_response=True,
        # Agents call http://lollo-browser:8000/mcp from the stack network and
        # send no Origin; the Guard refuses the rest first.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{INTERNAL_NAME}:*", INTERNAL_NAME],
            allowed_origins=[],
        ),
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await service.start()
        async with mcp_app.router.lifespan_context(mcp_app), anyio.create_task_group() as tg:

            async def housekeeping() -> None:
                while True:
                    await anyio.sleep(housekeeping_seconds)
                    await service.expire()
                    if not network.known:
                        network.detect()

            tg.start_soon(service.run_engine)
            tg.start_soon(housekeeping)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()
        await service.stop()

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok" if service.connected else "engine not connected", status_code=200)

    app = Starlette(
        routes=[Route("/healthz", healthz), *page_routes, Mount("/", app=mcp_app)],
        lifespan=lifespan,
    )
    return Guard(app, network)
