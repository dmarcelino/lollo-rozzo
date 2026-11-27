"""The senders' API at /notify/v1/send and delivery (notifications/delivery)."""

import json
import logging
import threading
import time

import pytest
from lollo_common.notify import MAX_TITLE

from lollo_notifications import delivery
from lollo_notifications.app import device_id
from lollo_notifications.config import MAX_PUSH_FAILURES, SEND_PER_HOUR, SEND_PER_MINUTE

from .conftest import (
    BROWSER_TOKEN,
    EMAIL,
    INTERNAL,
    ORIGIN,
    OTHER,
    SECRET_BODY,
    SECRET_TITLE,
    SEND,
    VAPID_PRIVATE,
    VAULT_TOKEN,
    FakeSender,
    browser_payload,
    vault_payload,
)

# -- The send API is for senders only -------------------------------------------


@pytest.mark.parametrize("token", [None, "", "00" * 32, VAULT_TOKEN.upper(), VAULT_TOKEN + "0"])
def test_agent_without_a_token_is_refused(hub, token):
    hub.enroll(1)
    headers = {} if token is None else {"authorization": f"Bearer {token}"}
    r = hub.client.post(SEND, json=vault_payload(), headers=headers)
    assert r.status_code == 401
    r = hub.send(vault_payload(), token=None, authorization=VAULT_TOKEN)  # no "Bearer "
    assert r.status_code == 401
    hub.stop()
    assert hub.sender.calls == []


@pytest.mark.parametrize("origin", [ORIGIN, "null", "https://evil.example.com"])
def test_request_from_a_page_is_refused_even_with_a_valid_token(hub, origin):
    hub.enroll(1)
    assert hub.send(vault_payload(), origin=origin).status_code == 403
    hub.stop()
    assert hub.sender.calls == []


@pytest.mark.parametrize("header", ["cf-access-jwt-assertion", "cf-connecting-ip", "cf-ray"])
def test_request_through_the_tunnel_is_refused(hub, header):
    assert hub.send(vault_payload(), **{header: "x"}).status_code == 403


def test_only_post_at_the_one_path(hub):
    auth = {"authorization": f"Bearer {VAULT_TOKEN}"}
    assert hub.client.get(SEND, headers=auth).status_code == 405
    assert hub.client.post(f"{INTERNAL}/notify/v1/other", json=vault_payload(), headers=auth).status_code == 404
    assert hub.client.post(f"{INTERNAL}/notify/v2/send", json=vault_payload(), headers=auth).status_code == 404


def test_without_any_token_nobody_may_send(hub_for):
    hub = hub_for(tokens={})
    hub.enroll(1)
    assert hub.send(vault_payload()).status_code == 401
    hub.stop()
    assert hub.sender.calls == []


def test_a_sender_is_answered_202_once_queued(hub):
    r = hub.send(vault_payload())
    assert r.status_code == 202 and r.json() == {"queued": True}
    assert hub.send(browser_payload(), token=BROWSER_TOKEN).status_code == 202


def test_the_body_is_json_and_capped(hub):
    auth = {"authorization": f"Bearer {VAULT_TOKEN}"}
    r = hub.client.post(SEND, content=json.dumps(vault_payload()), headers={**auth, "content-type": "text/plain"})
    assert r.status_code == 400
    r = hub.client.post(SEND, content=b"{not json", headers={**auth, "content-type": "application/json"})
    assert r.status_code == 400
    big = json.dumps(vault_payload(body="x" * 9000))
    r = hub.client.post(SEND, content=big, headers={**auth, "content-type": "application/json"})
    assert r.status_code == 413
    r = hub.client.post(SEND, json=[vault_payload()], headers=auth)
    assert r.status_code == 400


def test_a_large_body_is_refused_before_it_is_read_wherever_it_is_sent(hub):
    big = b"{" + b" " * (2 * 1024 * 1024) + b"}"
    # Without a token, so nothing but the cap could refuse it before the handler.
    for path in (SEND, "/notifications/subscriptions", "/notifications/"):
        assert hub.client.post(path, content=big, headers={"content-type": "application/json"}).status_code == 413
    assert hub.send(vault_payload()).status_code == 202


# -- A sender says only what is its own ------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        browser_payload(url="/vault/r/abc"),
        browser_payload(tag="vault-request"),
        browser_payload(url="/vault/r/abc", tag="vault-request"),
    ],
)
def test_browser_posing_as_the_vault_is_refused(hub, payload):
    hub.enroll(1)
    assert hub.send(payload, token=BROWSER_TOKEN).status_code == 400
    hub.stop()
    assert hub.sender.calls == []


