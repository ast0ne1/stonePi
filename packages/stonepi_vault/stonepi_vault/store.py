from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_DEFAULT_DIR = Path(os.environ.get("STONEPI_VAULT_DIR", "") or "/var/lib/stonepi/vault")
_CACHE_TTL_SECONDS = 30.0


class Vault:
    """Encrypted key/value store for StonePi platform secrets."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _DEFAULT_DIR
        self.root.mkdir(parents=True, exist_ok=True)
        self._key_path = self.root / "vault.key"
        self._store_path = self.root / "secrets.enc"
        self._cache: dict[str, str] | None = None
        self._cache_at: float = 0.0

    def _secure_file(self, path: Path) -> None:
        """Group-readable (640) so stonepi-vault members can read; owner writes."""
        try:
            os.chmod(path, 0o640)
        except OSError:
            pass
        if not hasattr(os, "chown"):
            return
        try:
            gid = self.root.stat().st_gid
            os.chown(path, -1, gid)
        except OSError:
            pass

    def _fernet(self) -> Fernet:
        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self._key_path.write_bytes(key)
            self._secure_file(self._key_path)
        return Fernet(key)

    def _invalidate_cache(self) -> None:
        self._cache = None
        self._cache_at = 0.0

    def _load(self) -> dict[str, str]:
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_at) < _CACHE_TTL_SECONDS:
            return dict(self._cache)
        data = self._load_disk()
        self._cache = dict(data)
        self._cache_at = now
        return dict(data)

    def _load_disk(self) -> dict[str, str]:
        if not self._store_path.exists():
            return {}
        try:
            raw = self._fernet().decrypt(self._store_path.read_bytes())
            data = json.loads(raw.decode("utf-8"))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, InvalidToken, json.JSONDecodeError, ValueError):
            return {}

    def _save(self, data: dict[str, str]) -> None:
        payload = self._fernet().encrypt(json.dumps(data, indent=2).encode("utf-8"))
        self._store_path.write_bytes(payload)
        self._secure_file(self._store_path)
        # Keep the key at the same mode in case an older install left it 600.
        if self._key_path.exists():
            self._secure_file(self._key_path)
        self._invalidate_cache()

    def list_keys(self) -> list[str]:
        return sorted(self._load().keys())

    def get(self, key: str, default: str = "") -> str:
        return self._load().get(key, default)

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[str(key)] = str(value)
        self._save(data)

    def delete(self, key: str) -> bool:
        data = self._load()
        if key not in data:
            return False
        del data[key]
        self._save(data)
        return True

    def get_or_env(self, key: str, env_name: str | None = None, default: str = "") -> str:
        stored = self.get(key)
        if stored:
            return stored
        return os.environ.get(env_name or key, default)


_vault: Vault | None = None


def configure(root: Path | None = None) -> Vault:
    global _vault
    _vault = Vault(root)
    return _vault


def get_vault() -> Vault:
    global _vault
    if _vault is None:
        # Prefer STONEPI_VAULT_DIR; otherwise Pi default (/var/lib/...), not a repo-relative path.
        configured = (os.environ.get("STONEPI_VAULT_DIR") or "").strip()
        root = Path(configured).expanduser() if configured else _DEFAULT_DIR
        _vault = Vault(root)
    return _vault


def get_secret(key: str, env_name: str | None = None, default: str = "") -> str:
    return get_vault().get_or_env(key, env_name=env_name, default=default)


def set_secret(key: str, value: str) -> None:
    get_vault().set(key, value)
