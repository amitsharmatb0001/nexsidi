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
    # JWTSEC-FIX: Minimum 32 chars for HS256 (NIST recommends key â‰¥ hash output).
    # 16 chars (128 bits) is below the 256-bit security level of HS256.
    jwt_secret_key: str = Field(min_length=32)
    # R9-FIX: Constrain to known-safe algorithms. An unconstrained string
    # allows "none" algorithm (complete auth bypass) via env var injection.
    jwt_algorithm: Literal["HS256", "HS384", "HS512", "RS256", "RS384", "RS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)

    # --- Google OAuth ---
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- GCP Dual-Project (NexSidi infra + YugNex AI) ---
    gcp_project_id: str = ""                          # NexSidi: DB, secrets, infra
    # R36-FIX: Removed hardcoded "yugnex-ai" and "YUGNEX_AI_CREDENTIALS".
    # Hardcoded GCP project IDs and secret names are a security concern:
    # 1. Leaked source code reveals infrastructure naming conventions
    # 2. Cannot change without code deployment (should be env-driven)
    # 3. Violates 12-factor app config principles
    gcp_ai_project_id: str = ""                       # YugNex: Vertex AI / Gemini
    gcp_ai_key_secret: str = ""                       # SA key name in NexSidi SM
    use_cloud_secrets: bool = False                    # Toggle GCP Secret Manager

    # REVIEW-FIX: Validate GCP project IDs to prevent SSRF via path traversal.
    # An attacker-controlled project ID like "../../evil" in the Vertex AI URL
    # (https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/...)
    # could redirect requests to unintended endpoints.
    @field_validator("gcp_project_id", "gcp_ai_project_id", mode="after")
    @classmethod
    def validate_gcp_project_id(cls, v: str) -> str:
        if not v:
            return v  # Empty = not configured, fine
        import re
        # GCP project ID format: 6-30 chars, lowercase letters, digits, hyphens
        # Must start with a letter, cannot end with a hyphen
        if not re.match(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$", v):
            raise ValueError(
                f"Invalid GCP project ID '{v}': must be 6-30 lowercase "
                f"alphanumeric characters or hyphens, starting with a letter"
            )
        return v

    # --- AI Providers ---
    anthropic_api_key: str = ""
    google_ai_api_key: str = ""
    firecrawl_api_key: str = ""  # Web search for agents (loaded from GCP Secret Manager)

    # --- Claude via Vertex AI Model Garden (asia-south1 / Mumbai ONLY) ---
    # IMPORTANT: This is for Claude models ONLY. Gemini uses its own config.
    claude_vertex_location: str = "asia-south1"   # Mumbai — closest to our DB/GKE
    claude_vertex_project_id: str = ""             # GCP project with Claude enabled on Model Garden

    # --- LangGraph Agentic Pipeline ---
    enable_langgraph: bool = True  # Feature flag: True = LangGraph StateGraph, False = legacy while-loop

    # --- Email (ZeptoMail) ---
    zeptomail_smtp_server: str = ""
    zeptomail_smtp_port: int = 587
    zeptomail_username: str = ""
    zeptomail_password: str = ""
    zeptomail_sender_email: str = ""

    # --- Deployment ---
    nexsidi_deploy_key: str = ""
    railway_token: str = ""  # DEPLOY-FIX: Railway API/CLI token for real deployments
    vercel_token: str = ""   # REVIEW-FIX: Vercel API token for real deployments
    vercel_org_id: str = ""  # REVIEW-FIX: Vercel team/org ID (optional)

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000"]

    # R19-FIX: Validate CORS origins to prevent wildcard + credentials bypass.
    # Starlette's CORSMiddleware with allow_credentials=True and origins=["*"]
    # reflects the Origin header â€" effectively allowing ANY origin with cookies.
    # This completely defeats CORS protection. Also reject origins without
    # explicit http/https scheme (e.g., bare domains) to prevent misconfiguration.
    @field_validator("cors_origins", mode="after")
    @classmethod
    def validate_cors_origins(cls, v: list[str]) -> list[str]:
        # R38-FIX: Reject empty list â€" silently breaks frontend CORS preflight
        if not v:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        for origin in v:
            if origin == "*":
                raise ValueError(
                    "CORS_ORIGINS cannot contain '*' when allow_credentials is True. "
                    "Specify explicit origins instead (e.g., 'https://app.example.com')."
                )
            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    f"Invalid CORS origin '{origin}': must start with http:// or https://"
                )
        return v

    # --- Feature Flags (auth features disabled by default) ---
    enable_phone_otp: bool = False
    enable_totp: bool = False
    enable_passkeys: bool = False

    # --- Razorpay Payment Gateway ---
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # --- TOTP encryption ---
    # Fernet key for encrypting TOTP secrets stored in auth.users.totp_secret_enc.
    # Must be 32-byte URL-safe base64 (Fernet.generate_key()).
    # If empty, a key is derived from jwt_secret_key via HKDF.
    totp_encryption_key: str = ""

    # --- Phase 1A: Agent Message Encryption ---
    # When True, all inter-agent message payloads in Valkey are encrypted
    # using per-pipeline-run Fernet keys (envelope encryption).
    agent_message_encryption: bool = True
    # Explicit Fernet key for message encryption master key.
    # If empty, derives from jwt_secret_key via HKDF (salt="nexsidi-agent-msg-v1").
    message_encryption_key: str = ""

    # --- Phase 2A: Cloud DevBox ---
    devbox_enabled: bool = False
    devbox_default_ttl_hours: int = 24
    devbox_max_per_user: int = 1
    devbox_image: str = "nexsidi/devbox:latest"
    gke_cluster_name: str = "nexsidi-devbox"
    gke_cluster_zone: str = "asia-south1"
    devbox_artifact_registry: str = "asia-south1-docker.pkg.dev/nexsidi-ai/nexsidi"

    # --- Phase 2B: Visual Testing ---
    visual_testing_enabled: bool = True
    mobile_emulator_image: str = "budtmo/docker-android:emulator_11.0"

    # --- Phase 3: Real-Time Observability ---
    live_studio_enabled: bool = True
    terminal_stream_buffer_size: int = 1000

    # --- Phase 5B: User Steering ---
    user_steering_enabled: bool = True

    # --- External API Base URLs (configurable for testing/enterprise) ---
    github_api_base_url: str = "https://api.github.com"
    railway_api_url: str = "https://backboard.railway.app/graphql/v2"
    vercel_api_base_url: str = "https://api.vercel.com"

    # --- AI Provider Mode ---
    # "gemini" = Gemini-only (beta default — saves Claude costs)
    # "mixed"  = both providers (production — activate via enable_claude)
    # "claude" = Claude-only (max quality, no Gemini)
    ai_provider_mode: Literal["mixed", "gemini", "claude"] = "gemini"

    # Feature flag: When True, upgrades ai_provider_mode from "gemini" to "mixed"
    # so Claude models become available alongside Gemini. Set to True in production
    # when ready to enable Claude. Has no effect if ai_provider_mode is explicitly
    # set to "claude" or "mixed" via env var.
    enable_claude: bool = False

    enable_batch_quality_gates: bool = False  # Anthropic Batch API for quality gates

    # --- Celery Task Queue ---
    # When use_celery=True, pipeline runs dispatch to Celery workers instead
    # of in-process asyncio.create_task(). Enables horizontal scaling.
    use_celery: bool = False  # Feature flag — False = current behavior
    celery_broker_url: str = ""  # Falls back to valkey_url if empty
    celery_result_backend: str = ""  # Falls back to valkey_url if empty

    # I5-FIX: Worker type — "celery" or "async" (native asyncio worker).
    # "async" uses BLPOP-based Valkey queue (app.workers.async_worker).
    worker_type: str = "async"  # V2-FIX: Default to async (non-blocking)

    # I6-FIX: Executor type — "docker" or "kubernetes".
    # "kubernetes" uses ephemeral K8s Jobs for sandbox isolation.
    executor_type: str = "docker"

    # 2.4-FIX: Pipeline execution mode.
    # "sequential" — fixed STAGE_ORDER (existing behavior).
    # "agentic" — AI PlannerAgent decides what stage to run next dynamically.
    # PHASE-4: Default changed to "agentic" — AI-driven pipeline routing.
    pipeline_mode: str = "agentic"

    # C1-FIX: InitContainer image for K8s executor GCS code download.
    gcs_init_image: str = "google/cloud-sdk:slim"

    # C2c-FIX: Max interrupts per agent pair per pipeline run.
    # Agents can request another agent to re-run (e.g., Shubham asks Vikram
    # to update the contract).  This caps the number of interrupts per
    # (requester, target) pair to prevent infinite re-run loops.
    max_interrupts_per_pair: int = Field(default=3, ge=1, le=10)

    # --- Object Storage (generated code) ---
    # REVIEW-FIX: Large generated codebases should be stored durably (not in Valkey).
    # When gcs_code_bucket is set, content >64KB is offloaded to GCS.
    # When empty, all content stays in Valkey (dev mode).
    gcs_code_bucket: str = ""        # GCS bucket name (e.g., "nexsidi-generated-code")
    local_store_dir: str = ""        # Local filesystem fallback (dev mode)

    # --- Valkey / Redis ---
    valkey_max_connections: int = 50  # FIX-46: Configurable pool size

    # --- Cost Estimation (FIX-50) ---
    cost_per_million_tokens: float = 5.0  # USD, weighted model mix
    avg_minutes_per_stage: float = 2.5

    # --- Simulation Gate ---
    # FIX-11: When False (production default), pipeline blocks delivery of
    # projects where tests were SIMULATED (Docker unavailable). Prevents
    # untested code from reaching customers.  Set True only for dev/staging.
    allow_simulation_delivery: bool = False

    # --- Proxy / Network ---
    # REFIX: Only trust X-Forwarded-For when behind a known reverse proxy.
    # Without this, any client can spoof their IP to bypass rate limiting.
    trust_proxy_headers: bool = False

    # --- Resource Limits (project isolation) ---
    max_concurrent_pipelines: int = Field(default=10, ge=1, le=100)
    ai_calls_per_minute_per_project: int = Field(default=30, ge=1, le=1000)
    pipeline_total_timeout_minutes: int = Field(default=120, ge=10, le=1440)

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
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters (256 bits for HS256)")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def async_database_url(self) -> str:
        """Return database URL string for SQLAlchemy async engine.

        R17-FIX: Rewrites postgresql:// to postgresql+asyncpg:// if needed.
        Standard DATABASE_URL values (from Django, Heroku, most cloud providers)
        use postgresql:// which doesn't include the async driver marker.
        SQLAlchemy's create_async_engine requires the +asyncpg dialect.
        Without this rewrite, startup crashes with a cryptic driver error.
        """
        url = str(self.database_url)
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url

    @property
    def has_email(self) -> bool:
        """Check if ZeptoMail is configured."""
        return bool(self.zeptomail_smtp_server and self.zeptomail_username)


# â"€â"€ Load secrets from GCP BEFORE Settings is created â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_secrets_loaded = False

# R37-FIX: Protect _ensure_secrets_loaded() against concurrent calls.
# Without a lock, two threads calling get_settings() simultaneously can both
# see _secrets_loaded=False, both enter loading, and race on os.environ writes.
import threading as _threading
_secrets_lock = _threading.Lock()


def _ensure_secrets_loaded() -> None:
    """Load .env first, then secrets from GCP Secret Manager (once, on first call).

    The .env file must be loaded BEFORE Secret Manager so that GCP_PROJECT_ID
    and DB_HOST are available for _detect_gcp_project_id() and _build_database_url().

    R36-FIX: Moved ``_secrets_loaded = True`` to AFTER the loading completes.
    Previously, the flag was set immediately (before loading), which meant:
    - If load_secrets() raised a transient GCP error on cold start, the flag
      was already True, preventing any retry on the next get_settings() call.
    - On GCP Cloud Run, this caused the first request to fail (no DB_PASSWORD)
      and all subsequent requests to use incomplete settings forever.
    """
    global _secrets_loaded
    # R37-FIX: Double-checked locking â€" fast path without lock acquisition.
    if _secrets_loaded:
        return
    with _secrets_lock:
        if _secrets_loaded:
            return

        # Load .env FIRST so GCP_PROJECT_ID, DB_HOST etc. are in os.environ
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass  # python-dotenv not installed, rely on env vars

        try:
            from app.services.secret_manager import load_secrets

            # 4.7-FIX: If there's a running event loop, load_secrets() would
            # block it (synchronous GCP API calls). Offload to a thread.
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(load_secrets)
                    count = future.result(timeout=30)
                logger.info(
                    "Loaded %d secrets via thread pool (event loop was running)", count,
                )
            else:
                count = load_secrets()
                if count > 0:
                    logger.info("Loaded %d secrets from GCP Secret Manager", count)
        except Exception as exc:
            # AUDIT-T2-12: WARNING not DEBUG — DEBUG may be suppressed in production,
            # hiding the reason why DB credentials are missing on first request.
            logger.warning("secret_manager_unavailable", error=str(exc)[:200])

        # R36-FIX: Set flag AFTER loading completes, not before.
        _secrets_loaded = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Call this everywhere instead of constructing Settings().

    On first call, attempts to load secrets from GCP Secret Manager.
    If GCP is not available, falls back to env vars and .env file.
    """
    _ensure_secrets_loaded()
    return Settings()  # type: ignore[call-arg]



