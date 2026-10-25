"""Ripple Phase 3: photo uploads, cleaning, private serving and cleanup."""

from __future__ import annotations

import io
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import db, photos, routes
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SAM = "0b1d3c5e-1111-4a2b-9c3d-5e6f7a8b9c0d"
SECRET = "ripple-secret"
GPS_IFD = 0x8825
ORIENTATION = 0x0112


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    routes._uploads.clear()


def _client(monkeypatch, *, uid=JO, name="Jo", is_admin=False, granted=True, signed_in=True) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    if signed_in:
        c.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=SECRET, user_id=uid, username=name.lower(), display_name=name, is_admin=is_admin,
                apps=["ripple"] if granted else [], session_id="s", permissions={},
            ),
        )
    return c


def _jpeg(size=(400, 200), *, gps=True, orientation=None, color="steelblue") -> bytes:
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # camera make
    if orientation:
        exif[ORIENTATION] = orientation
    if gps:
        exif[GPS_IFD] = {1: "N", 2: (55.0, 40.0, 30.0), 3: "E", 4: (12.0, 34.0, 56.0)}
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def _png() -> bytes:
    img = Image.new("RGBA", (64, 48), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _webp() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), "green").save(buf, "WEBP")
    return buf.getvalue()


def _gif() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "GIF")
    return buf.getvalue()


def _send(c: TestClient, files: list[tuple[str, bytes]], *, body="", captions=None, url="/posts", ajax=False, extra=None):
    data = {"body": body, "csrf_token": "csrf", "show_on_display": "1", "captions": captions or [], **(extra or {})}
    parts = [("photos", (name, content, "application/octet-stream")) for name, content in files]
    headers = {"X-Ripple-Ajax": "1"} if ajax else {}
    return c.post(url, data=data, files=parts or None, headers=headers, follow_redirects=False)


def _media_files() -> list[str]:
    folder = photos.media_dir()
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _only_post() -> dict:
    posts = db.list_posts(1, 100)[0]
    assert len(posts) == 1
    return posts[0]


# -- cleaning --------------------------------------------------------------------------


def test_jpeg_loses_gps_and_camera_details_and_is_turned_upright(monkeypatch):
    c = _client(monkeypatch)
    r = _send(c, [("IMG_0001.jpg", _jpeg((400, 200), orientation=6))], captions=["Harbour walk"])
    assert r.status_code == 303 and r.headers["location"] == "/?msg=Posted"
    media = _only_post()["media"][0]
    assert media["caption"] == "Harbour walk" and media["mime_type"] == "image/jpeg"
    stored = photos.photo_path(media["storage_key"], "jpg")
    raw = stored.read_bytes()
    assert b"Exif" not in raw and b"PhoneMaker" not in raw
    with Image.open(stored) as img:
        assert img.size == (200, 400)  # orientation 6: rotated upright
        assert not img.getexif()
    with Image.open(photos.thumb_path(media["storage_key"])) as thumb:
        assert thumb.format == "JPEG" and not thumb.getexif()
    assert (media["width"], media["height"]) == (200, 400)


def test_storage_never_uses_the_uploaded_name(monkeypatch):
    _send(_client(monkeypatch), [("../../etc/passwd.jpg", _jpeg())])
    key = _only_post()["media"][0]["storage_key"]
    assert photos.KEY_RE.match(key)
    assert _media_files() == sorted([f"{key}.jpg", f"{key}-thumb.jpg"])


def test_png_and_webp_are_kept_in_their_format(monkeypatch):
    _send(_client(monkeypatch), [("a.png", _png()), ("b.webp", _webp())], body="Two")
    first, second = _only_post()["media"]
    assert (first["mime_type"], first["ext"], second["mime_type"], second["ext"]) == ("image/png", "png", "image/webp", "webp")
    with Image.open(photos.thumb_path(first["storage_key"])) as thumb:
        assert thumb.mode == "RGB"  # transparency flattened onto white


