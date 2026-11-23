"""Sessions per task and kept sign-ins (specs/browser/sessions), end to end
through the tools, with the person's choices made through the page API."""

from __future__ import annotations

import json

import pytest

from .conftest import ref_of

pytestmark = pytest.mark.anyio


async def whoami(env, task, url):
    tab = await env.tab(task, url)
    data = (await env.snap(task, tab))["untrusted_page_data"]["snapshot"]
    return data


async def sign_in(env, task, url):
    await env.tab(task, url)


async def test_concurrent_tasks_share_nothing(env, sites):
    a = await env.task("task A")
    b = await env.task("task B")
    await sign_in(env, a, sites.https("shop.example.com", "/signin"))
    seen_a = await whoami(env, a, sites.https("shop.example.com", "/whoami"))
    seen_b = await whoami(env, b, sites.https("shop.example.com", "/whoami"))
    assert "signed in as alfred-shop.example.com" in seen_a and "storage: alfred" in seen_a
    assert "signed out" in seen_b and "storage: none" in seen_b


async def test_unkept_site_starts_signed_out(env, sites):
    a = await env.task()
    await sign_in(env, a, sites.https("shop.example.com", "/signin"))
    await env.call("end_task", {"task": a})
    assert not (env.data / "signins").exists() or not list((env.data / "signins").iterdir())
    b = await env.task()
    assert "signed out" in await whoami(env, b, sites.https("shop.example.com", "/whoami"))


async def test_kept_site_is_saved_and_others_are_not(env, sites):
    env.service.sessions.set_keep_signed_in("example.com", True)
    a = await env.task()
    await sign_in(env, a, sites.https("shop.example.com", "/signin"))
    await sign_in(env, a, sites.https("other.test", "/signin"))
    await env.call("end_task", {"task": a})
    files = list((env.data / "signins").glob("*.json"))
    assert len(files) == 1
    saved = files[0].read_text()
    assert "other.test" not in saved and "alfred-shop.example.com" in saved
    b = await env.task()
    seen = await whoami(env, b, sites.https("accounts.example.com", "/whoami"))
    assert "signed in as alfred-shop.example.com" in seen
    # Local storage belongs to its origin: kept for shop.example.com itself.
    assert "storage: alfred" in await whoami(env, b, sites.https("shop.example.com", "/whoami"))
    other = await whoami(env, b, sites.https("other.test", "/whoami"))
    assert "signed out" in other and "storage: none" in other
    started = [e for e in env.audit() if e["event"] == "task_started"]
    assert started[-1]["kept_sites"] == ["example.com"]


async def test_forgotten_site_starts_signed_out(env, sites):
    sessions = env.service.sessions
    sessions.set_keep_signed_in("example.com", True)
    a = await env.task()
    await sign_in(env, a, sites.https("shop.example.com", "/signin"))
    await env.call("end_task", {"task": a})
    b = await env.task("open while forgetting")
    assert "signed in" in await whoami(env, b, sites.https("shop.example.com", "/whoami"))
    sessions.forget("example.com")
    assert not list((env.data / "signins").glob("*.json"))
    # The open task keeps what it loaded, and saves nothing when it ends.
    assert "signed in" in await whoami(env, b, sites.https("shop.example.com", "/whoami"))
    await env.call("end_task", {"task": b})
    assert not list((env.data / "signins").glob("*.json"))
    c = await env.task()
    assert "signed out" in await whoami(env, c, sites.https("shop.example.com", "/whoami"))


async def test_forget_all(env, sites):
    sessions = env.service.sessions
    for site in ("example.com", "other.test"):
        sessions.set_keep_signed_in(site, True)
    a = await env.task()
    await sign_in(env, a, sites.https("shop.example.com", "/signin"))
    await sign_in(env, a, sites.https("other.test", "/signin"))
    await env.call("end_task", {"task": a})
    assert len(list((env.data / "signins").glob("*.json"))) == 2
    assert sessions.forget_all() == ["example.com", "other.test"]
    b = await env.task()
    assert "signed out" in await whoami(env, b, sites.https("other.test", "/whoami"))


async def test_idle_end_saves_kept_sign_ins(env, sites):
    env.service.sessions.set_keep_signed_in("example.com", True)
    a = await env.task()
    await sign_in(env, a, sites.https("shop.example.com", "/signin"))
    env.clock.t += 3600
    await env.service.expire()
    assert len(list((env.data / "signins").glob("*.json"))) == 1


