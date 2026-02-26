"""Tests for app.config — Settings validation."""

import os

import pytest
from pydantic import ValidationError


class TestSettings:
    """Test Settings configuration."""

    def test_settings_loads(self, settings):
        assert settings.environment == "development"
        assert settings.jwt_algorithm == "HS256"
        assert settings.access_token_expire_minutes == 15
        assert settings.refresh_token_expire_days == 7

    def test_settings_has_required_fields(self, settings):
        assert settings.jwt_secret_key
        assert len(settings.jwt_secret_key) >= 32
        assert str(settings.database_url)

    def test_settings_feature_flags_default_off(self, settings):
        assert settings.enable_phone_otp is False
        assert settings.enable_totp is False
        assert settings.enable_passkeys is False

    def test_settings_is_production(self, settings):
        assert settings.is_production is False

    def test_settings_async_database_url(self, settings):
        url = settings.async_database_url
        assert "postgresql" in url

    def test_settings_cors_origins(self, settings):
        assert isinstance(settings.cors_origins, list)

    def test_settings_pool_defaults(self, settings):
        assert settings.db_pool_size == 10
        assert settings.db_max_overflow == 20
        assert settings.db_pool_recycle == 300

    def test_jwt_secret_too_short_raises(self):
        """JWT secret under 32 chars should fail validation."""
        from app.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/db",
                jwt_secret_key="short",
            )
