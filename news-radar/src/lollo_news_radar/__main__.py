"""Entry point: python -m lollo_news_radar"""

from __future__ import annotations

import sys

import uvicorn
from lollo_common.auth import Authenticator
from lollo_common.notify import Notifier

from . import logs
from .app import create_app
from .config import ConfigError, Settings
from .fetch import Fetcher
from .service import Service
from .store import Store


def main() -> None:
    logs.setup()
    try:
        settings = Settings.from_env()
        # Notices of monitors and moves to confirm go through lollo-notifications,
        # with this service's own token (NOTIFY_TOKEN).
        notifier = Notifier.from_env("news-radar")
    except (ConfigError, ValueError) as e:
        print(f"news-radar: {e}", file=sys.stderr)
        sys.exit(2)
    service = Service(Store(settings.db_path), Fetcher(), settings, notifier)
    app = create_app(service, Authenticator(settings), settings.executor_token)
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
