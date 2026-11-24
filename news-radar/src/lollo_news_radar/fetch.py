"""The fetcher: the most exposed code here, reading hostile content from URLs an
agent chose.

- Only http and https, only public addresses. The connection itself goes to an
  address resolved and checked here (PublicOnlyBackend), so a name that resolves
  differently between a check and the connect gains nothing. Redirects are
  followed by hand, each checked again, at most MAX_REDIRECTS.
- Polite: one user agent naming the project, robots.txt honoured for it (and its
  Crawl-delay), at least HOST_SPACING seconds between two requests to one host, a
  pause after a 429 or 503, conditional requests reused. Rules are matched as RFC
  9309 says (`*`, `$`, the longest rule wins), as the image's Python 3.14
  `urllib.robotparser` does; test_fetch holds any interpreter to it. A feed's
  own URL (`feed=True`) is read whatever robots.txt says, as feed readers do,
  but only a feed is kept from it.
- Bounded: a timeout per request, a cap on every body.
- No cookies, no credentials, no script: a challenge or waiting room is a failure.

Agents can reach the stack network themselves, so the address rule is not their
boundary: it keeps a scheduled job from walking the stack's services or the host.
"""

from __future__ import annotations

import codecs
import ipaddress
import math
import re
import socket
import ssl
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import anyio
import httpcore
import httpx

from .config import (
    ERROR_BODY,
    HOST_BACKOFF,
    HOST_SPACING,
    MAX_CRAWL_DELAY,
    MAX_REDIRECTS,
    MAX_RESPONSE,
    REQUEST_TIMEOUT,
    ROBOTS_KEEP,
    ROBOTS_RETRY,
    USER_AGENT,
    USER_AGENT_NAME,
)

Resolver = Callable[[str, int], Awaitable[list[str]]]

# Markers of a bot challenge, waiting room or CAPTCHA served instead of content.
CHALLENGE_MARKERS = (
    b"/cdn-cgi/challenge-platform/",
    b"cf-chl-",
    b"<title>just a moment",
    b"queue-it.net",
    b"queue-it_",
    b"g-recaptcha",
    b"h-captcha",
    b"captcha-delivery.com",
    b"_incapsula_resource",
    b"px-captcha",
)
WAITING_ROOM_HOSTS = ("queue-it.net",)


class FetchError(Exception):
    """A fetch that yields nothing; the message says why, in a few words.
    retry_after: the seconds the site asked to be left alone, when it asked.
    page: what the site answered with an error status, its body cut to
    ERROR_BODY: website text for an agent repairing the source, never for logs."""

    def __init__(self, message: str, retry_after: int = 0, page: Page | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.page = page


class RobotsRefusal(FetchError):
    """robots.txt refuses the URL, or answered without rules (unreadable: RFC
    9309 then refuses everything). A feed's own URL is read past it (Fetcher.get's
    feed=True); a host that could not be reached at all is a plain FetchError."""

    def __init__(self, message: str, unreadable: bool = False, page: Page | None = None):
        super().__init__(message, page=page)
        self.unreadable = unreadable


class HostBusy(FetchError):
    """The host asked for a longer pause than this call may wait: try again later.
    Never cached as the host's robots.txt answer."""


class BlockedAddress(Exception):
    pass


class UnknownHost(Exception):
    """A name that does not resolve: said as such, not as a failed connection."""


def is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Connects only to public addresses: the name is resolved here, every address
    checked, and the connection made to the checked address. TLS still verifies
    the name (httpcore passes the URL's host as server_hostname)."""

    def __init__(self, resolver: Resolver = system_resolver, inner: httpcore.AsyncNetworkBackend | None = None):
        self._resolve = resolver
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await self._resolve(host, port)
        except OSError as e:
            raise UnknownHost(host) from e
        if not addresses:
            raise UnknownHost(host)
        if not all(is_public(a) for a in addresses):
            raise BlockedAddress(host)
        last: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        assert last is not None
        raise last

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None) -> Any:
        raise BlockedAddress("unix socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _Stream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response):
        self._response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._response.aiter_stream():
            yield chunk

    async def aclose(self) -> None:
        await self._response.aclose()


class PublicTransport(httpx.AsyncBaseTransport):
    """httpx over an httpcore pool whose connections go through PublicOnlyBackend."""

    def __init__(self, resolver: Resolver = system_resolver):
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PublicOnlyBackend(resolver),
            max_connections=8,
            keepalive_expiry=30.0,
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        core = httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=url.raw_scheme, host=url.raw_host, port=url.port, target=url.raw_path),
            headers=request.headers.raw,
            content=request.stream,  # type: ignore[arg-type]
            extensions=request.extensions,
        )
        try:
            response = await self._pool.handle_async_request(core)
        except httpcore.TimeoutException as e:
            raise httpx.TimeoutException("timed out", request=request) from e
        except (httpcore.NetworkError, httpcore.ProtocolError, httpcore.UnsupportedProtocol) as e:
            raise httpx.TransportError(str(e) or type(e).__name__) from e
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_Stream(response),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    body: bytes
    etag: str = ""
    last_modified: str = ""
    # The encoding the Content-Type header names, when it names one Python knows.
    charset: str = ""
    # A feed read past robots.txt (Fetcher.get's feed=True): "disallowed" when its
    # rules refuse it, "unreadable" when it answered without rules; "" otherwise.
    robots_exempt: str = ""

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def text(self) -> str:
        return self.body.decode(self.charset or "utf-8", errors="replace")


