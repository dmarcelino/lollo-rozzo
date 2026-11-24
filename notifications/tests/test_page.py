"""The person's page at /notifications/ (notifications/devices)."""

import hashlib
import json
import re

import pytest

from lollo_notifications.config import TEST_PER_MINUTE

from .conftest import EMAIL, NAVIGATE, ORIGIN, OTHER, SCRIPT_FETCH, VAPID_PUBLIC, FakeSender, make_jwt, subscription

ASSETS = ["/notifications/sw.js", "/notifications/app.js", "/notifications/app.css", "/notifications/lollo.css"]
ASSETS += ["/notifications/icon-192.png"]


def device_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


# -- The notifications page ----------------------------------------------------------


def test_the_page_lists_the_persons_own_devices_only(hub):
    hub.clock.t = 1_800_000_000
    phone = hub.enroll(1, host="fcm.googleapis.com")
    hub.clock.t += 86400
    laptop = hub.enroll(2, host="updates.push.services.mozilla.com")
    theirs = hub.enroll(3, email=OTHER, host="web.push.apple.com")
    r = hub.page()
    assert r.status_code == 200
    html = r.text
    assert f'data-device="{device_id(phone)}"' in html and f'data-device="{device_id(laptop)}"' in html
    assert device_id(theirs) not in html and "web.push.apple.com" not in html and OTHER not in html
    assert "via fcm.googleapis.com" in html and "via updates.push.services.mozilla.com" in html
    assert "Added 2027-01-15" in html and "Added 2027-01-16" in html
    # The newest first; endpoints themselves are never on the page.
    assert html.index(device_id(laptop)) < html.index(device_id(phone))
    assert "device-1" not in html and "BKey1" not in html
    assert f"Signed in as {EMAIL}" in html


def test_with_no_device_the_page_says_so(hub):
    assert "No device has notifications on yet." in hub.page().text


def test_the_page_gives_its_script_the_form_token_and_the_public_key(hub):
    html = hub.page().text
    assert f'<meta name="csrf" content="{hub.csrf()}">' in html
    assert f'<meta name="vapid" content="{VAPID_PUBLIC}">' in html
    assert '<script src="/notifications/app.js" defer></script>' in html
    # No inline script: the CSP runs only the page's own file.
    assert len(re.findall(r"<script", html)) == 1


def test_enable_lists_the_device_and_disable_removes_it(hub):
    sub = subscription(7)
    r = hub.call("/subscribe", {**sub, "expirationTime": None})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert device_id(sub["endpoint"]) in hub.page().text
    r = hub.call("/unsubscribe", {"endpoint": sub["endpoint"]})
    assert r.status_code == 200
    assert device_id(sub["endpoint"]) not in hub.page().text
    assert hub.store.all() == []


def test_disable_never_removes_another_persons_device(hub):
    theirs = hub.enroll(3, email=OTHER)
    assert hub.call("/unsubscribe", {"endpoint": theirs}).status_code == 200
    assert [s.endpoint for s in hub.store.all()] == [theirs]


@pytest.mark.parametrize(
    "body",
    [
        {"endpoint": "http://push.example.com/x", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https:///x", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https://push.example.com/" + "x" * 2048, "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https://push.example.com/a b", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https://push.example.com/x", "keys": {"p256dh": "a<b", "auth": "b"}},
        {"endpoint": "https://push.example.com/x", "keys": {"p256dh": "a", "auth": ""}},
        {"endpoint": "https://push.example.com/x", "keys": "a"},
        {"endpoint": "https://push.example.com/x"},
        ["https://push.example.com/x"],
    ],
)
def test_a_malformed_subscription_is_refused(hub, body):
    assert hub.call("/subscribe", body).status_code == 400
    assert hub.store.all() == []


def test_a_large_body_is_refused_before_it_is_read(hub):
    sub = {**subscription(1), "padding": "x" * 5000}
    assert hub.call("/subscribe", sub).status_code == 413
    assert hub.store.all() == []


def test_test_reaches_every_device_of_that_person_only_and_says_how_many(hub_for):
    sender = FakeSender()
    hub = hub_for(sender)
    mine = [hub.enroll(1), hub.enroll(2)]
    theirs = hub.enroll(3, email=OTHER)
    gone = hub.enroll(4)
    sender.fail[gone] = 410
    r = hub.call("/test", {})
    assert r.status_code == 200 and r.json() == {"reached": 2}
    assert sorted(sender.endpoints()) == sorted([*mine, gone]) and theirs not in sender.endpoints()
    sent = json.loads(sender.calls[0]["data"])
    assert sent == {
        "title": "Lollo: Test notification",
        "body": "Lollo notifications will arrive like this.",
        "url": "/notifications/",
        "tag": "notifications-test",
    }
    # The device gone is dropped, and so missing from the list.
    assert device_id(gone) not in hub.page().text


def test_tests_are_rate_limited_per_person(hub):
    hub.enroll(1)
    codes = [hub.call("/test", {}).status_code for _ in range(TEST_PER_MINUTE + 1)]
    assert codes == [200] * TEST_PER_MINUTE + [429]
    assert hub.call("/test", {}, email=OTHER).status_code == 200
    hub.clock.t += 61
    assert hub.call("/test", {}).status_code == 200


def test_device_dropped_is_missing_from_the_list(hub):
    endpoint = hub.enroll(1)
    hub.store.remove(endpoint)
    assert device_id(endpoint) not in hub.page().text


def test_the_page_links_the_lollo_app_and_the_landing_page(hub):
    html = hub.page().text
    assert '<link rel="manifest" href="/assets/manifest.webmanifest" crossorigin="use-credentials">' in html
    assert '<link rel="apple-touch-icon" href="/assets/icon-192.png">' in html
    assert '<a class="topbar-note" href="/">Lollo Rozzo</a>' in html


def test_no_slash_redirects_to_the_page(hub):
    r = hub.client.get("/notifications")
    assert r.status_code == 308 and r.headers["location"] == "/notifications/"


def test_healthz(hub):
    assert hub.client.get("/healthz").text == "ok"


# -- The service worker -------------------------------------------------------------


def test_the_service_worker_may_take_the_whole_host(hub):
    r = hub.client.get("/notifications/sw.js", headers={"cf-access-jwt-assertion": hub.jwt()})
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "notificationclick" in r.text and "tapTarget" in r.text


def test_the_service_worker_caches_nothing_and_has_no_actions(hub):
    text = hub.client.get("/notifications/sw.js", headers={"cf-access-jwt-assertion": hub.jwt()}).text
    code = re.sub(r"//.*", "", text)
    assert '"fetch"' not in code and "caches" not in code and "actions" not in code


def test_the_script_registers_the_worker_at_the_root(hub):
    text = hub.client.get("/notifications/app.js", headers={"cf-access-jwt-assertion": hub.jwt()}).text
    assert 'register(`${BASE}/sw.js`, { scope: "/" })' in text


# -- The notifications page is a lollo page -------------------------------------------


def all_routes(hub, jwt: str | None, headers: dict[str, str] | None = None):
    sent = {} if jwt is None else {"cf-access-jwt-assertion": jwt}
    sent |= headers or {}
    for path in ["/notifications/", *ASSETS]:
        yield path, hub.client.get(path, headers={**NAVIGATE, **sent})
    for path in ["/subscribe", "/unsubscribe", "/test"]:
        csrf = {"origin": ORIGIN, "x-csrf-token": hub.csrf(), **SCRIPT_FETCH}
        yield path, hub.client.post(f"/notifications{path}", json=subscription(1), headers={**csrf, **sent})


def test_every_route_needs_a_jwt(hub):
    for path, r in all_routes(hub, None):
        assert r.status_code == 403, path
    assert hub.store.all() == []


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": ["lettuce-aud"]},
        {"iss": "https://other.cloudflareaccess.com"},
        {"email": "eve@example.com"},
        {"exp": 1},
        {"email": None},
    ],
)
def test_only_a_jwt_of_the_lollos_application_for_a_listed_person(hub, signing_key, claims):
    jwt = make_jwt(signing_key, **claims)
    for path, r in all_routes(hub, jwt):
        assert r.status_code == 403, path
        assert "eve@example.com" not in r.text


