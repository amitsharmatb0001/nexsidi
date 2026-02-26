"""Application configuration via environment variables.

Uses Pydantic BaseSettings for validation and type coercion.
Every setting has a sensible default or raises at startup if missing.

Secret loading order (highest priority wins):
1. Explicit environment variables (e.g. set in Cloud Run / k8s)
2. GCP Secret Manager (fetched at startup via load_secrets())
3. .env file (development fallback)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("nexsidi.config")


class Settings(BaseSettings):
    """Immutable application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Environment ---
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Database (app user -- RLS enforced, no DDL) ---
    database_url: PostgresDsn
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_recycle: int = Field(default=300, ge=60, description="Seconds before connection recycled")

    # --- Database (admin -- migrations only, never used by app) ---
    database_admin_url: PostgresDsn | None = None

    # --- Valkey (Redis-compatible cache) ---
    valkey_url: str = "redis://localhost:6379/0"
    valkey_password: str = ""

    # --- JWT Auth ---
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)

    # --- Google OAuth ---
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- GCP Dual-Project (NexSidi infra + YugNex AI) ---
    gcp_project_id: str = ""                          # NexSidi: DB, secrets, infra
    gcp_ai_project_id: str = "yugnex-ai"              # YugNex: Vertex AI / Gemini
    gcp_ai_key_secret: str = "YUGNEX_AI_CREDENTIALS"  # SA key stored in NexSidi SM
    use_cloud_secrets: bool = False                    # Toggle GCP Secret Manager

    # --- AI Providers ---
    anthropic_api_key: str = ""
    google_ai_api_key: str = ""

    # --- Email (ZeptoMail) ---
    zeptomail_smtp_server: str = ""
    zeptomail_smtp_port: int = 587
    zeptomail_username: str = ""
    zeptomail_password: str = ""
    zeptomail_sender_email: str = ""

    # --- Deployment ---
    nexsidi_deploy_key: str = ""

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Feature Flags (auth features disabled by default) ---
    enable_phone_otp: bool = False
    enable_totp: bool = False
    enable_passkeys: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL is required")
        return v

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def async_database_url(self) -> str:
        """Return database URL string for SQLAlchemy async engine."""
        return str(self.database_url)

    @property
    def has_email(self) -> bool:
        """Check if ZeptoMail is configured."""
        return bool(self.zeptomail_smtp_server and self.zeptomail_username)


# ── Load secrets from GCP BEFORE Settings is created ───────────

_secrets_loaded = False


def _ensure_secrets_loaded() -> None:
    """Load secrets from GCP Secret Manager (once, on first call)."""
    global _secrets_loaded
    if _secrets_loaded:
        return
    _secrets_loaded = True

    try:
        from app.services.secret_manager import load_secrets
        count = load_secrets()
        if count > 0:
            logger.info("Loaded %d secrets from GCP Secret Manager", count)
    except Exception as exc:
        # Non-fatal: fall back to env vars / .env
        logger.debug("Secret Manager unavailable: %s", exc)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Call this everywhere instead of constructing Settings().

    On first call, attempts to load secrets from GCP Secret Manager.
    If GCP is not available, falls back to env vars and .env file.
    """
    _ensure_secrets_loaded()
    return Settings()  # type: ignore[call-arg]
