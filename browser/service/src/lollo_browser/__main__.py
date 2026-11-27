"""Entry point: python -m lollo_browser"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from lollo_common.auth import Authenticator
from lollo_common.notify import Notifier

from .app import create_app
from .audit import AuditLog
from .config import ConfigError, Settings
from .engine import RemoteEngine
from .guard import EngineNetwork
from .handoffs import Handoffs
from .page import page_routes
from .scrub import SCRUBBER, setup_logging
from .sessions import Sessions
from .tasks import BrowserService

log = logging.getLogger("lollo_browser")

# Where the browser kept its own push subscriptions before the notifications
# service held them all. Nothing is sent to them any more.
OLD_SUBSCRIPTIONS = "push.json"


def remove_old_subscriptions(data_dir: str | Path) -> bool:
    """Deletes the old push.json from browser-data; True when there was one."""
    try:
        (Path(data_dir) / OLD_SUBSCRIPTIONS).unlink()
    except FileNotFoundError:
        return False
    log.info("old push subscriptions removed: notifications go through lollo-notifications")
    return True


def main() -> None:
    setup_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        print(f"lollo-browser: {e}", file=sys.stderr)
        sys.exit(2)
    try:
        notifier = Notifier.from_env("browser")
    except ValueError as e:
        print(f"lollo-browser: {e}", file=sys.stderr)
        sys.exit(2)
    remove_old_subscriptions(settings.data_dir)
    # The secret is the engine's WebSocket path: scrubbed from every log line,
    # pinned so that typed values never push it out.
    SCRUBBER.pin(settings.engine_secret)
    network = EngineNetwork(settings.engine_network, urlsplit(settings.engine_url).hostname or "")
    network.detect()
    service = BrowserService(
        RemoteEngine(settings.engine_url, settings.engine_secret),
        Sessions(settings.data_dir, deny_hosts=settings.deny_hosts),
        AuditLog(settings.data_dir),
        Handoffs(),
        settings.limits,
        on_connected=network.detect,
        notifier=notifier,
    )
    routes = page_routes(service, settings.page, Authenticator(settings.page))
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
