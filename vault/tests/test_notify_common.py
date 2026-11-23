"""lollo_common.notify: the payload checks the notifications service and its
senders share, and the senders' client. lollo-common has no suite of its own;
the vault, a sender, tests it here."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import pytest
from lollo_common import notify
from lollo_common.notify import (
    DEFAULT_URL,
    MAX_BODY,
    MAX_TAG,
    MAX_TITLE,
    MAX_URL,
    NoNotifier,
    Notifier,
    PayloadError,
    check_payload,
)

from .conftest import CARD, SECRET

TOKEN = "5a" * 32
GOOD = {
    "title": "Vault request",
    "body": "An agent asks for Visa: number.",
    "url": "/vault/r/abc123",
    "tag": "vault-request",
}
# Distinctive title and body: no log may hold either.
SECRET_TITLE = f"Title {SECRET}"
SECRET_BODY = f"Body {CARD}"


def payload(**over: object) -> dict[str, object]:
    return {**GOOD, **over}


# -- the payload ---------------------------------------------------------------


def test_a_good_payload_is_kept():
    assert check_payload("vault", GOOD) == GOOD
    browser = {"title": "Your turn", "body": "", "url": "/browser/h/42?x=1#top", "tag": "browser-handoff-42"}
    assert check_payload("browser", browser) == browser


def test_text_is_made_one_line_and_shortened():
    out = check_payload("vault", payload(title="  a\n\tb\x1b[1m " + "t" * 200, body="line\r\nnext " + "b" * 500))
    assert "\n" not in out["title"] and "\x1b" not in out["title"] and len(out["title"]) == MAX_TITLE
    assert out["title"].startswith("a b [1m t") and out["title"].endswith("...")
    assert out["body"].startswith("line next b") and len(out["body"]) == MAX_BODY
    assert check_payload("vault", payload(title="\ud800 x"))["title"] == "� x"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "text",
        ["title", "body", "url", "tag"],
        {k: v for k, v in GOOD.items() if k != "body"},
        {k: v for k, v in GOOD.items() if k != "tag"},
        payload(actions=[{"action": "approve", "title": "Approve"}]),
        payload(icon="/vault/icon.png"),
        payload(v=1),
        payload(title=5),
        payload(body=None),
        payload(url=["/vault/"]),
        payload(tag=b"vault-x"),
    ],
)
def test_only_title_body_url_and_tag_as_strings(bad):
    with pytest.raises(PayloadError):
        check_payload("vault", bad)


@pytest.mark.parametrize("field", ["title", "url", "tag"])
def test_empty_title_url_or_tag_is_refused(field):
    with pytest.raises(PayloadError):
        check_payload("vault", payload(**{field: ""}))
    if field == "title":
        with pytest.raises(PayloadError):
            check_payload("vault", payload(title=" \n\t "))


@pytest.mark.parametrize(
    "url",
    [
        "/browser/h/1",
        "/vaultx/",
        "/vault",
        "vault/r/1",
        "https://evil.example/vault/r/1",
        "https://alfred-lollo.example.com/vault/r/1",
        "//evil.example/vault/r/1",
        "/vault//evil.example/",
        "javascript:alert(1)//vault/",
        "/vault/../browser/h/1",
        "/vault/x/..",
        "/vault/./r/1",
        "/vault/%2e%2e/browser/",
        "/vault/%2E./browser/",
        "/vault/.%2e/browser/",
        "/vault/%2e/r",
        "/vault/%2f%2e%2e%2fbrowser/",
        "/vault/\\evil.example",
        "/vault/%5c..%5cbrowser",
        "/vault/a b",
        "/vault/a\tb",
        "/vault/a\nb",
        "/vault/a\x00",
        "/vault/a%00",
        "/vault/café",
        "/vault/" + "a" * MAX_URL,
    ],
)
def test_the_url_is_a_path_under_the_senders_own_prefix(url):
    with pytest.raises(PayloadError):
        check_payload("vault", payload(url=url))


@pytest.mark.parametrize(
    "tag",
    [
        "request",
        "browser-handoff-1",
        "vault",
        "vault-",
        "vaultx-1",
        "Vault-request",
        "vault-Request",
        "vault_request",
        "vault-a b",
        "vault-a\n",
        "vault-" + "a" * MAX_TAG,
    ],
)
def test_the_tag_starts_with_the_senders_name(tag):
    with pytest.raises(PayloadError):
        check_payload("vault", payload(tag=tag))


@pytest.mark.parametrize(
    "over",
    [{"url": "/vault/r/abc123"}, {"tag": "vault-request"}, {"url": "/vault/r/1", "tag": "vault-request"}],
)
def test_the_browser_cannot_pose_as_the_vault(over):
    with pytest.raises(PayloadError):
        check_payload("browser", {"title": "t", "body": "b", "url": "/browser/h/1", "tag": "browser-x", **over})


@pytest.mark.parametrize("source", ["", "Vault", "../vault", "vault/", "-vault", "a" * 33])
def test_an_unknown_source_name_is_refused(source):
    with pytest.raises(PayloadError):
        check_payload(source, GOOD)


def test_a_refusal_never_quotes_the_payload():
    with pytest.raises(PayloadError) as e:
        check_payload("vault", payload(title=SECRET_TITLE, url=f"/browser/{SECRET}"))
    assert SECRET not in str(e.value)


# -- the client ------------------------------------------------------------------


class FakeTransport:
    def __init__(self, status: int = 202, error: Exception | None = None, hang: bool = False):
        self.status, self.error, self.hang = status, error, hang
        self.calls: list[tuple[str, dict, dict, float]] = []

    async def __call__(self, url, body, headers, timeout):
        self.calls.append((url, json.loads(body), dict(headers), timeout))
        if self.hang:
            await anyio.sleep_forever()
        if self.error:
            raise self.error
        return self.status


@pytest.mark.anyio
async def test_send_posts_the_checked_payload_with_the_token():
    t = FakeTransport()
    n = Notifier("vault", TOKEN, transport=t)
    assert await n.send(payload(title="a\nb"))
    (url, body, headers, timeout) = t.calls[0]
    assert url == DEFAULT_URL == "http://lollo-notifications:8000/notify/v1/send"
    assert body == {**GOOD, "title": "a b"}
    assert headers == {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
    assert timeout == notify.SEND_TIMEOUT
    assert await n.accept(GOOD)
    assert t.calls[1][3] == notify.ACCEPT_TIMEOUT < notify.SEND_TIMEOUT


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 204, 400, 401, 403, 413, 429, 500, 503])
async def test_only_a_202_counts_as_accepted(status):
    n = Notifier("vault", TOKEN, transport=FakeTransport(status))
    assert not await n.send(GOOD)
    assert not await n.accept(GOOD)


@pytest.mark.anyio
@pytest.mark.parametrize("error", [ConnectionRefusedError(), OSError("no route"), ValueError("bad"), TimeoutError()])
async def test_a_network_error_is_not_raised(error):
    n = Notifier("vault", TOKEN, transport=FakeTransport(error=error))
    assert not await n.send(GOOD)
    assert not await n.accept(GOOD)


@pytest.mark.anyio
async def test_accept_gives_up_at_its_deadline(monkeypatch):
    monkeypatch.setattr(notify, "ACCEPT_TIMEOUT", 0.1)
    n = Notifier("vault", TOKEN, transport=FakeTransport(hang=True))
    with anyio.fail_after(2):
        assert not await n.accept(GOOD)


@pytest.mark.anyio
async def test_a_bad_payload_is_never_sent():
    t = FakeTransport()
    n = Notifier("browser", TOKEN, transport=t)
    assert not await n.send(GOOD)
    assert not await n.accept(payload(actions=[]))
    assert t.calls == []


@pytest.mark.anyio
async def test_no_notifier_sends_nothing():
    assert not await NoNotifier().send(GOOD)
    assert not await NoNotifier().accept(GOOD)


def test_settings_come_from_the_environment():
    n = Notifier.from_env("vault", {"NOTIFY_TOKEN": TOKEN})
    assert n.source == "vault" and n.url == DEFAULT_URL
    other = "http://hub.internal:9000/notify/v1/send"
    assert Notifier.from_env("vault", {"NOTIFY_TOKEN": TOKEN, "NOTIFY_URL": other}).url == other
    assert TOKEN not in repr(n)


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"NOTIFY_TOKEN": ""},
        {"NOTIFY_TOKEN": TOKEN.upper()},
        {"NOTIFY_TOKEN": TOKEN[:-1]},
        {"NOTIFY_TOKEN": TOKEN + "0"},
        {"NOTIFY_TOKEN": TOKEN[:-1] + "g"},
        {"NOTIFY_TOKEN": f" {TOKEN}"},
        {"NOTIFY_TOKEN": TOKEN, "NOTIFY_URL": "ftp://lollo-notifications/notify/v1/send"},
        {"NOTIFY_TOKEN": TOKEN, "NOTIFY_URL": "lollo-notifications:8000"},
    ],
)
def test_bad_settings_are_refused_without_quoting_the_token(env):
    with pytest.raises(ValueError) as e:
        Notifier.from_env("vault", env)
    assert TOKEN not in str(e.value) and TOKEN.upper() not in str(e.value)


def test_a_notifier_needs_a_source_name_and_a_token():
    for source, token in [("Vault", TOKEN), ("vault/", TOKEN), ("vault", "")]:
        with pytest.raises(ValueError):
            Notifier(source, token)


@pytest.mark.anyio
async def test_logs_hold_source_tag_and_outcome_never_title_or_body(caplog, monkeypatch):
    monkeypatch.setattr(notify, "ACCEPT_TIMEOUT", 0.1)
    secret = payload(title=SECRET_TITLE, body=SECRET_BODY)
    with caplog.at_level(logging.INFO, logger="lollo_common.notify"):
        assert await Notifier("vault", TOKEN, transport=FakeTransport()).send(secret)
        assert not await Notifier("vault", TOKEN, transport=FakeTransport(401)).send(secret)
        assert not await Notifier("vault", TOKEN, transport=FakeTransport(error=OSError(SECRET))).send(secret)
        assert not await Notifier("vault", TOKEN, transport=FakeTransport(hang=True)).accept(secret)
        assert not await Notifier("vault", TOKEN, transport=FakeTransport()).send({**secret, "url": "/browser/"})
    lines = [r.getMessage() for r in caplog.records if r.name == "lollo_common.notify"]
    assert len(lines) == 5
    assert all("source=vault" in line for line in lines)
    assert sum("tag=vault-request" in line for line in lines) == 4
    assert "status=202" in lines[0] and "status=401" in lines[1] and "error=OSError" in lines[2]
    assert "error=timeout" in lines[3] and "reason=" in lines[4]
    for text in (SECRET, CARD, "Title", "Body", TOKEN):
        assert not any(text in line for line in lines)
        assert text not in caplog.text


# -- against a local HTTP stub -------------------------------------------------------


class Stub:
    """A notifications service on loopback that answers `status`, or holds the
    request until released when `hold` is set."""

    def __init__(self, status: int = 202, hold: bool = False, location: str = ""):
        self.status, self.hold, self.location = status, hold, location
        self.release = threading.Event()
        self.seen: list[tuple[str, str, dict[str, str], bytes]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", "0")))
                stub.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if stub.hold:
                    stub.release.wait(5)
                self.send_response(stub.status)
                if stub.location:
                    self.send_header("location", stub.location)
                self.send_header("content-length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/notify/v1/send"

    def __enter__(self) -> "Stub":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.anyio
async def test_a_202_from_the_service_is_accepted():
    with Stub(202) as stub:
        n = Notifier("vault", TOKEN, url=stub.url)
        assert await n.send(GOOD)
        assert await n.accept(GOOD)
    method, path, headers, body = stub.seen[0]
    assert (method, path) == ("POST", "/notify/v1/send")
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["content-type"] == "application/json"
    assert "origin" not in headers and not any(h.startswith("cf-") for h in headers)
    assert json.loads(body) == GOOD


@pytest.mark.anyio
async def test_a_refusal_from_the_service_is_not_accepted():
    with Stub(401) as stub:
        n = Notifier("vault", TOKEN, url=stub.url)
        assert not await n.send(GOOD)
        assert not await n.accept(GOOD)
    assert len(stub.seen) == 2


@pytest.mark.anyio
async def test_a_redirect_is_not_followed():
    with Stub(307, location="http://127.0.0.1:9/elsewhere") as stub:
        assert not await Notifier("vault", TOKEN, url=stub.url).send(GOOD)
    assert [s[1] for s in stub.seen] == ["/notify/v1/send"]


@pytest.mark.anyio
async def test_a_service_that_does_not_answer_times_out(monkeypatch):
    monkeypatch.setattr(notify, "ACCEPT_TIMEOUT", 0.3)
    with Stub(202, hold=True) as stub:
        with anyio.fail_after(3):
            assert not await Notifier("vault", TOKEN, url=stub.url).accept(GOOD)
    assert len(stub.seen) == 1


@pytest.mark.anyio
async def test_no_service_listening_is_not_raised():
    with Stub() as stub:
        url = stub.url
    with anyio.fail_after(3):
        assert not await Notifier("vault", TOKEN, url=url).send(GOOD)
