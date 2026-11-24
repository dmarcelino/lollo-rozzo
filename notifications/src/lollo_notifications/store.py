"""The devices' push subscriptions, in subscriptions.json on notifications-data.

Each entry is a person's email, the push service's endpoint, the keys that
encrypt to it (p256dh, auth) and when it was added. At most MAX_SUBSCRIPTIONS
are kept in all, the oldest dropped first. Every change rewrites the file
through a temporary file and a rename, so a crash leaves the old file or the
new one, never half of one.

The file also holds the push key's public half (LOLLO_VAPID_PUBLIC_KEY) the
subscriptions were made with: a subscription works only with that key. When
the service starts with another key (an older stack.env restored next to newer
subscriptions, say), every subscription is deleted, so the page shows those
devices off and the person enables them again. A file without a key (version 1)
is taken as made with the current key, which the next change writes."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import MAX_SUBSCRIPTIONS

log = logging.getLogger("lollo_notifications.store")

FILE = "subscriptions.json"
VERSION = 2


@dataclass(frozen=True)
class Subscription:
    email: str
    endpoint: str
    p256dh: str
    auth: str
    added_at: int


def _write_json(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(entry: Any) -> Subscription | None:
    if not isinstance(entry, dict):
        return None
    try:
        sub = Subscription(
            email=entry["email"],
            endpoint=entry["endpoint"],
            p256dh=entry["p256dh"],
            auth=entry["auth"],
            added_at=entry["added_at"],
        )
    except KeyError:
        return None
    strings = (sub.email, sub.endpoint, sub.p256dh, sub.auth)
    if not all(isinstance(s, str) and s for s in strings) or not isinstance(sub.added_at, int):
        return None
    return sub


class Subscriptions:
    def __init__(
        self,
        data_dir: str | Path,
        key: str,
        clock: Callable[[], float] = time.time,
        limit: int = MAX_SUBSCRIPTIONS,
    ):
        """`key`: the push key's public half the service sends with. Subscriptions
        made with another one are deleted here, at start."""
        if not key:
            raise ValueError("the store needs the push public key")
        self._path = Path(data_dir) / FILE
        self._key = key
        self._lock = threading.Lock()
        self._clock = clock
        self._limit = limit
        self._drop_other_key()

    def _read_file(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError:
            log.warning("%s is not JSON; starting with no subscriptions", FILE)
            return {}
        return data if isinstance(data, dict) else {}

    def _load(self) -> list[Subscription]:
        entries = self._read_file().get("subscriptions")
        if not isinstance(entries, list):
            return []
        subs = [_read(e) for e in entries]
        return [s for s in subs if s is not None]

    def _save(self, subs: list[Subscription]) -> None:
        _write_json(self._path, {"v": VERSION, "key": self._key, "subscriptions": [asdict(s) for s in subs]})

    def _drop_other_key(self) -> None:
        with self._lock:
            data = self._read_file()
            # No key: a file from before keys were kept, made with the current one.
            if "key" not in data or data["key"] == self._key:
                return
            dropped = len(self._load())
            self._save([])
        log.warning(
            "%s was made with another push key; removed %s subscriptions, their devices must enable again",
            FILE,
            dropped,
        )

    def all(self) -> list[Subscription]:
        with self._lock:
            return self._load()

    def of(self, email: str) -> list[Subscription]:
        """One person's devices, oldest first."""
        return [s for s in self.all() if s.email == email]

    def add(self, email: str, endpoint: str, p256dh: str, auth: str) -> None:
        """Adds a device, or renews it: an endpoint is one device, whoever
        enrolled it before, and it moves to the newest place."""
        with self._lock:
            subs = [s for s in self._load() if s.endpoint != endpoint]
            subs.append(Subscription(email, endpoint, p256dh, auth, int(self._clock())))
            self._save(subs[-self._limit :])

    def remove(self, endpoint: str, email: str | None = None) -> bool:
        """Removes a device; with `email`, only when it is that person's. True when one went."""
        with self._lock:
            subs = self._load()
            kept = [s for s in subs if not (s.endpoint == endpoint and (email is None or s.email == email))]
            if len(kept) == len(subs):
                return False
            self._save(kept)
            return True
