#!/usr/bin/env bash
# Privileged Tailscale helper for stonepi-dash (NOPASSWD via sudoers).
# Uses Tailscale LocalAPI (unix socket) for interactive login URLs.
# Does not touch nginx, Avahi, or exposure.
# stdout = JSON only. Logs go to /tmp/stonepi-tailscale-helper.log
set -uo pipefail

cmd="${1:-}"
AUTH_CACHE="/var/lib/stonepi/dashboard/tailscale_auth_url"
DEBUG_LOG="/tmp/stonepi-tailscale-helper.log"

have_tailscale() {
  command -v tailscale >/dev/null 2>&1
}

case "$cmd" in
  install-check)
    if have_tailscale && systemctl list-unit-files tailscaled.service >/dev/null 2>&1; then
      ver="$(tailscale version 2>/dev/null | head -n1 || true)"
      python3 -c 'import json,sys; print(json.dumps({"ok": True, "installed": True, "version": sys.argv[1]}))' "${ver:-unknown}"
      exit 0
    fi
    python3 -c 'import json; print(json.dumps({"ok": False, "installed": False}))'
    exit 1
    ;;
  status|up|down)
    if ! have_tailscale; then
      python3 -c 'import json; print(json.dumps({"Installed": False, "BackendState": "NoState"}))'
      exit 0
    fi
    # Keep systemd chatter off stdout (JSON only).
    systemctl start tailscaled >/dev/null 2>&1 || true
    export STONEPI_TS_CMD="$cmd"
    export STONEPI_TS_AUTH_CACHE="$AUTH_CACHE"
    export STONEPI_TS_DEBUG_LOG="$DEBUG_LOG"
    mkdir -p "$(dirname "$AUTH_CACHE")" 2>/dev/null || true
    : >>"$DEBUG_LOG" 2>/dev/null || true
    # stderr from python must not become the dashboard JSON payload.
    python3 - <<'PY' 2>>"$DEBUG_LOG"
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

CMD = os.environ.get("STONEPI_TS_CMD", "status")
CACHE = Path(os.environ.get("STONEPI_TS_AUTH_CACHE", "/var/lib/stonepi/dashboard/tailscale_auth_url"))
# Legacy flag from older helpers — deleted on status/up/down; never used for ServeHTTP.
SERVE_FLAG = CACHE.parent / "tailscale_serve_ok"
DEBUG = Path(os.environ.get("STONEPI_TS_DEBUG_LOG", "/tmp/stonepi-tailscale-helper.log"))
SOCKETS = (
    "/var/run/tailscale/tailscaled.sock",
    "/run/tailscale/tailscaled.sock",
)
SERVE_TIMEOUT_S = 6.0


def dbg(msg: str) -> None:
    try:
        with DEBUG.open("a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
    except OSError:
        pass


def emit_json(payload: Dict[str, Any]) -> None:
    # Single JSON object on stdout — nothing else.
    print(json.dumps(payload), flush=True)


def delete_legacy_serve_flag() -> None:
    try:
        if SERVE_FLAG.is_file():
            SERVE_FLAG.unlink()
            dbg("deleted legacy tailscale_serve_ok")
    except OSError as exc:
        dbg("legacy serve flag delete failed: %s" % exc)


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, sock_path: str) -> None:
        super(UnixHTTPConnection, self).__init__("local-tailscaled.sock")
        self._sock_path = sock_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._sock_path)
        if self.timeout is not None:
            self.sock.settimeout(self.timeout)


def sock_path() -> Optional[str]:
    for path in SOCKETS:
        if os.path.exists(path):
            return path
    return None


def localapi(method: str, path: str, body: Optional[bytes] = None, timeout: float = 10.0) -> Tuple[int, bytes]:
    path_sock = sock_path()
    if not path_sock:
        raise RuntimeError("tailscaled socket not found under /var/run/tailscale or /run/tailscale")
    conn = UnixHTTPConnection(path_sock)
    conn.timeout = timeout
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, data


