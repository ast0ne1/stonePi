from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import TLS_DIR, env
from app.services import hostname

CA_KEY = TLS_DIR / "ca.key"
CA_CERT = TLS_DIR / "ca.crt"
SERVER_KEY = TLS_DIR / "server.key"
SERVER_CERT = TLS_DIR / "server.crt"
META_PATH = TLS_DIR / "server.json"

CA_YEARS = 10
SERVER_DAYS = 365
KEY_BITS = 2048


@dataclass
class CertificateStatus:
    ready: bool
    not_after: str | None
    sans: list[str]
    ca_present: bool
    message: str


def _write_private_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _load_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _load_key(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Expected RSA private key")
    return key


def desired_sans() -> list[str]:
    names = ["localhost"]
    ips = {"127.0.0.1", "::1"}
    if env.device_hostname:
        host = hostname.normalize_hostname(env.device_hostname)
        if host:
            names.append(f"{host}.local")
    lan = hostname.get_lan_ip()
    if lan:
        ips.add(lan)
    return sorted(names) + sorted(ips, key=lambda value: (":" in value, value))


def _san_list_from_cert(cert: x509.Certificate) -> list[str]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    out: list[str] = []
    for name in ext.value:
        if isinstance(name, x509.DNSName):
            out.append(name.value)
        elif isinstance(name, x509.IPAddress):
            out.append(str(name.value))
    return out


def _new_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=KEY_BITS)


def _ensure_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    if CA_CERT.exists() and CA_KEY.exists():
        try:
            return _load_cert(CA_CERT), _load_key(CA_KEY)
        except Exception:
            pass
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    key = _new_rsa_key()
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "EventTrakr Local CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EventTrakr"),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365 * CA_YEARS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_private_key(CA_KEY, key)
    _write_cert(CA_CERT, cert)
    return cert, key


def _build_server_cert(
    ca_cert: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    sans: list[str],
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _new_rsa_key()
    now = datetime.now(timezone.utc)
    cn = next((s for s in sans if s.endswith(".local")), "eventtrakr.local")
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EventTrakr"),
        ]
    )
    alt_names: list[x509.GeneralName] = []
    for entry in sans:
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(entry)))
        except ValueError:
            alt_names.append(x509.DNSName(entry))
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=SERVER_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return cert, key


def ensure_certificate() -> tuple[str, str]:
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = _ensure_ca()
    sans = desired_sans()
    if SERVER_CERT.exists() and SERVER_KEY.exists() and META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            if meta.get("sans") == sans:
                cert = _load_cert(SERVER_CERT)
                if cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=14):
                    return str(SERVER_CERT), str(SERVER_KEY)
        except Exception:
            pass

    server_cert, server_key = _build_server_cert(ca_cert, ca_key, sans)
    _write_private_key(SERVER_KEY, server_key)
    _write_cert(SERVER_CERT, server_cert)
    META_PATH.write_text(
        json.dumps(
            {
                "sans": sans,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "not_after": server_cert.not_valid_after_utc.isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return str(SERVER_CERT), str(SERVER_KEY)


def certificate_status() -> CertificateStatus:
    ca_present = CA_CERT.exists()
    if not SERVER_CERT.exists() or not SERVER_KEY.exists():
        return CertificateStatus(
            ready=False,
            not_after=None,
            sans=[],
            ca_present=ca_present,
            message="No server certificate yet. Enable HTTPS in Settings.",
        )
    try:
        cert = _load_cert(SERVER_CERT)
    except Exception:
        return CertificateStatus(
            ready=False,
            not_after=None,
            sans=[],
            ca_present=ca_present,
            message="Server certificate corrupted or unreadable.",
        )
    sans = _san_list_from_cert(cert)
    not_after = cert.not_valid_after_utc
    expired = not_after < datetime.now(timezone.utc)
    if expired:
        return CertificateStatus(
            ready=False,
            not_after=not_after.date().isoformat(),
            sans=sans,
            ca_present=ca_present,
            message="Certificate expired. Save Settings with HTTPS enabled to renew.",
        )
    return CertificateStatus(
        ready=True,
        not_after=not_after.date().isoformat(),
        sans=sans,
        ca_present=ca_present,
        message="Certificate active. Download and trust the Root CA on your device.",
    )


def root_ca_pem_bytes() -> bytes:
    if not CA_CERT.exists():
        _ensure_ca()
    return CA_CERT.read_bytes()


def ssl_file_paths() -> tuple[str, str]:
    if not SERVER_CERT.exists() or not SERVER_KEY.exists():
        return ensure_certificate()
    return str(SERVER_CERT), str(SERVER_KEY)