def test_the_vault_posing_as_the_browser_is_refused(hub):
    assert hub.send(vault_payload(url="/browser/h/1")).status_code == 400
    assert hub.send(vault_payload(tag="browser-handoff")).status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/vault/r/1",
        "//evil.example.com/vault/",
        f"{ORIGIN}/vault/r/1",
        "javascript:alert(1)",
        "/vault/../notifications/",
        "/vault/%2e%2e/x",
        "/vault\\..\\x",
        "vault/r/1",
    ],
)
def test_link_to_another_site_or_page_is_refused(hub, url):
    assert hub.send(vault_payload(url=url)).status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {**vault_payload(), "actions": [{"action": "approve", "title": "Approve"}]},
        {**vault_payload(), "icon": "https://evil.example.com/x.png"},
        {k: v for k, v in vault_payload().items() if k != "tag"},
        vault_payload(title=["x"]),
        vault_payload(title=""),
    ],
)
def test_unknown_or_missing_field_is_refused(hub, payload):
    assert hub.send(payload).status_code == 400


def test_every_field_is_sent_as_one_line(hub):
    hub.enroll(1)
    assert hub.send(vault_payload(title="Vault\nrequest\x1b", body="line one\r\nline two")).status_code == 202
    hub.stop()
    sent = json.loads(hub.sender.calls[0]["data"])
    assert sent == {
        "title": "Vault: Vault request",
        "body": "line one line two",
        "url": "/vault/r/abc",
        "tag": "vault-request",
    }


# -- Senders are rate limited -------------------------------------------------------


def test_browser_flood_is_refused_and_logged_and_the_vault_still_goes_out(hub, caplog):
    hub.enroll(1)
    caplog.set_level(logging.INFO)
    codes = [hub.send(browser_payload(), token=BROWSER_TOKEN).status_code for _ in range(SEND_PER_MINUTE + 5)]
    assert codes == [202] * SEND_PER_MINUTE + [429] * 5
    assert hub.send(vault_payload()).status_code == 202
    hub.stop()
    tags = [json.loads(c["data"])["tag"] for c in hub.sender.calls]
    assert tags.count("browser-handoff") == SEND_PER_MINUTE and tags.count("vault-request") == 1
    assert "notification refused source=browser tag=browser-handoff reason=rate limit" in caplog.text


