"""GCP Secret Manager integration -- zero .env files in production.

All secrets are fetched from Google Cloud Secret Manager at app startup.
They are injected as environment variables BEFORE Pydantic Settings loads.

Usage:
    # Called automatically by config.py before Settings() is created.
    # In production (GKE / Cloud Run), GCP_PROJECT_ID is auto-detected.
    # In development, set GCP_PROJECT_ID env var or fall back to .env file.

Secret mapping (GCP secret name -> env var):
    ANTHROPIC_API_KEY       -> ANTHROPIC_API_KEY
    database-password       -> DB_PASSWORD (used to build DATABASE_URL)
    GOOGLE_API_KEY          -> GOOGLE_AI_API_KEY
    JWT_SECRET              -> JWT_SECRET_KEY
    REDIS_URL               -> VALKEY_URL
    NEXSIDI_DEPLOY_KEY      -> NEXSIDI_DEPLOY_KEY
    YUGNEX_AI_CREDENTIALS   -> YUGNEX_AI_CREDENTIALS
    ZEPTOMAIL_PASSWORD      -> ZEPTOMAIL_PASSWORD
    ZEPTOMAIL_SENDER_EMAIL  -> ZEPTOMAIL_SENDER_EMAIL
    ZEPTOMAIL_SMTP_PORT     -> ZEPTOMAIL_SMTP_PORT
    ZEPTOMAIL_SMTP_SERVER   -> ZEPTOMAIL_SMTP_SERVER
    ZEPTOMAIL_USERNAME      -> ZEPTOMAIL_USERNAME

DATABASE_URL is constructed from:
    DB_USER     (env var, default: nexsidi_app)
    DB_PASSWORD (from Secret Manager)
    DB_HOST     (env var, e.g. GCP Cloud SQL private IP)
    DB_PORT     (env var, default: 5432)
    DB_NAME     (env var, default: nexsidi)
"""

from __future__ import annotations

import logging
import os
from typing import Any

# Use stdlib logging (structlog may not be configured yet at import time)
logger = logging.getLogger("nexsidi.secret_manager")

# ── GCP Secret Name -> Environment Variable Mapping ────────────

SECRET_TO_ENV: dict[str, str] = {
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "database-password": "DB_PASSWORD",
    "GOOGLE_API_KEY": "GOOGLE_AI_API_KEY",
    "JWT_SECRET": "JWT_SECRET_KEY",
    "REDIS_URL": "VALKEY_URL",
    "NEXSIDI_DEPLOY_KEY": "NEXSIDI_DEPLOY_KEY",
    "YUGNEX_AI_CREDENTIALS": "YUGNEX_AI_CREDENTIALS",
    "ZEPTOMAIL_PASSWORD": "ZEPTOMAIL_PASSWORD",
    "ZEPTOMAIL_SENDER_EMAIL": "ZEPTOMAIL_SENDER_EMAIL",
    "ZEPTOMAIL_SMTP_PORT": "ZEPTOMAIL_SMTP_PORT",
    "ZEPTOMAIL_SMTP_SERVER": "ZEPTOMAIL_SMTP_SERVER",
    "ZEPTOMAIL_USERNAME": "ZEPTOMAIL_USERNAME",
}


def _detect_gcp_project_id() -> str | None:
    """Auto-detect GCP project ID from metadata server (Cloud Run / GKE).

    Falls back to GCP_PROJECT_ID environment variable.
    Returns None if not running on GCP and no env var set.
    """
    # 1. Explicit env var
    project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project_id:
        return project_id

    # 2. GCP metadata server (available on Cloud Run, GKE, GCE)
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


def _fetch_secret(client: Any, project_id: str, secret_name: str) -> str | None:
    """Fetch the latest version of a secret from GCP Secret Manager.

    Returns None if the secret doesn't exist or access is denied.
    """
    resource = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    try:
        response = client.access_secret_version(name=resource)
        return response.payload.data.decode("utf-8")
    except Exception as exc:
        # NotFound, PermissionDenied, etc.
        logger.debug("secret_fetch_skip: %s (%s)", secret_name, exc)
        return None


def _build_database_url() -> str | None:
    """Construct DATABASE_URL from individual env vars.

    Uses: DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_NAME.
    Returns None if DB_HOST or DB_PASSWORD are not set.
    """
    host = os.environ.get("DB_HOST")
    password = os.environ.get("DB_PASSWORD")

    if not host or not password:
        return None

    user = os.environ.get("DB_USER", "nexsidi_app")
    port = os.environ.get("DB_PORT", "5432")
    dbname = os.environ.get("DB_NAME", "nexsidi")

    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"


def load_secrets() -> int:
    """Fetch all secrets from GCP Secret Manager and inject as env vars.

    Called ONCE before Settings() is created.

    Returns:
        Number of secrets successfully loaded.

    Behavior:
        - In production (GCP): fetches all secrets, builds DATABASE_URL.
        - In development (no GCP): silently returns 0, Settings falls back to
          .env file or existing env vars.
        - Never overwrites env vars that are already set (explicit env wins).
    """
    project_id = _detect_gcp_project_id()
    if not project_id:
        logger.info("GCP project not detected -- skipping Secret Manager (dev mode)")
        return 0

    # Import GCP client only when needed (avoids ImportError in dev)
    try:
        from google.cloud.secretmanager import SecretManagerServiceClient
    except ImportError:
        logger.warning(
            "google-cloud-secret-manager not installed. "
            "Install with: pip install google-cloud-secret-manager"
        )
        return 0

    client = SecretManagerServiceClient()
    loaded = 0

    for gcp_name, env_var in SECRET_TO_ENV.items():
        # Don't overwrite existing env vars (explicit config wins)
        if os.environ.get(env_var):
            logger.debug("env_already_set: %s (skipping GCP fetch)", env_var)
            continue

        value = _fetch_secret(client, project_id, gcp_name)
        if value:
            os.environ[env_var] = value
            loaded += 1
            # Log the name but never the value
            logger.info("secret_loaded: %s -> %s", gcp_name, env_var)

    # Build DATABASE_URL from parts if not already set
    if not os.environ.get("DATABASE_URL"):
        db_url = _build_database_url()
        if db_url:
            os.environ["DATABASE_URL"] = db_url
            loaded += 1
            logger.info("DATABASE_URL constructed from DB_HOST + DB_PASSWORD")

    logger.info("secret_manager: loaded %d secrets from project %s", loaded, project_id)
    return loaded
