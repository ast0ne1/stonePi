from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import make_zim

from app import db
from app.config import DEFAULT_CONTENT_DIR, GIB, LIBRARY_XML
from app.services import backup, catalog, content, kiwix, storage

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
  <entry>
    <id>urn:uuid:c22094d0-6ad1-6360-36b7-dab953595143</id>
    <title>Wikipedia</title><updated>2026-06-17T00:00:00Z</updated>
    <summary>The free encyclopedia</summary><language>eng</language>
    <name>wikipedia_en_all</name><flavour>nopic</flavour>
    <dc:issued>2026-06-17T00:00:00Z</dc:issued>
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
      href="https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_2026-06.zim.meta4" length="52690707456" />
  </entry>
  <entry>
    <id>urn:uuid:50e94998-c1ec-b5e7-ec34-499f1d907c30</id>
    <title>Wikipedia</title><updated>2026-08-25T00:00:00Z</updated>
    <summary>The free encyclopedia</summary><language>eng</language>
    <name>wikipedia_en_all</name><flavour>maxi</flavour>
    <dc:issued>2026-08-25T00:00:00Z</dc:issued>
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
      href="https://lb.download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2026-08.zim.meta4" length="127418088448" />
  </entry>
  <entry>
    <id>urn:uuid:aaaaaaaa-c1ec-b5e7-ec34-499f1d907c30</id>
    <title>Wikipedia</title><summary>old</summary><language>eng</language>
    <name>wikipedia_en_all</name><flavour>maxi</flavour>
    <dc:issued>2026-02-01T00:00:00Z</dc:issued>
    <link rel="http://opds-spec.org/acquisition/open-access" href="https://x/wikipedia_en_all_maxi_2026-02.zim.meta4" length="1" />
  </entry>
</feed>"""


def _install_row(path: Path, **extra) -> int:
    row = {
        "book_id": "11111111-1111-1111-1111-111111111111",
        "name": "wikipedia_en_100",
        "flavour": "",
        "title": "Wikipedia 100",
        "file_name": path.name,
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": "",
        "status": "installed",
        "issued": "2026-08-20",
        **extra,
    }
    return db.add_content(row)


# ---------- catalogue ----------

def test_parse_feed_and_variants():
    entries = catalog.parse_feed(FEED)
    assert len(entries) == 3
    assert entries[0]["book_id"] == "c22094d0-6ad1-6360-36b7-dab953595143"
    assert entries[0]["file_name"] == "wikipedia_en_all_nopic_2026-06.zim"
    assert entries[0]["size"] == 52690707456
    rows = catalog.variants(entries)
    # Newest per flavour, most complete first.
    assert [(r["flavour"], r["issued"]) for r in rows] == [("maxi", "2026-08-25"), ("nopic", "2026-06-17")]
    assert rows[0]["flavour_label"] == "Full, with images"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("wikipedia_da_all", ("wikipedia_da_all", None)),
        ("wikipedia_da_all_maxi_2026-05.zim", ("wikipedia_da_all", "maxi")),
        ("https://download.kiwix.org/zim/wikipedia/wikipedia_en_100_2026-08.zim", ("wikipedia_en_100", "")),
        ("https://browse.library.kiwix.org/content/wikivoyage_en_all_nopic_2026-07", ("wikivoyage_en_all", "nopic")),
        ("https://lb.download.kiwix.org/zim/x/ted_en_technology_2026-01.zim.meta4", ("ted_en_technology", "")),
        ("https://browse.library.kiwix.org/viewer#devdocs_en_python_2026-07/index", ("devdocs_en_python", "")),
        ("https://browse.library.kiwix.org/content/wikipedia_da_all_nopic_2026-05/A/Danmark", ("wikipedia_da_all", "nopic")),
        ("https://browse.library.kiwix.org/#lang=eng&books.name=stackexchange_en_cooking", ("stackexchange_en_cooking", None)),
        ("https://browse.library.kiwix.org/catalog/v2/entries?name=gutenberg_en_all", ("gutenberg_en_all", None)),
    ],
)
def test_parse_user_reference(text, expected):
    assert catalog.parse_user_reference(text) == expected


def test_words_search_the_catalogue_and_names_are_looked_up():
    assert not catalog.looks_like_name("python")
    assert not catalog.looks_like_name("stack exchange cooking")
    assert catalog.looks_like_name("wikipedia_da_all")
    assert catalog.looks_like_name("https://browse.library.kiwix.org/viewer#devdocs_en_python_2026-07")
    assert catalog.looks_like_name("ted_en_technology_2026-01.zim")


SEARCH_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
 <entry><id>urn:uuid:1</id><title>Python Docs</title><summary>Python documentation</summary><language>eng</language>
  <name>devdocs_en_python</name><flavour></flavour><dc:issued>2026-07-06T00:00:00Z</dc:issued>
  <link rel="http://opds-spec.org/acquisition/open-access" href="https://lb.download.kiwix.org/zim/devdocs/devdocs_en_python_2026-07.zim.meta4" length="1000"/></entry>
 <entry><id>urn:uuid:2</id><title>Python-tutorials</title><summary>Tutorials</summary><language>fra</language>
  <name>unine.ch_fr_python</name><flavour></flavour><dc:issued>2026-05-01T00:00:00Z</dc:issued>
  <link rel="http://opds-spec.org/acquisition/open-access" href="https://lb.download.kiwix.org/zim/other/unine.ch_fr_python_2026-05.zim.meta4" length="2000"/></entry>
</feed>"""


