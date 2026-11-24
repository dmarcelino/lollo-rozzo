"""Field-based masking, the first line of design decision 6.

Playwright's ai-mode snapshot writes an input's value after its ref, password
fields included:  `- textbox "Password" [ref=e7]: hunter2`. When the line's
key holds ': ', ' #', a quote and the like, Playwright quotes the whole key
YAML-style:  `- 'textbox "Password: 8+ characters" [ref=e7]': hunter2`, with
a single quote inside doubled. Both forms are parsed. After every snapshot,
each line that carries a value (or, for a combobox, its options) for a
field-like role is checked against the live element, and its value replaced
when the element is a secret field (`is_secret_field`), in any frame. A line
whose element cannot be resolved is masked, and so is any line with a `[ref=`
that cannot be parsed: it fails closed.

A secret field is a password-type input; a field whose `autocomplete` names a
card's number, expiry or security code, a one-time code, or a current or new
password (cc-name, its parts and cc-type are not secret, and agents need to
read them); a field drawn with `-webkit-text-security` other than none; or a
field typed with type_secret or type_totp (marked with MARK, re-applied from
the element handles the service keeps before every snapshot and screenshot).
So card details and codes the person types during a take-over are masked too.

The attribute checks go through Playwright's own (utility) world, with the
same selector as the screenshot masks, so page script cannot change what they
see. The text-security check reads the computed style in the page's world: a
page can fake it only for its own field, whose value it already has. A page can
still remove MARK or the attributes from an element; the value scrub
(scrub.py) is the second line for that."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from playwright.async_api import ElementHandle, Locator, Page

from .scrub import MASK

MARK = "data-lollo-secret"
# autocomplete tokens (HTML's autofill field names) whose field is a secret.
SECRET_AUTOCOMPLETE = (
    "cc-number",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
    "cc-csc",
    "one-time-code",
    "current-password",
    "new-password",
)
MASK_SELECTOR = ", ".join(
    ["input[type=password i]", f"[{MARK}]", *(f'[autocomplete~="{t}" i]' for t in SECRET_AUTOCOMPLETE)]
)
# The same test, of one element (a locator chained from it).
_IS_SECRET = f":scope:is({MASK_SELECTOR})"
_TEXT_SECURITY_JS = """el => {
  const v = getComputedStyle(el).getPropertyValue('-webkit-text-security').trim().toLowerCase();
  return v !== '' && v !== 'none';
}"""
# Roles whose snapshot line can end with the element's value.
VALUE_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton"})
_CHECK_TIMEOUT_MS = 1_000

REF = re.compile(r"(?:f\d+)?e\d+")
_ITEM = re.compile(r"^(?P<indent>\s*)- (?P<body>.*)$")
# A line's key: role, then the quoted name, then attributes such as [ref=e7].
_KEY = re.compile(r'(?P<role>[a-z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)')
_ATTR_REF = re.compile(r" \[ref=([a-z0-9]+)\]")


@dataclass(frozen=True)
class Snapshot:
    text: str
    # ref -> a short description of the element, from its line: `textbox "Password"`.
    refs: dict[str, str]


def describe(role: str, name: str | None) -> str:
    return f'{role} "{name}"' if name else role


async def is_secret_field(locator: Locator) -> bool:
    """True for a password-type input, a card or code field by its autocomplete,
    a field drawn with text-security, or a field marked as typed with a secret."""
    if await locator.locator(_IS_SECRET).count():
        return True
    return bool(await locator.evaluate(_TEXT_SECURITY_JS, timeout=_CHECK_TIMEOUT_MS))


def _split(body: str) -> tuple[str, str | None] | None:
    """A list item's key and what follows its colon (None: no colon), or None
    when the item can't be parsed."""
    if body.startswith("'"):
        i, key = 1, []
        while True:
            j = body.find("'", i)
            if j < 0:
                return None  # no closing quote
            key.append(body[i:j])
            if body.startswith("'", j + 1):
                key.append("'")  # a doubled quote is one quote
                i = j + 2
                continue
            rest = body[j + 1 :]
            break
        if rest == "":
            return "".join(key), None
        if rest == ":" or rest.startswith(": "):
            return "".join(key), rest[1:]
        return None
    if body.startswith('"'):
        return None  # Playwright never writes this; whatever it is, it isn't parsed
    m = re.match(r"(?P<key>.*?)(?::(?P<rest> .*|$)|$)", body)
    assert m  # always matches
    return m["key"], m["rest"]


def _parse(body: str) -> tuple[str | None, str | None, str, str | None] | None:
    """(ref, name, role, value-or-None) of a list item; ref None for a line
    without one. None when the item can't be parsed."""
    split = _split(body)
    if split is None:
        return None
    key, rest = split
    m = _KEY.fullmatch(key)
    if not m:
        return None
    refs = _ATTR_REF.findall(m["attrs"])
    if len(refs) > 1:
        return None
    return (refs[0] if refs else None), m["name"], m["role"], rest


async def mask_snapshot(page: Any, raw: str) -> Snapshot:
    lines = raw.splitlines()
    out: list[str] = []
    refs: dict[str, str] = {}
    skip_deeper_than: int | None = None
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                continue  # the rest of a masked value (a block scalar) or its options
            skip_deeper_than = None
        item = _ITEM.match(line)
        parsed = _parse(item["body"]) if item else None
        if parsed is None:
            if "[ref=" in line:
                # Fail closed: a line with a ref we can't parse may hold a value.
                out.append(f"{item['indent'] if item else ' ' * indent}- {MASK}")
                skip_deeper_than = indent
            else:
                out.append(line)
            continue
        ref, name, role, rest = parsed
        if ref is None:
            out.append(line)
            continue
        refs[ref] = describe(role, name)
        # A value after the colon, or nothing after it: children follow (a
        # combobox's options, one of them selected).
        if role not in VALUE_ROLES or rest is None:
            out.append(line)
            continue
        try:
            secret = await is_secret_field(page.get_by_ref(ref))
        except Exception:
            secret = True  # fail closed: a value we cannot place is masked
        if secret:
            # The line up to its colon: rest is all that follows it.
            out.append(f"{line[: len(line) - len(rest) - 1]}: {MASK}")
            skip_deeper_than = indent
        else:
            out.append(line)
    return Snapshot("\n".join(out), refs)


async def remark(handles: list[ElementHandle]) -> list[ElementHandle]:
    """Put MARK back on every field typed with a secret; drop handles whose
    element is gone. Returns the handles still attached."""
    alive = []
    for handle in handles:
        try:
            await handle.evaluate("(el, name) => el.setAttribute(name, '')", MARK)
        except Exception:
            continue
        alive.append(handle)
    return alive


def mask_locators(page: Page) -> list[Locator]:
    """For page.screenshot(mask=...): password-type inputs, card and code
    fields, and marked fields, in every frame. Fields drawn with text-security
    need no mask: the engine draws their value as discs."""
    return [frame.locator(MASK_SELECTOR) for frame in page.frames]
