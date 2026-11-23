"""Settings, read once from the environment that compose/notifications.yml passes in."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from lollo_common.executor import SOURCE
from lollo_common.notify import NOTIFY_TOKEN

# The person's page lives under this path on the lollo's host, behind the
# tunnel route of the same name. It is also the source name of the page's own
# test notification (notifications-test), so no sender may take it.
PREFIX = "/notifications"
OWN_SOURCE = "notifications"
# Each sender's token comes from NOTIFY_TOKEN_<SOURCE>: NOTIFY_TOKEN_VAULT is the
# vault's, NOTIFY_TOKEN_GMAIL_TRIGGERS would be gmail-triggers'.
TOKEN_ENV = "NOTIFY_TOKEN_"
# How many notifications one sender may hand over, per period; the rest are
# refused (429). One sender's count never touches another's.
SEND_PER_MINUTE = 30
SEND_PER_HOUR = 300
# Test notifications per person per minute (each one rings every device of theirs).
TEST_PER_MINUTE = 5
# Subscriptions kept in all; past this the oldest is dropped.
MAX_SUBSCRIPTIONS = 50
# Notifications waiting for delivery, per sender (each has its own queue); past
# this that sender's sends are refused (503), and no other sender's.
MAX_QUEUED = 100
# A push service keeps an undelivered notification this long, in seconds.
PUSH_TTL_SECONDS = 600
PUSH_TIMEOUT_SECONDS = 10
# A notification goes to a person's devices at once, at most this many at a time.
PUSH_CONCURRENCY = 10
# A subscription whose push service times out or cannot be reached this many
# times in a row is deleted; a push it takes resets the count.
MAX_PUSH_FAILURES = 3
# The VAPID pair as lolloctl makes it (lib/common.sh): base64url without padding,
# an uncompressed P-256 point and a 32-byte scalar.
VAPID_PUBLIC = re.compile(r"[A-Za-z0-9_-]{87}")
VAPID_PRIVATE = re.compile(r"[A-Za-z0-9_-]{43}")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    team_domain: str
    audience: str
    issuer: str
    allowed_users: frozenset[str]
    public_origin: str
    vapid_public_key: str
    vapid_private_key: str = field(repr=False)
    vapid_contact: str
    # Source name to its token; a source without a token cannot send.
    tokens: dict[str, str] = field(default_factory=dict, repr=False)
    data_dir: str = "/data"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)

        def required(key: str) -> str:
            value = env.get(key, "").strip()
            if not value:
                raise ConfigError(f"{key} is required")
            return value

        team = required("CF_ACCESS_TEAM_DOMAIN")
        users = frozenset(u.strip().lower() for u in required("ALLOWED_USERS").split(",") if u.strip())
        if not users:
            raise ConfigError("ALLOWED_USERS is required")
        origin = required("PUBLIC_ORIGIN").rstrip("/")
        parts = urlsplit(origin)
        if parts.scheme != "https" or not parts.hostname or parts.path or parts.query or parts.fragment:
            raise ConfigError("PUBLIC_ORIGIN must be https://<host>")
        public = required("LOLLO_VAPID_PUBLIC_KEY")
        private = required("LOLLO_VAPID_PRIVATE_KEY")
        if not VAPID_PUBLIC.fullmatch(public) or not VAPID_PRIVATE.fullmatch(private):
            raise ConfigError("LOLLO_VAPID_PUBLIC_KEY and LOLLO_VAPID_PRIVATE_KEY must be the pair lolloctl up makes")
        contact = required("PUSH_VAPID_CONTACT_EMAIL")
        if "@" not in contact or any(c.isspace() for c in contact):
            raise ConfigError("PUSH_VAPID_CONTACT_EMAIL must be an email address")
        return cls(
            team_domain=team,
            audience=required("CF_ACCESS_AUD"),
            issuer=env.get("CF_ACCESS_ISSUER", "").strip() or f"https://{team}.cloudflareaccess.com",
            allowed_users=users,
            public_origin=origin,
            vapid_public_key=public,
            vapid_private_key=private,
            vapid_contact=contact,
            tokens=read_tokens(env),
            data_dir=env.get("NOTIFICATIONS_DATA_DIR", "").strip() or "/data",
        )


def read_tokens(env: dict[str, str]) -> dict[str, str]:
    """The senders' tokens, by source name. An empty value means that source
    cannot send; a malformed or shared token stops the service, since it could
    not say who called."""
    tokens: dict[str, str] = {}
    for key in sorted(env):
        if not key.startswith(TOKEN_ENV):
            continue
        value = env[key].strip()
        if not value:
            continue
        source = key.removeprefix(TOKEN_ENV).lower().replace("_", "-")
        if not SOURCE.fullmatch(source):
            raise ConfigError(f"{key} does not name a sender")
        if source == OWN_SOURCE:
            raise ConfigError(f"{key}: {OWN_SOURCE} is this service's own name, not a sender's")
        if not NOTIFY_TOKEN.fullmatch(value):
            raise ConfigError(f"{key} must be 64 lowercase hex characters (lolloctl up makes it)")
        if value in tokens.values():
            raise ConfigError(f"{key} is the same as another sender's token; each sender needs its own")
        tokens[source] = value
    return tokens