def charset_of(content_type: str) -> str:
    """The charset parameter of a Content-Type header, if Python has that codec."""
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset":
            value = value.strip().strip("\"'").lower()
            try:
                info = codecs.lookup(value) if value else None
            except LookupError:
                return ""
            # Only text encodings: base64 or rot13 are codecs too, but not a page's charset.
            return info.name if info is not None and info._is_text_encoding else ""
    return ""


def check_url(url: str) -> str:
    """The URL if it is an absolute http(s) URL without credentials, else FetchError."""
    if len(url) > 2000:
        raise FetchError("URL over 2000 characters")
    try:
        parts = urlsplit(url)
    except ValueError as e:
        raise FetchError("not a URL") from e
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("only http and https URLs")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials are refused")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public(host):
            raise FetchError("not a public address")
    return url


def host_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def looks_challenged(body: bytes) -> bool:
    head = body[:65536].lower()
    return any(m in head for m in CHALLENGE_MARKERS)


# The root element of an RSS, Atom or RSS 1.0 document, with or without a prefix.
FEED_ROOT = re.compile(r"<(?:[A-Za-z_][\w.-]{0,40}:)?(?:rss|feed|rdf)[\s/>]", re.I)
# How much of an answer is scanned for the root: long licence comments and
# stylesheet lines come before it in some feeds.
FEED_HEAD = 65536


def looks_like_feed(body: bytes) -> bool:
    """Whether the body starts as an RSS, Atom or RSS 1.0 document (its root
    element, after a BOM, UTF-8 or UTF-16, whitespace, processing instructions
    such as the XML declaration or a stylesheet, comments and a doctype), or is a
    JSON Feed, which feedparser reads too. What robots.txt refuses is read only
    when it is a feed. A scan, not a pattern: nothing here can backtrack on a
    hostile answer."""
    head = body[:FEED_HEAD]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = head.decode("utf-16", errors="replace")
    else:
        text = head.decode("utf-8", errors="replace").removeprefix("\ufeff")
    if text.lstrip().startswith("{"):
        return "jsonfeed.org/version/" in text
    i = 0
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if text.startswith("<?", i):
            end = text.find("?>", i + 2)
            i = end + 2
        elif text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            i = end + 3
        elif text[i : i + 9].lower() == "<!doctype":
            end, subset = text.find(">", i), text.find("[", i)
            if 0 <= subset < end:  # an internal subset: the doctype ends after its "]"
                bracket = text.find("]", subset)
                end = text.find(">", bracket) if bracket >= 0 else -1
            i = end + 1
        else:
            return FEED_ROOT.match(text, i) is not None
        if end < 0:
            return False


