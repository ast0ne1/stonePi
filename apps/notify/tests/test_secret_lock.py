from fastapi.testclient import TestClient

import app.main as main
import app.routes as routes


def _lock(monkeypatch, platform_dir, secret):
    monkeypatch.setattr(main.platform_lock, "is_dev", lambda: False)
    monkeypatch.setattr(main.platform_lock, "platform_dir", platform_dir)
    monkeypatch.setattr(main.platform_lock, "ttl", 0)
    monkeypatch.setattr(routes, "_session_secret", lambda: secret)


def test_platform_install_without_session_secret_is_locked(tmp_path, monkeypatch):
    _lock(monkeypatch, tmp_path, "")
    client = TestClient(main.app)
    assert client.get("/").status_code == 503
    assert client.get("/healthz").status_code == 200


def test_solo_run_without_platform_dir_stays_open(tmp_path, monkeypatch):
    _lock(monkeypatch, tmp_path / "missing", "")
    assert not main.platform_lock.locked()