def test_phone_mpo_jpeg_is_kept_as_a_plain_jpeg(monkeypatch):
    first, second = Image.new("RGB", (64, 48), "red"), Image.new("RGB", (64, 48), "blue")
    buf = io.BytesIO()
    first.save(buf, "MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(buf.getvalue())).format == "MPO"
    r = _send(_client(monkeypatch), [("portrait.jpg", buf.getvalue())])
    assert r.status_code == 303 and r.headers["location"] == "/?msg=Posted"
    media = _only_post()["media"][0]
    assert (media["ext"], media["mime_type"]) == ("jpg", "image/jpeg")
    with Image.open(photos.photo_path(media["storage_key"], "jpg")) as stored:
        assert stored.format == "JPEG" and stored.getpixel((5, 5))[0] > 200  # the first (red) picture


def test_cmyk_jpeg_drops_its_cmyk_profile(monkeypatch):
    buf = io.BytesIO()
    Image.new("CMYK", (64, 48), (0, 255, 255, 0)).save(buf, "JPEG", icc_profile=b"CMYK-PROFILE" * 10)
    _send(_client(monkeypatch), [("print.jpg", buf.getvalue())], body="Scan")
    media = _only_post()["media"][0]
    with Image.open(photos.photo_path(media["storage_key"], "jpg")) as stored:
        assert stored.mode == "RGB" and "icc_profile" not in stored.info


def test_rgb_jpeg_keeps_its_colour_profile(monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(buf, "JPEG", icc_profile=b"RGB-PROFILE" * 10)
    _send(_client(monkeypatch), [("p3.jpg", buf.getvalue())], body="Wide colour")
    media = _only_post()["media"][0]
    with Image.open(photos.photo_path(media["storage_key"], "jpg")) as stored:
        assert stored.info.get("icc_profile", b"").startswith(b"RGB-PROFILE")


def test_unexpected_failure_mid_upload_leaves_nothing(monkeypatch):
    real_store = photos.store
    calls = {"n": 0}

    def flaky(data, caption=""):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real_store(data, caption)

    monkeypatch.setattr(photos, "store", flaky)
    r = _send(_client(monkeypatch), [("a.jpg", _jpeg()), ("b.jpg", _jpeg())], body="Hi", ajax=True)
    assert r.status_code == 400 and r.json()["error"].startswith("Photo 2 (b.jpg) couldn't be saved")
    assert db.list_posts()[0] == [] and _media_files() == []


def test_refused_uploads_dont_use_the_hourly_allowance(monkeypatch):
    monkeypatch.setattr(routes, "UPLOADS_PER_HOUR", 2)
    c = _client(monkeypatch)
    for _ in range(3):
        _send(c, [("a.jpg", _jpeg()), ("notes.jpg", b"not a photo")], ajax=True)
    assert _send(c, [("a.jpg", _jpeg()), ("b.jpg", _jpeg())]).status_code == 303


def test_big_jpeg_is_decoded_small_and_stored_at_most_store_side(monkeypatch):
    opened: list = []
    real_open = Image.open

    def spy(*args, **kwargs):
        img = real_open(*args, **kwargs)
        opened.append(img)
        return img

    monkeypatch.setattr(photos.Image, "open", spy)
    _send(_client(monkeypatch), [("big.jpg", _jpeg((8000, 6000), gps=False))])  # a 48 MP phone photo
    media = _only_post()["media"][0]
    assert (media["width"], media["height"]) == (4000, 3000)  # decoded at half size, ~12 MP
    assert opened[0].size == (4000, 3000)  # draft: the decoder never built the full 48 MP image


def test_usual_phone_photo_keeps_its_full_size(monkeypatch):
    _send(_client(monkeypatch), [("iphone.jpg", _jpeg((4032, 3024), gps=False))])
    media = _only_post()["media"][0]
    assert (media["width"], media["height"]) == (4032, 3024)


def test_wide_png_is_scaled_to_store_side(monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (6000, 1000), "orange").save(buf, "PNG")
    _send(_client(monkeypatch), [("panorama.png", buf.getvalue())])
    media = _only_post()["media"][0]
    assert (media["width"], media["height"]) == (photos.STORE_SIDE, 683)


def test_huge_png_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(photos, "MAX_PIXELS_FULL_DECODE", 1000)
    r = _send(_client(monkeypatch), [("screen.png", _png())], ajax=True)
    assert r.status_code == 400 and "24 megapixels" in r.json()["error"] and _media_files() == []


def test_photos_are_processed_one_at_a_time(monkeypatch):
    import threading

    active = {"now": 0, "most": 0}
    guard = threading.Lock()
    real = photos._store

    def counting(data, caption):
        with guard:
            active["now"] += 1
            active["most"] = max(active["most"], active["now"])
        try:
            return real(data, caption)
        finally:
            with guard:
                active["now"] -= 1

    monkeypatch.setattr(photos, "_store", counting)
    threads = [threading.Thread(target=photos.store, args=(_jpeg(),)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert active["most"] == 1


def test_large_photo_gets_a_small_thumbnail(monkeypatch):
    _send(_client(monkeypatch), [("big.jpg", _jpeg((3000, 1500), gps=False))])
    media = _only_post()["media"][0]
    with Image.open(photos.thumb_path(media["storage_key"])) as thumb:
        assert thumb.size == (photos.THUMB_SIDE, 600)


# -- refusals --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, content, why",
    [
        ("anim.gif", _gif(), "isn&#39;t a JPEG"),
        ("notes.jpg", b"definitely not a photo", "isn&#39;t a JPEG"),
        ("cut.jpg", _jpeg()[:200], "isn&#39;t a JPEG"),
    ],
)
def test_non_photos_are_refused_and_nothing_is_kept(monkeypatch, name, content, why):
    c = _client(monkeypatch)
    r = _send(c, [("ok.jpg", _jpeg()), (name, content)], body="Hi", ajax=True)
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False and body["error"].startswith(f"Photo 2 ({name})")
    assert db.list_posts()[0] == [] and _media_files() == []


def test_too_big_file_is_refused(monkeypatch):
    monkeypatch.setattr(photos, "MAX_BYTES", 1000)
    r = _send(_client(monkeypatch), [("huge.jpg", _jpeg((800, 800)))], ajax=True)
    assert "is over" in r.json()["error"] and _media_files() == []


def test_too_many_pixels_is_refused(monkeypatch):
    monkeypatch.setattr(photos, "MAX_PIXELS", 10_000)
    r = _send(_client(monkeypatch), [("wide.jpg", _jpeg((400, 200)))], ajax=True)
    assert "too large" in r.json()["error"] and _media_files() == []


def test_photo_limit_per_post(monkeypatch):
    db.set_setting("max_photos", "2")
    r = _send(_client(monkeypatch), [("a.jpg", _jpeg()), ("b.jpg", _jpeg()), ("c.jpg", _jpeg())], ajax=True)
    assert r.status_code == 400 and "up to 2 photos" in r.json()["error"]
    assert _media_files() == []


def test_hourly_upload_limit(monkeypatch):
    monkeypatch.setattr(routes, "UPLOADS_PER_HOUR", 2)
    c = _client(monkeypatch)
    assert _send(c, [("a.jpg", _jpeg()), ("b.jpg", _jpeg())]).status_code == 303
    r = _send(c, [("c.jpg", _jpeg())], ajax=True)
    assert r.status_code == 429 and len(db.list_posts()[0]) == 1


def test_expired_form_with_photos_saves_nothing(monkeypatch):
    c = _client(monkeypatch)
    r = c.post("/posts", data={"csrf_token": "wrong"}, files=[("photos", ("a.jpg", _jpeg(), "image/jpeg"))],
               headers={"X-Ripple-Ajax": "1"})
    assert r.status_code == 403 and _media_files() == []


# -- posting with photos ---------------------------------------------------------------


def test_photo_only_post_and_ajax_success(monkeypatch):
    r = _send(_client(monkeypatch), [("a.jpg", _jpeg())], ajax=True)
    assert r.json() == {"ok": True, "redirect": "/?msg=Posted"}
    assert _only_post()["body"] == ""


def test_ajax_redirect_carries_the_nginx_prefix(monkeypatch):
    monkeypatch.setattr(routes.env, "stonepi_prefix", "/ripple")
    r = _send(_client(monkeypatch), [], body="Text only", ajax=True)
    assert r.json()["redirect"] == "/ripple/?msg=Posted"


def test_feed_shows_photos_and_escapes_captions(monkeypatch):
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg()), ("b.jpg", _jpeg())], body="Two photos", captions=["<b>Bold</b>", "Second"])
    key = _only_post()["media"][0]["storage_key"]
    html = c.get("/").text
    assert f'src="/media/{key}/thumb"' in html and f'href="/media/{key}"' not in html  # previews, not links
    assert "post-photos is-grid" in html
    assert "&lt;b&gt;Bold&lt;/b&gt;" in html and "<b>Bold</b>" not in html


