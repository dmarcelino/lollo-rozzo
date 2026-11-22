"""The parts that need no browser: sites, URLs, TOTP, the scrub, the audit
log, settings and kept sign-in files, hand-offs, configuration."""

from __future__ import annotations

import json
import logging
import os

import anyio
import pytest

from lollo_browser import totp
from lollo_browser.audit import AuditLog, url_for_log
from lollo_browser.config import ConfigError, Settings
from lollo_browser.handoffs import Handoffs
from lollo_browser.scrub import MASK, Scrubber, ScrubFilter
from lollo_browser.sessions import Sessions, SiteError, normalize_site
from lollo_browser.sites import UrlRefused, check_web_url, same_site_https, site_of

from .conftest import SECRET, TOTP_KEY

# ── sites and URLs ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host,site",
    [
        ("shop.example.com", "example.com"),
        ("accounts.example.com", "example.com"),
        ("https://shop.example.com:8443/x?y", "example.com"),
        ("shop.example.co.uk", "example.co.uk"),
        ("alice.github.io", "alice.github.io"),  # PSL private section
        ("shop-example.com.evil.test", "evil.test"),  # unknown TLD: the default rule
        ("other.test", "other.test"),
        ("127.0.0.1", "127.0.0.1"),
        ("localhost", "localhost"),
        ("co.uk", ""),
        ("", ""),
    ],
)
def test_site_of(host, site):
    assert site_of(host) == site


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "chrome://settings",
        "devtools://devtools/bundled/inspector.html",
        "view-source:https://example.com",
        "javascript:alert(1)",
        "data:text/html,<p>x",
        "about:blank",
        "ftp://example.com/x",
        "blob:https://example.com/1",
        "example.com",
        "https://",
        "https://user:pw@example.com/",
        "https://exa\nmple.com/",
        "  ",
    ],
)
def test_non_web_urls_refused(url):
    with pytest.raises(UrlRefused):
        check_web_url(url)


def test_web_urls_pass():
    assert check_web_url(" https://shop.example.com/a?b#c ") == "https://shop.example.com/a?b#c"
    assert check_web_url("http://shop.example.com") == "http://shop.example.com"


def test_same_site_https():
    assert same_site_https("https://login.example.com/x", "example.com")
    assert not same_site_https("http://login.example.com/x", "example.com")
    assert not same_site_https("https://shop-example.com.evil.test/", "example.com")
    assert not same_site_https("https://example.com.evil.test/", "example.com")
    assert not same_site_https("about:srcdoc", "example.com")
    assert not same_site_https("https://example.com/", "")


# ── TOTP ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "at,expected",
    [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")],
)
def test_totp_rfc6238_vectors(at, expected):
    key = totp.parse_key(f"otpauth://totp/x?secret={TOTP_KEY}&digits=8")
    assert totp.code(key, at) == expected


def test_totp_key_forms():
    plain = totp.parse_key(TOTP_KEY.lower())
    spaced = totp.parse_key(" ".join(TOTP_KEY[i : i + 4] for i in range(0, len(TOTP_KEY), 4)))
    uri = totp.parse_key(f"otpauth://totp/Shop:alfred?secret={TOTP_KEY}&issuer=Shop")
    assert totp.code(plain, 59) == totp.code(spaced, 59) == totp.code(uri, 59) == "287082"
    sha256 = totp.parse_key(f"otpauth://totp/x?secret={TOTP_KEY}&algorithm=SHA256&period=60")
    assert sha256.algorithm == "SHA256" and sha256.period == 60


@pytest.mark.parametrize(
    "key", ["", "not base32!", "otpauth://hotp/x?secret=GEZD", "steam://ABC", "otpauth://totp/x?secret=GEZD&digits=9"]
)
def test_totp_bad_keys_never_echo_the_key(key):
    with pytest.raises(totp.TotpError) as e:
        totp.parse_key(key)
    assert not key.strip() or key not in str(e.value)


# ── the scrub ────────────────────────────────────────────────────────────────


def test_scrub_every_form():
    s = Scrubber()
    s.add(SECRET)
    spaced = " ".join(SECRET)
    dashed = "-".join(SECRET)
    text = f"a {SECRET} b {spaced} c {dashed} d {SECRET.upper()} e"
    out = s.text(text)
    assert SECRET not in out and spaced not in out and dashed not in out and SECRET.upper() not in out
    assert out.count(MASK) == 4 and out.startswith("a ") and out.endswith(" e")


def test_scrub_leaves_short_values_to_field_masking():
    s = Scrubber()
    s.add("42", "4-2-1-9-7")
    assert s.text("code 42 and 4-2 and 42197") == "code 42 and 4-2 and 42197"


