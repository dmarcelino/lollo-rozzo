"""Checkout recognition, hand-offs and notifications (specs/browser/checkout)."""

from __future__ import annotations

import anyio
import pytest

from lollo_browser import checkout
from lollo_browser.checkout import Features, recognise

from .conftest import PAGES as SITE_PAGES
from .conftest import ref_of, wait_for

# ── recognition alone ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,url",
    [
        ("Place order", "https://shop.example.com/checkout/review"),
        ("Place your order", "https://shop.example.com/x"),
        ("Pay 34.90 EUR", "https://shop.example.com/x"),
        ("Buy now", "https://shop.example.com/product/1"),
        ("Complete purchase", "https://shop.example.com/x"),
        ("Zahlungspflichtig bestellen", "https://shop.example.de/kasse"),
        ("Jetzt kaufen", "https://shop.example.de/x"),
        ("Bestellung abschicken", "https://shop.example.de/x"),
        ("Valider la commande", "https://boutique.example.fr/commande"),
        ("Commander et payer", "https://boutique.example.fr/x"),
        ("Payer maintenant", "https://boutique.example.fr/x"),
        ("Realizar pedido", "https://tienda.example.es/pedido"),
        ("Finalizar compra", "https://tienda.example.es/x"),
        ("Comprar ahora", "https://tienda.example.es/x"),
        ("Conferma l’ordine", "https://negozio.example.it/ordine"),
        ("Acquista ora 34,90 €", "https://negozio.example.it/x"),
        ("Procedi al pagamento", "https://negozio.example.it/x"),
    ],
)
def test_checkout_buttons_in_five_languages(name, url):
    assert recognise(Features(names=[name], url=url)).is_checkout, name


@pytest.mark.parametrize(
    "name,fields,url",
    [
        ("Sign in", ["email", "password"], "https://shop.example.com/checkout/login"),
        ("Subscribe", ["email"], "https://shop.example.com/newsletter"),
        ("Search", ["q"], "https://shop.example.com/search"),
        ("Send message", ["message"], "https://shop.example.com/contact"),
        ("Add to cart", [], "https://shop.example.com/product/1"),
        ("Continue to payment", ["quantity"], "https://shop.example.com/cart"),
        ("Weiter zur Kasse", [], "https://shop.example.de/warenkorb"),
        ("Order history", [], "https://shop.example.com/account"),
        ("Continue", ["name", "address"], "https://shop.example.com/checkout/address"),
        ("Border settings", [], "https://shop.example.com/border"),
    ],
)
def test_not_checkouts(name, fields, url):
    verdict = recognise(Features(names=[name], fields=fields, url=url))
    assert not verdict.is_checkout, (name, verdict)


def test_card_form_without_a_telling_button():
    v = recognise(Features(names=["Continue"], fields=["cardnumber cc-number", "cvc"], url="https://s.example/pay"))
    assert v.is_checkout and "the form asks for card details" in v.reasons


def test_submit_during_take_over():
    assert checkout.submit_looks_like_checkout("https://s.example/checkout", "https://s.example/checkout/place", False)
    assert checkout.submit_looks_like_checkout("https://s.example/x", "https://s.example/order", True)
    assert not checkout.submit_looks_like_checkout("https://s.example/login", "https://s.example/session", False)
    assert not checkout.submit_looks_like_checkout("https://s.example/search", "https://s.example/search", True)


# ── through the tools ────────────────────────────────────────────────────────

PAGES = ["/checkout/review", "/kasse", "/commande", "/pedido", "/ordine", "/shop/buy", "/pay"]
BUTTONS = {
    "/checkout/review": "Place order",
    "/kasse": "Zahlungspflichtig bestellen",
    "/commande": "Valider la commande",
    "/pedido": "Realizar pedido",
    "/ordine": "Conferma l’ordine",
    "/shop/buy": "Acquista ora 34,90 €",
    "/pay": "Continue",
}


async def out_text(env, task, tab):
    return await env.page(task, tab).locator("#out").text_content()


@pytest.mark.anyio
@pytest.mark.parametrize("path", PAGES)
async def test_checkout_on_a_site_not_allowed_becomes_a_hand_off(env, sites, path):
    task = await env.task("buy a kettle")
    tab = await env.tab(task, sites.https("shop.example.com", path))
    snap = await env.snap(task, tab)
    result = await env.call("click", {"task": task, "tab": tab, "ref": ref_of(snap, BUTTONS[path])})
    assert result["status"] == "handed_off" and result["handoff"].startswith("h")
    assert "handed to the person" in result["message"]
    assert await out_text(env, task, tab) == ""  # not performed
    h = env.service.handoffs.get(result["handoff"])
    assert h.kind == "checkout" and h.site == "example.com" and h.status == "pending"
    entries = [e for e in env.audit() if e["event"] == "checkout_handed_off"]
    assert entries and entries[0]["site"] == "example.com" and entries[0]["purpose"] == "buy a kettle"
    await wait_for(lambda: env.sender.calls)
    push = env.sender.payloads()[0]
    assert push["url"] == f"/browser/h/{h.id}" and "example.com" in push["body"]


