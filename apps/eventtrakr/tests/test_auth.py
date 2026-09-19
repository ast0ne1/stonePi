import pytest
from app.services import passwords
from app.services.auth import create_session_token, parse_session_token
from app.db import init_db


def test_password_hashing():
    pw = "Secret123!"
    hashed = passwords.hash_password(pw)
    assert passwords.is_hashed(hashed)
    assert passwords.verify_password(hashed, pw)
    assert not passwords.verify_password(hashed, "WrongPass")


def test_session_token():
    init_db()
    token = create_session_token(1, "admin", "admin")
    assert token is not None
    user = parse_session_token(token)
    assert user is not None
    assert user.username == "admin"
    assert user.role == "admin"
