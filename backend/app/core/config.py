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




class Settings(BaseSettings):
    """Application settings"""
    
    # App info
    app_name: str = "NexSidi"
    app_version: str = "1.0.0"
    env: str = "development"
    debug: bool = True
    
    # Database
    database_url: str = "sqlite:///./nexsidi.db"
    
    # Security (NO DEFAULTS - MUST be set via environment or GCP Secret Manager)
    jwt_secret: Optional[str] = None
    jwt_algorithm: str = "HS256"
    jwt_expiration_days: int = 7
    
    # Secure storage
    secrets_dir: str = "~/.nexsidi/keys"  # Secure key storage location
    use_cloud_secrets: bool = False  # Enable GCP Secret Manager
    gcp_project_id: Optional[str] = None
    
    # GCP Multi-Account Secret Names
    gcp_deployment_key_secret: str = "NEXSIDI_DEPLOY_KEY"
    gcp_ai_key_secret: str = "YUGNEX_AI_CREDENTIALS"
    
    # AI APIs
    anthropic_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    
    # Google Cloud
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
    allowed_origins: list = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173"
    ]
    
    def validate_security(self):
        """
        Validate security configuration on startup.
        FAILS HARD if JWT_SECRET is missing (production-ready enforcement).
        """
        # CRITICAL: JWT_SECRET must be set
        if not self.jwt_secret:
            raise ValueError(
                "🚨 SECURITY ERROR: JWT_SECRET is not set! "
                "Set JWT_SECRET environment variable or enable GCP Secret Manager. "
                "This is REQUIRED for all environments."
            )
        
        # Warn if using cloud secrets but missing project ID
        if self.use_cloud_secrets and not self.gcp_project_id:
            raise ValueError(
                "🚨 CONFIGURATION ERROR: USE_CLOUD_SECRETS=true but GCP_PROJECT_ID is not set!"
            )
        
        logging.getLogger("settings").info("✅ Security validation passed")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    
    Using lru_cache ensures we only create one instance.
    """
    settings = Settings()
    
    # If using cloud secrets, load JWT_SECRET from GCP
    if settings.use_cloud_secrets:
        from app.core.secret_manager import get_secret_manager
        secret_manager = get_secret_manager()
        
        jwt_secret = secret_manager.get_secret("nexsidi-jwt-secret")
        if jwt_secret:
            settings.jwt_secret = jwt_secret
    
    settings.validate_security()  # Validate on initialization
    return settings


# Global settings instance
settings = get_settings()