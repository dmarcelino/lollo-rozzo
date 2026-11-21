from __future__ import annotations

import httpcore
import pytest

from lollo_news_radar.fetch import (
    BlockedAddress,
    FetchError,
    PublicOnlyBackend,
    check_url,
    is_public,
    looks_like_feed,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "address, public",
    [
        ("93.184.216.34", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.18.0.3", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("::1", False),
        ("fd00::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),
        ("not-an-ip", False),
    ],
)
def test_is_public(address, public):
    assert is_public(address) is public


@pytest.mark.parametrize(
    "url",
    ["ftp://example.org/", "file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/", "https://user:pw@example.org/"],
)
def test_check_url_refuses(url):
    with pytest.raises(FetchError):
        check_url(url)


class FakeInner(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        raise httpcore.ConnectError("stop here")

    async def sleep(self, seconds):
        pass


async def test_backend_connects_only_to_checked_public_addresses():
    async def resolve(host, port):
        return {"good.example.org": ["93.184.216.34"], "inside.example.org": ["93.184.216.34", "172.18.0.2"]}[host]

    inner = FakeInner()
    backend = PublicOnlyBackend(resolve, inner)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("good.example.org", 443)
    assert inner.connected == ["93.184.216.34"]  # the checked address, not the name
    with pytest.raises(BlockedAddress):
        await backend.connect_tcp("inside.example.org", 443)
    assert inner.connected == ["93.184.216.34"]
    with pytest.raises(BlockedAddress):
        await backend.connect_unix_socket("/var/run/docker.sock")


async def test_robots_disallow_is_honoured(fetcher, web):
    web.add("https://news.example.org/robots.txt", "User-agent: *\nDisallow: /private/\n", "text/plain")
    web.add("https://news.example.org/private/a", "<html>no</html>")
    with pytest.raises(FetchError, match="robots.txt disallows"):
        await fetcher.get("https://news.example.org/private/a")
    assert web.fetched() == []


async def test_robots_for_named_crawlers_only_allows_us(fetcher, web):
    web.add(
        "https://shop.example.org/robots.txt",
        "User-agent: AhrefsBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
        "text/plain",
    )
    web.add("https://shop.example.org/list", "<html><body>ok</body></html>")
    page = await fetcher.get("https://shop.example.org/list")
    assert page.status == 200


async def test_unreadable_robots_means_nothing_is_fetched(fetcher, web):
    web.add("https://down.example.org/robots.txt", "oops", "text/plain", status=500)
    with pytest.raises(FetchError, match="robots.txt could not be read"):
        await fetcher.get("https://down.example.org/list")


async def test_an_unreadable_robots_is_asked_again_after_ten_minutes(fetcher, web, clock):
    web.add("https://later.example.org/robots.txt", "oops", "text/plain", status=503)
    with pytest.raises(FetchError, match="HTTP 503"):
        await fetcher.get("https://later.example.org/list")
    web.add("https://later.example.org/robots.txt", "User-agent: *\nAllow: /\n", "text/plain")
    web.add("https://later.example.org/list", "<p>ok</p>")
    with pytest.raises(FetchError):
        await fetcher.get("https://later.example.org/list")  # still within the ten minutes
    clock.t += 601 + 300  # past the retry, and past the 503's pause
    assert (await fetcher.get("https://later.example.org/list")).status == 200


async def test_redirects_are_checked_each_time(fetcher, web):
    web.add("https://a.example.org/go", "", status=302, location="http://127.0.0.1:8000/mcp")
    with pytest.raises(FetchError, match="public"):
        await fetcher.get("https://a.example.org/go")
    web.add("https://a.example.org/loop", "", status=302, location="/loop")
    with pytest.raises(FetchError, match="redirects"):
        await fetcher.get("https://a.example.org/loop")
    web.add("https://a.example.org/wait", "", status=302, location="https://site.queue-it.net/?c=1")
    with pytest.raises(FetchError, match="waiting room"):
        await fetcher.get("https://a.example.org/wait")


async def test_challenge_pages_and_errors_fail(fetcher, web):
    web.add(
        "https://b.example.org/a",
        '<html><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/x"></script>',
    )
    with pytest.raises(FetchError, match="challenge"):
        await fetcher.get("https://b.example.org/a")
    web.add("https://b.example.org/b", "nope", status=403)
    with pytest.raises(FetchError, match="HTTP 403"):
        await fetcher.get("https://b.example.org/b")


async def test_an_unreadable_robots_txt_carries_what_the_site_answered(fetcher, web):
    web.add("https://e.example.org/robots.txt", "Origin is unreachable", "text/plain", status=503)
    with pytest.raises(FetchError, match="robots.txt could not be read") as e:
        await fetcher.get("https://e.example.org/x")
    assert e.value.page is not None and b"Origin is unreachable" in e.value.page.body


async def test_an_error_carries_what_the_site_answered(fetcher, web):
    web.add("https://b.example.org/c", "Payment required\n\nDEPLOYMENT_DISABLED", "text/plain", status=402)
    with pytest.raises(FetchError, match="^HTTP 402$") as e:
        await fetcher.get("https://b.example.org/c")
    assert e.value.page is not None and e.value.page.status == 402
    assert b"DEPLOYMENT_DISABLED" in e.value.page.body


async def test_size_cap(fetcher, web):
    web.add("https://c.example.org/big", "x" * 2000)
    with pytest.raises(FetchError, match="over 1000 bytes"):
        await fetcher.get("https://c.example.org/big", max_bytes=1000)


async def test_conditional_requests_and_not_modified(fetcher, web):
    web.add("https://d.example.org/feed", "", status=304)
    page = await fetcher.get("https://d.example.org/feed", etag='"v1"', last_modified="Fri, 15 Jan 2027 10:00:00 GMT")
    assert page.not_modified
    sent = [r for r in web.requests if str(r.url).endswith("/feed")][0]
    assert sent.headers["if-none-match"] == '"v1"'
    assert sent.headers["user-agent"].startswith("lollo-rozzo-news-radar/")
    assert "cookie" not in sent.headers


async def test_requests_to_one_host_are_spaced(fetcher, web, sleeps):
    web.add("https://e.example.org/robots.txt", "User-agent: *\nCrawl-delay: 10\n", "text/plain")
    web.add("https://e.example.org/1", "<p>1</p>")
    web.add("https://e.example.org/2", "<p>2</p>")
    await fetcher.get("https://e.example.org/1")
    await fetcher.get("https://e.example.org/2")
    assert sleeps.calls and max(sleeps.calls) == pytest.approx(10, abs=0.01)


async def test_a_429_pauses_the_host(fetcher, web, sleeps):
    web.add("https://f.example.org/x", "slow down", status=429, **{"retry-after": "30"})
    web.add("https://f.example.org/y", "<p>y</p>")
    with pytest.raises(FetchError, match="HTTP 429"):
        await fetcher.get("https://f.example.org/x")
    await fetcher.get("https://f.example.org/y")
    assert max(sleeps.calls) == pytest.approx(30, abs=0.01)


async def test_a_long_pause_fails_at_once_instead_of_stalling_every_fetch(fetcher, web, sleeps):
    web.add("https://g.example.org/x", "slow down", status=429, **{"retry-after": "3600"})
    web.add("https://g.example.org/y", "<p>y</p>")
    with pytest.raises(FetchError, match="HTTP 429"):
        await fetcher.get("https://g.example.org/x")
    with pytest.raises(FetchError, match="asked to wait"):
        await fetcher.get("https://g.example.org/y")
    assert max(sleeps.calls, default=0) <= 60


async def test_a_call_can_wait_less_than_a_site_asks(fetcher, web, sleeps):
    web.add("https://h.example.org/robots.txt", "User-agent: *\nCrawl-delay: 10\n", "text/plain")
    web.add("https://h.example.org/1", "<p>1</p>")
    web.add("https://h.example.org/2", "<p>2</p>")
    await fetcher.get("https://h.example.org/1", max_wait=5)
    with pytest.raises(FetchError, match="asked to wait"):
        await fetcher.get("https://h.example.org/2", max_wait=5)
    assert max(sleeps.calls, default=0) <= 5


async def test_a_name_that_resolves_inside_the_stack_is_refused_as_such(clock, sleeps):
    import httpx

    from lollo_news_radar.fetch import Fetcher

    def handler(request):
        raise BlockedAddress(request.url.host)  # what PublicOnlyBackend raises for a private address

    fetcher = Fetcher(transport=httpx.MockTransport(handler), clock=clock, sleep=sleeps)
    with pytest.raises(FetchError, match="not a public address"):
        await fetcher.get("http://lollo-vault:8000/")


async def test_a_name_that_does_not_resolve_says_so(clock, sleeps):
    import socket

    import httpx

    from lollo_news_radar.fetch import Fetcher, UnknownHost

    async def resolve(host, port):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    with pytest.raises(UnknownHost):
        await PublicOnlyBackend(resolve, FakeInner()).connect_tcp("nope.example.org", 443)

    def handler(request):
        raise UnknownHost(request.url.host)  # what PublicOnlyBackend raises

    fetcher = Fetcher(transport=httpx.MockTransport(handler), clock=clock, sleep=sleeps)
    with pytest.raises(FetchError, match="no such host: nope.example.org"):
        await fetcher.get("https://nope.example.org/")


async def test_a_pause_says_how_long(fetcher, web):
    web.add("https://p.example.org/x", "slow down", status=429, **{"retry-after": "300"})
    with pytest.raises(FetchError) as first:
        await fetcher.get("https://p.example.org/x")
    assert first.value.retry_after == 300
    with pytest.raises(FetchError, match="asked to wait") as second:
        await fetcher.get("https://p.example.org/x")
    assert 290 <= second.value.retry_after <= 300


# What GitHub's robots.txt says to every agent, trimmed: wildcards and `$`.
WILDCARD_ROBOTS = "User-agent: *\nDisallow: /*.atom$\nDisallow: /*/*/commits/\nDisallow: /a/\nAllow: /a/open/\n"
FEED = b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'


async def test_robots_wildcards_and_dollar_are_honoured(fetcher, web):
    web.add("https://code.example.org/robots.txt", WILDCARD_ROBOTS, "text/plain")
    for path in ("/o/r/releases.atom", "/o/r/commits/main", "/o/r/commits/main.atom"):
        web.add("https://code.example.org" + path, "<html>no</html>")
        with pytest.raises(FetchError, match="robots.txt disallows"):
            await fetcher.get("https://code.example.org" + path)
    assert web.fetched() == []
    # `$` ends the pattern: a query after .atom is another URL.
    web.add("https://code.example.org/o/r/releases.atom?page=2", "<html>ok</html>")
    web.add("https://code.example.org/o/r/pull/1", "<html>ok</html>")
    await fetcher.get("https://code.example.org/o/r/releases.atom?page=2")
    await fetcher.get("https://code.example.org/o/r/pull/1")


async def test_the_longest_robots_rule_wins_whatever_its_order(fetcher, web):
    web.add("https://code.example.org/robots.txt", WILDCARD_ROBOTS, "text/plain")
    web.add("https://code.example.org/a/open/x", "<html>ok</html>")
    await fetcher.get("https://code.example.org/a/open/x")
    with pytest.raises(FetchError, match="robots.txt disallows"):
        await fetcher.get("https://code.example.org/a/closed")
    web.add("https://other.example.org/robots.txt", "User-agent: *\nAllow: /b/\nDisallow: /b/shut/\n", "text/plain")
    with pytest.raises(FetchError, match="robots.txt disallows"):
        await fetcher.get("https://other.example.org/b/shut/x")


async def test_a_feed_is_read_where_robots_refuses_it(fetcher, web):
    web.add("https://code.example.org/robots.txt", WILDCARD_ROBOTS, "text/plain")
    web.add("https://code.example.org/o/r/releases.atom", b"\xef\xbb\xbf" + FEED, "application/atom+xml")
    web.add("https://code.example.org/o/r/releases.atom?page=2", FEED, "application/atom+xml")
    page = await fetcher.get("https://code.example.org/o/r/releases.atom", feed=True)
    assert page.status == 200 and page.robots_exempt
    allowed = await fetcher.get("https://code.example.org/o/r/releases.atom?page=2", feed=True)
    assert not allowed.robots_exempt


async def test_a_refused_url_that_is_not_a_feed_stays_refused_without_a_sample(fetcher, web):
    web.add("https://shut.example.org/robots.txt", "User-agent: *\nDisallow: /\n", "text/plain")
    web.add("https://shut.example.org/page", "<html><body>private listing</body></html>")
    web.add("https://shut.example.org/gone", "<html><body>private error</body></html>", status=404)
    with pytest.raises(FetchError, match="not a feed, and robots.txt refuses .*robots.txt disallows") as e:
        await fetcher.get("https://shut.example.org/page", feed=True)
    assert e.value.page is None
    # An error status is the refusal too, not a status for an agent to repair.
    with pytest.raises(FetchError, match="robots.txt disallows") as e:
        await fetcher.get("https://shut.example.org/gone", feed=True)
    assert e.value.page is None


async def test_a_refused_url_that_was_not_a_feed_is_not_asked_again_until_robots_is_read(fetcher, web, clock):
    web.add("https://shut.example.org/robots.txt", "User-agent: *\nDisallow: /\n", "text/plain")
    web.add("https://shut.example.org/old", "", status=301, location="https://shut.example.org/page")
    web.add("https://shut.example.org/page", "<html><body>private listing</body></html>")
    for _ in range(2):
        with pytest.raises(FetchError, match="robots.txt disallows"):
            await fetcher.get("https://shut.example.org/old", feed=True)
    # Neither the URL nor the page it redirected to was asked for again.
    assert web.fetched() == ["https://shut.example.org/old", "https://shut.example.org/page"]
    web.add("https://shut.example.org/page", FEED, "application/rss+xml")
    clock.t += 86400 + 1  # robots.txt is read again, and the URL with it
    page = await fetcher.get("https://shut.example.org/old", feed=True)
    assert page.robots_exempt


async def test_a_feed_is_read_when_robots_txt_cannot_be_read(fetcher, web):
    web.add("https://down.example.org/robots.txt", "oops", "text/plain", status=500)
    web.add("https://down.example.org/feed", FEED, "application/rss+xml")
    page = await fetcher.get("https://down.example.org/feed", feed=True)
    assert page.robots_exempt
    with pytest.raises(FetchError, match="robots.txt could not be read"):
        await fetcher.get("https://down.example.org/feed")


async def test_a_feed_robots_refuses_still_keeps_its_crawl_delay(fetcher, web, sleeps):
    web.add("https://slow.example.org/robots.txt", "User-agent: *\nCrawl-delay: 10\nDisallow: /\n", "text/plain")
    web.add("https://slow.example.org/feed", FEED, "application/rss+xml")
    await fetcher.get("https://slow.example.org/feed", feed=True)
    await fetcher.get("https://slow.example.org/feed", feed=True)
    assert sleeps.calls and max(sleeps.calls) == pytest.approx(10, abs=0.01)


@pytest.mark.parametrize(
    "body, feed",
    [
        (b'<?xml version="1.0"?>\n<!-- generated -->\n<?xml-stylesheet href="s.xsl"?><feed xmlns="x">', True),
        (b"\n  <RSS version='2.0'>", True),
        (b'<rdf:RDF xmlns:rdf="x">', True),
        (b"<!doctype html><html><body><rss>", False),
        (b'{"rss": 1}', False),
        (b"<feedback/>", False),
        (
            b'<?xml version="1.0"?><!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN" '
            b'"http://my.netscape.com/publish/formats/rss-0.91.dtd"><rss version="0.91">',
            True,
        ),
        (b'<!DOCTYPE rss [<!ENTITY nbsp "&#160;">]><rss>', True),
        (b'<atom:feed xmlns:atom="http://www.w3.org/2005/Atom">', True),
        ("<?xml version='1.0' encoding='utf-16'?><feed>".encode("utf-16"), True),
        (b"<!--" + b"x" * 3000 + b"--><rss>", True),
        (b'<?xml-stylesheet href="a.xsl?x=>"?><rss>', True),
        (b"<!-- never closed <rss>", False),
        (b"<!DOCTYPE html><html>", False),
    ],
)
def test_looks_like_feed(body, feed):
    assert looks_like_feed(body) is feed
