from app.services import token_crypto


def test_seal_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("STONEPI_VAULT_DIR", str(tmp_path))
    token_crypto.reset_for_tests()
    sealed = token_crypto.seal("ya29.access")
    assert token_crypto.is_sealed(sealed)
    assert "ya29" not in sealed
    assert token_crypto.open_token(sealed) == "ya29.access"
    assert token_crypto.open_token("legacy-plain") == "legacy-plain"
    assert token_crypto.seal("") == ""
    assert token_crypto.seal(sealed) == sealed
