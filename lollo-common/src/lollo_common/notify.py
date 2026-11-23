"""Notifications to the person's devices, sent through the notifications service
(lollo-notifications) at /notify/v1/send on the stack network.

A sender (the vault, the browser) never pushes on its own: it posts a payload to
the service with a token that only it and the service hold (NOTIFY_TOKEN). The
service holds the push keys and the subscriptions.

A sender says only what is its own. The checks here run in the sender's client,
so its own tests fail first, and again in the service, which trusts no sender:
- the payload has exactly a title, a body, a url and a tag, all strings;
- the url is a path under the sender's own prefix (/vault/), never a scheme, a
  host or a dot segment, and the tag starts with the sender's name (vault-);
- every field is one line of plain text (one_line); the title and the body are
  shortened to their caps, a url or tag over its cap is refused.

The title and the body may name a secret's labels, so neither is ever logged:
only the source, the tag and the outcome.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import unquote, urlsplit

import anyio

from .executor import SOURCE
from .text import CONTROL, one_line

log = logging.getLogger("lollo_common.notify")

NOTIFY_PREFIX = "/notify/v1"
SEND_PATH = f"{NOTIFY_PREFIX}/send"
DEFAULT_URL = f"http://lollo-notifications:8000{SEND_PATH}"
NOTIFY_TOKEN = re.compile(r"[0-9a-f]{64}")

KEYS = frozenset({"title", "body", "url", "tag"})
MAX_TITLE = 100
MAX_BODY = 400
MAX_URL = 512
MAX_TAG = 64
# A checked payload as JSON is well under this; the service reads at most this much.
MAX_SEND_BODY = 8 * 1024

SEND_TIMEOUT = 10.0
ACCEPT_TIMEOUT = 5.0

# Printable ASCII without spaces: anything else in a link is refused, not cleaned.
_URL_CHARS = re.compile(r"[\x21-\x7e]+")
_TAG = re.compile(r"[a-z0-9-]+")


class PayloadError(ValueError):
    """A payload the service would refuse; the message is a short reason that
    never quotes the payload."""


def check_payload(source: str, payload: object) -> dict[str, str]:
    """The payload as it is sent, or PayloadError."""
    if not isinstance(source, str) or not SOURCE.fullmatch(source):
        raise PayloadError("unknown source")
    if not isinstance(payload, dict):
        raise PayloadError("not an object")
    if set(payload) != KEYS:
        raise PayloadError("fields must be exactly title, body, url and tag")
    if not all(isinstance(payload[k], str) for k in KEYS):
        raise PayloadError("fields must be strings")
    title = one_line(payload["title"], MAX_TITLE)
    if not title:
        raise PayloadError("empty title")
    return {
        "title": title,
        "body": one_line(payload["body"], MAX_BODY),
        "url": _check_url(source, payload["url"]),
        "tag": _check_tag(source, payload["tag"]),
    }


def _check_url(source: str, url: str) -> str:
    if not url:
        raise PayloadError("empty url")
    if len(url) > MAX_URL:
        raise PayloadError("url too long")
    if not _URL_CHARS.fullmatch(url) or "\\" in url:
        raise PayloadError("url has characters a link may not have")
    if not url.startswith(f"/{source}/"):
        raise PayloadError("url is not under the sender's own prefix")
    path = url.split("?", 1)[0].split("#", 1)[0]
    # A browser reads %2e as a dot, so a dot segment is looked for decoded too.
    decoded = unquote(path)
    if "//" in path or "\\" in decoded or CONTROL.search(decoded):
        raise PayloadError("url has characters a link may not have")
    if any(seg in (".", "..") for seg in decoded.split("/")):
        raise PayloadError("url has a dot segment")
    return one_line(url, MAX_URL)


def _check_tag(source: str, tag: str) -> str:
    if not tag:
        raise PayloadError("empty tag")
    if len(tag) > MAX_TAG:
        raise PayloadError("tag too long")
    if not _TAG.fullmatch(tag):
        raise PayloadError("tag may hold only a-z, 0-9 and -")
    if not tag.startswith(f"{source}-") or tag == f"{source}-":
        raise PayloadError("tag does not start with the sender's name")
    return one_line(tag, MAX_TAG)


# ── the senders' client ──────────────────────────────────────────────────────

# POSTs `body` to `url` with `headers` within `timeout` seconds and returns the
# HTTP status; raises on a network error.
Transport = Callable[[str, bytes, Mapping[str, str], float], Awaitable[int]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A redirect would carry the token elsewhere: it is an answer, not followed.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# The service is on the stack network: never through a proxy, never redirected.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _post_sync(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


async def urllib_post(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    """The default transport: urllib in a worker thread, abandoned at the deadline."""
    return await anyio.to_thread.run_sync(_post_sync, url, body, headers, timeout, abandon_on_cancel=True)


def _check_url_setting(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("NOTIFY_URL must be an http or https URL")
    return url


class Notifier:
    """A sender's client of the notifications service. Neither method raises: a
    notice that was not accepted is logged with its source, tag and status."""

    def __init__(self, source: str, token: str, url: str = DEFAULT_URL, transport: Transport = urllib_post):
        if not SOURCE.fullmatch(source):
            raise ValueError(f"not a source name: {source!r}")
        if not token:
            raise ValueError("a notifier needs a token")
        self.source = source
        self._token = token
        self.url = _check_url_setting(url)
        self._transport = transport

    def __repr__(self) -> str:
        return f"Notifier(source={self.source!r}, url={self.url!r})"

    @classmethod
    def from_env(
        cls, source: str, env: Mapping[str, str] | None = None, transport: Transport = urllib_post
    ) -> Notifier:
        """Settings from NOTIFY_TOKEN (64 lowercase hex characters) and, optionally, NOTIFY_URL."""
        env = os.environ if env is None else env
        token = env.get("NOTIFY_TOKEN", "")
        if not NOTIFY_TOKEN.fullmatch(token):
            raise ValueError("NOTIFY_TOKEN must be 64 lowercase hex characters (lolloctl up makes it)")
        return cls(source, token, env.get("NOTIFY_URL") or DEFAULT_URL, transport)

    async def send(self, payload: object) -> bool:
        """Hands a notice to the service; True when it took it (202)."""
        return await self._post(payload, SEND_TIMEOUT)

    async def accept(self, payload: object) -> bool:
        """Like `send`, with a shorter wait, for a caller that acts only once the
        service took the notice (the vault's erase)."""
        return await self._post(payload, ACCEPT_TIMEOUT)

    async def _post(self, payload: object, timeout: float) -> bool:
        try:
            checked = check_payload(self.source, payload)
        except PayloadError as e:
            log.warning("notification refused before sending source=%s reason=%s", self.source, e)
            return False
        tag = checked["tag"]
        body = json.dumps(checked, ensure_ascii=False, separators=(",", ":")).encode()
        headers = {"authorization": f"Bearer {self._token}", "content-type": "application/json"}
        try:
            with anyio.fail_after(timeout):
                status = await self._transport(self.url, body, headers, timeout)
        except TimeoutError:
            log.warning("notification not accepted source=%s tag=%s error=timeout", self.source, tag)
            return False
        except Exception as e:
            log.warning("notification not accepted source=%s tag=%s error=%s", self.source, tag, type(e).__name__)
            return False
        if status == 202:
            log.info("notification accepted source=%s tag=%s status=%s", self.source, tag, status)
            return True
        log.warning("notification not accepted source=%s tag=%s status=%s", self.source, tag, status)
        return False


class NoNotifier:
    """For tests and services that run without the notifications service: sends nothing."""

    source = ""

    async def send(self, payload: object) -> bool:
        return False

    async def accept(self, payload: object) -> bool:
        return False
