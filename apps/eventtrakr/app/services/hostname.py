import re
import socket
from urllib.parse import urlparse

from app.config import env

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_hostname(raw: str) -> str:
    return raw.strip().lower().removesuffix(".local").rstrip(".")


def valid_hostname(name: str) -> bool:
    return bool(name) and HOSTNAME_RE.fullmatch(name) is not None


def get_lan_ip() -> str:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("1.1.1.1", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        if sock is not None:
            sock.close()
    if not ip or ip.startswith("127."):
        return ""
    return ip


def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def get_lan_url(scheme: str = "http") -> str:
    ip = get_lan_ip()
    port_str = f":{env.port}" if (scheme == "http" and env.port != 80) or (scheme == "https" and env.port != 443) else ""
    if ip:
        return f"{scheme}://{ip}{port_str}"
    public = env.public_base_url.rstrip("/")
    if public and not _is_loopback(public):
        return public
    return f"{scheme}://127.0.0.1{port_str}"


def get_public_base_url(is_https: bool = False) -> str:
    scheme = "https" if is_https else "http"
    if env.device_hostname:
        host = normalize_hostname(env.device_hostname)
        port_str = f":{env.port}" if (scheme == "http" and env.port != 80) or (scheme == "https" and env.port != 443) else ""
        return f"{scheme}://{host}.local{port_str}"
    public = env.public_base_url.rstrip("/")
    if public and not _is_loopback(public):
        if is_https and public.startswith("http://"):
            return "https://" + public[len("http://"):]
        return public
    return get_lan_url(scheme=scheme)
