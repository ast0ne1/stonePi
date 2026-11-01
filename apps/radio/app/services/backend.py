"""Select live Odio client or in-process mock."""

from __future__ import annotations

from app.config import env


def client():
    if env.mock:
        from app.services import odio_mock as mod
    else:
        from app.services import odio as mod
    return mod
