"""Web Push to the person's devices, like the vault's (vault/src/lollo_vault/push.py),
with the stack's own BROWSER_VAPID_* keys, generated once and never rotated.

Subscriptions live in push.json on browser-data. Payloads are encrypted end to
end (RFC 8291) and carry a title, a short body, the page to open and a tag;
every payload passes through the value scrub first. A failed push decides
nothing: a hand-off just waits."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
from pywebpush import WebPushException, webpush

from .config import PUSH_TTL_SECONDS, PageSettings
from .scrub import SCRUBBER, Scrubber
from .sessions import _write_json

log = logging.getLogger("lollo_browser.push")

Sender = Callable[..., Any]
MAX_SUBSCRIPTIONS = 50


class Subscriptions:
    def __init__(self, data_dir: str | Path) -> None:
        self._path = Path(data_dir) / "push.json"
        self._lock = threading.Lock()

    def all(self) -> list[dict[str, str]]:
        with self._lock:
            return self._load()

    def _load(self) -> list[dict[str, str]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError, ValueError:
            return []
        return [s for s in data.get("subscriptions", []) if isinstance(s, dict) and "endpoint" in s]

    def add(self, endpoint: str, p256dh: str, auth: str, email: str) -> None:
        with self._lock:
            subs = [s for s in self._load() if s["endpoint"] != endpoint]
            subs.append({"endpoint": endpoint, "p256dh": p256dh, "auth": auth, "email": email})
            _write_json(self._path, {"v": 1, "subscriptions": subs[-MAX_SUBSCRIPTIONS:]})

    def remove(self, endpoint: str) -> None:
        with self._lock:
            subs = self._load()
            kept = [s for s in subs if s["endpoint"] != endpoint]
            if len(kept) != len(subs):
                _write_json(self._path, {"v": 1, "subscriptions": kept})


class Pusher:
    def __init__(
        self,
        settings: PageSettings,
        subscriptions: Subscriptions,
        sender: Sender = webpush,
        scrubber: Scrubber = SCRUBBER,
    ) -> None:
        self._settings = settings
        self.subscriptions = subscriptions
        self._send = sender
        self._scrubber = scrubber

    def _send_one(self, sub: dict[str, str], payload: dict[str, str]) -> bool:
        """True when delivered. Gone subscriptions are deleted."""
        host = urlsplit(sub["endpoint"]).hostname
        try:
            self._send(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload),
                vapid_private_key=self._settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:{self._settings.vapid_contact}"},
                ttl=PUSH_TTL_SECONDS,
                timeout=10,
            )
            return True
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                self.subscriptions.remove(sub["endpoint"])
                log.info("push subscription gone, removed host=%s", host)
            else:
                log.warning("push failed host=%s status=%s", host, status)
        except Exception as e:  # network errors from requests
            log.warning("push failed host=%s error=%s", host, type(e).__name__)
        return False

    def send_sync(self, payload: dict[str, str]) -> int:
        payload = self._scrubber.deep(payload)
        delivered = 0
        for sub in self.subscriptions.all():
            # Someone removed from ALLOWED_USERS gets nothing more.
            if sub.get("email") not in self._settings.allowed_users:
                continue
            delivered += self._send_one(sub, payload)
        return delivered

    async def send(self, payload: dict[str, str]) -> int:
        return await anyio.to_thread.run_sync(self.send_sync, payload)


class NoPusher:
    """For a service without push (tests of the tools alone)."""

    async def send(self, payload: dict[str, str]) -> int:
        return 0
