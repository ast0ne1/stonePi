from fastapi import FastAPI
from fastapi.testclient import TestClient

from stonepi_auth.platform_lock import PlatformLock, add_platform_lock


def _lock(tmp_path, secret, **kw):
    return PlatformLock(lambda: secret, is_dev=lambda: False, platform_dir=tmp_path, **kw)


def test_locks_platform_install_without_secret(tmp_path):
    assert _lock(tmp_path, "").locked() is True
    assert _lock(tmp_path, "s3cret").locked() is False


def test_solo_and_dev_runs_stay_open(tmp_path):
    assert _lock(tmp_path / "missing", "").locked() is False
    assert PlatformLock(lambda: "", is_dev=lambda: True, platform_dir=tmp_path).locked() is False


def test_unreadable_secret_counts_as_missing(tmp_path):
    def boom():
        raise OSError("vault unreadable")

    assert PlatformLock(boom, is_dev=lambda: False, platform_dir=tmp_path).locked() is True


def test_answer_is_cached_until_ttl(tmp_path):
    calls = []
    lock = PlatformLock(lambda: calls.append(1) or "", is_dev=lambda: False, platform_dir=tmp_path, ttl=60)
    assert lock.locked() and lock.locked()
    assert len(calls) == 1
    lock.clear()
    lock.locked()
    assert len(calls) == 2


def test_middleware_returns_503_but_keeps_health_and_static(tmp_path):
    app = FastAPI()

    @app.get("/")
    def home():
        return {"ok": True}

    @app.get("/healthz")
    def health():
        return {"ok": True}

    @app.get("/static/app.css")
    def css():
        return {"ok": True}

    lock = add_platform_lock(app, lambda: "", is_dev=lambda: False, platform_dir=tmp_path)
    client = TestClient(app)
    assert client.get("/").status_code == 503
    assert client.get("/healthz").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    lock.secret_getter = lambda: "s3cret"
    lock.clear()
    assert client.get("/").status_code == 200