def test_scrub_deep_and_logs(caplog):
    s = Scrubber()
    s.add(SECRET)
    assert s.deep({"a": [SECRET, {"b": f"x{SECRET}"}], SECRET: 1}) == {"a": [MASK, {"b": f"x{MASK}"}], SECRET: 1}
    caplog.handler.addFilter(ScrubFilter(s))
    log = logging.getLogger("t")
    log.warning("value %s", SECRET)
    try:
        raise ValueError(SECRET)
    except ValueError:
        log.exception("failed")
    assert SECRET not in caplog.text and MASK in caplog.text


# ── the audit log ────────────────────────────────────────────────────────────


class Task:
    id = "task-1"
    purpose = "buy a kettle"


def test_audit_scrubs_and_strips_urls(tmp_path):
    s = Scrubber()
    s.add(SECRET)
    log = AuditLog(tmp_path, scrubber=s)
    log.record(
        "secret_typed", Task(), site="example.com", field='textbox "Password"', url="https://a.example.com/p?q=1#f"
    )
    log.record("refused", Task(), reason=f"echo {SECRET}")
    raw = (tmp_path / "audit.jsonl").read_text()
    assert SECRET not in raw
    entries = log.read()
    assert entries[0]["event"] == "refused" and entries[1]["url"] == "https://a.example.com/p"
    assert entries[1]["task"] == "task-1" and entries[1]["purpose"] == "buy a kettle"
    assert oct(os.stat(tmp_path / "audit.jsonl").st_mode & 0o777) == "0o600"
    assert log.read(event="secret_typed")[0]["field"] == 'textbox "Password"'
    with pytest.raises(ValueError):
        log.record("anything")


def test_audit_is_bounded(tmp_path):
    log = AuditLog(tmp_path, max_bytes=2000)
    for i in range(100):
        log.record("navigated", Task(), url=f"https://example.com/{i}")
    assert (tmp_path / "audit.jsonl").stat().st_size <= 2000
    assert (tmp_path / "audit.jsonl.1").stat().st_size <= 2000
    assert log.read(limit=1)[0]["url"] == "https://example.com/99"


def test_url_for_log():
    assert url_for_log("https://u:p@example.com/a?token=1#x") == "https://example.com/a"
    assert url_for_log("data:text/html,secret") == "data:"


# ── settings and kept sign-ins ───────────────────────────────────────────────


def cookie(domain, name="sid"):
    return {
        "name": name,
        "value": "v",
        "domain": domain,
        "path": "/",
        "expires": -1,
        "httpOnly": False,
        "secure": False,
        "sameSite": "Lax",
    }


def test_site_settings(tmp_path):
    s = Sessions(tmp_path)
    assert normalize_site("https://Shop.Example.com/login") == "example.com"
    with pytest.raises(SiteError):
        normalize_site("co.uk")
    s.set_keep_signed_in("shop.example.com", True)
    s.set_may_checkout("other.test", True)
    assert [(x.site, x.keep_signed_in, x.may_checkout) for x in s.sites()] == [
        ("example.com", True, False),
        ("other.test", False, True),
    ]
    assert s.may_checkout("https://pay.other.test/") and not s.may_checkout("https://example.com/")
    s.set_may_checkout("other.test", False)
    assert [x.site for x in s.sites()] == ["example.com"]
    assert oct(os.stat(tmp_path / "settings.json").st_mode & 0o777) == "0o600"


