"""Process entry: serve EventTrakr over HTTP or HTTPS based on settings."""

from __future__ import annotations

import logging
import sys

from app import create_app
from app.config import env
from app.db import SessionLocal, init_db
from app.services import settings, tls

logger = logging.getLogger("eventtrakr.serve")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    init_db()
    app = create_app()

    use_https = False
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None

    with SessionLocal() as db:
        if settings.https_enabled(db):
            try:
                tls.ensure_certificate()
                ssl_certfile, ssl_keyfile = tls.ssl_file_paths()
                use_https = True
            except Exception:
                logger.exception("HTTPS enabled but certificate could not be prepared.")
                sys.exit(1)

    if use_https and ssl_certfile and ssl_keyfile:
        logger.info("Serving HTTPS on https://%s:%s", env.host, env.port)
        from werkzeug.serving import run_simple
        run_simple(
            env.host,
            env.port,
            app,
            ssl_context=(ssl_certfile, ssl_keyfile),
            threaded=True,
        )
    else:
        logger.info("Serving HTTP on http://%s:%s", env.host, env.port)
        from waitress import serve
        serve(app, host=env.host, port=env.port, threads=6)


if __name__ == "__main__":
    main()
