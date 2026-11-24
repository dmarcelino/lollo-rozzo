from __future__ import annotations

import json
import threading
import time
from typing import Any

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from lollo_common.auth import Authenticator
from starlette.testclient import TestClient

from lollo_notifications.app import create_app
from lollo_notifications.config import Settings
from lollo_notifications.delivery import Pusher
from lollo_notifications.store import Subscriptions

EMAIL = "alfred@gmail.com"
OTHER = "hal@example.com"
ORIGIN = "https://alfred-lollo.example.com"
INTERNAL = "http://lollo-notifications:8000"
VAULT_TOKEN = "5a" * 32
BROWSER_TOKEN = "b7" * 32
# Shaped like the pair lolloctl makes; never used to sign anything (FakeSender).
VAPID_PUBLIC = "B" + "p" * 86
VAPID_PRIVATE = "k" * 43
# What a browser sends when a person opens a page (Fetch Metadata), and when the
# page's own script calls fetch(); see lollo_common.auth.check_fetch.
NAVIGATE = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-site": "same-origin"}
SCRIPT_FETCH = {"sec-fetch-mode": "cors", "sec-fetch-dest": "empty", "sec-fetch-site": "same-origin"}
SEND = f"{INTERNAL}/notify/v1/send"
# Distinctive title and body: no log may hold either.
SECRET_TITLE = "Title TEST_SECRET_DO_NOT_USE_938742"
SECRET_BODY = "Body 4111111111114242"


def vault_payload(**over: object) -> dict[str, object]:
    return {
        "title": "Vault request",
        "body": "An agent asks for Visa: number.",
        "url": "/vault/r/abc",
        "tag": "vault-request",
        **over,
    }


def browser_payload(**over: object) -> dict[str, object]:
    return {
        "title": "Your turn",
        "body": "A sign-in needs you.",
        "url": "/browser/h/42",
        "tag": "browser-handoff",
        **over,
    }


def subscription(n: int, host: str = "push.example.com") -> dict[str, Any]:
    return {"endpoint": f"https://{host}/send/device-{n}", "keys": {"p256dh": f"BKey{n}", "auth": f"auth{n}"}}


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_settings(tmp_path: Any, users: frozenset[str] = frozenset({EMAIL, OTHER}), **over: Any) -> Settings:
    values: dict[str, Any] = {
        "team_domain": "team1",
        "audience": "aud1",
        "issuer": "https://team1.cloudflareaccess.com",
        "allowed_users": users,
        "public_origin": ORIGIN,
        "vapid_public_key": VAPID_PUBLIC,
        "vapid_private_key": VAPID_PRIVATE,
        "vapid_contact": "ops@example.com",
        "tokens": {"vault": VAULT_TOKEN, "browser": BROWSER_TOKEN},
        "data_dir": str(tmp_path),
    }
    values.update(over)
    return Settings(**values)