def test_the_minute_and_the_hour_both_cap(hub):
    sent = 0
    for _minute in range(SEND_PER_HOUR // SEND_PER_MINUTE + 2):
        for _ in range(SEND_PER_MINUTE):
            sent += hub.send(browser_payload(), token=BROWSER_TOKEN).status_code == 202
        assert hub.send(browser_payload(), token=BROWSER_TOKEN).status_code == 429
        hub.clock.t += 61
    assert sent == SEND_PER_HOUR
    hub.clock.t += 3600
    assert hub.send(browser_payload(), token=BROWSER_TOKEN).status_code == 202


def test_refused_payloads_do_not_count(hub):
    for _ in range(SEND_PER_MINUTE + 5):
        assert hub.send(vault_payload(url="/browser/")).status_code == 400
    assert hub.send(vault_payload()).status_code == 202


def wait_for(condition, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_service_busy_a_refusal_does_not_count_and_refuses_only_that_sender(hub_for, caplog):
    caplog.set_level(logging.INFO)
    sender = FakeSender()
    # The browser's one-place queue fills while its worker waits on a device.
    hub = hub_for(sender, queue_size=1)
    device = hub.enroll(1)
    release = threading.Event()
    sender.hold[device] = release
    try:
        codes = [hub.send(browser_payload(), token=BROWSER_TOKEN).status_code for _ in range(SEND_PER_MINUTE * 2)]
        assert codes[0] == 202 and set(codes) == {202, 503}, codes
        assert codes.count(503) >= SEND_PER_MINUTE * 2 - 2
        assert "notification refused source=browser tag=browser-handoff reason=busy" in caplog.text
        # The vault's own queue is not the browser's.
        assert hub.send(vault_payload()).status_code == 202
    finally:
        release.set()
    del sender.hold[device]
    accepted = codes.count(202)
    wait_for(lambda: len(sender.calls) == accepted + 1)
    # Only the accepted ones counted: the browser may still send the rest of its minute.
    for n in range(SEND_PER_MINUTE - accepted):
        assert hub.send(browser_payload(), token=BROWSER_TOKEN).status_code == 202, n
        wait_for(lambda n=n: len(sender.calls) == accepted + 1 + n + 1)
    assert hub.send(browser_payload(), token=BROWSER_TOKEN).status_code == 429


def test_rate_limiter_counts_only_what_is_recorded():
    clock = [0.0]
    limiter = delivery.RateLimiter(((2, 60),), lambda: clock[0])
    for _ in range(10):
        assert limiter.check("browser")  # checked, never recorded: still free
    limiter.record("browser")
    assert limiter.check("browser")
    limiter.record("browser")
    assert not limiter.check("browser") and not limiter.allow("browser")
    assert limiter.allow("vault")
    clock[0] = 61
    assert limiter.allow("browser")


# -- Accepted notifications reach every enrolled device ---------------------------


def test_two_devices_both_get_it(hub):
    phone = hub.enroll(1, host="fcm.googleapis.com")
    laptop = hub.enroll(2, host="updates.push.services.mozilla.com")
    assert hub.send(vault_payload()).status_code == 202
    hub.stop()
    assert sorted(hub.sender.endpoints()) == sorted([phone, laptop])
    call = hub.sender.calls[0]
    assert call["vapid_private_key"] == VAPID_PRIVATE
    assert call["vapid_claims"] == {"sub": "mailto:ops@example.com"}
    assert call["ttl"] == 600 and call["timeout"] == 10
    assert call["subscription_info"]["keys"] in (
        {"p256dh": "BKey1", "auth": "auth1"},
        {"p256dh": "BKey2", "auth": "auth2"},
    )


def test_every_listed_person_gets_it(hub):
    mine, theirs = hub.enroll(1), hub.enroll(2, email=OTHER)
    hub.send(vault_payload())
    hub.stop()
    assert sorted(hub.sender.endpoints()) == sorted([mine, theirs])


def test_a_person_removed_gets_nothing_more(hub_for, tmp_path):
    before = hub_for()
    mine, theirs = before.enroll(1), before.enroll(2, email=OTHER)
    before.stop()
    # The stack's users change: the service restarts with the new ALLOWED_USERS.
    after = hub_for(users=frozenset({EMAIL}))
    after.send(vault_payload())
    after.stop()
    assert after.sender.endpoints() == [mine]
    assert theirs in [s.endpoint for s in after.store.all()]


@pytest.mark.parametrize("status", [404, 410])
def test_a_device_gone_is_deleted_and_not_sent_to_again(hub_for, status):
    sender = FakeSender()
    hub = hub_for(sender)
    gone, kept = hub.enroll(1), hub.enroll(2)
    sender.fail[gone] = status
    hub.send(vault_payload())
    hub.send(vault_payload(tag="vault-request-2"))
    hub.stop()
    assert sender.endpoints().count(gone) == 1 and sender.endpoints().count(kept) == 2
    assert [s.endpoint for s in hub.store.all()] == [kept]


@pytest.mark.parametrize("status", [400, 429, 500])
def test_another_failure_is_logged_and_decides_nothing(hub_for, caplog, status):
    caplog.set_level(logging.INFO)
    sender = FakeSender()
    hub = hub_for(sender)
    failing = hub.enroll(1)
    sender.fail[failing] = status
    hub.send(vault_payload())
    hub.stop()
    assert [s.endpoint for s in hub.store.all()] == [failing]
    assert f"push failed source=vault tag=vault-request host=push.example.com status={status}" in caplog.text


def test_a_network_error_is_logged(hub_for, caplog):
    caplog.set_level(logging.INFO)

    def broken(**kw):
        raise ConnectionError("no route")

    hub = hub_for(broken)
    hub.enroll(1)
    hub.send(vault_payload())
    hub.stop()
    assert "push failed source=vault tag=vault-request host=push.example.com error=ConnectionError" in caplog.text
    assert len(hub.store.all()) == 1


# -- No secret value in a notification: the service's logs ------------------------


def test_logs_show_senders_tags_and_outcomes_never_a_title_or_body(hub_for, caplog):
    caplog.set_level(logging.DEBUG)
    sender = FakeSender()
    hub = hub_for(sender)
    ok, gone, failing = hub.enroll(1), hub.enroll(2), hub.enroll(3, host="fcm.googleapis.com")
    sender.fail.update({gone: 410, failing: 500})
    assert hub.send(vault_payload(title=SECRET_TITLE, body=SECRET_BODY)).status_code == 202
    hub.send(browser_payload(title=SECRET_TITLE, body=SECRET_BODY), token=BROWSER_TOKEN)
    hub.send(vault_payload(title=SECRET_TITLE, body=SECRET_BODY, url="/browser/"))  # refused
    for _ in range(SEND_PER_MINUTE):
        hub.send(browser_payload(title=SECRET_TITLE, body=SECRET_BODY), token=BROWSER_TOKEN)
    hub.call("/test", {})
    hub.stop()
    assert ok in hub.sender.endpoints()
    text = caplog.text
    for line in [
        "notification accepted source=vault tag=vault-request",
        "notification accepted source=browser tag=browser-handoff",
        "push delivered source=vault tag=vault-request host=push.example.com",
        "push subscription gone, removed source=vault tag=vault-request host=push.example.com",
        "push failed source=vault tag=vault-request host=fcm.googleapis.com status=500",
        "notification refused source=vault reason=url is not under the sender's own prefix",
        "notification refused source=browser tag=browser-handoff reason=rate limit",
        "notification sent source=vault tag=vault-request devices=1",
        "notification sent source=notifications tag=notifications-test devices=1",
    ]:
        assert line in text, line
    for secret in (
        SECRET_TITLE,
        SECRET_BODY,
        "TEST_SECRET_DO_NOT_USE",
        "4111111111114242",
        "Vault request",
        "Your turn",
    ):
        assert secret not in text
    # Nor an endpoint's path, the keys or the tokens.
    assert "device-1" not in text and "BKey1" not in text and "auth1" not in text
    assert VAULT_TOKEN not in text and BROWSER_TOKEN not in text and VAPID_PRIVATE not in text


# -- Each notification names its sender ---------------------------------------------


def test_browser_imitating_a_vault_request_shows_as_the_browser(hub):
    device = hub.enroll(1)
    assert hub.send(browser_payload(title="Approve sharing"), token=BROWSER_TOKEN).status_code == 202
    assert hub.send(vault_payload(title="Approve sharing")).status_code == 202
    hub.stop()
    titles = sorted(p["title"] for p in hub.sender.sent(device))
    assert titles == ["Browser: Approve sharing", "Vault: Approve sharing"]


def test_a_title_already_naming_another_sender_still_gets_its_own_label(hub):
    device = hub.enroll(1)
    hub.send(browser_payload(title="Vault: Approve sharing"), token=BROWSER_TOKEN)
    hub.stop()
    assert hub.sender.sent(device)[0]["title"] == "Browser: Vault: Approve sharing"


def test_any_other_sender_is_named_by_its_source(hub_for):
    token = "c3" * 32
    hub = hub_for(tokens={"vault": VAULT_TOKEN, "some-sender": token})
    device = hub.enroll(1)
    payload = {"title": "New mail", "body": "x", "url": "/some-sender/", "tag": "some-sender-new"}
    assert hub.send(payload, token=token).status_code == 202
    hub.stop()
    assert hub.sender.sent(device)[0]["title"] == "Some-sender: New mail"


GMAIL_TOKEN = "c3" * 32
RADAR_TOKEN = "d4" * 32


@pytest.mark.parametrize(
    ("source", "token", "payload", "title"),
    [
        (
            "gmail-triggers",
            GMAIL_TOKEN,
            {"title": "New sender", "body": "x", "url": "/gmail-triggers/r/1", "tag": "gmail-triggers-new-sender-1"},
            "Gmail triggers: New sender",
        ),
        (
            "news-radar",
            RADAR_TOKEN,
            {"title": "Confirm a monitor", "body": "x", "url": "/news-radar/", "tag": "news-radar-confirm-1"},
            "News Radar: Confirm a monitor",
        ),
    ],
)
def test_gmail_triggers_and_news_radar_are_named(hub_for, source, token, payload, title):
    hub = hub_for(tokens={"vault": VAULT_TOKEN, source: token})
    device = hub.enroll(1)
    assert hub.send(payload, token=token).status_code == 202
    hub.stop()
    assert hub.sender.sent(device)[0]["title"] == title


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "Approve", "body": "x", "url": "/vault/r/abc", "tag": "news-radar-confirm-1"},
        {"title": "Approve", "body": "x", "url": "/news-radar/", "tag": "vault-request"},
        {"title": "Approve", "body": "x", "url": "/gmail-triggers/r/1", "tag": "news-radar-confirm-1"},
    ],
)
def test_news_radar_posing_as_another_sender_is_refused(hub_for, payload):
    hub = hub_for(tokens={"vault": VAULT_TOKEN, "news-radar": RADAR_TOKEN})
    hub.enroll(1)
    assert hub.send(payload, token=RADAR_TOKEN).status_code == 400
    hub.stop()
    assert hub.sender.calls == []