def test_save_keeps_only_kept_and_touched_sites(tmp_path):
    s = Sessions(tmp_path)
    s.set_keep_signed_in("example.com", True)
    s.set_keep_signed_in("kept.test", True)
    state = {
        "cookies": [cookie(".example.com"), cookie("shop.example.com", "x"), cookie("other.test"), cookie("kept.test")],
        "origins": [
            {"origin": "https://shop.example.com", "localStorage": [{"name": "who", "value": "a"}]},
            {"origin": "https://other.test", "localStorage": [{"name": "who", "value": "b"}]},
        ],
    }
    # kept.test is kept but this task neither loaded nor visited it: not written.
    assert s.save(state, {"example.com", "other.test"}) == ["example.com"]
    files = list((tmp_path / "signins").glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert {c["domain"] for c in saved["cookies"]} == {".example.com", "shop.example.com"}
    assert [o["origin"] for o in saved["origins"]] == ["https://shop.example.com"]
    assert "other.test" not in files[0].read_text()
    state0, loaded = s.initial_state()
    assert loaded == {"example.com"} and len(state0["cookies"]) == 2
    s.forget("example.com")
    assert not list((tmp_path / "signins").glob("*.json"))
    assert s.initial_state() == (None, frozenset())
    assert s.save(state, {"example.com"}) == []


def test_forget_all(tmp_path):
    s = Sessions(tmp_path)
    for site in ("example.com", "kept.test"):
        s.set_keep_signed_in(site, True)
        s.save({"cookies": [cookie(site)], "origins": []}, {site})
    s.set_may_checkout("kept.test", True)
    assert s.forget_all() == ["example.com", "kept.test"]
    assert not list((tmp_path / "signins").glob("*.json"))
    assert [(x.site, x.keep_signed_in, x.may_checkout) for x in s.sites()] == [("kept.test", False, True)]


def test_initial_state_ignores_foreign_entries(tmp_path):
    s = Sessions(tmp_path)
    s.set_keep_signed_in("example.com", True)
    s.save({"cookies": [cookie("example.com")], "origins": []}, {"example.com"})
    path = next((tmp_path / "signins").glob("*.json"))
    data = json.loads(path.read_text())
    data["cookies"].append(cookie("evil.test"))
    data["origins"].append({"origin": "https://evil.test", "localStorage": []})
    path.write_text(json.dumps(data))
    state, _ = s.initial_state()
    assert [c["domain"] for c in state["cookies"]] == ["example.com"] and state["origins"] == []


# ── hand-offs ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_handoffs():
    now = [0.0]
    h = Handoffs(clock=lambda: now[0], ttl=100)
    a = h.request("task-1", "t1", "sign in please", "https://example.com/")
    assert (await h.wait(a.id, "task-1", 0.05)).status == "pending"
    with pytest.raises(LookupError):
        await h.wait(a.id, "task-2", 0)

    async def answer():
        await anyio.sleep(0.05)
        h.answer(a.id, done=True)

    async with anyio.create_task_group() as tg:
        tg.start_soon(answer)
        assert (await h.wait(a.id, "task-1", 5)).status == "done"
    b = h.request("task-1", "t1", "pay", "https://example.com/")
    now[0] = 200
    h.expire()
    assert b.status == "no answer"
    c = h.request("task-2", "t1", "x", "")
    h.end_task("task-2")
    assert c.status == "declined"
    with pytest.raises(LookupError):
        h.get(c.id)


# ── configuration ────────────────────────────────────────────────────────────


ENV = {
    "BROWSER_ENGINE_SECRET": "abcdefgh" + "a" * 24,
    "CF_ACCESS_TEAM_DOMAIN": "team1",
    "CF_ACCESS_AUD": "aud1",
    "ALLOWED_USERS": "Alfred@gmail.com, hal@example.com",
    "PUBLIC_ORIGIN": "https://alfred-lollo.example.com/",
    "BROWSER_VAPID_PUBLIC_KEY": "pub",
    "BROWSER_VAPID_PRIVATE_KEY": "priv",
    "PUSH_VAPID_CONTACT_EMAIL": "ops@example.com",
}


def test_settings_from_env():
    s = Settings.from_env(ENV)
    assert s.engine_url == "ws://lollo-browser-engine:3000" and s.data_dir == "/data"
    assert (s.limits.max_tasks, s.limits.max_tabs, s.limits.idle_seconds, s.limits.max_task_seconds) == (
        4,
        8,
        900,
        14400,
    )
    assert s.engine_network is None
    p = s.page
    assert p.allowed_users == {"alfred@gmail.com", "hal@example.com"}
    assert p.public_origin == "https://alfred-lollo.example.com" and p.issuer == "https://team1.cloudflareaccess.com"
    assert (p.vnc_host, p.vnc_port, p.vnc_password) == ("lollo-browser-engine", 5900, "abcdefgh")
    s = Settings.from_env({**ENV, "BROWSER_MAX_TASKS": "2", "BROWSER_ENGINE_NETWORK_CIDR": "172.30.0.0/16"})
    assert s.limits.max_tasks == 2 and str(s.engine_network) == "172.30.0.0/16"
    for bad in (
        {},
        {**ENV, "BROWSER_ENGINE_SECRET": "short"},
        {**ENV, "BROWSER_MAX_TABS": "0"},
        {**ENV, "BROWSER_ENGINE_URL": "http://x"},
        {**ENV, "BROWSER_ENGINE_NETWORK_CIDR": "nope"},
        {**ENV, "PUBLIC_ORIGIN": "http://alfred-lollo.example.com"},
        {**ENV, "BROWSER_VAPID_PRIVATE_KEY": ""},
        {**ENV, "CF_ACCESS_AUD": ""},
        {**ENV, "BROWSER_VNC": "lollo-browser-engine"},
    ):
        with pytest.raises(ConfigError):
            Settings.from_env(bad)