def test_missing_file_shows_unavailable(monkeypatch):
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg())], body="Hi")
    media = _only_post()["media"][0]
    photos.delete_files(media["storage_key"], media["ext"])
    assert "Photo unavailable" in c.get("/").text


# -- serving ---------------------------------------------------------------------------


def test_photos_are_served_privately(monkeypatch):
    _send(_client(monkeypatch), [("a.jpg", _jpeg())], body="Hi")
    key = _only_post()["media"][0]["storage_key"]
    member = _client(monkeypatch)
    full = member.get(f"/media/{key}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    assert full.headers["cache-control"] == "private, max-age=86400"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert member.get(f"/media/{key}/thumb").status_code == 200
    stranger = _client(monkeypatch, uid=SAM, name="Sam", granted=False)
    r = stranger.get(f"/media/{key}")
    assert r.status_code == 403 and r.headers["content-type"].startswith("text/html")
    signed_out = _client(monkeypatch, signed_in=False)
    assert signed_out.get(f"/media/{key}", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("path", ["/media/nope", "/media/aaaaaaaaaaaaaaaaaaaaaaaa", "/media/..%2F..%2Fripple.sqlite"])
def test_unknown_or_malformed_keys_are_not_found(monkeypatch, path):
    assert _client(monkeypatch).get(path).status_code == 404


def test_unknown_variant_is_not_found(monkeypatch):
    _send(_client(monkeypatch), [("a.jpg", _jpeg())], body="Hi")
    key = _only_post()["media"][0]["storage_key"]
    assert _client(monkeypatch).get(f"/media/{key}/original").status_code == 404


# -- editing, deleting, cleanup --------------------------------------------------------


def test_edit_changes_captions_removes_and_adds_photos(monkeypatch):
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg()), ("b.jpg", _jpeg())], body="Start", captions=["One", "Two"])
    post = _only_post()
    first, second = post["media"]
    edit_html = c.get(f"/posts/{post['id']}/edit").text
    assert f'name="caption_{first["id"]}"' in edit_html and 'value="One"' in edit_html
    r = _send(
        c, [("c.png", _png())], body="Edited", captions=["Three"], url=f"/posts/{post['id']}",
        extra={f"caption_{first['id']}": "One, renamed", f"caption_{second['id']}": "Two", "remove": [str(second["id"])]},
    )
    assert r.headers["location"] == "/?msg=Saved"
    media = _only_post()["media"]
    assert [m["caption"] for m in media] == ["One, renamed", "Three"]
    assert not photos.photo_path(second["storage_key"], "jpg").exists()
    assert not photos.thumb_path(second["storage_key"]).exists()