class Fetcher:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        spacing: float = HOST_SPACING,
        max_wait: float = MAX_CRAWL_DELAY,
    ):
        self._client = httpx.AsyncClient(
            transport=transport or PublicTransport(),
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
            follow_redirects=False,
            trust_env=False,
            headers={"user-agent": USER_AGENT, "accept-encoding": "gzip, deflate"},
        )
        self._clock = clock
        self._sleep = sleep
        self._spacing = spacing
        # The longest a call waits for a host's pause (spacing, Crawl-delay, a
        # 429's Retry-After); a longer one fails the call at once, so one slow
        # host never holds up the others or an agent's tool call.
        self._max_wait = max_wait
        self._next_at: dict[str, float] = {}
        self._locks: dict[str, anyio.Lock] = {}
        # Per host: when read, the parser (None when nothing may be fetched), the crawl delay, why not.
        self._robots: dict[str, tuple[float, robotparser.RobotFileParser | None, float, str]] = {}
        # Per host: what robots.txt answered when it could not be read, for the repair's sample.
        self._robots_answers: dict[str, Page] = {}
        # Refused URLs read as feeds that answered with something else: per URL, the
        # robots.txt reading it was refused under. Until that host's robots.txt is
        # read again, they fail without a request.
        self._not_feeds: dict[str, float] = {}
        # Hosts whose robots.txt could not be asked for at all (no connection, a timeout).
        self._robots_unreachable: set[str] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        accept: str = "*/*",
        etag: str = "",
        last_modified: str = "",
        max_bytes: int = MAX_RESPONSE,
        max_wait: float | None = None,
        feed: bool = False,
    ) -> Page:
        """GET `url`, following redirects by hand; FetchError for anything but a
        2xx or 304 page. feed: a feed's own URL, read even where robots.txt
        refuses it (or can't be read), as feed readers do, and its redirects with
        it. Only a feed is kept from such a URL: any other answer, an error status
        included, fails with the refusal and keeps no sample, and the URL then
        fails without a request until robots.txt is read again (a 429 or 5xx is
        asked again). A host whose robots.txt could not be asked for at all is
        not asked for the feed either, and a pause robots.txt's 429 or 503 asks
        for holds the feed too."""
        check_url(url)
        refused: FetchError | None = None
        exempt = ""
        refused_hops: list[str] = []
        for _ in range(MAX_REDIRECTS + 1):
            if any(h in (urlsplit(url).hostname or "") for h in WAITING_ROOM_HOSTS):
                raise FetchError("sent to a waiting room")
            wait_limit = self._max_wait if max_wait is None else min(max_wait, self._max_wait)
            refusal = await self._refusal(url, wait_limit)
            if refusal is not None:
                if not feed or not isinstance(refusal, RobotsRefusal):
                    raise refusal
                exempt = exempt or ("unreadable" if refusal.unreadable else "disallowed")
                refused = refused or FetchError(f"not a feed, and robots.txt refuses anything else here: {refusal}")
                if self._not_feeds.get(url) == self._robots_read(url):
                    raise refused
                refused_hops.append(url)
            headers = {"accept": accept}
            if etag:
                headers["if-none-match"] = etag
            if last_modified:
                headers["if-modified-since"] = last_modified
            status, response_headers, body = await self._request(url, headers, max_bytes, wait_limit)
            if status in (301, 302, 303, 307, 308):
                location = response_headers.get("location", "")
                if not location:
                    raise FetchError(f"HTTP {status} without a location")
                url = check_url(urljoin(url, location))
                etag = last_modified = ""
                continue
            if status == 304:
                return Page(url, 304, "", b"", etag, last_modified, robots_exempt=exempt)
            pause = 0
            if status in (429, 503):
                pause = self._back_off(url, response_headers.get("retry-after", ""))
            header = response_headers.get("content-type", "")
            content_type = header.split(";")[0].strip().lower()
            if refused is not None and not (200 <= status < 300 and looks_like_feed(body)):
                # A passing failure (a 429, a 5xx) is asked again; any other answer is not.
                if status < 500 and status != 429:
                    self._remember_not_feeds(refused_hops)
                raise FetchError(str(refused), retry_after=pause)
            if status in (401, 402, 403, 407, 429, 503) and looks_challenged(body):
                raise FetchError(f"HTTP {status}: a challenge or waiting room", retry_after=pause)
            if not 200 <= status < 300:
                answer = Page(url, status, content_type, body[:ERROR_BODY], charset=charset_of(header))
                raise FetchError(f"HTTP {status}", retry_after=pause, page=answer)
            if content_type in ("text/html", "application/xhtml+xml") and looks_challenged(body):
                raise FetchError("a challenge or waiting room instead of the page")
            return Page(
                url,
                status,
                content_type,
                body,
                response_headers.get("etag", ""),
                response_headers.get("last-modified", ""),
                charset_of(header),
                robots_exempt=exempt,
            )
        raise FetchError(f"more than {MAX_REDIRECTS} redirects")

    async def _request(
        self, url: str, headers: dict[str, str], max_bytes: int, max_wait: float
    ) -> tuple[int, httpx.Headers, bytes]:
        host = host_of(url)
        lock = self._locks.setdefault(host, anyio.Lock())
        async with lock:
            wait = self._next_at.get(host, 0.0) - self._clock()
            if wait > max_wait:
                raise HostBusy(
                    f"the site asked to wait {math.ceil(wait)} s more (a 429 or 503, or its Crawl-delay); "
                    "nothing was fetched",
                    retry_after=math.ceil(wait),
                )
            if wait > 0:
                await self._sleep(wait)
            try:
                async with self._client.stream("GET", url, headers=headers) as response:
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError(f"response over {max_bytes} bytes")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise FetchError(f"response over {max_bytes} bytes")
                    return response.status_code, response.headers, bytes(body)
            except BlockedAddress as e:
                raise FetchError("not a public address") from e
            except UnknownHost as e:
                raise FetchError(f"no such host: {e} (the name does not resolve; check the address)") from e
            except httpx.TimeoutException as e:
                raise FetchError("timed out") from e
            except httpx.HTTPError as e:
                raise FetchError(f"cannot connect ({type(e).__name__})") from e
            finally:
                delay = max(self._spacing, self._crawl_delay(host))
                self._next_at[host] = max(self._next_at.get(host, 0.0), self._clock() + delay)

    def _back_off(self, url: str, retry_after: str) -> int:
        """Pauses the host for its Retry-After, or HOST_BACKOFF; returns the seconds."""
        delay = HOST_BACKOFF
        if retry_after.isdigit():
            delay = min(max(int(retry_after), 1), 3600)
        else:
            try:
                delay = min(max(parsedate_to_datetime(retry_after).timestamp() - time.time(), 1), 3600)
            except (TypeError, ValueError):
                pass
        host = host_of(url)
        self._next_at[host] = max(self._next_at.get(host, 0.0), self._clock() + delay)
        return math.ceil(delay)

    def sitemaps(self, url: str) -> list[str]:
        """The sitemaps the robots.txt of this URL's host lists, once it has been read."""
        entry = self._robots.get(host_of(url))
        parser = entry[1] if entry else None
        return list(dict.fromkeys(parser.site_maps() or []))[:10] if parser else []

    def _robots_read(self, url: str) -> float:
        """When this URL's host's robots.txt was last read (asked for), or -1."""
        entry = self._robots.get(host_of(url))
        return entry[0] if entry else -1.0

    def _remember_not_feeds(self, urls: list[str]) -> None:
        if len(self._not_feeds) > 10_000:
            self._not_feeds.clear()
        for u in urls:
            self._not_feeds[u] = self._robots_read(u)

    def _crawl_delay(self, host: str) -> float:
        entry = self._robots.get(host)
        return entry[2] if entry else 0.0

    async def _refusal(self, url: str, max_wait: float) -> FetchError | None:
        """Why robots.txt keeps this URL from being fetched, or None. HostBusy (a
        pause longer than the call may wait) is raised, feed or not."""
        host = host_of(url)
        entry = self._robots.get(host)
        # A robots.txt that could not be read is asked for again soon: the site may be back.
        keep = ROBOTS_KEEP if entry is not None and entry[1] is not None else ROBOTS_RETRY
        if entry is None or self._clock() - entry[0] > keep:
            entry = await self._load_robots(host, max_wait)
            self._robots[host] = entry
        parser = entry[1]
        if parser is None:
            if host in self._robots_unreachable:
                return FetchError(entry[3])
            return RobotsRefusal(entry[3], unreadable=True, page=self._robots_answers.get(host))
        if not parser.can_fetch(USER_AGENT_NAME, url):
            return RobotsRefusal("robots.txt disallows it")
        return None

    async def _load_robots(
        self, host: str, max_wait: float
    ) -> tuple[float, robotparser.RobotFileParser | None, float, str]:
        parser = robotparser.RobotFileParser()
        url = host + "/robots.txt"
        self._robots_answers.pop(host, None)
        self._robots_unreachable.discard(host)
        unreadable = "robots.txt could not be read, which refuses everything (RFC 9309)"
        for _ in range(MAX_REDIRECTS + 1):
            try:
                status, headers, body = await self._request(url, {"accept": "text/plain"}, 512 * 1024, max_wait)
            except HostBusy:
                raise
            except FetchError as e:
                # The site itself is out of reach: say why, not that robots.txt is missing.
                # Nothing is asked of it, a feed included: it would only time out again.
                self._robots_unreachable.add(host)
                return self._clock(), None, 0.0, str(e)
            if status in (301, 302, 303, 307, 308) and headers.get("location"):
                try:
                    url = check_url(urljoin(url, headers["location"]))
                except FetchError as e:
                    return self._clock(), None, 0.0, f"robots.txt redirects elsewhere: {e}"
                continue
            break
        else:
            return self._clock(), None, 0.0, unreadable
        if 200 <= status < 300:
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
        elif 400 <= status < 500 and status != 429:
            # No robots.txt: everything is allowed.
            parser.allow_all = True
            parser.modified()
        else:
            if status in (429, 503):
                # The pause it asks for holds every request to the host, a feed's too.
                self._back_off(url, headers.get("retry-after", ""))
            header = headers.get("content-type", "")
            content_type = header.split(";")[0].strip().lower()
            self._robots_answers[host] = Page(url, status, content_type, body[:ERROR_BODY], charset=charset_of(header))
            return self._clock(), None, 0.0, f"{unreadable}: HTTP {status}"
        delay = parser.crawl_delay(USER_AGENT_NAME)
        return self._clock(), parser, min(float(delay or 0), MAX_CRAWL_DELAY), ""
