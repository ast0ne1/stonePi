from app.services import tls


def test_tls_generation():
    cert_file, key_file = tls.ensure_certificate()
    assert cert_file.endswith("server.crt")
    assert key_file.endswith("server.key")

    status = tls.certificate_status()
    assert status.ready is True
    assert status.ca_present is True
    assert "localhost" in status.sans
    assert "127.0.0.1" in status.sans

    ca_pem = tls.root_ca_pem_bytes()
    assert b"BEGIN CERTIFICATE" in ca_pem
