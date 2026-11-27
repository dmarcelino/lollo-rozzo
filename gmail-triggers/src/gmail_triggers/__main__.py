"""Entry point: python -m gmail_triggers"""

from __future__ import annotations

import os
import sys

import httpx
import uvicorn
from lollo_common.auth import Authenticator
from lollo_common.notify import Notifier

from . import logs
from .app import create_app
from .config import ConfigError, Settings
from .google import Google
from .poller import Poller
from .store import Store


def main() -> None:
    logs.setup()
    try:
        settings = Settings.from_env()
        # New-sender notices go through lollo-notifications, with this service's
        # own token (NOTIFY_TOKEN).
        notifier = Notifier.from_env("gmail-triggers")
    except (ConfigError, ValueError) as e:
        print(f"gmail-triggers: {e}", file=sys.stderr)
        sys.exit(2)
    os.makedirs(settings.data_dir, mode=0o700, exist_ok=True)
    store = Store(settings.db_path)
    client = httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=False)
    google = Google(settings.policy_dir, settings.creds_dir, client)
    poller = Poller(store, google, settings.poll_seconds, notifier)
    app = create_app(
        store,
        google,
        Authenticator(settings),
        settings.executor_token,
        poller=poller,
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
