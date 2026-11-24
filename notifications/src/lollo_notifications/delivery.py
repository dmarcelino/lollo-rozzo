"""Web Push to the person's devices, with the stack's one VAPID pair
(LOLLO_VAPID_*, made once by lolloctl up and never rotated: a new key breaks
every subscription made with the old one).

A notification goes to every subscription whose email is in ALLOWED_USERS
(someone removed from the stack gets nothing more), to all of them at once, at
most PUSH_CONCURRENCY at a time. Payloads are encrypted end to end (RFC 8291).
A subscription the push service reports gone (404, 410) is deleted, and so is
one whose push service times out or cannot be reached MAX_PUSH_FAILURES times
in a row (a push it takes resets the count); any other failure is logged and
decides nothing.

Each sender has a queue and a worker of its own, so neither a busy sender nor a
slow device delays or refuses another sender's notifications.

The title and the body may name a secret's labels, so neither is ever logged:
only the source, the tag, the outcome and the push service's host."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import anyio
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from pywebpush import WebPushException, webpush
from requests import RequestException

from .config import (
    MAX_PUSH_FAILURES,
    MAX_QUEUED,
    PUSH_CONCURRENCY,
    PUSH_TIMEOUT_SECONDS,
    PUSH_TTL_SECONDS,
    SEND_PER_HOUR,
    SEND_PER_MINUTE,
    Settings,
)
from .store import Subscription, Subscriptions

log = logging.getLogger("lollo_notifications.delivery")

Sender = Callable[..., Any]
# How long shutdown waits for queued notifications to go out.
DRAIN_SECONDS = 5


class Busy(Exception):
    """The sender's queue is full; it may try again later."""


class RateLimiter:
    """Sliding windows of (limit, seconds), counted per key; a refused call is not counted."""

    def __init__(self, windows: tuple[tuple[int, float], ...], clock: Callable[[], float] = time.monotonic):
        self._windows = windows
        self._longest = max(seconds for _, seconds in windows)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> tuple[float, deque[float]]:
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._longest:
            hits.popleft()
        return now, hits

    def check(self, key: str) -> bool:
        """True when one more call is within every window; records nothing."""
        now, hits = self._recent(key)
        return all(sum(1 for t in hits if now - t < seconds) < limit for limit, seconds in self._windows)

    def record(self, key: str) -> None:
        """Counts a call that `check` allowed and that went ahead."""
        now, hits = self._recent(key)
        hits.append(now)

    def allow(self, key: str) -> bool:
        """`check`, then `record` when allowed."""
        if not self.check(key):
            return False
        self.record(key)
        return True


def send_limiter(clock: Callable[[], float] = time.monotonic) -> RateLimiter:
    return RateLimiter(((SEND_PER_MINUTE, 60), (SEND_PER_HOUR, 3600)), clock)


