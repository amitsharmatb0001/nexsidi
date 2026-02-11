"""
GCP Secret Manager Integration
================================

Secure secret management using Google Cloud Secret Manager.
Replaces environment variables and filesystem storage with cloud-based secrets.

Features:
- Automatic secret retrieval from GCP
- Caching with TTL
- Fallback to environment variables
- Secret rotation support
"""

import logging
import os
import json
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from google.oauth2 import service_account


class SecretManager:
    """
    Abstract interface for secret management.
    Supports GCP Secret Manager with fallback to environment variables.
    """
    
    def __init__(self, use_cloud: bool = False, project_id: Optional[str] = None):
        self.logger = logging.getLogger("secret_manager")
        self.use_cloud = use_cloud
        self.project_id = project_id
        
        # Cache secrets with TTL
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = timedelta(minutes=15)
        
        # Initialize GCP client if enabled
        self.gcp_client = None
        if self.use_cloud and self.project_id:
            try:
                from google.cloud import secretmanager
                self.gcp_client = secretmanager.SecretManagerServiceClient()
                self.logger.info(f"[OK] GCP Secret Manager initialized for project: {project_id}")
            except ImportError:
                self.logger.error("[ERROR] google-cloud-secret-manager not installed. Run: pip install google-cloud-secret-manager")
                self.use_cloud = False
            except Exception as e:
                self.logger.error(f"[ERROR] Failed to initialize GCP Secret Manager: {e}")
                self.use_cloud = False
    
    def get_secret(self, name: str, version: str = "latest") -> Optional[str]:
        """
        Retrieve secret from GCP or environment variables.
        """
        # Check cache first
        cache_key = f"{name}:{version}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if datetime.now() < cached["expires"]:
                return cached["value"]
            else:
                del self._cache[cache_key]
        
        # Try GCP Secret Manager
        if self.use_cloud and self.gcp_client:
            try:
                secret_path = f"projects/{self.project_id}/secrets/{name}/versions/{version}"
                response = self.gcp_client.access_secret_version(request={"name": secret_path})
                secret_value = response.payload.data.decode('UTF-8')
                
                # Cache the secret
                self._cache[cache_key] = {
                    "value": secret_value,
                    "expires": datetime.now() + self._cache_ttl
                }
                
                self.logger.info(f"[SECURE] Retrieved {name} from GCP Secret Manager")
                return secret_value
                
            except Exception as e:
                self.logger.debug(f"[DEBUG] Failed to retrieve {name} from GCP: {e}")
                # Fall through to environment variable
        
        # Fallback to environment variable
        env_name = name.upper().replace("-", "_")
        env_value = os.getenv(env_name)
        
        if env_value:
            self.logger.info(f"[INFO] Retrieved {name} from environment variable")
            return env_value
        
        self.logger.debug(f"[DEBUG] Secret {name} not found in GCP or environment")
        return None
    
    def set_secret(self, name: str, value: str) -> bool:
        """
        Store secret in GCP Secret Manager.
        """
        if not self.use_cloud or not self.gcp_client:
            return False
        
        try:
            parent = f"projects/{self.project_id}"
            
            # Create secret if it doesn't exist
            try:
                self.gcp_client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": name,
                        "secret": {
                            "replication": {"automatic": {}}
                        }
                    }
                )
            except Exception:
                pass
            
            # Add secret version
            secret_path = f"{parent}/secrets/{name}"
            self.gcp_client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": value.encode('UTF-8')}
                }
            )
            
            # Invalidate cache
            cache_key = f"{name}:latest"
            if cache_key in self._cache:
                del self._cache[cache_key]
            
            return True
            
        except Exception as e:
            self.logger.error(f"[ERROR] Failed to store {name} in GCP: {e}")
            return False

    def get_gcp_credentials(self, secret_name: str) -> Optional[service_account.Credentials]:
        """
        Retrieve GCP service account credentials from Secret Manager.
        """
        key_json = self.get_secret(secret_name)
        if not key_json:
            return None
            
        try:
            key_data = json.loads(key_json)
            credentials = service_account.Credentials.from_service_account_info(
                key_data,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            return credentials
        except Exception:
            return None


# Global instance
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """Get or create global secret manager instance."""
    global _secret_manager
    
    if _secret_manager is None:
        use_cloud = os.getenv("USE_CLOUD_SECRETS", "false").lower() == "true"
        project_id = os.getenv("GCP_PROJECT_ID")
        
        _secret_manager = SecretManager(
            use_cloud=use_cloud,
            project_id=project_id
        )
    
    return _secret_manager
