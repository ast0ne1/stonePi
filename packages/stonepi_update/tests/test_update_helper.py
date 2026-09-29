"""deploy/stonepi-update-helper.py — the root side of Settings → Updates.

Loaded straight from deploy/ with its paths pointed at a temp tree; systemctl, pip and the
health check are stubbed, so this runs anywhere (the real thing only runs as root on the Pi).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

HELPER_PATH = Path(__file__).resolve().parents[3] / "deploy" / "stonepi-update-helper.py"

CATALOG = """
APP_CATALOG = [
    {"id": "dashboard", "unit": "stonepi-dashboard", "port": 8010, "health": "/healthz", "ships_with": "platform"},
    {"id": "pinboard", "unit": "stonepi-pinboard", "port": 8004, "health": "/healthz"},
]
"""


def _load_helper():
    spec = importlib.util.spec_from_file_location("stonepi_update_helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_app(root: Path, version: str, *, extra: dict[str, str] | None = None) -> None:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "app" / "main.py").write_text(f"VERSION = {version!r}\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    for rel, body in (extra or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _zip(path: Path, version: str, *, members: dict[str, str] | None = None) -> str:
    files = members if members is not None else {
        "app/__init__.py": f'__version__ = "{version}"\n',
        "app/main.py": f"VERSION = {version!r}\n",
        "app/new_module.py": "NEW = True\n",
        "requirements.txt": "fastapi\n",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def helper(tmp_path, monkeypatch):
    mod = _load_helper()
    root = tmp_path / "opt"
    catalog = root / "packages" / "stonepi_auth" / "stonepi_auth" / "catalog.py"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(CATALOG, encoding="utf-8")
    monkeypatch.setattr(mod, "STONEPI_ROOT", root)
    monkeypatch.setattr(mod, "CATALOG_FILE", catalog)
    monkeypatch.setattr(mod, "DOWNLOADS_ROOT", tmp_path / "dash-updates")
    monkeypatch.setattr(mod, "STATE_ROOT", tmp_path / "state")
    calls = {"pip": 0, "restart": 0, "health_ok": True}
    monkeypatch.setattr(mod, "install_requirements", lambda app_dir, unit: calls.__setitem__("pip", calls["pip"] + 1))

    def fake_restart(unit, entry):
        calls["restart"] += 1
        if not calls["health_ok"]:
            raise mod.HelperError(f"{unit} did not pass its health check within 45s.")

    monkeypatch.setattr(mod, "restart_and_wait", fake_restart)
    app_dir = root / "apps" / "pinboard"
    _write_app(app_dir, "0.0.6", extra={"app/stale.py": "OLD = True\n"})
    mod.calls = calls
    mod.app_dir = app_dir
    mod.downloads = tmp_path / "dash-updates" / "pinboard" / "staging"
    return mod


def test_apply_installs_new_code_and_keeps_a_copy(helper):
    zip_path = helper.downloads / "pinboard-0.0.7.zip"
    sha = _zip(zip_path, "0.0.7")
    result = helper.cmd_apply("pinboard", str(zip_path), sha)
    assert result["ok"] is True and result["version"] == "0.0.7" and result["previous"] == "0.0.6"
    assert helper.read_version(helper.app_dir) == "0.0.7"
    assert (helper.app_dir / "app" / "new_module.py").exists()
    assert not (helper.app_dir / "app" / "stale.py").exists()  # files the release dropped are removed
    saved = helper.STATE_ROOT / "pinboard" / "previous"
    assert helper.read_version(saved) == "0.0.6" and (saved / "app" / "stale.py").exists()
    assert helper.calls == {"pip": 1, "restart": 1, "health_ok": True}


def test_failed_health_check_puts_the_old_code_back(helper):
    helper.calls["health_ok"] = False
    zip_path = helper.downloads / "pinboard-0.0.7.zip"
    sha = _zip(zip_path, "0.0.7")
    result = helper.cmd_apply("pinboard", str(zip_path), sha)
    assert result["ok"] is False
    assert "health check" in result["message"]
    assert helper.read_version(helper.app_dir) == "0.0.6"
    assert (helper.app_dir / "app" / "stale.py").exists()
    assert not (helper.app_dir / "app" / "new_module.py").exists()


def test_rollback_restores_the_saved_copy(helper, monkeypatch):
    zip_path = helper.downloads / "pinboard-0.0.7.zip"
    helper.cmd_apply("pinboard", str(zip_path), _zip(zip_path, "0.0.7"))
    result = helper.cmd_rollback("pinboard")
    assert result["ok"] is True and result["version"] == "0.0.6" and result["previous"] == "0.0.7"
    assert helper.read_version(helper.app_dir) == "0.0.6"
    assert helper.cmd_status("pinboard")["version"] == "0.0.6"


def test_rollback_without_a_saved_copy(helper):
    with pytest.raises(helper.HelperError, match="No saved copy"):
        helper.cmd_rollback("pinboard")


def test_checksum_must_match(helper):
    zip_path = helper.downloads / "pinboard-0.0.7.zip"
    _zip(zip_path, "0.0.7")
    with pytest.raises(helper.HelperError, match="checksum"):
        helper.cmd_apply("pinboard", str(zip_path), "0" * 64)
    with pytest.raises(helper.HelperError, match="checksum is required"):
        helper.cmd_apply("pinboard", str(zip_path), "not-a-hash")
    assert helper.read_version(helper.app_dir) == "0.0.6"


def test_zip_must_come_from_dashboards_download_folder(helper, tmp_path):
    elsewhere = tmp_path / "elsewhere" / "pinboard-0.0.7.zip"
    sha = _zip(elsewhere, "0.0.7")
    with pytest.raises(helper.HelperError, match="Dashboard downloaded"):
        helper.cmd_apply("pinboard", str(elsewhere), sha)
    other_app = tmp_path / "dash-updates" / "studio" / "staging" / "x.zip"
    sha = _zip(other_app, "0.0.7")
    with pytest.raises(helper.HelperError, match="Dashboard downloaded"):
        helper.cmd_apply("pinboard", str(other_app), sha)


def test_path_traversal_in_zip_is_refused(helper):
    zip_path = helper.downloads / "evil.zip"
    sha = _zip(zip_path, "0.0.7", members={
        "app/__init__.py": '__version__ = "0.0.7"\n',
        "app/main.py": "",
        "requirements.txt": "",
        "../../etc/cron.d/evil": "* * * * * root id\n",
    })
    with pytest.raises(helper.HelperError, match="Unsafe path"):
        helper.cmd_apply("pinboard", str(zip_path), sha)
    assert helper.read_version(helper.app_dir) == "0.0.6"


def test_not_newer_is_refused(helper):
    zip_path = helper.downloads / "pinboard-0.0.5.zip"
    sha = _zip(zip_path, "0.0.5")
    with pytest.raises(helper.HelperError, match="not newer"):
        helper.cmd_apply("pinboard", str(zip_path), sha)


def test_only_updatable_catalog_apps(helper):
    with pytest.raises(helper.HelperError, match="updates with the StonePi platform"):
        helper.catalog_entry("dashboard")
    with pytest.raises(helper.HelperError, match="not a StonePi app"):
        helper.catalog_entry("nginx")
    with pytest.raises(helper.HelperError, match="Invalid app id"):
        helper.catalog_entry("../pinboard")


def test_main_always_prints_json(helper, capsys):
    assert helper.main(["rollback", "pinboard"]) == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False and "No saved copy" in out["message"]
    assert helper.main(["bogus"]) == 1
    assert "usage" in json.loads(capsys.readouterr().out.strip())["message"]
