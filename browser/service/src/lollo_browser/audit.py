"""The audit log on browser-data: one JSON object per line, in audit.jsonl,
rotated to audit.jsonl.1 past AUDIT_MAX_BYTES (one old file is kept).

Entries name the task, its purpose, the site, the field, never a typed value:
every entry passes through the scrubber before it is written, and URLs are
written without their query or fragment."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import AUDIT_MAX_BYTES
from .scrub import SCRUBBER, Scrubber

# The events the log records. The page (group 6) filters on these.
EVENTS = (
    "task_started",
    "task_ended",
    "navigated",
    "submitted",
    "secret_typed",
    "code_typed",
    "handoff_requested",
    "handoff_answered",
    "download_blocked",
    "refused",
    "engine_disconnected",
    "checkout_submitted",
    "checkout_handed_off",
    "taken_over",
    "given_back",
    "setting_changed",
    "signin_forgotten",
)


def url_for_log(url: str) -> str:
    """The URL without query or fragment, which may carry tokens or personal data."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return parts.scheme + ":" if parts.scheme else ""
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))


class AuditLog:
    def __init__(
        self,
        data_dir: str | Path,
        scrubber: Scrubber = SCRUBBER,
        clock: Callable[[], float] = time.time,
        max_bytes: int = AUDIT_MAX_BYTES,
    ) -> None:
        self.path = Path(data_dir) / "audit.jsonl"
        self._scrubber = scrubber
        self._clock = clock
        self._max_bytes = max_bytes
        self._lock = threading.Lock()

    def record(self, event: str, task: Any = None, **fields: Any) -> dict[str, Any]:
        """task is a tasks.Task (its id and purpose are written) or None."""
        if event not in EVENTS:
            raise ValueError(f"unknown audit event {event!r}")
        entry: dict[str, Any] = {"ts": round(self._clock(), 3), "event": event}
        if task is not None:
            entry["task"] = task.id
            entry["purpose"] = task.purpose
        for key, value in fields.items():
            if value is None:
                continue
            entry[key] = url_for_log(value) if key == "url" and isinstance(value, str) else value
        entry = self._scrubber.deep(entry)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self._rotate(len(line.encode()))
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        return entry

    def _rotate(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming > self._max_bytes:
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))

    def read(
        self,
        limit: int = 200,
        task: str | None = None,
        event: str | None = None,
        site: str | None = None,
    ) -> list[dict[str, Any]]:
        """The newest entries first, optionally filtered; for the page (group 6)."""
        entries: list[dict[str, Any]] = []
        with self._lock:
            for path in (self.path, self.path.with_name(self.path.name + ".1")):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    continue
                for line in reversed(lines):
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if task and entry.get("task") != task:
                        continue
                    if event and entry.get("event") != event:
                        continue
                    if site and entry.get("site") != site:
                        continue
                    entries.append(entry)
                    if len(entries) >= limit:
                        return entries
        return entries
