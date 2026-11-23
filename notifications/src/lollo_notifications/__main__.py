"""Entry point: python -m lollo_notifications"""

from __future__ import annotations

import sys

import uvicorn
from lollo_common.auth import Authenticator

from . import logs
from .app import create_app
from .config import ConfigError, Settings
from .delivery import Pusher
from .store import Subscriptions


def main() -> None:
    logs.setup()
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"notifications: {e}", file=sys.stderr)
        sys.exit(2)
    pusher = Pusher(settings, Subscriptions(settings.data_dir, settings.vapid_public_key))
    app = create_app(settings, Authenticator(settings), pusher)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
        # No access log: nothing here needs paths, and none should hint at who was notified.
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
