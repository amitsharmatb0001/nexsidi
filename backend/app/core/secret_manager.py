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
                self.logger.info(f"✅ GCP Secret Manager initialized for project: {project_id}")
            except ImportError:
                self.logger.error("❌ google-cloud-secret-manager not installed. Run: pip install google-cloud-secret-manager")
                self.use_cloud = False
            except Exception as e:
                self.logger.error(f"❌ Failed to initialize GCP Secret Manager: {e}")
                self.use_cloud = False
    
    def get_secret(self, name: str, version: str = "latest") -> Optional[str]:
        """
        Retrieve secret from GCP or environment variables.
        
        Args:
            name: Secret name (e.g., "jwt-secret", "anthropic-api-key")
            version: Secret version (default: "latest")
        
        Returns:
            Secret value or None if not found
        """
        # Check cache first
        cache_key = f"{name}:{version}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if datetime.now() < cached["expires"]:
                self.logger.debug(f"🔑 Retrieved {name} from cache")
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
                
                self.logger.info(f"🔐 Retrieved {name} from GCP Secret Manager")
                return secret_value
                
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to retrieve {name} from GCP: {e}")
                # Fall through to environment variable
        
        # Fallback to environment variable
        env_name = name.upper().replace("-", "_")
        env_value = os.getenv(env_name)
        
        if env_value:
            self.logger.info(f"🔑 Retrieved {name} from environment variable")
            return env_value
        
        self.logger.error(f"❌ Secret {name} not found in GCP or environment")
        return None
    
    def set_secret(self, name: str, value: str) -> bool:
        """
        Store secret in GCP Secret Manager.
        
        Args:
            name: Secret name
            value: Secret value
        
        Returns:
            True if successful, False otherwise
        """
        if not self.use_cloud or not self.gcp_client:
            self.logger.warning("⚠️ GCP Secret Manager not enabled, cannot store secret")
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
                self.logger.info(f"✅ Created new secret: {name}")
            except Exception:
                # Secret already exists, that's fine
                pass
            
            # Add secret version
            secret_path = f"{parent}/secrets/{name}"
            self.gcp_client.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": value.encode('UTF-8')}
                }
            )
            
            self.logger.info(f"✅ Stored {name} in GCP Secret Manager")
            
            # Invalidate cache
            cache_key = f"{name}:latest"
            if cache_key in self._cache:
                del self._cache[cache_key]
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to store {name} in GCP: {e}")
            return False
    
    def rotate_secret(self, name: str, new_value: str) -> bool:
        """
        Rotate secret by creating new version.
        
        Args:
            name: Secret name
            new_value: New secret value
        
        Returns:
            True if successful
        """
        return self.set_secret(name, new_value)
    
    def list_secrets(self) -> list:
        """
        List all secrets in GCP Secret Manager.
        
        Returns:
            List of secret names
        """
        if not self.use_cloud or not self.gcp_client:
            return []
        
        try:
            parent = f"projects/{self.project_id}"
            secrets = self.gcp_client.list_secrets(request={"parent": parent})
            
            secret_names = [secret.name.split("/")[-1] for secret in secrets]
            self.logger.info(f"📋 Found {len(secret_names)} secrets in GCP")
            
            return secret_names
            
        except Exception as e:
            self.logger.error(f"❌ Failed to list secrets: {e}")
            return []

    def get_gcp_credentials(self, secret_name: str) -> Optional[service_account.Credentials]:
        """
        Retrieve GCP service account credentials from Secret Manager.
        
        Args:
            secret_name: Name of the secret containing the JSON key
            
        Returns:
            Credentials object or None if failed
        """
        key_json = self.get_secret(secret_name)
        if not key_json:
            self.logger.error(f"❌ Could not find secret: {secret_name}")
            return None
            
        try:
            key_data = json.loads(key_json)
            credentials = service_account.Credentials.from_service_account_info(
                key_data,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self.logger.info(f"✅ Loaded credentials from secret: {secret_name}")
            return credentials
        except Exception as e:
            self.logger.error(f"❌ Failed to parse credentials from secret {secret_name}: {e}")
            return None


# Global instance
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """Get or create global secret manager instance."""
    global _secret_manager
    
    if _secret_manager is None:
        # Check if cloud secrets are enabled
        use_cloud = os.getenv("USE_CLOUD_SECRETS", "false").lower() == "true"
        project_id = os.getenv("GCP_PROJECT_ID")
        
        _secret_manager = SecretManager(
            use_cloud=use_cloud,
            project_id=project_id
        )
    
    return _secret_manager
