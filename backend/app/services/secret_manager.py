"""GCP Secret Manager integration -- zero .env files in production.

Dual-project architecture:
    NexSidi project (GCP_PROJECT_ID) -- DB, infra, all secrets
    YugNex project  (GCP_AI_PROJECT_ID) -- Vertex AI / Gemini models

All secrets live in the NexSidi project's Secret Manager, including
YUGNEX_AI_CREDENTIALS (a service account JSON key for the YugNex project).
The app fetches that key, parses it into Credentials, and uses it to call
Vertex AI REST API in the YugNex project.

Secret mapping (GCP secret name -> env var):
    ANTHROPIC_API_KEY       -> ANTHROPIC_API_KEY
    database-password       -> DB_PASSWORD (used to build DATABASE_URL)
    GOOGLE_API_KEY          -> GOOGLE_AI_API_KEY
    JWT_SECRET              -> JWT_SECRET_KEY
    REDIS_URL               -> VALKEY_URL
    NEXSIDI_DEPLOY_KEY      -> NEXSIDI_DEPLOY_KEY
    YUGNEX_AI_CREDENTIALS   -> YUGNEX_AI_CREDENTIALS  (service account JSON)
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

import json
import logging
import os
import time
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

# Cache TTL for secrets (15 minutes)
_CACHE_TTL_SECONDS = 900


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


def _build_admin_database_url() -> str | None:
    """Construct DATABASE_ADMIN_URL for migrations (DDL permissions).

    Same DB_HOST/DB_PORT/DB_NAME but uses DB_ADMIN_USER (default: nexsidi_admin)
    and DB_ADMIN_PASSWORD (falls back to DB_PASSWORD if not set separately).
    """
    host = os.environ.get("DB_HOST")
    password = os.environ.get("DB_ADMIN_PASSWORD") or os.environ.get("DB_PASSWORD")

    if not host or not password:
        return None

    user = os.environ.get("DB_ADMIN_USER", "nexsidi_admin")
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

    # Build DATABASE_ADMIN_URL for migrations (DDL permissions)
    if not os.environ.get("DATABASE_ADMIN_URL"):
        admin_url = _build_admin_database_url()
        if admin_url:
            os.environ["DATABASE_ADMIN_URL"] = admin_url
            loaded += 1
            logger.info("DATABASE_ADMIN_URL constructed from DB_HOST + DB_ADMIN_USER")

    logger.info("secret_manager: loaded %d secrets from project %s", loaded, project_id)
    return loaded


# ── Vertex AI Credential Manager (dual-project) ───────────────

class VertexAICredentialManager:
    """Manages Vertex AI credentials for the YugNex project.

    Fetches YUGNEX_AI_CREDENTIALS (service account JSON) from NexSidi's
    Secret Manager, parses it, and handles token refresh for Vertex AI
    REST API calls.

    Usage:
        mgr = get_vertex_credentials()
        token = mgr.get_access_token()
        # Use token in: Authorization: Bearer {token}
        # Against: https://aiplatform.googleapis.com/v1/projects/{yugnex-ai}/...
    """

    def __init__(self) -> None:
        self._credentials: Any = None
        self._token: str | None = None
        self._token_expiry: float = 0
        self._initialized = False

    def initialize(self) -> bool:
        """Load YugNex service account credentials.

        Tries in order:
        1. YUGNEX_AI_CREDENTIALS env var (JSON string, set by load_secrets)
        2. GOOGLE_APPLICATION_CREDENTIALS file path (local dev fallback)

        Returns True if credentials are available.
        """
        if self._initialized:
            return self._credentials is not None

        self._initialized = True

        # Method 1: From env var (populated by Secret Manager)
        creds_json = os.environ.get("YUGNEX_AI_CREDENTIALS")
        if creds_json:
            try:
                from google.oauth2 import service_account
                key_data = json.loads(creds_json)
                self._credentials = service_account.Credentials.from_service_account_info(
                    key_data,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                logger.info("Vertex AI credentials loaded from YUGNEX_AI_CREDENTIALS")
                return True
            except Exception as exc:
                logger.error("Failed to parse YUGNEX_AI_CREDENTIALS: %s", exc)

        # Method 2: Local file fallback (dev)
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if creds_path and os.path.isfile(creds_path):
            try:
                from google.oauth2 import service_account
                self._credentials = service_account.Credentials.from_service_account_file(
                    creds_path,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                logger.info("Vertex AI credentials loaded from file: %s", creds_path)
                return True
            except Exception as exc:
                logger.error("Failed to load credentials file: %s", exc)

        logger.warning("No Vertex AI credentials available -- Gemini models disabled")
        return False

    def get_access_token(self) -> str | None:
        """Get a valid access token for Vertex AI REST API.

        Automatically refreshes if expired (tokens last ~1 hour).
        Returns None if no credentials are available.
        """
        if self._credentials is None:
            if not self.initialize():
                return None

        # Token still valid (with 5-min buffer)
        if self._token and time.time() < (self._token_expiry - 300):
            return self._token

        # Refresh token
        try:
            import google.auth.transport.requests
            auth_req = google.auth.transport.requests.Request()
            self._credentials.refresh(auth_req)
            self._token = self._credentials.token
            self._token_expiry = time.time() + 3600  # 1 hour
            logger.info("Vertex AI token refreshed")
            return self._token
        except Exception as exc:
            logger.error("Token refresh failed: %s", exc)
            self._token = None
            return None

    @property
    def has_credentials(self) -> bool:
        """Check if Vertex AI credentials are available."""
        if not self._initialized:
            self.initialize()
        return self._credentials is not None

    @property
    def ai_project_id(self) -> str:
        """The YugNex GCP project ID for Vertex AI calls."""
        return os.environ.get("GCP_AI_PROJECT_ID", "yugnex-ai")


# ── Singleton ──────────────────────────────────────────────────

_vertex_mgr: VertexAICredentialManager | None = None


def get_vertex_credentials() -> VertexAICredentialManager:
    """Get or create the Vertex AI credential manager singleton."""
    global _vertex_mgr
    if _vertex_mgr is None:
        _vertex_mgr = VertexAICredentialManager()
    return _vertex_mgr
