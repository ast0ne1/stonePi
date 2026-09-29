from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from .destinations import configure as configure_destinations
from .history import configure_history
from .ingest import PeopleFn, configure_ingest
from .subscriptions import configure_subscriptions

logger = logging.getLogger("stonepi.notify")

_scheduler_started = False
_lock = threading.Lock()
_push_fn: Callable[[], dict] | None = None


def configure(
    *,
    data_dir: Path,
    vault_token_fn: Callable[[], str] | None = None,
    webhook_url_fn: Callable[[], str] | None = None,
    push_trmnl_fn: Callable[[], dict] | None = None,
    people_fn: PeopleFn | None = None,
) -> None:
    global _push_fn
    data = Path(data_dir)
    configure_destinations(data_dir=data, vault_token_fn=vault_token_fn, webhook_url_fn=webhook_url_fn)
    configure_history(data)
    configure_ingest(data, people_fn=people_fn)
    configure_subscriptions(data)
    _push_fn = push_trmnl_fn


def _scheduler_loop() -> None:
    """Every minute, let the Display push fn decide which Displays are due."""
    import time

    while True:
        try:
            if _push_fn:
                _push_fn()
        except Exception:
            logger.exception("Notify scheduler error")
        time.sleep(60)


def start_scheduler() -> None:
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        if _push_fn is None:
            return
        thread = threading.Thread(target=_scheduler_loop, name="stonepi-notify", daemon=True)
        thread.start()
        _scheduler_started = True