def test_a_long_title_keeps_the_label_and_its_cap(hub):
    device = hub.enroll(1)
    hub.send(browser_payload(title="x" * MAX_TITLE), token=BROWSER_TOKEN)
    hub.stop()
    title = hub.sender.sent(device)[0]["title"]
    assert title.startswith("Browser: xxx") and title.endswith("...") and len(title) <= MAX_TITLE


# -- One sender never delays another ------------------------------------------------


def test_browser_busy_and_a_slow_device_never_delay_the_vault(hub_for, monkeypatch):
    # The slow device stays enrolled for the whole test.
    monkeypatch.setattr(delivery, "MAX_PUSH_FAILURES", 10_000)
    sender = FakeSender()
    hub = hub_for(sender)
    slow, fast = hub.enroll(1, host="slow.example.com"), hub.enroll(2)
    sender.timeout[slow] = 0.1
    codes = [hub.send(browser_payload(), token=BROWSER_TOKEN).status_code for _ in range(SEND_PER_MINUTE)]
    assert codes == [202] * SEND_PER_MINUTE
    assert hub.send(vault_payload()).status_code == 202
    # The vault's reaches the fast device while most of the browser's still wait
    # behind the slow one (each takes 0.1 s; one after another, 3 s).
    wait_for(lambda: any(p["tag"] == "vault-request" for p in sender.sent(fast)))
    tags = [p["tag"] for p in sender.sent(fast)]
    assert tags.index("vault-request") < SEND_PER_MINUTE // 2, tags
    hub.stop()


