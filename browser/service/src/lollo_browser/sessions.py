"""Per-site settings and kept sign-ins, on browser-data (design decision 5).

A site is a registrable domain (sites.site_of). For each site the person can
choose "keep me signed in" and "may finish checkout". Both are changed only by
the person's page (group 6) through the methods marked "page API" below; no
MCP tool calls them.

Kept sign-ins: when a task ends, its context's storage state (cookies, local
storage, IndexedDB) is filtered down to the kept sites a frame of the task
visited, and written as one file per site. A new task starts with the merged
files of every kept site. Nothing of any other site is ever written. Session
storage is not kept (Playwright does not capture it).

Tasks overlap, so each site has an epoch, in memory, that every save and
every forget moves on; a task records the epochs when it starts and saves a
site only if its epoch has not moved since. A task never writes back a copy
older than one another task saved, nor brings back a sign-in the person
forgot while it ran, even if they kept the site again. Tasks live in memory
too, so the epochs need not outlive the service.

Files: settings.json, and signins/<sha256 of the site>.json, all mode 0600."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .sites import host_of, site_of


class SiteError(ValueError):
    pass


@dataclass(frozen=True)
class SiteSetting:
    site: str
    keep_signed_in: bool = False
    may_checkout: bool = False
    # When a task last saved this site's sign-in (None: nothing saved).
    saved_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_site(value: str) -> str:
    """A site the person typed or picked: a host, a URL or a registrable domain."""
    if not isinstance(value, str) or not value.strip() or len(value) > 300:
        raise SiteError("a site is required")
    text = value.strip().lower()
    try:
        text = text.encode("idna").decode("ascii") if not text.isascii() else text
    except UnicodeError as e:
        raise SiteError("not a valid site name") from e
    site = site_of(text)
    if not site:
        raise SiteError("not a site that can hold a sign-in (a public suffix or an empty name)")
    return site


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _cookie_site(cookie: dict[str, Any]) -> str:
    return site_of(str(cookie.get("domain", "")).lstrip("."))


def _origin_site(origin: dict[str, Any]) -> str:
    return site_of(host_of(str(origin.get("origin", ""))))


class Sessions:
    def __init__(self, data_dir: str | Path, clock: Callable[[], float] = time.time) -> None:
        self._dir = Path(data_dir)
        self._settings_path = self._dir / "settings.json"
        self._signins = self._dir / "signins"
        self._clock = clock
        self._lock = threading.Lock()
        # site -> how many times its sign-in was saved or forgotten (see above).
        self._epochs: dict[str, int] = {}

    # ── storage ──────────────────────────────────────────────────────────

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        sites = data.get("sites", {}) if isinstance(data, dict) else {}
        return {k: v for k, v in sites.items() if isinstance(k, str) and isinstance(v, dict)}

    def _store(self, sites: dict[str, dict[str, Any]]) -> None:
        _write_json(self._settings_path, {"v": 1, "sites": sites})

    def _signin_path(self, site: str) -> Path:
        return self._signins / (hashlib.sha256(site.encode()).hexdigest()[:32] + ".json")

    def _setting(self, site: str, entry: dict[str, Any]) -> SiteSetting:
        saved_at = None
        path = self._signin_path(site)
        if path.exists():
            saved_at = path.stat().st_mtime
        return SiteSetting(
            site=site,
            keep_signed_in=bool(entry.get("keep_signed_in")),
            may_checkout=bool(entry.get("may_checkout")),
            saved_at=saved_at,
        )

    # ── page API (group 6): the person's choices ─────────────────────────

    def sites(self) -> list[SiteSetting]:
        """Every site with a setting, sorted by name."""
        with self._lock:
            sites = self._load()
            return [self._setting(site, sites[site]) for site in sorted(sites)]

    def setting(self, site: str) -> SiteSetting:
        site = normalize_site(site)
        with self._lock:
            return self._setting(site, self._load().get(site, {}))

    def set_keep_signed_in(self, site: str, keep: bool) -> SiteSetting:
        """Keep a site's sign-in from the next task that ends; False forgets it."""
        site = normalize_site(site)
        if not keep:
            return self.forget(site)
        return self._update(site, keep_signed_in=True)

    def set_may_checkout(self, site: str, allowed: bool) -> SiteSetting:
        return self._update(normalize_site(site), may_checkout=bool(allowed))

    def forget(self, site: str) -> SiteSetting:
        """Delete the site's saved sign-in at once and stop keeping it. Tasks
        already open keep what they loaded until they end, and save nothing."""
        site = normalize_site(site)
        with self._lock:
            self._signin_path(site).unlink(missing_ok=True)
            self._bump(site)
        return self._update(site, keep_signed_in=False)

    def forget_all(self) -> list[str]:
        """Forget every kept sign-in; returns the sites that were kept."""
        with self._lock:
            sites = self._load()
            kept = sorted(s for s, e in sites.items() if e.get("keep_signed_in"))
            for site in kept:
                self._bump(site)
            for entry in sites.values():
                entry.pop("keep_signed_in", None)
            self._store({s: e for s, e in sites.items() if any(e.values())})
            if self._signins.is_dir():
                for path in self._signins.glob("*.json"):
                    path.unlink(missing_ok=True)
        return kept

    def _update(self, site: str, **changes: bool) -> SiteSetting:
        with self._lock:
            sites = self._load()
            entry = {**sites.get(site, {}), **changes}
            entry = {k: v for k, v in entry.items() if v}
            if entry:
                sites[site] = entry
            else:
                sites.pop(site, None)
            self._store(sites)
            return self._setting(site, entry)

    def _bump(self, site: str) -> None:
        self._epochs[site] = self._epochs.get(site, 0) + 1

    # ── service API ──────────────────────────────────────────────────────

    def epochs(self) -> dict[str, int]:
        """For a task starting: taken before initial_state, and given back to save."""
        with self._lock:
            return dict(self._epochs)

    def kept_sites(self) -> frozenset[str]:
        with self._lock:
            return frozenset(s for s, e in self._load().items() if e.get("keep_signed_in"))

    def may_checkout(self, host_or_url: str) -> bool:
        """For checkout recognition (group 7)."""
        site = site_of(host_or_url)
        with self._lock:
            return bool(site) and bool(self._load().get(site, {}).get("may_checkout"))

    def initial_state(self) -> tuple[dict[str, Any] | None, frozenset[str]]:
        """The storage state a new task starts with (None when nothing is
        kept), and the sites it holds."""
        cookies: list[dict[str, Any]] = []
        origins: list[dict[str, Any]] = []
        loaded: set[str] = set()
        now = self._clock()
        for site in sorted(self.kept_sites()):
            try:
                data = json.loads(self._signin_path(site).read_text(encoding="utf-8"))
            except FileNotFoundError, ValueError:
                continue
            # Only what belongs to the site, even if the file were tampered with.
            for cookie in data.get("cookies", []):
                expires = cookie.get("expires", -1)
                if _cookie_site(cookie) == site and (not expires or expires < 0 or expires > now):
                    cookies.append(cookie)
            origins.extend(o for o in data.get("origins", []) if _origin_site(o) == site)
            loaded.add(site)
        if not loaded:
            return None, frozenset()
        return {"cookies": cookies, "origins": origins}, frozenset(loaded)

    def save(self, state: dict[str, Any], touched: Iterable[str], since: Mapping[str, int] | None = None) -> list[str]:
        """Write the kept sites among `touched` (the sites the task's frames
        visited) from a context's storage state, but not a site saved or
        forgotten after `since` (the task's epochs() at its start). Returns the
        sites written."""
        kept = self.kept_sites()
        written = []
        for site in sorted(kept & set(touched)):
            data = {
                "v": 1,
                "site": site,
                "cookies": [c for c in state.get("cookies", []) if _cookie_site(c) == site],
                "origins": [o for o in state.get("origins", []) if _origin_site(o) == site],
            }
            with self._lock:
                # Forgotten while the task ran: write nothing.
                if not self._load().get(site, {}).get("keep_signed_in"):
                    continue
                # Saved by another task, or forgotten, since this one started.
                if since is not None and self._epochs.get(site, 0) != since.get(site, 0):
                    continue
                _write_json(self._signin_path(site), data)
                self._bump(site)
            written.append(site)
        return written
