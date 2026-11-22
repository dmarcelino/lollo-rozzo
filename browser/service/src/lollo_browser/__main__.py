"""Entry point: python -m lollo_browser"""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

import uvicorn
from lollo_common.auth import Authenticator

from .app import create_app
from .audit import AuditLog
from .config import ConfigError, Settings
from .engine import RemoteEngine
from .guard import EngineNetwork
from .handoffs import Handoffs
from .page import page_routes
from .push import Pusher, Subscriptions
from .scrub import SCRUBBER, setup_logging
from .sessions import Sessions
from .tasks import BrowserService


def main() -> None:
    setup_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"lollo-browser: {e}", file=sys.stderr)
        sys.exit(2)
    # The secret is the engine's WebSocket path: scrubbed from every log line.
    SCRUBBER.add(settings.engine_secret)
    network = EngineNetwork(settings.engine_network, urlsplit(settings.engine_url).hostname or "")
    network.detect()
    pusher = Pusher(settings.page, Subscriptions(settings.data_dir))
    service = BrowserService(
        RemoteEngine(settings.engine_url, settings.engine_secret),
        Sessions(settings.data_dir),
        AuditLog(settings.data_dir),
        Handoffs(),
        settings.limits,
        on_connected=network.detect,
        pusher=pusher,
    )
    routes = page_routes(service, settings.page, Authenticator(settings.page), pusher)
    uvicorn.run(
        create_app(service, network, routes),
        host="0.0.0.0",
        port=8000,
        log_config=None,
        # No access log: URLs and paths are not needed, and keep logs small.
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