@pytest.mark.anyio
async def test_enter_in_a_checkout_form_becomes_a_hand_off(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    snap = await env.snap(task, tab)
    result = await env.call("press_key", {"task": task, "tab": tab, "key": "Enter", "ref": ref_of(snap, "Name")})
    assert result["status"] == "handed_off"
    assert await out_text(env, task, tab) == ""


# A checkout in a same-site frame, for keys pressed with the focus inside it.
SITE_PAGES["/frame-checkout"] = (
    "<title>Pay</title><iframe src='https://pay.example.com:{https}/checkout/review'></iframe>"
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key,ref,focus",
    [
        ("Enter", None, "input"),  # the focused field submits its form
        ("Shift+Enter", "Name", None),
        ("Enter", "Place order", None),
        ("Space", "Place order", None),
        (" ", "Place order", None),
        ("Space", None, "button"),
        ("Enter", None, "button"),
        (" ", None, "button"),
    ],
)
async def test_every_submitting_key_in_a_checkout_becomes_a_hand_off(env, sites, key, ref, focus):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    snap = await env.snap(task, tab)
    args = {"task": task, "tab": tab, "key": key}
    if ref:
        args["ref"] = ref_of(snap, ref)
    else:
        await env.page(task, tab).locator(focus).focus()
    result = await env.call("press_key", args)
    assert result["status"] == "handed_off", result
    assert await out_text(env, task, tab) == ""
    assert env.service.handoffs.get(result["handoff"]).kind == "checkout"


@pytest.mark.anyio
async def test_a_key_pressed_with_the_focus_in_a_frame_is_checked_there(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/frame-checkout"))
    await env.snap(task, tab)
    frame = env.page(task, tab).frame_locator("iframe")
    await frame.locator("button").focus()
    result = await env.call("press_key", {"task": task, "tab": tab, "key": "Space"})
    assert result["status"] == "handed_off", result
    assert await frame.locator("#out").text_content() == ""


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["\n", "\r", "Shift+\n", "\t"])
async def test_control_characters_are_not_keys(env, sites, key):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    await env.snap(task, tab)
    await env.page(task, tab).locator("input").focus()
    assert "not allowed" in await env.error("press_key", {"task": task, "tab": tab, "key": key})
    assert await out_text(env, task, tab) == ""


@pytest.mark.anyio
async def test_keys_that_submit_nothing_are_pressed(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    snap = await env.snap(task, tab)
    name = ref_of(snap, "Name")
    for args in ({"key": "Space", "ref": name}, {"key": "a", "ref": name}, {"key": "Tab"}):
        result = await env.call("press_key", {"task": task, "tab": tab, **args})
        assert "handoff" not in result, (args, result)
    assert await env.page(task, tab).locator("input").input_value() == " a"
    assert not [e for e in env.audit() if e["event"].startswith("checkout")]


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/newsletter", "/search", "/contact", "/cart", "/login"])
async def test_other_forms_are_performed(env, sites, path):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", path))
    snap = await env.snap(task, tab)
    button = {
        "/newsletter": "Subscribe",
        "/search": "Search",
        "/contact": "Send message",
        "/cart": "Add to cart",
        "/login": "Sign in",
    }[path]
    result = await env.call("click", {"task": task, "tab": tab, "ref": ref_of(snap, button)})
    assert "handoff" not in result
    assert not [e for e in env.audit() if e["event"].startswith("checkout")]


@pytest.mark.anyio
async def test_checkout_on_an_allowed_site_is_performed_notified_and_audited(env, sites):
    env.service.sessions.set_may_checkout("example.com", True)
    task = await env.task("order the kettle")
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    snap = await env.snap(task, tab)
    result = await env.call("click", {"task": task, "tab": tab, "ref": ref_of(snap, "Place order")})
    assert "handoff" not in result
    assert await out_text(env, task, tab) == "ORDER PLACED"
    entry = next(e for e in env.audit() if e["event"] == "checkout_submitted")
    assert entry["by"] == "agent" and entry["site"] == "example.com" and entry["url"].endswith("/checkout/review")
    assert entry["task"] == task and "ts" in entry
    await wait_for(lambda: env.sender.calls)
    assert env.sender.payloads()[0]["title"] == "Checkout submitted"
    # Another site stays not allowed.
    tab2 = await env.tab(task, sites.https("other.test", "/checkout/review"))
    snap2 = await env.snap(task, tab2)
    assert (await env.call("click", {"task": task, "tab": tab2, "ref": ref_of(snap2, "Place order")}))["status"] == (
        "handed_off"
    )


@pytest.mark.anyio
async def test_a_checkout_click_that_fails_is_not_reported(env, sites):
    """On an allowed site the checkout is recorded and pushed only once the
    click went through, so a failed click neither reports an order nor hides
    the retry behind the de-duplication."""
    env.service.sessions.set_may_checkout("example.com", True)
    task = await env.task("order the kettle")
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    snap = await env.snap(task, tab)
    page = env.page(task, tab)
    await page.evaluate("document.querySelector('button').disabled = true")
    args = {"task": task, "tab": tab, "ref": ref_of(snap, "Place order")}
    assert "failed" in await env.error("click", args)
    await anyio.sleep(0.2)
    assert not [e for e in env.audit() if e["event"] == "checkout_submitted"] and not env.sender.calls
    await page.evaluate("document.querySelector('button').disabled = false")
    await env.call("click", args)
    assert await out_text(env, task, tab) == "ORDER PLACED"
    assert [e for e in env.audit() if e["event"] == "checkout_submitted"]
    await wait_for(lambda: env.sender.calls)


@pytest.mark.anyio
async def test_person_submitting_a_checkout_during_take_over_is_notified_and_audited(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/confirm"))
    assert await env.service.take_over(task, tab, "alfred@gmail.com")
    page = env.page(task, tab)
    # What the person's click in the live view does: the form posts.
    await page.locator("button").click()
    await page.wait_for_url("**/checkout/place-order")
    await wait_for(lambda: any(e["event"] == "checkout_submitted" for e in env.audit()))
    entry = next(e for e in env.audit() if e["event"] == "checkout_submitted")
    assert entry["by"] == "person" and entry["site"] == "example.com"
    await wait_for(lambda: any(p["title"] == "Checkout submitted" for p in env.sender.payloads()))


@pytest.mark.anyio
async def test_a_pages_own_posts_during_take_over_are_not_checkouts(env, sites):
    """Only a navigation counts: a checkout page's fetch or XHR (an address
    check, analytics) is not the person submitting it."""
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/confirm"))
    assert await env.service.take_over(task, tab, "alfred@gmail.com")
    page = env.page(task, tab)
    status = await page.evaluate(
        """async () => {
          const r = await fetch('/checkout/place-order', {method: 'POST', body: 'x'});
          await new Promise((done) => { const x = new XMLHttpRequest(); x.onloadend = done;
            x.open('POST', '/checkout/place-order'); x.send('y'); });
          return r.status;
        }"""
    )
    assert status == 200
    await anyio.sleep(0.5)
    assert not [e for e in env.audit() if e["event"] == "checkout_submitted"]
    assert not env.sender.calls


# ── hand-offs ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_handoff_done_declined_and_no_answer(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/checkout/review"))
    summary = "2 items, 34.90 EUR, delivery Tuesday"
    h = await env.call("request_handoff", {"task": task, "tab": tab, "summary": summary})
    await wait_for(lambda: env.sender.calls)
    push = env.sender.payloads()[0]
    assert summary in push["body"] and push["url"] == f"/browser/h/{h['handoff']}"
    env.service.handoffs.answer(h["handoff"], done=True, by="alfred@gmail.com")
    assert (await env.call("wait_handoff", {"task": task, "handoff": h["handoff"]}))["status"] == "done"

    d = await env.call("request_handoff", {"task": task, "tab": tab, "summary": "solve the challenge"})
    env.service.handoffs.answer(d["handoff"], done=False)
    assert (await env.call("wait_handoff", {"task": task, "handoff": d["handoff"]}))["status"] == "declined"

    n = await env.call("request_handoff", {"task": task, "tab": tab, "summary": "sign in"})
    env.clock.t += 25 * 3600
    env.service.handoffs.expire()
    result = await env.call("wait_handoff", {"task": task, "handoff": n["handoff"], "wait_seconds": 0})
    assert result["status"] == "no answer" and "stays open" in result["message"]


@pytest.mark.anyio
async def test_wait_is_below_the_bridge_limit(env):
    r = await env.rpc("tools/list")
    tool = next(t for t in r.json()["result"]["tools"] if t["name"] == "wait_handoff")
    assert tool["inputSchema"]["properties"]["wait_seconds"]["default"] < 120
    from lollo_browser.config import MAX_WAIT_SECONDS

    assert MAX_WAIT_SECONDS < 120