def test_search_groups_titles_household_language_first_and_caches(fresh_data, monkeypatch):
    seen = {}

    class _Resp:
        text = SEARCH_FEED

        def raise_for_status(self):
            return None

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            seen.update(params or {})
            return _Resp()

    monkeypatch.setattr(catalog, "_client", lambda: _Client())
    results, err = catalog.search("  python ", "fr")
    assert err is None and seen["q"] == "python"
    assert [r["name"] for r in results] == ["unine.ch_fr_python", "devdocs_en_python"]
    # Install looks names up in the cache: search results must be there.
    assert catalog.entries_for("devdocs_en_python")[0]["file_name"] == "devdocs_en_python_2026-07.zim"
    assert catalog.search("p")[1]


def test_lookup_rejects_odd_names():
    rows, err = catalog.lookup("../etc/passwd")
    assert rows == [] and err


# ---------- kiwix ----------

def test_zim_uuid_and_library_xml(fresh_data):
    zim = make_zim(fresh_data / "zim" / "wikipedia_en_100_2026-08.zim", uid=bytes(range(16)))
    assert kiwix.zim_uuid(zim) == "00010203-0405-0607-0809-0a0b0c0d0e0f"
    (fresh_data / "zim" / "notazim.zim").write_bytes(b"nope" * 10)
    assert kiwix.zim_uuid(fresh_data / "zim" / "notazim.zim") is None
    _install_row(zim)
    assert kiwix.write_library_xml() == 1
    xml = LIBRARY_XML.read_text(encoding="utf-8")
    assert 'name="wikipedia_en_100"' in xml and str(zim) in xml
    assert kiwix.reader_url({"file_name": zim.name}) == "/library/read/content/wikipedia_en_100_2026-08"


# ---------- content ----------

def test_remove_deletes_only_the_recorded_file(fresh_data):
    folder = fresh_data / "drive"
    zim = make_zim(folder / "wikipedia_en_100_2026-08.zim")
    other = folder / "holiday-photos.jpg"
    other.write_bytes(b"keep me")
    cid = _install_row(zim)
    ok, _ = content.remove(cid)
    assert ok
    assert not zim.exists()
    assert other.exists() and folder.exists()
    assert db.list_content() == []


def test_remove_refuses_paths_that_are_not_the_recorded_zim(fresh_data):
    folder = fresh_data / "drive"
    folder.mkdir()
    keep = folder / "important.txt"
    keep.write_text("x")
    cid = db.add_content({"book_id": "x", "name": "n", "title": "t", "file_name": "t.zim", "path": str(keep), "size": 1, "status": "installed"})
    content.remove(cid)
    assert keep.exists()