class Pusher:
    def __init__(self, settings: Settings, subscriptions: Subscriptions, sender: Sender = webpush):
        self._settings = settings
        self.subscriptions = subscriptions
        self._send = sender
        # Timeouts and network errors in a row, by endpoint; kept in memory only,
        # so a restart starts every count again.
        self._failures: dict[str, int] = {}
        self._failures_lock = threading.Lock()

    def _failed(self, sub: Subscription, source: str, tag: str, host: str | None) -> None:
        with self._failures_lock:
            count = self._failures.get(sub.endpoint, 0) + 1
            self._failures[sub.endpoint] = count
            if count >= MAX_PUSH_FAILURES:
                del self._failures[sub.endpoint]
        if count >= MAX_PUSH_FAILURES:
            self.subscriptions.remove(sub.endpoint)
            log.warning(
                "push subscription unreachable, removed source=%s tag=%s host=%s failures=%s",
                source,
                tag,
                host,
                count,
            )

    def _reset(self, endpoint: str) -> None:
        with self._failures_lock:
            self._failures.pop(endpoint, None)

    def _send_one(self, sub: Subscription, source: str, payload: dict[str, str]) -> bool:
        """True when the push service took it. Gone subscriptions are deleted, and
        so are those that time out or cannot be reached too many times in a row."""
        host = urlsplit(sub.endpoint).hostname
        tag = payload["tag"]
        try:
            self._send(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=json.dumps(payload),
                vapid_private_key=self._settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:{self._settings.vapid_contact}"},
                ttl=PUSH_TTL_SECONDS,
                timeout=PUSH_TIMEOUT_SECONDS,
            )
            self._reset(sub.endpoint)
            log.info("push delivered source=%s tag=%s host=%s", source, tag, host)
            return True
        except WebPushException as e:
            # The push service answered: it is reachable, whatever it said.
            self._reset(sub.endpoint)
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                self.subscriptions.remove(sub.endpoint)
                log.info("push subscription gone, removed source=%s tag=%s host=%s", source, tag, host)
            else:
                log.warning("push failed source=%s tag=%s host=%s status=%s", source, tag, host, status)
        except (RequestException, OSError) as e:  # a timeout or a network error
            log.warning("push failed source=%s tag=%s host=%s error=%s", source, tag, host, type(e).__name__)
            self._failed(sub, source, tag, host)
        except Exception as e:
            log.warning("push failed source=%s tag=%s host=%s error=%s", source, tag, host, type(e).__name__)
        return False

    def targets(self, email: str | None = None) -> list[Subscription]:
        """Every device of every listed person, or of `email` alone."""
        return [
            sub
            for sub in self.subscriptions.all()
            if sub.email in self._settings.allowed_users and (email is None or sub.email == email)
        ]

    async def deliver(self, source: str, payload: dict[str, str], email: str | None = None) -> int:
        """Sends to every device of every listed person (or of `email` alone), all
        at once, at most PUSH_CONCURRENCY at a time; returns how many push
        services took it."""
        limiter = anyio.CapacityLimiter(PUSH_CONCURRENCY)
        reached = 0

        async def one(sub: Subscription) -> None:
            nonlocal reached
            if await anyio.to_thread.run_sync(self._send_one, sub, source, payload, limiter=limiter):
                reached += 1

        async with anyio.create_task_group() as tg:
            for sub in self.targets(email):
                tg.start_soon(one, sub)
        return reached


class Queue:
    """Accepted notifications, a queue and a worker per sender: each worker
    delivers its sender's notifications one after another in the background,
    and a full queue refuses only its own sender. `running` starts the workers
    in a task group; on the way out it waits up to DRAIN_SECONDS for what is
    queued."""

    def __init__(self, pusher: Pusher, sources: Iterable[str], size: int = MAX_QUEUED):
        self._pusher = pusher
        self._sources = tuple(sources)
        self._size = size
        self._send: dict[str, MemoryObjectSendStream[dict[str, str]]] = {}

    def put(self, source: str, payload: dict[str, str]) -> None:
        stream = self._send.get(source)
        if stream is None:
            raise Busy("not running")
        try:
            stream.send_nowait(payload)
        except anyio.WouldBlock as e:
            raise Busy("queue full") from e

    async def _work(self, source: str, receive: MemoryObjectReceiveStream[dict[str, str]], done: anyio.Event) -> None:
        try:
            async with receive:
                async for payload in receive:
                    try:
                        reached = await self._pusher.deliver(source, payload)
                        log.info("notification sent source=%s tag=%s devices=%s", source, payload["tag"], reached)
                    except Exception as e:
                        log.warning(
                            "notification failed source=%s tag=%s error=%s",
                            source,
                            payload["tag"],
                            type(e).__name__,
                        )
        finally:
            done.set()

    @asynccontextmanager
    async def running(self, tg: TaskGroup) -> AsyncIterator[None]:
        dones: list[anyio.Event] = []
        streams: dict[str, MemoryObjectSendStream[dict[str, str]]] = {}
        for source in self._sources:
            send, receive = anyio.create_memory_object_stream[dict[str, str]](self._size)
            done = anyio.Event()
            tg.start_soon(self._work, source, receive, done)
            streams[source] = send
            dones.append(done)
        self._send = streams
        try:
            yield
        finally:
            self._send = {}
            for send in streams.values():
                send.close()
            with anyio.move_on_after(DRAIN_SECONDS):
                for done in dones:
                    await done.wait()
