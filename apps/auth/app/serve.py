from __future__ import annotations

import logging

import uvicorn

from app.config import env


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("stonepi.auth").info("Serving auth on %s:%s", env.host, env.port)
    uvicorn.run("app.main:app", host=env.host, port=env.port, timeout_keep_alive=15, access_log=False)


if __name__ == "__main__":
    main()
