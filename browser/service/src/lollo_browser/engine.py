"""The connection to the engine's Chromium (browser/engine/).

The engine runs Playwright's launchServer with fixed options (sandbox on,
proxy to pipelock, no QUIC); this side can only connect, create contexts and
drive pages. Tests inject a local Chromium through the same interface."""

from __future__ import annotations

from typing import Protocol

from playwright.async_api import Browser, Playwright


class Engine(Protocol):
    async def connect(self, playwright: Playwright) -> Browser:
        """A connected Browser. Raises on failure; the service retries."""
        ...


class RemoteEngine:
    """The engine container: ws://lollo-browser-engine:3000/<secret>. The path
    is the secret; errors that would show it are scrubbed (scrub.SCRUBBER has it)."""

    def __init__(self, url: str, secret: str, timeout_ms: float = 30_000) -> None:
        self._endpoint = f"{url.rstrip('/')}/{secret}"
        self._timeout = timeout_ms

    async def connect(self, playwright: Playwright) -> Browser:
        return await playwright.chromium.connect(self._endpoint, timeout=self._timeout)