def test_a_notification_goes_to_every_device_at_once(hub_for):
    sender = FakeSender()
    hub = hub_for(sender)
    devices = [hub.enroll(n) for n in range(3)]
    # Each push waits until all three are being sent: one after another would never get there.
    barrier = threading.Barrier(len(devices), timeout=5)
    waited: list[str] = []

    def at_once(**kw):
        barrier.wait()
        waited.append(kw["subscription_info"]["endpoint"])
        sender(**kw)

    hub.pusher._send = at_once
    r = hub.call("/test", {})
    assert r.status_code == 200 and r.json() == {"reached": 3}
    assert sorted(waited) == sorted(devices)


def test_a_device_that_keeps_timing_out_is_deleted_and_shows_off(hub_for, caplog):
    caplog.set_level(logging.INFO)
    sender = FakeSender()
    hub = hub_for(sender)
    slow, kept = hub.enroll(1, host="slow.example.com"), hub.enroll(2)
    sender.timeout[slow] = 0
    for n in range(MAX_PUSH_FAILURES + 1):
        assert hub.send(vault_payload(tag=f"vault-request-{n}")).status_code == 202
    hub.stop()
    assert sender.endpoints().count(slow) == MAX_PUSH_FAILURES
    assert sender.endpoints().count(kept) == MAX_PUSH_FAILURES + 1
    assert [s.endpoint for s in hub.store.all()] == [kept]
    assert "push subscription unreachable, removed source=vault" in caplog.text
    assert "host=slow.example.com failures=3" in caplog.text


def test_a_push_taken_resets_the_count(hub_for):
    sender = FakeSender()
    hub = hub_for(sender)
    flaky = hub.enroll(1)

    def send() -> None:
        before = len(sender.calls)
        assert hub.send(vault_payload()).status_code == 202
        wait_for(lambda: len(sender.calls) == before + 1)

    # Never MAX_PUSH_FAILURES timeouts in a row: a push taken comes between.
    for _ in range(2):
        sender.timeout[flaky] = 0
        for _ in range(MAX_PUSH_FAILURES - 1):
            send()
        del sender.timeout[flaky]
        send()
    hub.stop()
    assert [s.endpoint for s in hub.store.all()] == [flaky]


def test_another_failure_never_counts_as_a_timeout(hub_for):
    sender = FakeSender()
    hub = hub_for(sender)
    failing = hub.enroll(1)
    sender.fail[failing] = 500
    for n in range(MAX_PUSH_FAILURES + 2):
        hub.send(vault_payload(tag=f"vault-request-{n}"))
    hub.stop()
    assert [s.endpoint for s in hub.store.all()] == [failing]


# -- Subscriptions under another push key ----------------------------------------


def test_subscriptions_under_another_key_are_deleted_at_start_and_show_off(hub_for, caplog):
    before = hub_for()
    phone = before.enroll(1)
    before.stop()
    # An older stack.env restored next to these subscriptions: another public key.
    caplog.set_level(logging.INFO)
    after = hub_for(vapid_public_key="C" + "q" * 86)
    assert after.store.all() == []
    assert "made with another push key; removed 1 subscriptions" in caplog.text
    assert device_id(phone) not in after.page().text
    after.send(vault_payload())
    after.stop()
    assert after.sender.calls == []
