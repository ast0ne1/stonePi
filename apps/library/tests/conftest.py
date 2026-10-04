import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Isolated data dir before app.config is imported.
_TMP = Path(tempfile.mkdtemp(prefix="library-tests-"))
os.environ["STONEPI_DATA_DIR"] = str(_TMP)
os.environ["STONEPI_SESSION_SECRET"] = ""
os.environ["LIBRARY_BACKUP_STAMP"] = str(_TMP / "last-usb-backup.txt")
os.environ["LIBRARY_BACKUP_CONF"] = str(_TMP / "backup.conf")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def fresh_data():
    from app import db
    from app.services import kiwix

    for child in _TMP.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()
    db.init_db()
    kiwix.forget_status()
    yield _TMP


def make_zim(path: Path, size: int = 4096, uid: bytes = b"\x11" * 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = b"ZIM\x04" + b"\x06\x00\x01\x00" + uid
    path.write_bytes(head + b"\0" * (size - len(head)))
    return path