def test_reconcile_marks_missing_and_relocates(fresh_data, monkeypatch):
    gone_drive = fresh_data / "mnt" / "usb" / "StonePi-Library" / "zim"
    zim = make_zim(gone_drive / "wikipedia_en_100_2026-08.zim")
    cid = _install_row(zim)
    db.set_setting("storage_path", str(gone_drive))
    shutil.rmtree(fresh_data / "mnt")
    content.reconcile()
    assert db.get_content(cid)["status"] == "missing"
    # A restore put the same file on the microSD default folder.
    make_zim(DEFAULT_CONTENT_DIR / "wikipedia_en_100_2026-08.zim")
    content.reconcile()
    row = db.get_content(cid)
    assert row["status"] == "installed"
    assert Path(row["path"]).parent == DEFAULT_CONTENT_DIR
    assert db.get_setting("storage_kind") == "sd"


def test_manifest_lists_installed_files(fresh_data):
    zim = make_zim(fresh_data / "zim" / "a_2026-01.zim")
    _install_row(zim, sha256="abc")
    content.write_manifest()
    data = json.loads((fresh_data / "backup-manifest.json").read_text())
    assert data["files"][0]["file_name"] == "a_2026-01.zim"
    assert data["files"][0]["sha256"] == "abc"


# ---------- backup capacity ----------

def _stamp(fresh_data, **kv):
    (fresh_data / "last-usb-backup.txt").write_text("\n".join(f"{k}={v}" for k, v in kv.items()))


def _fake_content(size: int):
    db.add_content({"book_id": "b", "name": "wikipedia_en_all", "flavour": "maxi", "title": "Wikipedia", "file_name": "w.zim", "path": "/x/w.zim", "size": size, "status": "installed"})


def test_capacity_unknown_without_drive_facts(fresh_data):
    _fake_content(10 * GIB)
    assert backup.check()["state"] == "unknown"
    ok, msg = backup.set_enabled(True)
    assert not ok and "check its space" in msg


@pytest.mark.parametrize(
    "avail_gb,total_gb,state",
    [(500, 1000, "ok"), (200, 1000, "low"), (150, 1000, "insufficient")],
)
def test_capacity_states_from_last_backup(fresh_data, avail_gb, total_gb, state):
    _fake_content(100 * GIB)
    _stamp(fresh_data, TIMESTAMP="2026-09-30T03:30:00", SIZE_KB=2 * 1024 * 1024, DEST_TOTAL_KB=total_gb * 1024 * 1024, DEST_AVAIL_KB=avail_gb * 1024 * 1024)
    result = backup.check()
    # 100 GB content + 2 GB platform + max(5 GB, 5%) margin = 152 GB on a 1 TB drive.
    assert result["required"] == 100 * GIB + 2 * GIB + 50 * GIB
    assert result["state"] == state


def test_capacity_counts_content_already_on_the_drive(fresh_data):
    _fake_content(100 * GIB)
    _stamp(fresh_data, SIZE_KB=1024 * 1024, DEST_TOTAL_KB=200 * 1024 * 1024, DEST_AVAIL_KB=40 * 1024 * 1024, LIBRARY_CONTENT_BYTES=100 * GIB)
    result = backup.check()
    assert result["new_bytes"] == 0
    assert result["state"] == "ok"


def test_install_need_doubles_on_shared_backup_drive(monkeypatch):
    monkeypatch.setattr(storage, "usage", lambda path=None: {"total": 1000 * GIB, "used": 0, "free": 250 * GIB})
    monkeypatch.setattr(backup, "include_enabled", lambda: True)
    monkeypatch.setattr(storage, "shares_backup_drive", lambda: True)
    need = backup.install_need(100 * GIB)
    assert need["doubled"] and need["need"] == 200 * GIB + 50 * GIB and need["ok"]
    need = backup.install_need(110 * GIB)
    assert not need["ok"]
    monkeypatch.setattr(storage, "shares_backup_drive", lambda: False)
    assert backup.install_need(110 * GIB)["ok"]


