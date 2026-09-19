from __future__ import annotations

import logging

import uvicorn

from app.config import env


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("stonepi.dashboard").info("Serving dashboard on %s:%s", env.host, env.port)
    uvicorn.run("app.main:app", host=env.host, port=env.port)


if __name__ == "__main__":
    main()
