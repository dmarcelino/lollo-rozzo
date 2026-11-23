"""Settings, read once from the environment that compose/browser.yml passes in."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass

# How agents address the service on the stack network (compose/browser.yml);
# /mcp accepts this host alone.
INTERNAL_NAME = "lollo-browser"
# Longest `wait_handoff(wait_seconds=...)`; Lettuce's MCP bridge gives a tool
# call 120 s (bff/src/mcp-bridge/client.ts), like the vault's get_result.
MAX_WAIT_SECONDS = 50
# Playwright's timeouts. An action on a ref fails fast; a navigation waits
# longer, still well inside the bridge's 120 s.
ACTION_TIMEOUT_MS = 5_000
NAVIGATION_TIMEOUT_MS = 30_000
SCREENSHOT_TIMEOUT_MS = 15_000
# Saving a task's kept sign-ins when it ends: a busy page can stall
# storage_state, and housekeeping and end_task wait on it. Past this, nothing
# is saved and the task's context is closed anyway.
SAVE_SIGNINS_SECONDS = 5
# A snapshot longer than this is cut, with a note saying so.
MAX_SNAPSHOT_CHARS = 60_000
MAX_PURPOSE_LENGTH = 300
MAX_SUMMARY_LENGTH = 500
MAX_TEXT_LENGTH = 10_000
MAX_URL_LENGTH = 4_000
# A hand-off nobody answers is dropped after this long (design.md, Open Questions).
HANDOFF_TTL_SECONDS = 24 * 3600
# The audit log is rotated to audit.jsonl.1 past this size; one old file is kept.
AUDIT_MAX_BYTES = 5 * 1024 * 1024
# How often the service ends idle and overdue tasks.
HOUSEKEEPING_SECONDS = 15
# Wait between attempts to reach the engine, doubling up to the maximum.
RECONNECT_MIN_SECONDS = 1
RECONNECT_MAX_SECONDS = 30

ENGINE_SECRET = re.compile(r"[A-Za-z0-9_-]{16,256}")


# The person's page, behind the tunnel route of the same name.
PREFIX = "/browser"
# A live-view ticket is good once, for this long.
TICKET_TTL_SECONDS = 30
# Pushes: how long a push service keeps one for an offline device.
PUSH_TTL_SECONDS = 600
# Two recognised checkout submits on one tab closer than this are one.
CHECKOUT_DEDUPE_SECONDS = 10


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class PageSettings:
    """The lollo page's settings (lollo_common.auth.AccessSettings), push keys and the display."""

    team_domain: str
    audience: str
    issuer: str
    allowed_users: frozenset[str]
    public_origin: str
    vapid_public_key: str
    vapid_private_key: str
    vapid_contact: str
    # host:port of the engine's VNC server, and its password (the engine
    # secret's first 8 characters: VNC authentication uses at most 8).
    vnc_host: str
    vnc_port: int
    vnc_password: str


@dataclass(frozen=True)
class Limits:
    max_tasks: int = 4
    max_tabs: int = 8
    idle_seconds: float = 15 * 60
    max_task_seconds: float = 4 * 3600


@dataclass(frozen=True)
class Settings:
    engine_url: str
    engine_secret: str
    data_dir: str
    limits: Limits
    # The engine's network, when set explicitly; otherwise detected from the
    # interface that routes to the engine (guard.detect_network).
    engine_network: ipaddress.IPv4Network | ipaddress.IPv6Network | None
    page: PageSettings

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        url = env.get("BROWSER_ENGINE_URL", "ws://lollo-browser-engine:3000").rstrip("/")
        if not re.fullmatch(r"wss?://[A-Za-z0-9.-]+(:\d+)?", url):
            raise ConfigError("BROWSER_ENGINE_URL must be ws://host:port")
        secret = env.get("BROWSER_ENGINE_SECRET", "")
        if not ENGINE_SECRET.fullmatch(secret):
            raise ConfigError("BROWSER_ENGINE_SECRET is missing or not 16+ characters of [A-Za-z0-9_-]")
        cidr = env.get("BROWSER_ENGINE_NETWORK_CIDR", "").strip()
        try:
            network = ipaddress.ip_network(cidr, strict=False) if cidr else None
        except ValueError as e:
            raise ConfigError(f"BROWSER_ENGINE_NETWORK_CIDR: {e}") from e
        limits = Limits(
            max_tasks=_positive(env, "BROWSER_MAX_TASKS", Limits.max_tasks),
            max_tabs=_positive(env, "BROWSER_MAX_TABS", Limits.max_tabs),
            idle_seconds=_positive(env, "BROWSER_TASK_IDLE_SECONDS", Limits.idle_seconds),
            max_task_seconds=_positive(env, "BROWSER_TASK_MAX_SECONDS", Limits.max_task_seconds),
        )
        return cls(
            engine_url=url,
            engine_secret=secret,
            data_dir=env.get("BROWSER_DATA_DIR", "/data"),
            limits=limits,
            engine_network=network,
            page=_page(env, secret),
        )


def _page(env: dict[str, str], secret: str) -> PageSettings:
    def required(key: str) -> str:
        value = env.get(key, "").strip()
        if not value:
            raise ConfigError(f"{key} is required")
        return value

    team = required("CF_ACCESS_TEAM_DOMAIN")
    users = frozenset(u.strip().lower() for u in required("ALLOWED_USERS").split(",") if u.strip())
    origin = required("PUBLIC_ORIGIN").rstrip("/")
    if not origin.startswith("https://"):
        raise ConfigError("PUBLIC_ORIGIN must be https://")
    vnc = env.get("BROWSER_VNC", "lollo-browser-engine:5900").strip()
    m = re.fullmatch(r"([A-Za-z0-9.-]+):(\d{1,5})", vnc)
    if not m:
        raise ConfigError("BROWSER_VNC must be host:port")
    return PageSettings(
        team_domain=team,
        audience=required("CF_ACCESS_AUD"),
        issuer=env.get("CF_ACCESS_ISSUER", "").strip() or f"https://{team}.cloudflareaccess.com",
        allowed_users=users,
        public_origin=origin,
        vapid_public_key=required("BROWSER_VAPID_PUBLIC_KEY"),
        vapid_private_key=required("BROWSER_VAPID_PRIVATE_KEY"),
        vapid_contact=required("PUSH_VAPID_CONTACT_EMAIL"),
        vnc_host=m.group(1),
        vnc_port=int(m.group(2)),
        vnc_password=secret[:8],
    )


def _positive(env: dict[str, str], name: str, default: float) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return int(default)
    if not raw.isdigit() or int(raw) <= 0:
        raise ConfigError(f"{name} must be a positive whole number")
    return int(raw)
