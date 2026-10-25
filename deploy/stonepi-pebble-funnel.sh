#!/bin/bash
# Pebble "Away from home": Tailscale Funnel on :8443 → nginx's loopback-only Pebble listener
# (127.0.0.1:8091, deploy/nginx/stonepi.conf), which serves /agent/pebble/v1/ and nothing else.
# Notify → Sidekick runs this through sudoers when an Away from home switch changes; it's on
# only while at least one Pebble is allowed away. Writes the public base URL for AgentLink to
# /var/lib/stonepi/agentlink/away_url (removed when off).
#
# Usage: stonepi-pebble-funnel status|on|off     (one JSON line on stdout)
set -euo pipefail
# Root via sudo: fixed PATH, run from /, and Python in isolated mode (-I: no current folder
# or PYTHON* variables on the import path), whoever calls this and from wherever.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
cd /

PORT=8443
TARGET=http://127.0.0.1:8091
URL_FILE=/var/lib/stonepi/agentlink/away_url
AGENT_USER=stonepi-agentlink

cmd="${1:-status}"
case "$cmd" in
  status | on | off) ;;
  *)
    echo "usage: stonepi-pebble-funnel {status|on|off}" >&2
    exit 2
    ;;
esac

if ! command -v tailscale >/dev/null 2>&1; then
  # No Tailscale, no Funnel: don't leave AgentLink handing out a dead away address.
  rm -f "$URL_FILE"
  printf '{"ok": false, "on": false, "url": "", "message": "Tailscale isn'"'"'t installed."}\n'
  exit 0
fi

exec python3 -I - "$cmd" "$PORT" "$TARGET" "$URL_FILE" "$AGENT_USER" <<'PY'
import json
import os
import pwd
import re
import stat
import subprocess
import sys

cmd, port, target, url_file, agent_user = sys.argv[1:6]


# Kept short: Notify waits for this helper (75 s), and `on` makes three tailscale calls.
def run(args, timeout=15):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def status_json(args):
    code, out = run(args)
    start = out.find("{")
    if code != 0 or start < 0:
        return {}
    try:
        data, _end = json.JSONDecoder().raw_decode(out[start:])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def dns_name():
    data = status_json(["tailscale", "status", "--json"])
    if str(data.get("BackendState") or "") != "Running":
        return ""
    return str((data.get("Self") or {}).get("DNSName") or "").rstrip(".")


def funnel_on(name):
    serve = status_json(["tailscale", "serve", "status", "--json"])
    allowed = serve.get("AllowFunnel") or {}
    return bool(name) and bool(allowed.get(f"{name}:{port}"))


def write_url(url):
    """Root writing into a folder the AgentLink user owns: never follow anything it planted.

    The folder itself can't be swapped (its parent, /var/lib/stonepi, is root's), but any
    name inside it could be a symlink to /etc/... So: refuse a symlinked folder, unlink
    names rather than open them, create the temp file with O_EXCL | O_NOFOLLOW, set its
    owner and mode through the open descriptor, then rename it over the target (rename
    replaces a symlink, it doesn't follow it).
    """
    folder = os.path.dirname(url_file)
    try:
        st = os.lstat(folder)
    except FileNotFoundError:
        return  # AgentLink isn't installed: nothing to tell it
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise SystemExit(f"{folder} is not a plain folder; refusing to write there")
    if not url:
        try:
            os.unlink(url_file)
        except FileNotFoundError:
            pass
        return
    tmp = url_file + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        try:
            pw = pwd.getpwnam(agent_user)
            os.fchown(fd, pw.pw_uid, pw.pw_gid)
        except KeyError:
            pass
        os.fchmod(fd, 0o640)
        os.write(fd, (url + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, url_file)


def emit(**out):
    print(json.dumps(out))


name = dns_name()
if cmd == "status":
    on = funnel_on(name)
    url = f"https://{name}:{port}" if on else ""
    write_url(url)  # keep AgentLink's copy in step with what Tailscale really serves
    emit(ok=True, on=on, url=url, connected=bool(name),
         message="" if name else "Tailscale isn't connected.")
elif cmd == "off":
    run(["tailscale", "funnel", f"--https={port}", "off"])
    write_url("")
    emit(ok=True, on=funnel_on(name), url="", connected=bool(name), message="Away from home is off.")
else:
    if not name:
        emit(ok=False, on=False, url="", connected=False,
             message="Connect Tailscale first (Dashboard → Settings → Network).")
        sys.exit(0)
    code, out = run(["tailscale", "funnel", "--bg", f"--https={port}", target], timeout=25)
    on = funnel_on(name)
    url = f"https://{name}:{port}" if on else ""
    write_url(url)
    enable = ""
    if not on:
        found = re.findall(r"https://login\.tailscale\.com/\S+", out)
        enable = found[0].rstrip(".,)") if found else ""
    emit(ok=on, on=on, url=url, connected=True, enable_url=enable,
         message="Away from home is on." if on else
         "Tailscale didn't allow Funnel. Turn on HTTPS certificates and Funnel for this device in the "
         "Tailscale admin console, then save again.")
PY
