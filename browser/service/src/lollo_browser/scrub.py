"""The value scrub: the second line of masking (design decision 6). Every value
typed through type_secret or type_totp, the authenticator key, and the engine
secret are cut out of every tool result, error, audit entry and log line, in
any case and with spaces or dashes inserted, when they are 6 characters or more
(separators aside), like vault-run's rule. Shorter values (a card's expiry
month or year, its security code) are too common to cut out of everything:
"5" would take every 5 out of every result, ids and refs included. They are
protected by field-based masking (masking.py) only, which does not depend on
knowing the value. This line catches a page that copies a typed value into its
text, title or an error message.

Dict keys are never scrubbed, nor the service's own ids under their keys
(task, tab, closed, handoff): they are made by the service, never by a page,
and an agent can't work without them.

Matching is substring search over the text once normalised (separators
removed, lower-cased), with no regular expression built from the values, so
neither adding a value nor scrubbing a long text grows with the values'
length beyond a plain search."""

from __future__ import annotations

import logging
import re
import sys
import threading
from collections import OrderedDict
from typing import Any

MASK = "[masked]"
# Values are kept (in memory only) until the service restarts, so a kept
# sign-in page that shows a value in a later task is scrubbed too. The oldest
# are dropped past this many.
MAX_VALUES = 512
# Values shorter than this, separators aside, are left to field masking
# (vault-run's MIN_SCRUB_LENGTH, and the vault's log redaction, use 6 too).
MIN_LENGTH = 6
# Between two characters of a value, a page may insert these, any number of them.
_SEPARATOR = re.compile(r"[\s\-\u00a0\u2010-\u2015]+")
_NOT_SEPARATOR = re.compile(r"[^\s\-\u00a0\u2010-\u2015]+")
# The service's own ids, by the key they are under: never scrubbed.
_IDS = {
    "task": re.compile(r"task-[0-9a-f]{12}"),
    "tab": re.compile(r"t\d{1,6}"),
    "closed": re.compile(r"t\d{1,6}"),
    "handoff": re.compile(r"h[0-9a-f]{12}"),
}


def normalise(value: str) -> str:
    """How a value is kept and matched: no separators, lower-cased."""
    return _SEPARATOR.sub("", value).lower()


def _normalised(text: str) -> tuple[str, list[int]]:
    """The text normalised, and for each of its characters the index of the
    original character it came from."""
    lowered = text.lower()
    if len(lowered) == len(text):
        runs = [(m.start(), m.end()) for m in _NOT_SEPARATOR.finditer(text)]
        return "".join(lowered[a:b] for a, b in runs), [i for a, b in runs for i in range(a, b)]
    # A character whose lower case is longer (rare): one at a time.
    chars: list[str] = []
    where: list[int] = []
    for i, c in enumerate(text):
        if _SEPARATOR.fullmatch(c):
            continue
        low = c.lower()
        chars.append(low)
        where.extend([i] * len(low))
    return "".join(chars), where


class Scrubber:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: OrderedDict[str, None] = OrderedDict()
        self._forms: tuple[str, ...] = ()

    def add(self, *values: str) -> None:
        with self._lock:
            for value in values:
                # Separators inside the value itself are optional too: a key
                # stored as "ABCD EFGH" matches "ABCDEFGH" on a page, and a card
                # number as "4111 1111 1111 1111" matches it written any way.
                value = normalise(value or "")
                if len(value) < MIN_LENGTH:
                    continue
                self._values[value] = None
                self._values.move_to_end(value)
            while len(self._values) > MAX_VALUES:
                self._values.popitem(last=False)
            self._forms = tuple(self._values)

    def text(self, text: str) -> str:
        forms = self._forms
        if not forms or not text or len(text) < MIN_LENGTH:
            return text
        norm, where = _normalised(text)
        spans: list[tuple[int, int]] = []
        for form in forms:
            k = norm.find(form)
            while k >= 0:
                spans.append((where[k], where[k + len(form) - 1] + 1))
                k = norm.find(form, k + len(form))
        if not spans:
            return text
        spans.sort()
        out: list[str] = []
        pos = 0
        start, end = spans[0]
        for a, b in spans[1:]:
            if a < end:  # overlapping matches are masked as one
                end = max(end, b)
                continue
            out += [text[pos:start], MASK]
            pos, (start, end) = end, (a, b)
        out += [text[pos:start], MASK, text[end:]]
        return "".join(out)

    def deep(self, value: Any) -> Any:
        """Every string in a JSON-like value scrubbed, except dict keys and the
        service's own ids under their keys."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self._field(k, v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.deep(v) for v in value]
        return value

    def _field(self, key: Any, value: Any) -> Any:
        pattern = _IDS.get(key) if isinstance(key, str) else None
        if pattern is not None and isinstance(value, str) and pattern.fullmatch(value):
            return value
        return self.deep(value)


SCRUBBER = Scrubber()


class ScrubFilter(logging.Filter):
    """Rewrites every log record through the scrubber, traceback included."""

    def __init__(self, scrubber: Scrubber) -> None:
        super().__init__()
        self._scrubber = scrubber

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        cleaned = self._scrubber.text(message)
        exc = self._scrubber.text(record.exc_text) if record.exc_text else record.exc_text
        if cleaned != message or exc != record.exc_text:
            record.msg, record.args, record.exc_text, record.exc_info = cleaned, None, exc, None
        return True


def setup_logging(scrubber: Scrubber = SCRUBBER) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(ScrubFilter(scrubber))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # Libraries that could log request, page or protocol details stay quiet.
    for name in ("httpcore", "httpx", "mcp", "uvicorn.access", "asyncio", "playwright"):
        logging.getLogger(name).setLevel(logging.WARNING)
