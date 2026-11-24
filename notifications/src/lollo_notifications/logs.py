"""Logging to stdout. A notification's title and body are never logged, only
its source, tag and outcome; libraries that could log a request or a payload
stay quiet."""

from __future__ import annotations

import logging
import sys


def setup() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in ("httpcore", "httpx", "urllib3", "jwt", "pywebpush", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