# ---------- routes ----------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_pages_render(client):
    for path in ("/", "/browse", "/settings", "/settings?tab=backup", "/settings?tab=storage", "/settings?tab=reader"):
        assert client.get(path).status_code == 200, path
    assert client.get("/healthz").json() == {"ok": True, "service": "library"}
    assert client.get("/api/display").json()["state"] == "not_installed"


def test_auth_endpoint(client, monkeypatch):
    assert client.get("/_auth").status_code == 204  # standalone: open
    import app.routes as routes

    monkeypatch.setattr(routes, "session_secret", lambda: "s3cret")
    assert client.get("/_auth").status_code == 401


def test_login_keeps_reader_query(client):
    resp = client.get("/login?next=/library/read/search?pattern=cat&books.name=wikipedia_en_100", follow_redirects=False)
    assert resp.status_code == 303
    assert "%2Flibrary%2Fread%2Fsearch%3Fpattern%3Dcat%26books.name%3Dwikipedia_en_100" in resp.headers["location"]
    bad = client.get("/login?next=//evil.example/x", follow_redirects=False)
    assert "evil" not in bad.headers["location"].split("next=", 1)[1]
    # nginx's raw $request_uri: an encoded & inside a search stays encoded.
    kept = client.get("/login?next=/library/read/search?pattern=a%26b&books.name=x", follow_redirects=False)
    assert "pattern%3Da%2526b" in kept.headers["location"]
    for sneaky in ("/%09/evil.example/x", "/%5Cevil.example/x", "/%0A/evil.example"):
        resp = client.get(f"/login?next={sneaky}", follow_redirects=False)
        assert "evil" not in resp.headers["location"].split("next=", 1)[1]


# ---------- backup content copy (deploy/backup/stonepi-backup-library.py) ----------

