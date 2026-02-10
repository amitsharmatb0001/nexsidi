"""
Application Configuration
=========================
Location: app/core/config.py

Centralized settings management using Pydantic.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
import logging
import os
import time
import urllib.parse
from dotenv import load_dotenv

# Load .env manually to ensure os.getenv works immediately for other modules
load_dotenv()

print(f"!!! CONFIG LOADED [V2.0.4] @ {time.ctime()} !!!")

class Settings(BaseSettings):
    """Application settings"""
    
    # App info
    app_name: str = "NexSidi"
    app_version: str = "1.0.0"
    env: str = "development"
    debug: bool = True
    
    # Database
    database_url: str = "sqlite:///./nexsidi.db"
    database_password: Optional[str] = None
    
    # Security (NO DEFAULTS - MUST be set via environment or GCP Secret Manager)
    jwt_secret: Optional[str] = None
    jwt_algorithm: str = "HS256"
    jwt_expiration_days: int = 7
    
    # Secure storage
    secrets_dir: str = "~/.nexsidi/keys"
    use_cloud_secrets: bool = False
    gcp_project_id: Optional[str] = None
    gcp_ai_project_id: str = "yugnex-ai"
    gcp_ai_key_secret: str = "YUGNEX_AI_CREDENTIALS"  # Secret name in GCP Secret Manager
    
    # AI APIs
    anthropic_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    
    # Google Cloud specifics
    google_application_credentials: str = ""
    google_cloud_project: str = ""
    google_project_id: str = ""
    
    # AI Feature Flags
    enable_gemini_fallback: bool = False
    auto_switch_on_rate_limit: bool = False
    google_genai_use_vertexai: bool = False
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # File uploads
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 200
    
    # Rate limiting
    rate_limit_per_minute: int = 60
    
    # CORS
    allowed_origins: list = ["*"]
    
    def validate_security(self):
        """Validate security configuration on startup."""
        if not self.jwt_secret and self.env != "development":
            raise ValueError("[SECURITY ERROR] JWT_SECRET must be set in non-dev environments!")
        
        if self.use_cloud_secrets and not self.gcp_project_id:
            raise ValueError("[CONFIGURATION ERROR] USE_CLOUD_SECRETS=true but GCP_PROJECT_ID is missing!")
        
        logging.getLogger("settings").info("[OK] Security validation passed")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    
    # Load Database Password from local env or GCP
    db_pass = None
    
    # 1. Try local environment first (allows local override/bypass of GCP hangs)
    if os.getenv("DATABASE_PASSWORD"):
        db_pass = os.getenv("DATABASE_PASSWORD")
        logging.getLogger("settings").info("[INFO] Database password found in local environment")

    # 2. If using cloud secrets and not found locally, fetch from GCP
    if settings.use_cloud_secrets and not db_pass:
        # PESSIMISTIC CHECK: Skip if we already have a real password in the URL
        has_placeholder = "SECRET_PASSWORD" in settings.database_url
        needs_jwt = settings.jwt_secret == "SECRET_IN_GCP" or settings.jwt_secret is None
        
        if not has_placeholder and not needs_jwt:
            print("!!! SKIPPING GCP SECRET FETCH: Credentials already provided locally !!!")
        else:
            try:
                from app.core.secret_manager import get_secret_manager
                secret_manager = get_secret_manager()
                
                if needs_jwt:
                    jwt_secret = secret_manager.get_secret("JWT_SECRET")
                    if jwt_secret: settings.jwt_secret = jwt_secret
                    
                if has_placeholder:
                    db_pass = secret_manager.get_secret("DATABASE_PASSWORD") or \
                             secret_manager.get_secret("databse-paasword")
            except Exception as e:
                logging.getLogger("settings").error(f"[ERROR] GCP Secret Manager error: {e}")

    # 3. Surgical Injection if we have a password
    if db_pass and settings.database_url:
        try:
            # Clean and encode - MUST strip hidden newlines from GCP Secrets
            db_pass = db_pass.strip()
            p_encoded = urllib.parse.quote_plus(db_pass)
            
            if "SECRET_PASSWORD" in settings.database_url:
                settings.database_url = settings.database_url.replace("SECRET_PASSWORD", p_encoded)
                logging.getLogger("settings").info("[SECURE] Database password injected via placeholder")
            elif ":" in settings.database_url and "@" in settings.database_url:
                parts = settings.database_url.split("@")
                head = parts[0]
                tail = "@".join(parts[1:])
                head_parts = head.rsplit(":", 1)
                if len(head_parts) > 1:
                    settings.database_url = f"{head_parts[0]}:{p_encoded}@{tail}"
                    logging.getLogger("settings").info("[SECURE] Database password injected via DSN parsing")
        except Exception as e:
            logging.getLogger("settings").error(f"[ERROR] Failed to inject database password: {e}")

    # 4. Load Redis URL (Only if placeholder exists)
    if "REDIS_IN_GCP" in settings.redis_url and settings.use_cloud_secrets:
        from app.core.secret_manager import get_secret_manager
        redis_url = get_secret_manager().get_secret("REDIS_URL")
        if redis_url: settings.redis_url = redis_url
    
    settings.validate_security()
    mask_db = settings.database_url.split("@")[-1] if "@" in settings.database_url else "LOCAL_DB"
    print(f"!!! SETTINGS INITIALIZED [V2.0.3] [DB HOST: {mask_db}] !!!")
    return settings


# Global settings instance
settings = get_settings()