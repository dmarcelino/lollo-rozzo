"""Settings from the environment compose/notifications.yml passes in."""

import pytest

from lollo_notifications.config import ConfigError, Settings

from .conftest import BROWSER_TOKEN, EMAIL, ORIGIN, VAPID_PRIVATE, VAPID_PUBLIC, VAULT_TOKEN

BASE = {
    "CF_ACCESS_TEAM_DOMAIN": "team1",
    "CF_ACCESS_AUD": "aud1",
    "ALLOWED_USERS": f" {EMAIL.upper()} , hal@example.com,",
    "PUBLIC_ORIGIN": ORIGIN + "/",
    "LOLLO_VAPID_PUBLIC_KEY": VAPID_PUBLIC,
    "LOLLO_VAPID_PRIVATE_KEY": VAPID_PRIVATE,
    "PUSH_VAPID_CONTACT_EMAIL": "ops@example.com",
    "NOTIFY_TOKEN_VAULT": VAULT_TOKEN,
    "NOTIFY_TOKEN_BROWSER": BROWSER_TOKEN,
    "TZ": "UTC",
}


def env(**over: str | None) -> dict[str, str]:
    values = {**BASE, **over}
    return {k: v for k, v in values.items() if v is not None}


def test_reads_everything():
    s = Settings.from_env(env())
    assert s.team_domain == "team1" and s.audience == "aud1"
    assert s.issuer == "https://team1.cloudflareaccess.com"
    assert s.allowed_users == frozenset({EMAIL, "hal@example.com"})
    assert s.public_origin == ORIGIN
    assert (s.vapid_public_key, s.vapid_private_key, s.vapid_contact) == (
        VAPID_PUBLIC,
        VAPID_PRIVATE,
        "ops@example.com",
    )
    assert s.tokens == {"vault": VAULT_TOKEN, "browser": BROWSER_TOKEN}
    assert s.data_dir == "/data"


def test_secrets_stay_out_of_its_repr():
    text = repr(Settings.from_env(env()))
    assert VAPID_PRIVATE not in text and VAULT_TOKEN not in text and BROWSER_TOKEN not in text


def test_an_explicit_issuer_is_kept():
    assert Settings.from_env(env(CF_ACCESS_ISSUER="https://issuer.example.com")).issuer == "https://issuer.example.com"


@pytest.mark.parametrize(
    "key",
    [
        "CF_ACCESS_TEAM_DOMAIN",
        "CF_ACCESS_AUD",
        "ALLOWED_USERS",
        "PUBLIC_ORIGIN",
        "LOLLO_VAPID_PUBLIC_KEY",
        "LOLLO_VAPID_PRIVATE_KEY",
        "PUSH_VAPID_CONTACT_EMAIL",
    ],
)
def test_each_required_setting_is_required(key):
    with pytest.raises(ConfigError, match=key):
        Settings.from_env(env(**{key: None}))
    with pytest.raises(ConfigError, match=key):
        Settings.from_env(env(**{key: "  "}))


@pytest.mark.parametrize(
    "origin", ["http://alfred-lollo.example.com", "https://", "https://example.com/path", "https://example.com?x"]
)
def test_the_origin_must_be_https_and_a_host(origin):
    with pytest.raises(ConfigError, match="PUBLIC_ORIGIN"):
        Settings.from_env(env(PUBLIC_ORIGIN=origin))


@pytest.mark.parametrize(
    "over",
    [
        {"LOLLO_VAPID_PUBLIC_KEY": "short"},
        {"LOLLO_VAPID_PRIVATE_KEY": "k" * 44},
        {"LOLLO_VAPID_PRIVATE_KEY": "-----BEGIN EC PRIVATE KEY-----"},
    ],
)
def test_the_vapid_pair_must_be_shaped_like_lolloctls(over):
    with pytest.raises(ConfigError, match="LOLLO_VAPID"):
        Settings.from_env(env(**over))


def test_the_contact_must_be_an_email():
    with pytest.raises(ConfigError, match="PUSH_VAPID_CONTACT_EMAIL"):
        Settings.from_env(env(PUSH_VAPID_CONTACT_EMAIL="ops"))


def test_a_source_comes_from_the_tokens_name():
    s = Settings.from_env(env(NOTIFY_TOKEN_GMAIL_TRIGGERS="c3" * 32))
    assert s.tokens["gmail-triggers"] == "c3" * 32


def test_a_missing_or_empty_token_means_that_sender_cannot_send():
    s = Settings.from_env(env(NOTIFY_TOKEN_BROWSER=None, NOTIFY_TOKEN_VAULT=""))
    assert s.tokens == {}
    assert Settings.from_env(env(NOTIFY_TOKEN_BROWSER=None)).tokens == {"vault": VAULT_TOKEN}


def test_the_senders_own_variable_is_not_a_source():
    # NOTIFY_TOKEN is what a sender reads; the service has one per sender.
    assert "" not in Settings.from_env(env(NOTIFY_TOKEN="d4" * 32)).tokens


@pytest.mark.parametrize(
    "value",
    ["5a" * 31, "5a" * 33, ("5A" * 32), "g" * 64, " " + "5a" * 31 + "  x"],
)
def test_a_malformed_token_stops_the_service(value):
    with pytest.raises(ConfigError, match="NOTIFY_TOKEN_VAULT must be 64 lowercase hex"):
        Settings.from_env(env(NOTIFY_TOKEN_VAULT=value))


def test_a_shared_token_stops_the_service():
    with pytest.raises(ConfigError, match="same as another sender's token"):
        Settings.from_env(env(NOTIFY_TOKEN_BROWSER=VAULT_TOKEN))


@pytest.mark.parametrize(
    "key", ["NOTIFY_TOKEN_", "NOTIFY_TOKEN__X", "NOTIFY_TOKEN_9LIVES", "NOTIFY_TOKEN_NOTIFICATIONS"]
)
def test_a_token_must_name_a_sender_other_than_this_service(key):
    with pytest.raises(ConfigError, match=key):
        Settings.from_env(env(**{key: "e5" * 32}))