class FakeSender:
    """Stands in for pywebpush's webpush: records each call, and fails with
    `fail[endpoint]` (an HTTP status) for the endpoints listed there. An
    endpoint in `timeout` waits that many seconds, then times out as requests
    does; one in `hold` waits until that event is set (at most 10 seconds)."""

    def __init__(self, fail: dict[str, int] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.fail = fail or {}
        self.timeout: dict[str, float] = {}
        self.hold: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def __call__(self, **kw: Any) -> None:
        endpoint = kw["subscription_info"]["endpoint"]
        with self._lock:
            self.calls.append(kw)
        if endpoint in self.hold:
            self.hold[endpoint].wait(10)
        if endpoint in self.timeout:
            time.sleep(self.timeout[endpoint])
            raise requests.exceptions.ReadTimeout("timed out")
        status = self.fail.get(endpoint)
        if status:
            from pywebpush import WebPushException

            class Resp:
                status_code = status

            raise WebPushException("push failed", response=Resp())

    def endpoints(self) -> list[str]:
        return [c["subscription_info"]["endpoint"] for c in self.calls]

    def sent(self, endpoint: str | None = None) -> list[dict[str, str]]:
        """The payloads sent, to `endpoint` alone when given, in order."""
        return [
            json.loads(c["data"])
            for c in self.calls
            if endpoint is None or c["subscription_info"]["endpoint"] == endpoint
        ]


@pytest.fixture(scope="session")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_jwt(key: Any, **over: Any) -> str:
    now = int(time.time())
    claims = {
        "aud": ["aud1"],
        "iss": "https://team1.cloudflareaccess.com",
        "email": EMAIL,
        "iat": now,
        "exp": now + 3600,
        "sub": "user-1",
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


class Hub:
    """The app, running (its lifespan entered), with a fake push sender and a
    fake clock. `client` reaches it as the tunnel would (base URL: the lollo's
    origin); `send` posts as a sender on the stack network. `stop` leaves the
    lifespan, which waits for queued notifications to go out."""

    def __init__(
        self,
        tmp_path: Any,
        signing_key: Any,
        sender: FakeSender | None = None,
        queue_size: int | None = None,
        **settings: Any,
    ):
        self.settings = make_settings(tmp_path, **settings)
        self.signing_key = signing_key
        public = signing_key.public_key()
        self.authenticator = Authenticator(self.settings, key_resolver=lambda token: public)
        self.clock = Clock()
        self.store = Subscriptions(tmp_path, self.settings.vapid_public_key, clock=self.clock)
        self.sender = sender or FakeSender()
        self.pusher = Pusher(self.settings, self.store, self.sender)
        sized = {} if queue_size is None else {"queue_size": queue_size}
        self.app = create_app(self.settings, self.authenticator, self.pusher, clock=self.clock, **sized)
        self.client = TestClient(self.app, base_url=ORIGIN, follow_redirects=False)
        self.client.__enter__()
        self._running = True

    def stop(self) -> None:
        if self._running:
            self._running = False
            self.client.__exit__(None, None, None)

    def send(self, payload: Any, token: str | None = VAULT_TOKEN, **headers: str):
        if token is not None:
            headers.setdefault("authorization", f"Bearer {token}")
        return self.client.post(SEND, json=payload, headers=headers)

    def jwt(self, email: str = EMAIL) -> str:
        return make_jwt(self.signing_key, email=email)

    def page(self, path: str = "/notifications/", email: str = EMAIL, **headers: str):
        return self.client.get(path, headers={**NAVIGATE, "cf-access-jwt-assertion": self.jwt(email), **headers})

    def csrf(self, email: str = EMAIL) -> str:
        return self.authenticator.csrf_token(email)

    def call(self, path: str, body: Any, email: str = EMAIL, **headers: str):
        """A post from the page's own script."""
        sent = {
            **SCRIPT_FETCH,
            "cf-access-jwt-assertion": self.jwt(email),
            "origin": ORIGIN,
            "x-csrf-token": self.csrf(email),
            **headers,
        }
        sent = {k: v for k, v in sent.items() if v is not None}
        return self.client.post(f"/notifications{path}", json=body, headers=sent)

    def enroll(self, n: int, email: str = EMAIL, host: str = "push.example.com") -> str:
        sub = subscription(n, host)
        r = self.call("/subscribe", sub, email)
        assert r.status_code == 200, r.text
        return sub["endpoint"]


@pytest.fixture
def hub_for(tmp_path, signing_key):
    hubs: list[Hub] = []

    def make(sender: FakeSender | None = None, queue_size: int | None = None, **settings: Any) -> Hub:
        hub = Hub(tmp_path, signing_key, sender, queue_size, **settings)
        hubs.append(hub)
        return hub

    yield make
    for hub in hubs:
        hub.stop()


@pytest.fixture
def hub(hub_for) -> Hub:
    return hub_for()