def _copy_script():
    path = Path(__file__).resolve().parents[3] / "deploy" / "backup" / "stonepi-backup-library.py"
    spec = importlib.util.spec_from_file_location("backup_library", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The copier walks paths with O_DIRECTORY/O_NOFOLLOW (Linux): run on the Pi / CI.
posix_only = pytest.mark.skipif(not hasattr(os, "O_DIRECTORY"), reason="needs Linux O_DIRECTORY")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _allowed(mod, root: Path) -> None:
    # Treat the test folder as the Library's own content root.
    mod.DEFAULT_TARGET = str(root)


def test_backup_copier_only_accepts_safe_names_and_folders():
    mod = _copy_script()
    assert mod.safe_dir("/var/lib/stonepi/library/zim")
    assert mod.safe_dir("/mnt/stonepi-library-ab12cd34/StonePi-Library/zim")
    assert mod.safe_dir("/mnt/stonepi-backup/StonePi-Library/zim")
    # The backup drive holds every app's data: only its Library folder.
    assert not mod.safe_dir("/mnt/stonepi-backup/other")
    assert not mod.safe_dir("/mnt/stonepi-backup/RaspberryPi-Backup/x")
    assert not mod.safe_dir("/etc/stonepi")
    assert not mod.safe_dir("/mnt/usb/../../etc")
    rows = mod.entries([
        {"file_name": "ok_2026-01.zim", "path": "/var/lib/stonepi/library/zim/ok_2026-01.zim", "size": 5},
        {"file_name": "../x.zim", "path": "/var/lib/stonepi/library/zim/../x.zim", "size": 5},
        {"file_name": "a.zim", "path": "/etc/a.zim", "size": 5},
        {"file_name": "b.zim", "path": "/var/lib/stonepi/library/zim/c.zim", "size": 5},
    ])
    assert [r["file_name"] for r in rows] == ["ok_2026-01.zim"]


@posix_only
def test_backup_store_copies_new_prunes_old_and_restores(fresh_data):
    mod = _copy_script()
    _allowed(mod, fresh_data)
    src = fresh_data / "content"
    a = make_zim(src / "a_2026-01.zim", size=5000)
    b = make_zim(src / "b_2026-01.zim", size=6000)
    store = fresh_data / "backup" / "library-content"
    manifest = fresh_data / "manifest.json"

    def write_manifest(*files, content_dir=src):
        manifest.write_text(json.dumps({"content_dir": str(content_dir), "files": [
            {"file_name": f.name, "path": str(f), "size": f.stat().st_size, "sha256": _sha(f)} for f in files
        ]}))

    write_manifest(a, b)
    assert mod.backup(str(manifest), str(store)) == "ok 11000"
    assert sorted(p.name for p in store.glob("*.zim")) == ["a_2026-01.zim", "b_2026-01.zim"]

    # b removed from the library, c added: c copied, b pruned.
    c = make_zim(src / "c_2026-02.zim", size=7000)
    write_manifest(a, c)
    assert mod.backup(str(manifest), str(store)) == "ok 12000"
    assert sorted(p.name for p in store.glob("*.zim")) == ["a_2026-01.zim", "c_2026-02.zim"]

    restored = fresh_data / "restored"
    write_manifest(a, c, content_dir=restored)
    assert mod.restore(str(store), str(manifest)) == f"ok 12000 {restored}"
    assert (restored / "c_2026-02.zim").stat().st_size == 7000


@posix_only
def test_restore_never_takes_over_a_folder_with_other_files(fresh_data, monkeypatch):
    mod = _copy_script()
    _allowed(mod, fresh_data)
    other = fresh_data / "someone-else"
    other.mkdir()
    (other / "notes.txt").write_text("not a zim")
    # Another owner's folder: only ZIMs inside may be handed to the Library.
    uid = os.stat(other).st_uid + 1
    assert not mod._ours_or_library_only(str(other), uid)
    (other / "notes.txt").unlink()
    make_zim(other / "a_2026-01.zim")
    assert mod._ours_or_library_only(str(other), uid)
    assert mod._ours_or_library_only(str(fresh_data / "missing"), uid)


@posix_only
def test_backup_store_skips_when_drive_is_full(fresh_data, monkeypatch):
    mod = _copy_script()
    _allowed(mod, fresh_data)
    a = make_zim(fresh_data / "content" / "a_2026-01.zim")
    manifest = fresh_data / "manifest.json"
    manifest.write_text(json.dumps({"files": [{"file_name": a.name, "path": str(a), "size": a.stat().st_size}]}))
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 10, 0))
    assert mod.backup(str(manifest), str(fresh_data / "store")).startswith("skipped")


@posix_only
def test_backup_store_rejects_corrupt_copy(fresh_data):
    mod = _copy_script()
    _allowed(mod, fresh_data)
    a = make_zim(fresh_data / "content" / "a_2026-01.zim")
    manifest = fresh_data / "manifest.json"
    manifest.write_text(json.dumps({"files": [{"file_name": a.name, "path": str(a), "size": a.stat().st_size, "sha256": "0" * 64}]}))
    store = fresh_data / "store"
    assert mod.backup(str(manifest), str(store)).startswith("failed")
    assert not list(store.glob("*.zim")) and not list(store.glob("*.part"))


def test_auth_fails_closed_without_secret_outside_dev(client, monkeypatch):
    import app.routes as routes

    monkeypatch.setattr(routes, "session_secret", lambda: "")
    monkeypatch.setattr(routes, "auth_optional", lambda: False)
    # nginx auth_request: no secret on a Pi must not unlock the Kiwix reader.
    assert client.get("/_auth").status_code == 403
    assert client.get("/").status_code == 503
    assert routes._can_manage(None) is False
    monkeypatch.setattr(routes, "auth_optional", lambda: True)
    assert client.get("/_auth").status_code == 204


