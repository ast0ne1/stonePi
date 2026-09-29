from stonepi_auth.internal import SIG_HEADER, TS_HEADER, sign_internal, verify_internal

SECRET = "s3cret"
PATH = "/api/internal/people"


def test_roundtrip():
    headers = sign_internal(SECRET, "GET", PATH, now=1000)
    assert verify_internal(SECRET, "GET", PATH, headers, now=1000)


def test_header_names_are_case_insensitive():
    headers = {k.lower(): v for k, v in sign_internal(SECRET, "GET", PATH, now=1000).items()}
    assert verify_internal(SECRET, "GET", PATH, headers, now=1000)


def test_rejects_unsigned_and_garbage():
    assert not verify_internal(SECRET, "GET", PATH, {}, now=1000)
    assert not verify_internal(SECRET, "GET", PATH, {TS_HEADER: "abc", SIG_HEADER: "x"}, now=1000)


def test_rejects_stale_or_future():
    headers = sign_internal(SECRET, "GET", PATH, now=1000)
    assert verify_internal(SECRET, "GET", PATH, headers, now=1060)
    assert not verify_internal(SECRET, "GET", PATH, headers, now=1061)
    assert not verify_internal(SECRET, "GET", PATH, headers, now=939)


def test_bound_to_secret_method_and_path():
    headers = sign_internal(SECRET, "GET", PATH, now=1000)
    assert not verify_internal("other", "GET", PATH, headers, now=1000)
    assert not verify_internal(SECRET, "POST", PATH, headers, now=1000)
    assert not verify_internal(SECRET, "GET", "/api/users", headers, now=1000)


def test_no_secret_never_verifies():
    headers = sign_internal(SECRET, "GET", PATH, now=1000)
    assert not verify_internal("", "GET", PATH, headers, now=1000)


def test_get_internal_json_signs_and_parses():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from stonepi_auth.internal import get_internal_json

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            ok = verify_internal(SECRET, "GET", self.path, dict(self.headers))
            body = json.dumps({"people": [{"id": "a", "name": "Jo"}]} if ok else {}).encode()
            self.send_response(200 if ok else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert get_internal_json(base, SECRET, "/api/internal/people/names") == {"people": [{"id": "a", "name": "Jo"}]}
        assert get_internal_json(base, "wrong", "/api/internal/people/names") is None
        assert get_internal_json(base, "", "/api/internal/people/names") is None
        assert get_internal_json("http://127.0.0.1:1", SECRET, "/x", timeout=0.5) is None
    finally:
        server.shutdown()
