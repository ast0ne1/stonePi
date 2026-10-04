"""Uvicorn entry for StonePi recovery."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8099"))
    uvicorn.run("app.main:app", host=host, port=port, log_level="warning", timeout_keep_alive=15, access_log=False)


if __name__ == "__main__":
    main()