def test_helper_simulated_only_in_dev(monkeypatch):
    from types import SimpleNamespace

    from app.services import helper

    monkeypatch.setattr(helper, "os", SimpleNamespace(name="posix", environ={}))
    assert helper.simulated() is False
    monkeypatch.setattr(helper, "os", SimpleNamespace(name="posix", environ={"LIBRARY_DEV_HELPER": "1"}))
    assert helper.simulated() is True
    monkeypatch.setattr(helper, "os", SimpleNamespace(name="nt", environ={}))
    assert helper.simulated() is True


def test_missing_helper_on_pi_is_an_error_not_fake_success(monkeypatch, tmp_path):
    from app.services import helper

    monkeypatch.setattr(helper, "simulated", lambda: False)
    monkeypatch.setattr(helper.env, "helper", str(tmp_path / "no-such-helper"))
    ok, data = helper.run("install-kiwix")
    assert ok is False
    assert data["error"] == helper.MISSING_HELPER
    assert helper.available() is False


# ---------- permissions: admin-only settings vs Manage content ----------

SECRET = "perm-test-secret"


def _sign_in(client, monkeypatch, *, admin: bool = False, manage: bool = True):
    import app.routes as routes
    from stonepi_auth.csrf import CSRF_COOKIE
    from stonepi_auth.session import COOKIE_NAME, encode_session

    monkeypatch.setattr(routes, "session_secret", lambda: SECRET)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET,
            user_id="u1",
            username="sam",
            display_name="Sam",
            is_admin=admin,
            apps=["library"],
            session_id="s1",
            permissions={"library": {"can_manage_content": manage}},
        ),
    )
    client.cookies.set(CSRF_COOKIE, "tok")
    return routes


def test_custom_storage_path_is_admin_only_server_side(monkeypatch):
    import app.routes as routes

    called = []
    monkeypatch.setattr(routes.helper, "run", lambda *a, **k: called.append(a) or (True, {}))
    monkeypatch.setattr(routes.helper, "simulated", lambda: False)
    path, _, _, err = routes._storage_choice("custom", "/mnt/usb/kiwix", admin=False)
    assert path is None and "admin" in err
    assert called == []  # never reaches the root helper
    path, kind, _, err = routes._storage_choice("custom", "/mnt/usb/kiwix", admin=True)
    assert err is None and kind == "custom"
    assert called == [("prepare-folder", "/mnt/usb/kiwix")]


@pytest.mark.parametrize(
    "path,data",
    [
        ("/setup", {"storage_choice": "custom", "custom_path": "/mnt/usb/kiwix"}),
        ("/settings/storage", {"storage_choice": "sd"}),
        ("/settings/backup", {"include": "1"}),
    ],
)
def test_manage_member_cannot_use_admin_settings(client, monkeypatch, path, data):
    routes = _sign_in(client, monkeypatch, manage=True)
    called = []
    monkeypatch.setattr(routes.helper, "run", lambda *a, **k: called.append(a) or (True, {}))
    resp = client.post(path, data={"csrf_token": "tok", **data}, follow_redirects=False)
    assert resp.status_code == 403
    assert called == []
    assert db.get_setting("storage_path") in (None, "")


def test_admin_can_use_admin_settings(client, monkeypatch):
    routes = _sign_in(client, monkeypatch, admin=True)
    monkeypatch.setattr(routes.backup, "set_enabled", lambda on: (True, "ok"))
    resp = client.post("/settings/backup", data={"csrf_token": "tok", "include": "1"}, follow_redirects=False)
    assert resp.status_code == 303


def test_member_sets_language_but_not_speed_limit(client, monkeypatch):
    routes = _sign_in(client, monkeypatch, manage=True)
    monkeypatch.setattr(routes.content, "refresh_catalog_in_background", lambda lang: True)
    db.set_setting("max_mbps", "5")
    resp = client.post("/settings/content", data={"csrf_token": "tok", "language": "fr"}, follow_redirects=False)
    assert resp.status_code == 303
    assert db.get_setting("language") == "fr"
    assert db.get_setting("max_mbps") == "5"
    resp = client.post("/settings/content", data={"csrf_token": "tok", "language": "fr", "max_mbps": ""}, follow_redirects=False)
    assert resp.status_code == 403
    assert db.get_setting("max_mbps") == "5"
    page = client.get("/settings?tab=content").text
    assert 'name="max_mbps"' not in page
    assert "Only a StonePi admin can change the speed limit" in page


