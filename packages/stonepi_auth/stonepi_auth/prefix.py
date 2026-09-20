from __future__ import annotations

import re
from typing import Any

_ATTR = re.compile(
    r'(?P<attr>href|src|action|poster|formaction|data-href)=(?P<q>["\'])(?P<path>/[^"\']*)',
    re.IGNORECASE,
)
_FETCH = re.compile(r'''(?P<fn>fetch|send)\(\s*(?P<q>["'])(?P<path>/[^"']*)''')


def rewrite_text(text: str, prefix: str) -> str:
    prefix = clean_prefix(prefix)
    if not prefix or not text:
        return text

    def attr(match: re.Match[str]) -> str:
        path = match.group("path")
        rewritten = prefix_path(path, prefix)
        if rewritten == path:
            return match.group(0)
        return f'{match.group("attr")}={match.group("q")}{rewritten}'

    def fetch(match: re.Match[str]) -> str:
        path = match.group("path")
        rewritten = prefix_path(path, prefix)
        if rewritten == path:
            return match.group(0)
        return f'{match.group("fn")}({match.group("q")}{rewritten}'

    text = _ATTR.sub(attr, text)
    return _FETCH.sub(fetch, text)


def rewrite_location(location: str | None, prefix: str) -> str | None:
    if not location:
        return location
    prefix = clean_prefix(prefix)
    if not prefix:
        return location
    if location.startswith("/") and not location.startswith("//"):
        return prefix_path(location, prefix)
    return location


def clean_prefix(prefix: str) -> str:
    value = (prefix or "").strip()
    if not value or value == "/":
        return ""
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/")


def prefix_path(path: str, prefix: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        return path
    if path == prefix or path.startswith(prefix + "/"):
        return path
    if _is_other_platform_path(path, prefix):
        return path
    return prefix + path


def strip_prefix(path: str, prefix: str) -> str:
    """Remove a mounted app prefix from an incoming request path.

    Needed when HTML is rewritten to ``/studio/static/...`` but the process is
    reached directly (no nginx strip), e.g. Windows ``run-dev`` on :8005.
    """
    prefix = clean_prefix(prefix)
    if not prefix or not path.startswith("/"):
        return path
    if path == prefix:
        return "/"
    if path.startswith(prefix + "/"):
        rest = path[len(prefix) :]
        return rest or "/"
    return path


def _is_other_platform_path(path: str, prefix: str) -> bool:
    for root in ("/auth", "/news", "/files", "/events", "/pinboard", "/studio"):
        if root == prefix:
            continue
        if path == root or path.startswith(root + "/"):
            return True
    return False


class PrefixRewriter:
    def __init__(self, prefix: str):
        self.prefix = clean_prefix(prefix)

    def apply_flask(self, response: Any) -> Any:
        if not self.prefix:
            return response
        location = response.headers.get("Location")
        if location:
            response.headers["Location"] = rewrite_location(location, self.prefix) or location
        content_type = (response.content_type or "").lower()
        if "html" in content_type or "javascript" in content_type:
            try:
                body = response.get_data(as_text=True)
            except Exception:
                return response
            rewritten = rewrite_text(body, self.prefix)
            if rewritten != body:
                response.set_data(rewritten)
        return response


class PrefixMiddleware:
    """Pure ASGI middleware: strip inbound prefix, rewrite outbound HTML/JS/Location.

    Starlette's BaseHTTPMiddleware does not reliably apply scope path changes to
    mounted StaticFiles; this ASGI form does.
    """

    def __init__(self, app, prefix: str = ""):
        self.app = app
        self.prefix = clean_prefix(prefix)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.prefix:
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        stripped = strip_prefix(path, self.prefix)
        if stripped != path:
            scope = dict(scope)
            scope["path"] = stripped
            root = scope.get("root_path") or ""
            if not str(root).endswith(self.prefix):
                scope["root_path"] = str(root).rstrip("/") + self.prefix

        start: dict[str, Any] | None = None
        buf: list[bytes] = []
        buffer_body = False

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal start, buffer_body
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])]
                new_headers: list[tuple[bytes, bytes]] = []
                ctype = ""
                for key, value in headers:
                    low = key.lower()
                    if low == b"location":
                        loc = rewrite_location(value.decode("latin-1"), self.prefix)
                        new_headers.append((key, (loc or value.decode("latin-1")).encode("latin-1")))
                        continue
                    if low == b"content-type":
                        ctype = value.decode("latin-1").lower()
                    new_headers.append((key, value))
                buffer_body = "html" in ctype or "javascript" in ctype
                start = {
                    "type": "http.response.start",
                    "status": message["status"],
                    "headers": new_headers,
                }
                if not buffer_body:
                    await send(start)
                    start = None
                return

            if message["type"] == "http.response.body":
                if not buffer_body:
                    await send(message)
                    return
                buf.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(buf)
                charset = "utf-8"
                if start:
                    for key, value in start["headers"]:
                        if key.lower() == b"content-type":
                            text = value.decode("latin-1")
                            if "charset=" in text.lower():
                                charset = text.split("charset=", 1)[-1].split(";")[0].strip() or "utf-8"
                            break
                rewritten = rewrite_text(body.decode(charset, errors="replace"), self.prefix).encode(charset)
                headers = [(k, v) for k, v in (start or {}).get("headers", []) if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(rewritten)).encode("latin-1")))
                await send(
                    {
                        "type": "http.response.start",
                        "status": (start or {}).get("status", 200),
                        "headers": headers,
                    }
                )
                await send({"type": "http.response.body", "body": rewritten})
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)
