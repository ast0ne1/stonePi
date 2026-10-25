"""Process-wide singletons (store, audit). Tests point them at a temp folder with ``reset``."""

from __future__ import annotations

from pathlib import Path

from stonepi_audit import AuditLog

from app.config import DATA_DIR
from app.store import Store

_store: Store | None = None
_audit: AuditLog | None = None


def reset(data_dir: Path | None = None) -> None:
    global _store, _audit
    root = Path(data_dir or DATA_DIR)
    _store = Store(root / "agentlink.db")
    _audit = AuditLog(root / "audit.db", retention_days=90)


def store() -> Store:
    if _store is None:
        reset()
    return _store  # type: ignore[return-value]


def audit() -> AuditLog:
    if _audit is None:
        reset()
    return _audit  # type: ignore[return-value]