def test_edit_respects_the_photo_limit(monkeypatch):
    db.set_setting("max_photos", "2")
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg()), ("b.jpg", _jpeg())], body="Full")
    pid = _only_post()["id"]
    r = _send(c, [("c.jpg", _jpeg())], body="Full", url=f"/posts/{pid}", ajax=True)
    assert r.status_code == 400 and len(_only_post()["media"]) == 2
    assert len(_media_files()) == 4


def test_removing_the_last_photo_needs_text(monkeypatch):
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg())])
    post = _only_post()
    r = _send(c, [], url=f"/posts/{post['id']}", ajax=True, extra={"remove": [str(post["media"][0]["id"])]})
    assert r.status_code == 400 and "Write something" in r.json()["error"]
    assert len(_only_post()["media"]) == 1


def test_member_cannot_edit_photos_on_someone_elses_post(monkeypatch):
    _send(_client(monkeypatch, uid=ADAM, name="Adam"), [("a.jpg", _jpeg())], body="Adam's")
    post = _only_post()
    r = _send(_client(monkeypatch), [], body="Mine now", url=f"/posts/{post['id']}", ajax=True,
              extra={"remove": [str(post["media"][0]["id"])]})
    assert r.status_code == 403 and len(_only_post()["media"]) == 1


def test_deleting_a_post_deletes_its_files_and_stops_serving(monkeypatch):
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg()), ("b.webp", _webp())], body="Bye")
    post = _only_post()
    key = post["media"][0]["storage_key"]
    c.post(f"/posts/{post['id']}/delete", data={"csrf_token": "csrf"})
    assert _media_files() == []
    assert c.get(f"/media/{key}").status_code == 404


def test_sweep_removes_old_orphans_only(monkeypatch):
    c = _client(monkeypatch)
    _send(c, [("a.jpg", _jpeg())], body="Keep")
    keep = _only_post()["media"][0]["storage_key"]
    folder = photos.media_dir()
    old = time.time() - photos.ORPHAN_AGE_S - 60
    for name in ("Zz9_orphan_key_00000000.jpg", "Zz9_orphan_key_00000000-thumb.jpg", "Yy8-crashed-upload-00000.png.part"):
        (folder / name).write_bytes(b"x")
        os.utime(folder / name, (old, old))
    (folder / "Xx7-fresh-upload-000000.jpg").write_bytes(b"x")  # an upload still in progress
    assert photos.sweep_orphans() == 3
    assert _media_files() == sorted([f"{keep}.jpg", f"{keep}-thumb.jpg", "Xx7-fresh-upload-000000.jpg"])


def test_settings_photo_limit_is_admin_only(monkeypatch):
    jo = _client(monkeypatch)
    r = jo.post("/settings/photos", data={"max_photos": "6", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and photos.max_per_post() == photos.DEFAULT_MAX_PER_POST
    admin = _client(monkeypatch, uid=ADAM, name="Adam", is_admin=True)
    admin.post("/settings/photos", data={"max_photos": "6", "csrf_token": "csrf"})
    assert photos.max_per_post() == 6
    r = admin.post("/settings/photos", data={"max_photos": "99", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and photos.max_per_post() == 6
