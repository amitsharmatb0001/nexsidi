"""Tests for app.services.auth — password hashing, JWT tokens."""

import uuid

import pytest
from jose import jwt

from app.config import get_settings
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    """Test bcrypt password operations."""

    def test_hash_password_returns_bcrypt_hash(self):
        hashed = hash_password("TestPassword123!")
        assert hashed.startswith("$2b$")
        assert len(hashed) == 60

    def test_verify_password_correct(self):
        pw = "MySecurePassword!@#"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_password_wrong(self):
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_hash_is_unique_per_call(self):
        pw = "same-password"
        h1 = hash_password(pw)
        h2 = hash_password(pw)
        assert h1 != h2  # Different salts

    def test_long_password_truncated_at_72_bytes(self):
        """bcrypt silently truncates at 72 bytes. Our _prep_password handles this."""
        long_pw = "A" * 200
        hashed = hash_password(long_pw)
        assert verify_password(long_pw, hashed) is True

    def test_unicode_password(self):
        pw = "password-with-unicode-\u0939\u093f\u0902\u0926\u0940"
        hashed = hash_password(pw)
        assert verify_password(pw, hashed) is True


class TestJWTTokens:
    """Test JWT creation and decoding."""

    def setup_method(self):
        get_settings.cache_clear()

    def test_create_access_token(self):
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        token = create_access_token(user_id, org_id, "member")
        assert isinstance(token, str)
        assert len(token) > 50

    def test_access_token_claims(self):
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        token = create_access_token(user_id, org_id, "org_admin")
        payload = decode_token(token)

        assert payload["sub"] == str(user_id)
        assert payload["org"] == str(org_id)
        assert payload["role"] == "org_admin"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_refresh_token_claims(self):
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        token = create_refresh_token(user_id, org_id)
        payload = decode_token(token)

        assert payload["sub"] == str(user_id)
        assert payload["org"] == str(org_id)
        assert payload["type"] == "refresh"
        assert "jti" in payload

    def test_decode_invalid_token_raises(self):
        from jose import JWTError
        with pytest.raises(JWTError):
            decode_token("invalid.token.here")

    def test_decode_wrong_secret_raises(self):
        from jose import JWTError
        settings = get_settings()
        token = jwt.encode(
            {"sub": "test", "type": "access"},
            "wrong-secret-key-that-is-32-chars-long!!",
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(JWTError):
            decode_token(token)

    def test_each_token_has_unique_jti(self):
        user_id = uuid.uuid4()
        org_id = uuid.uuid4()
        t1 = create_access_token(user_id, org_id, "member")
        t2 = create_access_token(user_id, org_id, "member")
        p1 = decode_token(t1)
        p2 = decode_token(t2)
        assert p1["jti"] != p2["jti"]
