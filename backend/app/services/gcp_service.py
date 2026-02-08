"""
GCP Cloud Run Deployment Service
==================================

Handles authentication, container building, and deployment to Google Cloud Run.

Requirements:
- GCP project with Cloud Run API enabled
- Service account with appropriate roles
- Service account key (JSON)
"""

import os
import json
import logging
import subprocess
from typing import Dict, Any, Optional
from pathlib import Path

from google.cloud import run_v2
from google.cloud import artifactregistry_v1
from google.auth import default
from google.oauth2 import service_account
import docker

from app.core.config import settings
from app.core.secret_manager import get_secret_manager

logger = logging.getLogger("gcp_service")


class GCPService:
    """
    Service for managing GCP Cloud Run deployments.
    
    Responsibilities:
    - Authenticate with GCP using service account
    - Build and push Docker images to Artifact Registry
    - Deploy services to Cloud Run
    - Manage environment variables and configurations
    """
    
    def __init__(self):
        self.project_id = os.getenv("GCP_PROJECT_ID")
        self.region = os.getenv("GCP_REGION", "us-central1")
        self.artifact_repo = os.getenv("ARTIFACT_REGISTRY_REPO", "nexsidi-containers")
        self.credentials = None
        self.authenticated = False
        
        # Docker client for building images
        try:
            self.docker_client = docker.from_env()
            logger.info("✅ Docker client initialized")
        except Exception as e:
            logger.warning(f"⚠️ Docker not available: {e}")
            self.docker_client = None
    
    def authenticate(self, secret_name: Optional[str] = None) -> bool:
        """
        Authenticate with GCP using service account key.
        
        Args:
            secret_name: Optional name of the secret in Secret Manager to use
            
        Returns:
            True if authentication successful, False otherwise
        """
        try:
            # Method 0: Use Secret Manager if secret_name is provided or enabled in settings
            secret_manager = get_secret_manager()
            use_secret = secret_name or (settings.use_cloud_secrets and settings.gcp_deployment_key_secret)
            
            if use_secret:
                self.credentials = secret_manager.get_gcp_credentials(use_secret)
                if self.credentials:
                    self.authenticated = True
                    logger.info(f"✅ Authenticated with service account from Secret Manager: {use_secret}")
                    return True

            # Method 1: JSON key from environment variable
            key_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
            if key_json:
                try:
                    # Try parsing as JSON string
                    key_data = json.loads(key_json)
                    self.credentials = service_account.Credentials.from_service_account_info(
                        key_data,
                        scopes=["https://www.googleapis.com/auth/cloud-platform"]
                    )
                    self.authenticated = True
                    logger.info("✅ Authenticated with service account (from env JSON)")
                    return True
                except json.JSONDecodeError:
                    # Try as file path
                    if os.path.exists(key_json):
                        self.credentials = service_account.Credentials.from_service_account_file(
                            key_json,
                            scopes=["https://www.googleapis.com/auth/cloud-platform"]
                        )
                        self.authenticated = True
                        logger.info("✅ Authenticated with service account (from file path)")
                        return True
            
            # Method 2: Application default credentials
            self.credentials, project = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if not self.project_id:
                self.project_id = project
            self.authenticated = True
            logger.info("✅ Authenticated with application default credentials")
            return True
            
        except Exception as e:
            logger.error(f"❌ GCP authentication failed: {e}")
            self.authenticated = False
            return False
    
    def build_and_push_image(
        self,
        workspace_path: str,
        service_name: str,
        dockerfile_path: Optional[str] = None
    ) -> Optional[str]:
        """
        Build Docker image and push to Artifact Registry.
        
        Args:
            workspace_path: Path to workspace containing code
            service_name: Name for the service (used in image tag)
            dockerfile_path: Optional custom Dockerfile path
            
        Returns:
            Full image URL or None if failed
        """
        if not self.authenticated:
            logger.error("❌ Not authenticated with GCP")
            return None
        
        if not self.docker_client:
            logger.error("❌ Docker client not available")
            return None
        
        try:
            # Construct image tag
            image_tag = f"{self.region}-docker.pkg.dev/{self.project_id}/{self.artifact_repo}/{service_name}:latest"
            
            # Build image
            logger.info(f"🔨 Building Docker image: {service_name}")
            dockerfile = dockerfile_path or str(Path(workspace_path) / "Dockerfile")
            
            image, build_logs = self.docker_client.images.build(
                path=workspace_path,
                dockerfile=dockerfile,
                tag=image_tag,
                rm=True
            )
            
            # Log build output
            for log in build_logs:
                if 'stream' in log:
                    logger.debug(log['stream'].strip())
            
            logger.info(f"✅ Image built: {image_tag}")
            
            # Configure Docker for Artifact Registry authentication
            logger.info("🔐 Configuring Docker authentication for Artifact Registry")
            self._configure_docker_auth()
            
            # Push image
            logger.info(f"📤 Pushing image to Artifact Registry: {image_tag}")
            push_logs = self.docker_client.images.push(
                image_tag,
                stream=True,
                decode=True
            )
            
            for log in push_logs:
                if 'status' in log:
                    logger.debug(f"{log['status']}: {log.get('progress', '')}")
                if 'error' in log:
                    raise Exception(f"Push failed: {log['error']}")
            
            logger.info(f"✅ Image pushed: {image_tag}")
            return image_tag
            
        except Exception as e:
            logger.error(f"❌ Failed to build/push image: {e}")
            return None
    
    def _configure_docker_auth(self):
        """Configure Docker to authenticate with Artifact Registry."""
        try:
            # Use gcloud to configure docker auth
            # This requires gcloud to be installed
            cmd = [
                "gcloud", "auth", "configure-docker",
                f"{self.region}-docker.pkg.dev",
                "--quiet"
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info("✅ Docker authentication configured")
        except subprocess.CalledProcessError as e:
            logger.warning(f"⚠️ Failed to configure docker auth via gcloud: {e}")
            logger.info("💡 Attempting alternative authentication method")
            # Alternative: use credential helper
            try:
                subprocess.run(
                    ["gcloud", "auth", "print-access-token"],
                    check=True,
                    capture_output=True
                )
            except Exception:
                logger.warning("⚠️ Alternative auth also failed, proceeding anyway")
    
    def deploy_to_cloud_run(
        self,
        service_name: str,
        image_url: str,
        env_vars: Optional[Dict[str, str]] = None,
        port: int = 8080,
        memory: str = "512Mi",
        cpu: str = "1",
        min_instances: int = 0,
        max_instances: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Deploy service to Cloud Run.
        
        Args:
            service_name: Name of the Cloud Run service
            image_url: Full container image URL
            env_vars: Environment variables for the service
            port: Container port
            memory: Memory limit (e.g., "512Mi")
            cpu: CPU limit (e.g., "1")
            min_instances: Minimum autoscaling instances
            max_instances: Maximum autoscaling instances
            
        Returns:
            Deployment info dict or None if failed
        """
        if not self.authenticated:
            logger.error("❌ Not authenticated with GCP")
            return None
        
        try:
            logger.info(f"🚀 Deploying service to Cloud Run: {service_name}")
            
            client = run_v2.ServicesClient(credentials=self.credentials)
            
            # Prepare environment variables
            env_list = []
            if env_vars:
                for key, value in env_vars.items():
                    env_list.append(run_v2.EnvVar(name=key, value=value))
            
            # Build service specification
            service = run_v2.Service()
            service.template = run_v2.RevisionTemplate()
            service.template.containers = [
                run_v2.Container(
                    image=image_url,
                    ports=[run_v2.ContainerPort(container_port=port)],
                    env=env_list,
                    resources=run_v2.ResourceRequirements(
                        limits={
                            "memory": memory,
                            "cpu": cpu
                        }
                    )
                )
            ]
            
            # Autoscaling configuration
            service.template.scaling = run_v2.RevisionScaling(
                min_instance_count=min_instances,
                max_instance_count=max_instances
            )
            
            # Service name and location
            parent = f"projects/{self.project_id}/locations/{self.region}"
            service_path = f"{parent}/services/{service_name}"
            
            # Check if service exists
            try:
                existing_service = client.get_service(name=service_path)
                logger.info(f"📝 Updating existing service: {service_name}")
                
                # Update service
                operation = client.update_service(
                    service=service,
                    allow_missing=True
                )
            except Exception:
                logger.info(f"🆕 Creating new service: {service_name}")
                
                # Create new service
                request = run_v2.CreateServiceRequest(
                    parent=parent,
                    service=service,
                    service_id=service_name
                )
                operation = client.create_service(request=request)
            
            # Wait for operation to complete
            logger.info("⏳ Waiting for deployment to complete...")
            result = operation.result(timeout=600)  # 10 minute timeout
            
            # Get service URL
            service_url = result.uri
            
            # Make service publicly accessible
            logger.info("🌐 Configuring service to allow public access...")
            self._allow_public_access(service_name)
            
            logger.info(f"✅ Service deployed: {service_url}")
            
            return {
                "service_name": service_name,
                "url": service_url,
                "image": image_url,
                "region": self.region,
                "status": "deployed"
            }
            
        except Exception as e:
            logger.error(f"❌ Cloud Run deployment failed: {e}")
            return None
    
    def _allow_public_access(self, service_name: str):
        """
        Configure IAM policy to allow public (unauthenticated) access.
        
        Args:
            service_name: Name of the service
        """
        try:
            # Use gcloud command to set IAM policy
            service_path = f"projects/{self.project_id}/locations/{self.region}/services/{service_name}"
            
            cmd = [
                "gcloud", "run", "services", "add-iam-policy-binding",
                service_name,
                f"--region={self.region}",
                "--member=allUsers",
                "--role=roles/run.invoker",
                "--quiet"
            ]
            
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info("✅ Public access configured")
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"⚠️ Failed to configure public access: {e}")
            logger.info("💡 You may need to manually configure IAM permissions")
    
    def get_service_url(self, service_name: str) -> Optional[str]:
        """
        Get the URL of a deployed Cloud Run service.
        
        Args:
            service_name: Name of the service
            
        Returns:
            Service URL or None if not found
        """
        if not self.authenticated:
            logger.error("❌ Not authenticated with GCP")
            return None
        
        try:
            client = run_v2.ServicesClient(credentials=self.credentials)
            service_path = f"projects/{self.project_id}/locations/{self.region}/services/{service_name}"
            
            service = client.get_service(name=service_path)
            return service.uri
            
        except Exception as e:
            logger.error(f"❌ Failed to get service URL: {e}")
            return None


# Global instance
gcp_service = GCPService()
