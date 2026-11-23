"""Hand-offs: an agent asks the person to do something in a task's tab (sign
in, solve a challenge, finish a checkout) and waits for the answer.

The person answers on /browser/ (done or declined); one nobody answers within
HANDOFF_TTL_SECONDS ends as "no answer". The agent waits with wait_handoff,
at most MAX_WAIT_SECONDS a call (below the MCP bridge's 120 s). Checkout
recognition creates hand-offs too (kind "checkout"), holding the agent's
action: the person may approve it, the service then performs it ("approved").
While the service performs it, the hand-off is claimed (`approving`): the
person's other answers are refused, and if its task ends meanwhile, the
service still records what it did. The held action is dropped as soon as the
hand-off ends, so it runs at most once. Hand-offs live in memory, like tasks: a restart, or the end of their
task, ends them."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anyio

from .config import HANDOFF_TTL_SECONDS

PENDING, DONE, DECLINED, NO_ANSWER, APPROVED = "pending", "done", "declined", "no answer", "approved"


class HandoffError(LookupError):
    pass


@dataclass
class Handoff:
    id: str
    task_id: str
    tab_id: str
    summary: str
    url: str
    created: float
    site: str = ""
    # "request" (an agent's request_handoff) or "checkout" (a recognised checkout).
    kind: str = "request"
    status: str = PENDING
    note: str = ""
    answered_by: str = ""
    answered: float | None = None
    # A checkout's held action (the service's HeldCheckout), until the hand-off ends.
    held: Any = field(default=None, repr=False)
    # Set while the service performs the held action (claim_approval).
    approving: bool = False
    _event: anyio.Event = field(default_factory=anyio.Event, repr=False)


class Handoffs:
    def __init__(self, clock: Callable[[], float] = time.time, ttl: float = HANDOFF_TTL_SECONDS) -> None:
        self._clock = clock
        self._ttl = ttl
        self._items: dict[str, Handoff] = {}

    def request(
        self, task_id: str, tab_id: str, summary: str, url: str, site: str = "", kind: str = "request"
    ) -> Handoff:
        h = Handoff(
            id="h" + secrets.token_hex(6),
            task_id=task_id,
            tab_id=tab_id,
            summary=summary,
            url=url,
            created=self._clock(),
            site=site,
            kind=kind,
        )
        self._items[h.id] = h
        return h

    def get(self, handoff_id: str, task_id: str | None = None) -> Handoff:
        h = self._items.get(handoff_id)
        if h is None or (task_id is not None and h.task_id != task_id):
            raise HandoffError(f"no hand-off {handoff_id!r} in this task")
        return h

    def pending(self) -> list[Handoff]:
        """For the page, oldest first."""
        return sorted((h for h in self._items.values() if h.status == PENDING), key=lambda h: h.created)

    def answer(self, handoff_id: str, done: bool, by: str = "", note: str = "") -> Handoff:
        """The person's answer, from the page."""
        h = self.get(handoff_id)
        if h.status != PENDING:
            raise HandoffError("this hand-off was already answered")
        if h.approving:
            raise HandoffError("the browser is placing this checkout, as you approved")
        h.answered_by = by
        self._finish(h, DONE if done else DECLINED, note)
        return h

    def claim_approval(self, handoff_id: str) -> Handoff:
        """Before the service performs a held checkout: the hand-off must be
        pending and not claimed already. Until approve() or release(), the
        person's other answers are refused and it does not expire."""
        h = self.get(handoff_id)
        if h.status != PENDING or h.approving:
            raise HandoffError("this hand-off was already answered")
        h.approving = True
        return h

    def approve(self, h: Handoff, by: str) -> Handoff:
        """The service performed the held checkout the person approved. Never
        refused, even if the task ended meanwhile: the order was placed."""
        h.approving = False
        h.answered_by = by
        self._finish(h, APPROVED, "the person approved it and the browser placed it")
        return h

    def release(self, h: Handoff) -> None:
        """The service did not perform the held checkout: the hand-off is open
        again, or declined if its task ended meanwhile."""
        h.approving = False
        if h.status == PENDING and self._items.get(h.id) is not h:
            self._finish(h, DECLINED, "the task ended")

    def end_task(self, task_id: str) -> None:
        """A task ended: its open hand-offs are declined and then dropped. One
        being approved is only dropped: approve() or release() ends it."""
        for h in list(self._items.values()):
            if h.task_id == task_id:
                if h.status == PENDING and not h.approving:
                    self._finish(h, DECLINED, "the task ended")
                del self._items[h.id]

    def expire(self) -> None:
        now = self._clock()
        for h in list(self._items.values()):
            if h.status == PENDING and not h.approving and now - h.created > self._ttl:
                self._finish(h, NO_ANSWER, "nobody answered in time")
            if h.status != PENDING and now - (h.answered or h.created) > self._ttl:
                del self._items[h.id]

    async def wait(self, handoff_id: str, task_id: str, seconds: float) -> Handoff:
        h = self.get(handoff_id, task_id)
        if h.status == PENDING and seconds > 0:
            with anyio.move_on_after(seconds):
                await h._event.wait()
        return h

    def _finish(self, h: Handoff, status: str, note: str) -> None:
        h.status, h.note, h.answered = status, note, self._clock()
        h.held = None
        h._event.set()
