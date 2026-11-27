from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import anyio
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from lollo_common.auth import Authenticator
from lollo_common.notify import Notifier

from lollo_vault.config import Settings
from lollo_vault.crypto import KdfParams
from lollo_vault.service import Service
from lollo_vault.vault import Vault, build_fields

# Deterministic fakes; tests that can see service output assert they never leak.
SECRET = "TEST_SECRET_DO_NOT_USE_938742"
STREET = "Rua Falsa 123"
CARD = "4111111111114242"
PASSPHRASE = "correct horse battery staple"
EMAIL = "alfred@gmail.com"
ORIGIN = "https://alfred-lollo.example.com"

# What a browser sends when a person opens a page or posts its form (Fetch
# Metadata), and when a page's own script calls fetch(); see lollo_common.auth.check_fetch.
NAVIGATE = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "sec-fetch-site": "same-origin"}
SCRIPT_FETCH = {"sec-fetch-mode": "cors", "sec-fetch-dest": "empty", "sec-fetch-site": "same-origin"}
FAST_KDF = KdfParams(n=2**10, r=8, p=1)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        team_domain="team1",
        audience="aud1",
        issuer="https://team1.cloudflareaccess.com",
        allowed_users=frozenset({EMAIL}),
        public_origin=ORIGIN,
        db_path=":memory:",
    )


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def vault_path(tmp_path) -> str:
    return str(tmp_path / "vault.db")


@pytest.fixture
def vault(vault_path, clock) -> Vault:
    v = Vault(vault_path, clock=clock, kdf=FAST_KDF)
    v.setup(PASSPHRASE)
    return v


PHONE = "+351 910 000 042"
ADDRESS = {
    "first_name": "Alfred",
    "last_name": "Test",
    "address1": STREET,
    "city": "Lisboa",
    "postal_code": "1000-001",
    "country": "Portugal",
    "phone": PHONE,
}


def add_address(vault: Vault, share_mode: str = "ask") -> str:
    """An identity holding an address: its address fields may be shared without asking, its phone may not."""
    return vault.add_secret("identity", "Home address", "deliveries", build_fields("identity", ADDRESS), share_mode)


def add_card(vault: Vault) -> str:
    fields = build_fields(
        "card",
        {
            "cardholder_name": "Alfred Test",
            "brand": "Visa",
            "number": CARD,
            "exp_month": "12",
            "exp_year": "2030",
            "code": "123",
            "notes": SECRET,
        },
    )
    return vault.add_secret("card", "Visa", "", fields)


NOTIFY_TOKEN = "ab" * 32


class FakeTransport:
    """Stands in for the notifications service behind a real Notifier, so every
    payload passes the client's checks (tags, links) before it gets here.
    `up` says whether the service takes a notice (202); down, the connection is
    refused. It may be a function, asked on every try."""

    def __init__(self, up: bool | Callable[[], bool] = True):
        self.up = up
        self.tried: list[dict[str, str]] = []  # every payload handed over, taken or not
        self.taken: list[dict[str, str]] = []  # the ones the service took

    async def __call__(self, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
        assert headers["authorization"] == f"Bearer {NOTIFY_TOKEN}"
        payload = json.loads(body)
        self.tried.append(payload)
        if not (self.up() if callable(self.up) else self.up):
            raise ConnectionRefusedError("lollo-notifications is not running")
        self.taken.append(payload)
        return 202

    def sent(self) -> str:
        """Everything that left the vault, to look for values in."""
        return json.dumps(self.tried)


class Sleeper:
    """Stands in for anyio.sleep between a notice's tries: advances the fake clock."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock.t += seconds
        await anyio.sleep(0)


def make_service(
    settings: Settings,
    vault: Vault,
    transport: FakeTransport | None = None,
    sleep: Callable[[float], Any] | None = None,
) -> Service:
    notifier = Notifier("vault", NOTIFY_TOKEN, transport=transport or FakeTransport())
    return Service(vault, notifier, settings.public_origin, **({"sleep": sleep} if sleep else {}))


async def with_service(service: Service, fn):
    """Runs fn, then waits for every notice it queued: leaving the task group waits
    for them, where a fixed sleep could cut one short."""
    async with anyio.create_task_group() as tg:
        service.bind_task_group(tg)
        try:
            return await fn()
        finally:
            service.bind_task_group(None)


# ── Access JWTs ──────────────────────────────────────────────────────────────


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
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def authenticator(settings: Settings, signing_key: Any) -> Authenticator:
    public = signing_key.public_key()
    return Authenticator(settings, key_resolver=lambda token: public)