async def test_no_tool_changes_settings(env, sites):
    """Run every tool; the settings file is never written by them."""
    sessions = env.service.sessions
    sessions.set_may_checkout("other.test", True)
    before = (env.data / "settings.json").read_bytes()
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/login"))
    snap = await env.snap(task, tab)
    user, pw = ref_of(snap, "User"), ref_of(snap, "Password")
    calls = [
        ("navigate", {"task": task, "tab": tab, "url": sites.https("shop.example.com", "/login")}),
        ("snapshot", {"task": task, "tab": tab}),
        ("type_text", {"task": task, "tab": tab, "ref": user, "text": "alfred"}),
        ("type_secret", {"task": task, "tab": tab, "ref": pw, "text": "pw-123456", "expect_site": "example.com"}),
        ("press_key", {"task": task, "tab": tab, "key": "Tab"}),
        ("scroll", {"task": task, "tab": tab}),
        ("screenshot", {"task": task, "tab": tab}),
        ("request_handoff", {"task": task, "tab": tab, "summary": "sign in"}),
        ("open_tab", {"task": task}),
        ("close_tab", {"task": task, "tab": "t2"}),
        ("end_task", {"task": task}),
    ]
    for name, args in calls:
        await env.call_raw(name, args)
    assert (env.data / "settings.json").read_bytes() == before
    assert json.loads(before)["sites"] == {"other.test": {"may_checkout": True}}


def saved(env) -> str:
    return "".join(p.read_text() for p in (env.data / "signins").glob("*.json"))


async def test_a_task_that_only_loaded_a_sign_in_does_not_save_it(env, sites):
    """Overlapping tasks: one that never visited a kept site does not write back
    the copy it loaded over a newer one."""
    env.service.sessions.set_keep_signed_in("example.com", True)
    first = await env.task()
    await sign_in(env, first, sites.https("shop.example.com", "/signin"))
    await env.call("end_task", {"task": first})
    a = await env.task("loads example.com, visits only other.test")
    await env.tab(a, sites.https("other.test", "/"))
    b = await env.task("signs in again")
    await sign_in(env, b, sites.https("login.example.com", "/signin"))
    await env.call("end_task", {"task": b})
    assert "alfred-login.example.com" in saved(env)
    await env.call("end_task", {"task": a})
    assert "alfred-login.example.com" in saved(env) and "alfred-shop.example.com" not in saved(env)


async def test_a_sign_in_saved_since_a_task_loaded_it_is_not_rolled_back(env, sites):
    env.service.sessions.set_keep_signed_in("example.com", True)
    first = await env.task()
    await sign_in(env, first, sites.https("shop.example.com", "/signin"))
    await env.call("end_task", {"task": first})
    a = await env.task("visits example.com with the old sign-in")
    assert "alfred-shop.example.com" in await whoami(env, a, sites.https("shop.example.com", "/whoami"))
    b = await env.task("signs in again")
    await sign_in(env, b, sites.https("login.example.com", "/signin"))
    await env.call("end_task", {"task": b})
    await env.call("end_task", {"task": a})
    assert "alfred-login.example.com" in saved(env) and "alfred-shop.example.com" not in saved(env)
    # A task that started after the last save does save its own.
    c = await env.task()
    await sign_in(env, c, sites.https("shop.example.com", "/signin"))
    await env.call("end_task", {"task": c})
    assert "alfred-shop.example.com" in saved(env)


async def test_forgotten_and_kept_again_is_not_resurrected_by_an_open_task(env, sites):
    sessions = env.service.sessions
    sessions.set_keep_signed_in("example.com", True)
    first = await env.task()
    await sign_in(env, first, sites.https("shop.example.com", "/signin"))
    await env.call("end_task", {"task": first})
    a = await env.task("open across forget and keep")
    assert "signed in" in await whoami(env, a, sites.https("shop.example.com", "/whoami"))
    sessions.forget("example.com")
    sessions.set_keep_signed_in("example.com", True)
    await env.call("end_task", {"task": a})
    assert not list((env.data / "signins").glob("*.json"))
    # Same with forget all.
    b = await env.task()
    await sign_in(env, b, sites.https("shop.example.com", "/signin"))
    sessions.forget_all()
    sessions.set_keep_signed_in("example.com", True)
    await env.call("end_task", {"task": b})
    assert not list((env.data / "signins").glob("*.json"))