def test_a_jwt_signed_by_another_key_is_refused(hub, other_key):
    for path, r in all_routes(hub, make_jwt(other_key)):
        assert r.status_code == 403, path


def test_the_page_loads_only_as_a_top_level_document(hub):
    jwt = {"cf-access-jwt-assertion": hub.jwt()}
    for headers in [
        SCRIPT_FETCH,
        {**NAVIGATE, "sec-fetch-dest": "iframe"},
        {},
    ]:
        r = hub.client.get("/notifications/", headers={**jwt, **headers})
        assert r.status_code == 403
        assert EMAIL not in r.text and hub.csrf() not in r.text
    # Arriving from another site (an Access re-login) is a page load like any other.
    assert (
        hub.client.get("/notifications/", headers={**jwt, **NAVIGATE, "sec-fetch-site": "cross-site"}).status_code
        == 200
    )


@pytest.mark.parametrize("path", ["/subscribe", "/unsubscribe", "/test"])
def test_a_change_needs_the_pages_script(hub, path):
    # A form post or a navigation is not the page's script.
    r = hub.call(path, subscription(1), **NAVIGATE)
    assert r.status_code == 403
    r = hub.call(path, subscription(1), **{"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/subscribe", "/unsubscribe", "/test"])
def test_subscribe_from_another_pages_script_without_the_form_token_is_refused(hub_for, path):
    sender = FakeSender()
    hub = hub_for(sender)
    endpoint = hub.enroll(9)
    for csrf in [None, "", "0" * 64, hub.csrf(OTHER)]:
        r = hub.call(
            path, subscription(1) if path != "/unsubscribe" else {"endpoint": endpoint}, **{"x-csrf-token": csrf}
        )
        assert r.status_code == 403, csrf
        assert EMAIL not in r.text and hub.csrf() not in r.text
    assert [s.endpoint for s in hub.store.all()] == [endpoint]
    assert sender.calls == []


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example.com", "https://alfred.example.com"])
def test_a_change_needs_the_lollos_origin(hub, origin):
    r = hub.call("/subscribe", subscription(1), origin=origin)
    assert r.status_code == 403
    assert hub.store.all() == []


def test_every_response_sets_coop_and_a_csp_that_runs_only_the_pages_own_script(hub):
    responses = list(all_routes(hub, hub.jwt()))
    responses += list(all_routes(hub, None))
    responses += [("refused page", hub.page(email="eve@example.com"))]
    for path, r in responses:
        assert r.headers["cross-origin-opener-policy"] == "same-origin", path
        csp = r.headers["content-security-policy"]
        assert "script-src 'self';" in csp and "default-src 'self'" in csp, path
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, path
        assert "worker-src 'self'" in csp and "manifest-src 'self'" in csp and "connect-src 'self'" in csp, path
        assert "frame-ancestors 'none'" in csp, path
        assert r.headers["x-content-type-options"] == "nosniff", path


def test_a_refusal_page_carries_neither_the_email_nor_the_form_token(hub):
    r = hub.page(**{"sec-fetch-dest": "iframe"})
    assert r.status_code == 403
    assert EMAIL not in r.text and hub.csrf() not in r.text and "<script" not in r.text
    assert "Not done" in r.text
