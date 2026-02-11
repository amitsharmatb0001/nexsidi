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
        
        logger.info("[OK] GCP Service initialized (using Cloud Build - no local Docker needed!)")
    
    def authenticate(self, secret_name: Optional[str] = None) -> bool:
        """
        Authenticate with GCP using service account key.
        
        Args:
            secret_name: Optional name of the secret in Secret Manager to use
            
        Returns:
            True if authentication successful, False otherwise
        """
        auth_success = False
        try:
            # Method 0: Use Secret Manager if secret_name is provided or enabled in settings
            secret_manager = get_secret_manager()
            use_secret = secret_name or (settings.use_cloud_secrets and settings.gcp_deployment_key_secret)
            
            if use_secret:
                self.credentials = secret_manager.get_gcp_credentials(use_secret)
                if self.credentials:
                    self.authenticated = True
                    logger.info(f"[OK] Authenticated with service account from Secret Manager: {use_secret}")
                    auth_success = True

            # Method 1: JSON key from environment variable
            if not auth_success:
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
                        logger.info("[OK] Authenticated with service account (from env JSON)")
                        auth_success = True
                    except json.JSONDecodeError:
                        # Try as file path
                        if os.path.exists(key_json):
                            self.credentials = service_account.Credentials.from_service_account_file(
                                key_json,
                                scopes=["https://www.googleapis.com/auth/cloud-platform"]
                            )
                            self.authenticated = True
                            logger.info("[OK] Authenticated with service account (from file path)")
                            auth_success = True
            
            # Method 2: Application default credentials
            if not auth_success:
                self.credentials, project = default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                if not self.project_id:
                    self.project_id = project
                self.authenticated = True
                logger.info("[OK] Authenticated with application default credentials")
                auth_success = True
            
            # If authentication succeeded, ensure infrastructure is ready
            if auth_success:
                self.ensure_infrastructure()
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"[ERROR] GCP authentication failed: {e}")
            self.authenticated = False
            return False

    def ensure_infrastructure(self):
        """
        Autonomously verify and setup required GCP infrastructure.
        - Enables Artifact Registry and Cloud Run APIs
        - Creates the Docker repository if it doesn't exist
        """
        if not self.authenticated:
            return

        logger.info("🛠️ Agents checking deployment infrastructure...")
        
        try:
            # 1. Enable required APIs (including Cloud Build!)
            logger.info("🔌 Ensuring GCP APIs are enabled (Artifact Registry, Cloud Run, Cloud Build)...")
            subprocess.run(
                ["gcloud", "services", "enable", 
                 "artifactregistry.googleapis.com", 
                 "run.googleapis.com",
                 "cloudbuild.googleapis.com",
                 f"--project={self.project_id}"],
                check=True, capture_output=True
            )
            
            # 2. Ensure Repository exists
            logger.info(f"📦 Checking Artifact Registry for repository: {self.artifact_repo}...")
            repo_path = f"projects/{self.project_id}/locations/{self.region}/repositories/{self.artifact_repo}"
            
            # Use gcloud to check/create repo for simplicity and reliability
            check_repo = subprocess.run(
                ["gcloud", "artifacts", "repositories", "describe", self.artifact_repo, f"--project={self.project_id}", f"--location={self.region}"],
                capture_output=True
            )
            
            if check_repo.returncode != 0:
                logger.info(f"🆕 Creating new Docker repository: {self.artifact_repo}...")
                subprocess.run(
                    [
                        "gcloud", "artifacts", "repositories", "create", self.artifact_repo,
                        "--repository-format=docker",
                        f"--location={self.region}",
                        f"--project={self.project_id}",
                        "--description=NexSidi Project Containers"
                    ],
                    check=True, capture_output=True
                )
                logger.info(f"[OK] Repository created: {self.artifact_repo}")
            else:
                logger.info(f"[OK] Repository ready: {self.artifact_repo}")

        except Exception as e:
            logger.warning(f"[WARN] Infrastructure check had issues: {e}. Agents will attempt to proceed anyway.")
    
    
    def build_and_push_image(
        self,
        workspace_path: str,
        service_name: str,
        dockerfile_path: Optional[str] = None
    ) -> Optional[str]:
        """
        Build Docker image using GCP Cloud Build (no local Docker required!)
        and push to Artifact Registry.
        
        Args:
            workspace_path: Path to workspace containing code
            service_name: Name for the service (used in image tag)
            dockerfile_path: Optional custom Dockerfile path
            
        Returns:
            Full image URL or None if failed
        """
        if not self.authenticated:
            logger.error("[ERROR] Not authenticated with GCP")
            return None
        
        try:
            # Construct image tag
            image_tag = f"{self.region}-docker.pkg.dev/{self.project_id}/{self.artifact_repo}/{service_name}:latest"
            
            logger.info(f"[BUILD] Building image using GCP Cloud Build (no local Docker needed!): {service_name}")
            
            # Use gcloud builds submit to build in the cloud
            # This runs entirely on GCP infrastructure - no local Docker required!
            build_cmd = [
                "gcloud", "builds", "submit",
                workspace_path,
                f"--tag={image_tag}",
                f"--project={self.project_id}",
                "--timeout=10m"
            ]
            
            # Add custom Dockerfile if specified
            if dockerfile_path:
                build_cmd.extend([f"--config={dockerfile_path}"])
            
            logger.info("☁️ Submitting build to GCP Cloud Build...")
            result = subprocess.run(
                build_cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"[OK] Image built and pushed: {image_tag}")
                return image_tag
            else:
                logger.error(f"[ERROR] Cloud Build failed: {result.stderr}")
                return None
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to build/push image: {e}")
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
            logger.info("[OK] Docker authentication configured")
        except subprocess.CalledProcessError as e:
            logger.warning(f"[WARN] Failed to configure docker auth via gcloud: {e}")
            logger.info("[TIP] Attempting alternative authentication method")
            # Alternative: use credential helper
            try:
                subprocess.run(
                    ["gcloud", "auth", "print-access-token"],
                    check=True,
                    capture_output=True
                )
            except Exception:
                logger.warning("[WARN] Alternative auth also failed, proceeding anyway")
    
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
            logger.error("[ERROR] Not authenticated with GCP")
            return None
        
        try:
            logger.info(f"[START] Deploying service to Cloud Run: {service_name}")
            
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
                logger.info(f"[LOG] Updating existing service: {service_name}")
                
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
            logger.info("[WAIT] Waiting for deployment to complete...")
            result = operation.result(timeout=600)  # 10 minute timeout
            
            # Get service URL
            service_url = result.uri
            
            # Make service publicly accessible
            logger.info("🌐 Configuring service to allow public access...")
            self._allow_public_access(service_name)
            
            logger.info(f"[OK] Service deployed: {service_url}")
            
            return {
                "service_name": service_name,
                "url": service_url,
                "image": image_url,
                "region": self.region,
                "status": "deployed"
            }
            
        except Exception as e:
            logger.error(f"[ERROR] Cloud Run deployment failed: {e}")
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
            logger.info("[OK] Public access configured")
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"[WARN] Failed to configure public access: {e}")
            logger.info("[TIP] You may need to manually configure IAM permissions")
    
    def get_service_url(self, service_name: str) -> Optional[str]:
        """
        Get the URL of a deployed Cloud Run service.
        
        Args:
            service_name: Name of the service
            
        Returns:
            Service URL or None if not found
        """
        if not self.authenticated:
            logger.error("[ERROR] Not authenticated with GCP")
            return None
        
        try:
            client = run_v2.ServicesClient(credentials=self.credentials)
            service_path = f"projects/{self.project_id}/locations/{self.region}/services/{service_name}"
            
            service = client.get_service(name=service_path)
            return service.uri
            
        except Exception as e:
            logger.error(f"[ERROR] Failed to get service URL: {e}")
            return None


# Global instance
gcp_service = GCPService()
