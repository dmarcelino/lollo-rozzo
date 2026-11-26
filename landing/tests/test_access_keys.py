"""lollo_common.auth: what a token must say before any key lookup, and how often
the team's Access key set is fetched. Shared by every lollo service."""

from __future__ import annotations

from typing import Any

import anyio
import jwt
import pytest
from jwt.algorithms import RSAAlgorithm
from lollo_common.auth import AccessKeys, Authenticator, AuthError

from .conftest import make_jwt, make_settings


class FakeJWKClient:
    """Stands in for PyJWKClient: serves `keys` by key id and counts fetches."""

    def __init__(self, keys: dict[str, Any], fail: bool = False):
        self.keys = keys
        self.fetches = 0
        self.fail = fail

    def get_jwk_set(self, refresh: bool = False) -> Any:
        assert refresh
        self.fetches += 1
        if self.fail:
            raise jwt.PyJWKClientError("unreachable")
        return jwt.PyJWKSet.from_dict(
            {"keys": [{**RSAAlgorithm.to_jwk(k, as_dict=True), "kid": kid} for kid, k in self.keys.items()]}
        )


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def token(key: Any, kid: str | None = "k1", alg: str = "RS256", secret: str = "") -> str:
    headers = {} if kid is None else {"kid": kid}
    if alg == "HS256":
        return jwt.encode({"email": "alfred@gmail.com"}, secret or "s" * 32, algorithm="HS256", headers=headers)
    return jwt.encode({"email": "alfred@gmail.com"}, key, algorithm=alg, headers=headers)


def test_a_known_key_is_served_from_the_cache(signing_key):
    client, clock = FakeJWKClient({"k1": signing_key.public_key()}), Clock()
    keys = AccessKeys("https://team1.cloudflareaccess.com/cdn-cgi/access/certs", client=client, clock=clock)
    for _ in range(5):
        assert keys(token(signing_key)).public_numbers() == signing_key.public_key().public_numbers()
    assert client.fetches == 1


def test_a_burst_of_unknown_key_ids_costs_one_fetch(signing_key):
    client, clock = FakeJWKClient({"k1": signing_key.public_key()}), Clock()
    keys = AccessKeys("u", client=client, clock=clock)
    for i in range(100):
        with pytest.raises(jwt.PyJWTError):
            keys(token(signing_key, kid=f"made-up-{i}"))
        clock.t += 0.5  # 100 tokens within 50 seconds
    assert client.fetches == 1


def test_a_new_key_is_fetched_once_the_interval_has_passed(signing_key, other_key):
    client, clock = FakeJWKClient({"k1": signing_key.public_key()}), Clock()
    keys = AccessKeys("u", client=client, clock=clock)
    keys(token(signing_key))
    # Cloudflare rotates its keys: k2 appears.
    client.keys["k2"] = other_key.public_key()
    with pytest.raises(jwt.PyJWTError):
        keys(token(other_key, kid="k2"))  # within the interval: no fetch
    assert client.fetches == 1
    clock.t += 61
    assert keys(token(other_key, kid="k2")).public_numbers() == other_key.public_key().public_numbers()
    assert client.fetches == 2


def test_an_unreachable_key_set_fails_closed(signing_key):
    keys = AccessKeys("u", client=FakeJWKClient({}, fail=True), clock=Clock())
    with pytest.raises(jwt.PyJWKClientError):
        keys(token(signing_key))


def test_after_a_failed_fetch_sign_ins_wait_for_a_retry_not_a_refusal(signing_key):
    # A sidecar starts during a network blip: its first fetch fails.
    client, clock = FakeJWKClient({"k1": signing_key.public_key()}, fail=True), Clock()
    keys = AccessKeys("u", client=client, clock=clock)
    with pytest.raises(jwt.PyJWKClientError):
        keys(token(signing_key))
    client.fail = False
    # Before the retry: still "could not verify right now" (503), never an
    # invalid sign-in, and no fetch.
    for _ in range(20):
        with pytest.raises(jwt.PyJWKClientError):
            keys(token(signing_key))
    assert client.fetches == 1
    # A few seconds later, not a whole interval: the fetch runs and signs in.
    clock.t += 5
    assert keys(token(signing_key)).public_numbers() == signing_key.public_key().public_numbers()
    assert client.fetches == 2


def test_the_authenticator_answers_503_while_the_key_set_is_unreachable(signing_key):
    keys = AccessKeys("u", client=FakeJWKClient({}, fail=True), clock=Clock())
    auth = Authenticator(make_settings(), key_resolver=keys)
    for _ in range(2):  # the failed fetch, then the wait before a retry
        with pytest.raises(AuthError) as e:
            anyio.run(auth.email, {"cf-access-jwt-assertion": make_jwt(signing_key)})
        assert e.value.status == 503


@pytest.mark.parametrize(
    "make",
    [
        lambda key: token(key, alg="HS256"),
        lambda key: token(key, alg="RS512"),
        lambda key: token(key, kid=None),
        lambda key: token(key, kid=""),
        lambda key: token(key, kid="k" * 129),
        lambda key: token(key, kid="bad kid"),
        lambda key: "not-a-token",
    ],
)
def test_tokens_that_are_not_access_tokens_never_reach_a_key_lookup(signing_key, make):
    client = FakeJWKClient({"k1": signing_key.public_key()})
    keys = AccessKeys("u", client=client, clock=Clock())
    with pytest.raises(jwt.PyJWTError):
        keys(make(signing_key))
    assert client.fetches == 0
    # The same through the Authenticator, whatever resolver it has: a 403, no lookup.
    looked_up: list[str] = []
    auth = Authenticator(make_settings(), key_resolver=lambda t: looked_up.append(t))
    with pytest.raises(AuthError) as e:
        anyio.run(auth.email, {"cf-access-jwt-assertion": make(signing_key)})
    assert e.value.status == 403 and str(e.value) == "invalid sign-in token"
    assert looked_up == []


def test_an_access_token_still_signs_in(signing_key):
    auth = Authenticator(make_settings(), key_resolver=lambda t: signing_key.public_key())
    assert anyio.run(auth.email, {"cf-access-jwt-assertion": make_jwt(signing_key)}) == "alfred@gmail.com"