def status_json() -> Dict[str, Any]:
    try:
        code, raw = localapi("GET", "/localapi/v0/status", timeout=8.0)
        if code == 200 and raw:
            data = json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                data["Installed"] = True
                return data
            dbg("localapi status: not a dict")
    except Exception as exc:
        dbg("localapi status failed: %s" % exc)
    try:
        raw = subprocess.check_output(
            ["tailscale", "status", "--json"],
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        data = json.loads(raw.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            data["Installed"] = True
            return data
    except Exception as exc:
        dbg("cli status failed: %s" % exc)
    return {"Installed": True, "BackendState": "Unknown", "Error": "status unavailable"}


def read_cache() -> str:
    try:
        if CACHE.is_file():
            return CACHE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def write_cache(url: str) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(url.strip() + "\n", encoding="utf-8")
        os.chmod(CACHE, 0o644)
    except OSError as exc:
        dbg("cache write failed: %s" % exc)


def clear_cache() -> None:
    try:
        if CACHE.is_file():
            CACHE.unlink()
    except OSError:
        pass
    delete_legacy_serve_flag()


def extract_url(data: Dict[str, Any]) -> str:
    return (data.get("AuthURL") or "").strip()


def url_from_text(blob: str) -> str:
    match = re.search(r"https://[^\s\"'<>]*tailscale\.com/[^\s\"'<>]+", blob or "")
    return match.group(0) if match else ""


def serve_status_json() -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            ["tailscale", "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        dbg("serve status --json failed: %s" % exc)
        return {}


def _proxies_loopback_80(handler: Any) -> bool:
    """True when a Serve handler proxies to loopback TCP 80 (local nginx)."""
    if isinstance(handler, str):
        target = handler
    elif isinstance(handler, dict):
        target = handler.get("Proxy") or handler.get("proxy") or ""
    else:
        return False
    if not isinstance(target, str) or not target.strip():
        return False
    raw = target.strip()
    if "://" not in raw:
        raw = "http://" + raw
    try:
        u = urlparse(raw)
        host = (u.hostname or "").lower()
        scheme = (u.scheme or "http").lower()
        port = u.port
    except Exception:
        return False
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    if port is None:
        if scheme in ("http", ""):
            port = 80
        else:
            return False
    return port == 80


def serve_http_configured(data: Optional[Dict[str, Any]] = None) -> bool:
    """True only when Serve proxies to local nginx (loopback:80)."""
    cfg = data if data is not None else serve_status_json()
    if not cfg:
        return False
    web = cfg.get("Web")
    if isinstance(web, dict):
        for _host, entry in web.items():
            if not isinstance(entry, dict):
                continue
            handlers = entry.get("Handlers") or {}
            if not isinstance(handlers, dict):
                continue
            for _path, handler in handlers.items():
                if _proxies_loopback_80(handler):
                    return True
    return False


def ensure_want_running() -> None:
    try:
        body = json.dumps({"WantRunning": True}).encode("utf-8")
        code, _raw = localapi("PATCH", "/localapi/v0/prefs", body=body, timeout=5.0)
        dbg("prefs WantRunning -> HTTP %s" % code)
    except Exception as exc:
        dbg("prefs patch failed: %s" % exc)
    try:
        subprocess.Popen(
            ["tailscale", "up", "--timeout=1s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        dbg("tailscale up nudge failed: %s" % exc)


def start_login_interactive() -> None:
    code, raw = localapi("POST", "/localapi/v0/login-interactive", timeout=10.0)
    dbg("login-interactive -> HTTP %s body=%r" % (code, raw[:200]))
    if code not in (200, 204):
        raise RuntimeError("login-interactive HTTP %s: %r" % (code, raw[:200]))


def watch_browse_url(seconds: float = 12.0) -> str:
    path_sock = sock_path()
    if not path_sock:
        return ""
    deadline = time.time() + seconds
    try:
        conn = UnixHTTPConnection(path_sock)
        conn.timeout = seconds + 2
        conn.request("GET", "/localapi/v0/watch-ipn-bus?timeout=15s")
        resp = conn.getresponse()
        if resp.status != 200:
            dbg("watch-ipn-bus HTTP %s" % resp.status)
            conn.close()
            return ""
        buf = b""
        while time.time() < deadline:
            chunk = resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    note = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                browse = note.get("BrowseToURL")
                if isinstance(browse, str) and browse.startswith("http"):
                    dbg("BrowseToURL from bus: %s" % browse)
                    conn.close()
                    return browse.strip()
        conn.close()
    except Exception as exc:
        dbg("watch-ipn-bus failed: %s" % exc)
    return ""


def wait_for_auth_url(seconds: float = 18.0) -> str:
    deadline = time.time() + seconds
    while time.time() < deadline:
        data = status_json()
        url = extract_url(data)
        if url:
            dbg("AuthURL from status: %s" % url)
            return url
        cached = read_cache()
        if cached:
            return cached
        time.sleep(0.7)
    return extract_url(status_json()) or read_cache()


def ensure_http_serve() -> Tuple[bool, str]:
    """Expose local nginx (:80) over Tailscale HTTPS. Returns (ok, serve_enable_url)."""
    if serve_http_configured():
        return True, ""
    attempts = (
        ["tailscale", "serve", "--bg", "http://127.0.0.1:80"],
        ["tailscale", "serve", "--bg", "80"],
    )
    enable_url = ""
    for cmd in attempts:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SERVE_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
            )
            blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
            dbg(
                "serve cmd=%s exit=%s out=%r err=%r"
                % (cmd, proc.returncode, (proc.stdout or "")[:200], (proc.stderr or "")[:200])
            )
            found = url_from_text(blob)
            if found and ("/f/serve" in found or "serve" in found.lower()):
                enable_url = found
            elif found and not enable_url:
                enable_url = found
            if proc.returncode == 0 and serve_http_configured():
                return True, ""
        except subprocess.TimeoutExpired as exc:
            blob = ""
            if exc.stdout:
                blob += exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
            if exc.stderr:
                blob += "\n" + (
                    exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", "replace")
                )
            found = url_from_text(blob)
            if found:
                enable_url = found
            dbg("serve timed out %s: %s enable=%r" % (cmd, exc, enable_url))
        except Exception as exc:
            dbg("serve failed %s: %s" % (cmd, exc))
    if serve_http_configured():
        return True, ""
    return False, enable_url


def reset_serve() -> None:
    try:
        subprocess.run(
            ["tailscale", "serve", "reset"],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        dbg("serve reset failed: %s" % exc)


def emit(data: Dict[str, Any], auth_override: str = "", enable_serve: bool = False) -> None:
    out = dict(data)
    out["Installed"] = True
    delete_legacy_serve_flag()
    backend = str(out.get("BackendState") or "")
    auth = (auth_override or extract_url(out) or read_cache()).strip()
    if backend == "Running":
        auth = ""
        try:
            if CACHE.is_file():
                CACHE.unlink()
        except OSError:
            pass
        serve_enable_url = ""
        if enable_serve:
            try:
                _ok, serve_enable_url = ensure_http_serve()
            except Exception as exc:
                dbg("ensure_http_serve: %s" % exc)
            try:
                refreshed = status_json()
                out.update(refreshed)
            except Exception:
                pass
        serve_ok = serve_http_configured()
        out["Installed"] = True
        out["ServeHTTP"] = serve_ok
        if serve_enable_url and not serve_ok:
            out["ServeEnableURL"] = serve_enable_url
    elif auth:
        out["AuthURL"] = auth
        if backend in ("NoState", "Stopped", "Unknown", ""):
            out["BackendState"] = "NeedsLogin"
        write_cache(auth)
        out["ServeHTTP"] = False
    else:
        out["ServeHTTP"] = False
    emit_json(out)


def do_down() -> None:
    reset_serve()
    subprocess.run(["tailscale", "logout"], capture_output=True, text=True, timeout=20)
    subprocess.run(["tailscale", "down"], capture_output=True, text=True, timeout=15)
    clear_cache()
    emit_json({"ok": True})


def do_up() -> None:
    dbg("up: begin")
    ensure_want_running()
    time.sleep(0.5)
    login_err = ""
    try:
        start_login_interactive()
    except Exception as exc:
        login_err = str(exc)
        dbg("login-interactive error: %s" % exc)
        try:
            proc = subprocess.run(
                ["tailscale", "login"],
                capture_output=True,
                text=True,
                timeout=12,
                stdin=subprocess.DEVNULL,
            )
            blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
            dbg("tailscale login exit=%s out=%r" % (proc.returncode, blob[:500]))
            found = url_from_text(blob)
            if found:
                emit(status_json(), found, enable_serve=True)
                return
        except Exception as exc2:
            dbg("cli login failed: %s" % exc2)
            login_err = "%s; cli login: %s" % (login_err, exc2)

    url = watch_browse_url(10.0) or wait_for_auth_url(10.0)
    data = status_json()
    if not url:
        data["Error"] = login_err or data.get("Error") or (
            "No login link from Tailscale yet. Check internet, then try Connect again."
        )
        dbg("up: no URL; backend=%s err=%s" % (data.get("BackendState"), data.get("Error")))
    emit(data, url, enable_serve=True)


def do_status() -> None:
    """Read-only: never runs `tailscale serve` setup."""
    data = status_json()
    auth = extract_url(data) or read_cache()
    emit(data, auth, enable_serve=False)


try:
    if CMD == "down":
        do_down()
    elif CMD == "up":
        do_up()
    else:
        do_status()
except Exception as exc:
    dbg("fatal: %s" % exc)
    emit_json(
        {
            "Installed": True,
            "BackendState": "Unknown",
            "Error": "helper failed: %s" % exc,
            "ServeHTTP": False,
        }
    )
PY
    ;;
  *)
    echo "usage: stonepi-tailscale {status|up|down|install-check}" >&2
    exit 2
    ;;
esac
