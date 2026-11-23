"""Masking (masking.py) and the value scrub (scrub.py): snapshot lines that
Playwright quotes, fields that hold card details and codes whoever filled them,
the scrub's 6-character floor, the service's own ids, and the scrub's cost."""

from __future__ import annotations

import json
import random
import re
import string
import time

import pytest

from lollo_browser import masking
from lollo_browser.scrub import MASK, MAX_VALUES, Scrubber

from .conftest import ref_of

pytestmark = pytest.mark.anyio


def lines_with(text: str, label: str) -> list[str]:
    quoted = label.replace("'", "''")
    return [line for line in text.splitlines() if "[ref=" in line and (f'"{label}"' in line or f'"{quoted}"' in line)]


# ── A: lines Playwright quotes ───────────────────────────────────────────────


async def test_quoted_snapshot_lines_are_masked_and_their_refs_kept(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/mask-quoted"))
    snap = await env.snap(task, tab)
    text = snap["untrusted_page_data"]["snapshot"]
    # Playwright quotes the whole key when the name holds ': ', ' #', a quote...
    assert '\'textbox "Password: at least 8 characters" [ref=' in text, text
    for value in ("quoted-pw-4471", "it-s-pw-5582", "hash-pw-6693"):
        assert value not in text, text
    for label in ("Password: at least 8 characters", "it's: the code", "Pin #2"):
        (line,) = lines_with(text, label)
        assert line.endswith(f": {MASK}"), line
    # A quoted line whose field is not secret keeps its value.
    (plain,) = lines_with(text, "plain: x")
    assert plain.endswith(": visible-5521"), plain
    refs = env.service._tasks[task].tabs[tab].refs
    pw = ref_of(snap, "Password: at least 8 characters")
    pay = ref_of(snap, "Pay: 34,90 €")
    assert refs[pw] == 'textbox "Password: at least 8 characters"'
    assert refs[pay] == 'button "Pay: 34,90 €"'
    assert refs[ref_of(snap, "it''s: the code")] == 'textbox "it\'s: the code"'


class _NoPage:
    """A page whose elements can't be resolved."""

    def get_by_ref(self, ref: str):
        raise RuntimeError("detached")


@pytest.mark.parametrize(
    "line",
    [
        "- 'textbox \"a: b\" [ref=e3]'x: hunter2",  # junk after the closing quote
        '- \'textbox "a: b" [ref=e3]: hunter2',  # no closing quote
        '- "textbox \\"a: b\\" [ref=e3]": hunter2',  # a double-quoted key
        '- textbox [ref=e3] "x": hunter2',  # attributes before the name
    ],
)
async def test_unparsable_lines_with_a_ref_are_masked(line):
    raw = f"- generic [ref=e1]:\n  {line}\n    - child: hunter2\n  - text: after"
    snap = await masking.mask_snapshot(_NoPage(), raw)
    assert "hunter2" not in snap.text and MASK in snap.text and "after" in snap.text
    assert "e3" not in snap.refs


async def test_unresolvable_field_is_masked_whatever_the_quoting():
    raw = '- \'textbox "a: b" [ref=e3]\': hunter2\n- textbox "c" [ref=e4]: hunter3\n- \'button "Pay: 1 €" [ref=e5]\''
    snap = await masking.mask_snapshot(_NoPage(), raw)
    assert "hunter" not in snap.text
    assert snap.refs == {"e3": 'textbox "a: b"', "e4": 'textbox "c"', "e5": 'button "Pay: 1 €"'}


async def test_ref_in_a_name_is_not_taken_for_the_lines_ref():
    raw = '- button "x [ref=e9]" [ref=e5]\n- text: "see [ref=e7]"'
    snap = await masking.mask_snapshot(_NoPage(), raw)
    assert snap.refs == {"e5": 'button "x [ref=e9]"'} and snap.text == raw


# ── B: card details and codes, whoever typed them ────────────────────────────

FIELD_VALUES = {
    "Card number": "4000056655665556",
    "Expiry": "11/29",
    "Security code": "9876",
    "One-time code": "482913",
    "Current password": "curpw-1199",
    "New password": "newpw-2288",
    "Styled": "styled-7733",
    "Billing number": "5555444433331111",
    "Inner card": "4242424242424242",
}


async def test_card_and_code_fields_are_masked_in_every_frame(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/mask-fields"))
    page = env.page(task, tab)
    assert len(page.frames) == 2
    await page.frames[1].wait_for_load_state()
    text = (await env.snap(task, tab))["untrusted_page_data"]["snapshot"]
    for label, value in FIELD_VALUES.items():
        assert value not in text, (label, text)
        (line,) = lines_with(text, label)
        assert line.endswith(f": {MASK}"), line
    # The selected expiry month, an option line under its combobox, is hidden too.
    assert 'option "07"' not in text
    (month,) = lines_with(text, "Expiry month")
    assert month.endswith(f": {MASK}")
    # The cardholder's name and an ordinary field stay readable.
    assert "alfred cardholder" in text and "visible-4410" in text
    # Screenshots cover every such field in every frame; text-security fields
    # are drawn as discs by the engine itself.
    counts = [await loc.count() for loc in masking.mask_locators(page)]
    assert sum(counts) == 9, counts
    assert (await env.call_raw("screenshot", {"task": task, "tab": tab}))["content"][1]["type"] == "image"


async def test_secret_tokens_match_the_selector():
    for token in masking.SECRET_AUTOCOMPLETE:
        assert f'"{token}"' in masking.MASK_SELECTOR
    assert "cc-name" not in masking.SECRET_AUTOCOMPLETE and "cc-type" not in masking.SECRET_AUTOCOMPLETE


# ── C: the scrub's floor and the service's ids ───────────────────────────────


def test_short_values_are_left_to_field_masking():
    s = Scrubber()
    s.add("5", "27", "123", "12 34", "a-b-c-d-e")
    text = "task-5a27b123c5d6 t5 h0123456789ab e27 f1e123 exp 5/27 cvc 123 12 34 abcde"
    assert s.text(text) == text
    assert s.deep({"5": "27", "tabs": [{"tab": "t5"}]}) == {"5": "27", "tabs": [{"tab": "t5"}]}


def test_six_characters_and_more_are_scrubbed_in_every_form():
    s = Scrubber()
    s.add("482913", "Tr0ub4", "4111 1111 1111 4242")
    out = s.text(
        "code 482913 4 8 2 9 1 3 4-8-2-9-1-3 pw TR0UB4 tr0ub4 T-r-0-u-b-4 "
        "card 4111111111114242 4111-1111-1111-4242 4111 1111 1111 4242 end"
    )
    assert out == f"code {MASK} {MASK} {MASK} pw {MASK} {MASK} {MASK} card {MASK} {MASK} {MASK} end"


def test_punctuation_is_ignored_in_values_and_pages():
    """An address or a phone a page writes another way is still cut out."""
    s = Scrubber()
    s.add("Rua das Flores, 12", "912 345 678", "ab!cd#ef")
    for text in ("RUA DAS FLORES 12.", "Rua das Flores,12", "(+351) 912.345.678", "tel. 912/345/678"):
        assert "flores" not in s.text(text).lower() and "345" not in s.text(text), text
    assert s.text("(+351) 912.345.678") == f"(+351) {MASK}"
    # Password symbols are not separators: the value keeps its length and is
    # matched as typed, with spaces or dashes inserted.
    assert s.text("pw AB!CD#EF end") == f"pw {MASK} end"
    assert s.text("pw abcdef end") == "pw abcdef end"


def test_keys_are_never_scrubbed():
    s = Scrubber()
    s.add("sekrit-value")
    assert s.deep({"sekrit-value": ["sekrit-value"], "x": {"SEKRIT-VALUE": 1}}) == {
        "sekrit-value": [MASK],
        "x": {"SEKRIT-VALUE": 1},
    }


def test_the_services_ids_are_never_scrubbed():
    s = Scrubber()
    s.add("a1b2c3d4e5f6", "f12e345")
    ids = {"task": "task-a1b2c3d4e5f6", "handoff": "ha1b2c3d4e5f6", "tab": "t1", "closed": "t2"}
    assert s.deep({**ids, "tabs": [{"tab": "t1", "url": "https://x.test/a1b2c3d4e5f6"}]}) == {
        **ids,
        "tabs": [{"tab": "t1", "url": f"https://x.test/{MASK}"}],
    }
    # Only id-shaped values under those keys: anything else is scrubbed.
    assert s.deep({"task": "a1b2c3d4e5f6", "note": "task-a1b2c3d4e5f6"}) == {"task": MASK, "note": f"task-{MASK}"}


async def test_short_secrets_keep_ids_and_refs_and_are_masked_by_field(env, sites):
    task = await env.task()
    tab = await env.tab(task, sites.https("shop.example.com", "/pay"))
    snap = await env.snap(task, tab)
    for label, value in (("CVC", "123"), ("Expiry", "5"), ("Card number", "27")):
        result = await env.call(
            "type_secret",
            {"task": task, "tab": tab, "ref": ref_of(snap, label), "text": value, "expect_site": "shop.example.com"},
        )
        assert result["task"] == task and result["tab"] == tab
    again = await env.snap(task, tab)
    assert again["task"] == task and again["tab"] == tab and again["tabs"] == [{"tab": tab, "url": again["url"]}]
    text = again["untrusted_page_data"]["snapshot"]
    assert MASK not in re.sub(r": \[masked\]$", "", text, flags=re.M)  # nothing scrubbed but the values
    for label in ("CVC", "Expiry", "Card number"):
        assert ref_of(again, label) == ref_of(snap, label)
        (line,) = lines_with(text, label)
        assert line.endswith(f": {MASK}"), line
    assert task in json.dumps(env.audit())


# ── D: the scrub's cost ──────────────────────────────────────────────────────


def test_scrub_stays_cheap_with_many_long_values():
    rng = random.Random(7)
    alphabet = string.ascii_letters + string.digits
    s = Scrubber()
    values = ["".join(rng.choices(alphabet, k=rng.choice((8, 20, 64, 2_000, 10_000)))) for _ in range(MAX_VALUES)]
    started = time.monotonic()
    for v in values:
        s.add(v)
    added = time.monotonic() - started
    text = "".join(rng.choices(alphabet + " -", k=60_000))
    text = text[:30_000] + " " + "-".join(values[-3]) + " " + text[30_000:]
    started = time.monotonic()
    out = s.text(text)
    scrubbed = time.monotonic() - started
    assert values[-3] not in re.sub(r"[ -]", "", out) and MASK in out
    assert added < 2 and scrubbed < 2, (added, scrubbed)