def test_admin_sets_speed_limit(client, monkeypatch):
    routes = _sign_in(client, monkeypatch, admin=True)
    monkeypatch.setattr(routes.content, "refresh_catalog_in_background", lambda lang: True)
    resp = client.post("/settings/content", data={"csrf_token": "tok", "language": "en", "max_mbps": "12"}, follow_redirects=False)
    assert resp.status_code == 303
    assert db.get_setting("max_mbps") == "12"
    assert 'name="max_mbps"' in client.get("/settings?tab=content").text


def test_templates_hide_admin_and_manage_controls(client, monkeypatch):
    _sign_in(client, monkeypatch, manage=False)
    home = client.get("/").text
    assert 'action="/setup"' not in home
    assert "An admin needs to set up the Library" in home
    content_tab = client.get("/settings?tab=content").text
    assert "Refresh now" not in content_tab
    storage_tab = client.get("/settings?tab=storage").text
    assert 'action="/settings/storage"' not in storage_tab
    assert 'action="/settings/backup"' not in client.get("/settings?tab=backup").text
    # A Manage-content member still gets Refresh now but not setup or storage.
    _sign_in(client, monkeypatch, manage=True)
    assert "Refresh now" in client.get("/settings?tab=content").text
    assert 'action="/setup"' not in client.get("/").text
    assert 'value="custom"' not in client.get("/settings?tab=storage").text
    _sign_in(client, monkeypatch, admin=True)
    assert 'action="/setup"' in client.get("/").text
    assert 'value="custom"' in client.get("/settings?tab=storage").text


def test_manage_member_keeps_catalog_refresh_and_reader_restart(client, monkeypatch):
    routes = _sign_in(client, monkeypatch, manage=True)
    monkeypatch.setattr(routes.content, "refresh_catalog", lambda lang: (True, "Catalogue updated"))
    monkeypatch.setattr(routes.content, "sync", lambda: (True, ""))
    assert client.post("/catalog/refresh", data={"csrf_token": "tok"}, follow_redirects=False).status_code == 303
    assert client.post("/settings/reader/restart", data={"csrf_token": "tok"}, follow_redirects=False).status_code == 303
    assert client.post("/settings/reader/remove", data={"csrf_token": "tok"}, follow_redirects=False).status_code == 403
    _sign_in(client, monkeypatch, manage=False)
    assert client.post("/catalog/refresh", data={"csrf_token": "tok"}, follow_redirects=False).status_code == 403


def test_background_catalog_refresh_runs_once_at_a_time(monkeypatch):
    import threading

    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(lang):
        calls.append(lang)
        started.set()
        release.wait(5)
        return True, ""

    # Other route tests may have left a real (network) refresh running.
    monkeypatch.setattr(content, "_refreshing", set())
    monkeypatch.setattr(content, "refresh_catalog", slow)
    assert content.refresh_catalog_in_background("en") is True
    assert started.wait(5)
    assert content.refresh_catalog_in_background("en") is False  # already running
    release.set()
    for _ in range(100):
        if not content._refreshing:
            break
        threading.Event().wait(0.02)
    assert content._refreshing == set()
    assert calls == ["en"]


def test_background_catalog_refresh_clears_guard_after_failure(monkeypatch):
    import threading

    done = threading.Event()

    def boom(lang):
        done.set()
        raise RuntimeError("offline")

    monkeypatch.setattr(content, "_refreshing", set())
    monkeypatch.setattr(content, "refresh_catalog", boom)
    assert content.refresh_catalog_in_background("en") is True
    assert done.wait(5)
    for _ in range(100):
        if not content._refreshing:
            break
        threading.Event().wait(0.02)
    assert content._refreshing == set()
