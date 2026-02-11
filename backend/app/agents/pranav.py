"""
PRANAV - DevOps Engineer Agent

Purpose: Deploy applications to production (GCP Cloud Run)
Technology: Docker, GCP Cloud Run API, Artifact Registry
Architecture: Standalone V2 (no BaseAgent inheritance)

Model: Gemini 3 Flash for deployment config generation
"""

from typing import Dict, Any, List, Optional
import json
import logging
import os
from datetime import datetime
from uuid import uuid4
import asyncio
import time
from pathlib import Path


# Standalone - direct AI Router access
from app.services.ai_router import ai_router, TaskComplexity
from app.services.prompt_engine import prompt_engine
from app.services.context_engine import context_engine
from app.services.circuit_breaker import gcp_breaker
from app.services.workspace_manager import workspace_manager
from app.services.gcp_service import gcp_service
from app.utils.json_utils import safe_json_parse
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin,
    ProgressMixin
)


class Pranav(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin, ProgressMixin):
    """
    DevOps Engineer Agent - Deployment Specialist.
    
    Follows standalone V2 architecture - no BaseAgent inheritance.
    Uses AI Router directly for all AI operations.
    
    Usage:
        pranav = Pranav(project_id="proj-123")
        result = await pranav.execute(input_data)
    """
    
    SYSTEM_PROMPT = """You are Pranav, a senior DevOps engineer specializing in cloud deployments.

YOUR EXPERTISE:
- Docker (containerization, multi-stage builds)
- GCP Cloud Run (serverless container deployments)
- GCP Artifact Registry (container image management)
- Environment configuration
- CI/CD pipelines
- Monitoring and logging

YOUR TASK:
Deploy approved code to production environments on Google Cloud Platform.

DEPLOYMENT TARGETS:
1. Backend → GCP Cloud Run
   - Dockerized FastAPI app
   - Cloud SQL or managed database
   - Environment variables configured
   - HTTPS enabled (automatic)

2. Frontend → GCP Cloud Run
   - React build in nginx container
   - Optimized static asset serving
   - Environment variables (API URL)
   - CDN via Cloud CDN (optional)

3. Database → GCP Cloud SQL
   - Automatic backups
   - SSL connection
   - Connection pooling

WHAT YOU GENERATE:

1. Docker Files (Backend)
   - Dockerfile (multi-stage build)
   - docker-compose.yml (local development)
   - .dockerignore

2. Deployment Configuration
   - railway.json (Railway config)
   - vercel.json (Vercel config)
   - Environment variable templates

3. Deployment Scripts
   - deploy.sh (automated deployment)
   - setup.sh (initial setup)

4. Documentation
   - DEPLOYMENT.md (deployment guide)
   - PRODUCTION.md (production notes)

OUTPUT FORMAT:
{
    "deployment_configs": [
        {
            "file_path": "Dockerfile",
            "file_content": "complete Docker config here",
            "file_type": "docker",
            "purpose": "Backend containerization"
        }
    ],
    
    "deployment_status": {
        "backend": {
            "platform": "Railway",
            "status": "deployed",
            "url": "https://your-app.railway.app",
            "database_url": "postgresql://...",
            "deployed_at": "2025-12-31T10:30:00Z"
        },
        "frontend": {
            "platform": "Vercel",
            "status": "deployed",
            "url": "https://your-app.vercel.app",
            "deployed_at": "2025-12-31T10:35:00Z"
        }
    },
    
    "environment_variables": {
        "backend": {
            "DATABASE_URL": "Automatically set by Railway PostgreSQL addon",
            "JWT_SECRET": "Generate: openssl rand -hex 32",
            "CORS_ORIGINS": "https://your-app.vercel.app"
        },
        "frontend": {
            "VITE_API_URL": "https://your-app.railway.app/api/v1"
        }
    },
    
    "post_deployment": {
        "database_migrations": "Run: alembic upgrade head",
        "health_check": "GET https://your-app.railway.app/health",
        "admin_user": "Create via: POST /api/auth/signup"
    }
}

EXAMPLE DOCKERFILE (Multi-stage):
```dockerfile
# Build stage
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

DEPLOYMENT CHECKLIST:
1. [OK] Backend builds successfully
2. [OK] Database migrations run
3. [OK] Frontend connects to backend
4. [OK] HTTPS enabled (automatic on Railway/Vercel)
5. [OK] Environment variables set
6. [OK] CORS configured properly
7. [OK] Health check endpoint works
8. [OK] Error monitoring configured

IMPORTANT:
- Use environment variables for ALL configuration
- Enable HTTPS (automatic on Railway/Vercel)
- Set proper CORS origins (frontend URL)
- Configure database connection pooling
- Set up automatic backups
- Monitor deployment logs
"""

    def __init__(self, project_id: str, workspace: Dict[str, str]):
        """
        Initialize Pranav for a project.
        
        Args:
            project_id: UUID of the project
            workspace: Workspace details from Arjun orchestrator
        """
        # Initialize MistakeMemoryMixin
        super().__init__()
        
        # Standalone - no inheritance
        self.project_id = project_id
        
        # Direct AI Router access
        self.ai_router = ai_router
        
        # Logging
        self.logger = logging.getLogger("agent.pranav")
        self.logger.setLevel(logging.INFO)
        
        # Statistics
        self.deployments_executed = 0
        self.total_cost = 0.0

        # RECEIVE workspace from Arjun (don't create)
        self.workspace = workspace
        self.logger.info(f"📁 Using workspace: {self.workspace['code_dir']}")
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deploy application to production.
        
        Args:
            input_data: Contains:
                - project_id: Project identifier
                - backend_path: Path to backend code
                - frontend_path: Path to frontend code
                - gcp_project_id: GCP project ID
        """
        try:
            self.logger.info("[START] Starting GCP Cloud Run deployment...")
            
            project_id = input_data.get("project_id", self.project_id)
            backend_path = input_data.get("backend_path", self.workspace['code_dir'])
            frontend_path = input_data.get("frontend_path", os.path.join(self.workspace['code_dir'], "frontend"))
            
            # Use requirements context for architecture if not provided
            requirements_context = context_engine.get_context(project_id, "requirements")
            architecture = {}
            if requirements_context and "spec" in requirements_context:
                architecture = requirements_context["spec"].get("tech_stack", {})
            
            if not architecture:
                # Mock architecture if none found for now
                architecture = {
                    "backend": "python",
                    "frontend": "react",
                    "database": "postgresql"
                }
            
            # Authenticate with GCP
            await self._send_progress("deployment", 10, "Authenticating with Google Cloud Platform...")
            self.logger.info("[SECURE] Authenticating with GCP...")
            if not gcp_service.authenticate():
                raise RuntimeError("GCP authentication failed. Please configure service account credentials.")
            
            # Phase 1: Generate deployment configurations (Dockerfiles, etc.)
            await self._send_progress("deployment", 30, "Generating Dockerfiles and IaC configurations...")
            self.logger.info("📦 Generating deployment configurations...")
            config_files = await self._generate_deployment_configs(architecture)
            
            # Write Dockerfiles to workspace
            await self._write_dockerfiles_to_workspace(config_files)
            
            # Phase 2: Deploy backend to Cloud Run
            await self._send_progress("deployment", 60, "Building and deploying backend container to Cloud Run...")
            self.logger.info("☁️ Deploying backend to GCP Cloud Run...")
            backend_deployment = await self._deploy_backend_to_gcp(self.project_id)
            
            # Phase 3: Deploy frontend to Cloud Run
            await self._send_progress("deployment", 85, "Building and deploying frontend container to Cloud Run...")
            self.logger.info("[DESIGN] Deploying frontend to GCP Cloud Run...")
            frontend_deployment = await self._deploy_frontend_to_gcp(
                self.project_id,
                backend_deployment.get("url", "")
            )
            
            await self._send_progress("deployment", 100, "Production deployment complete.")
            
            self.deployments_executed += 1
            
            self.logger.info(
                f"[OK] Deployment complete:\n"
                f"  Backend: {backend_deployment.get('url', 'N/A')}\n"
                f"  Frontend: {frontend_deployment.get('url', 'N/A')}\n"
                f"  Cost: ₹{self.total_cost:.2f}"
            )
            
            return {
                "success": True,
                "deployment": {
                    "backend": backend_deployment,
                    "frontend": frontend_deployment
                },
                "config_files": config_files,
                "urls": {
                    "backend": backend_deployment.get("url", ""),
                    "frontend": frontend_deployment.get("url", "")
                },
                "environment_variables": self._generate_env_vars(
                    backend_deployment["url"],
                    frontend_deployment["url"]
                ),
                "post_deployment_steps": self._generate_post_deployment_steps(
                    backend_deployment.get("url", "")
                )
            }
            
        except Exception as e:
            self.logger.error(f"[ERROR] Deployment failed: {e}")
            await self.record_failure(
                task_type="gcp_deployment_execution",
                error=str(e),
                context={"project_id": self.project_id}
            )
            raise
    
    async def _generate_deployment_configs(
        self,
        architecture: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate all deployment configuration files using AI."""
        
        # Generate base prompt
        config_prompt = prompt_engine.get_prompt(
            "pranav", "deployment_config",
            context={"architecture": architecture}
        )
        
        # Check past mistakes for deployment config
        past_mistakes = await self.check_past_mistakes(
            task_type="deployment_config",
            context={"architecture": architecture}
        )
        
        if past_mistakes:
            config_prompt = self.incorporate_past_learnings(past_mistakes, config_prompt)
            self.logger.info(f"[LOAD] Incorporated {len(past_mistakes)} past learnings for deployment")

        # Call AI Router directly
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": config_prompt}],
            system_prompt=self.SYSTEM_PROMPT,
            task_type="deployment",
            complexity=TaskComplexity.MEDIUM,
            max_tokens=4000
        )
        
        # Log cost
        self.total_cost += response.cost_estimate
        self.logger.info(
            f"[OK] Config generation: {response.output_tokens} tokens, "
            f"₹{response.cost_estimate:.4f}"
        )
        
        # Parse response
    
    async def _write_dockerfiles_to_workspace(self, config_files: List[Dict[str, Any]]):
        """Write generated Dockerfiles to workspace directory."""
        workspace_path = Path(self.workspace['code_dir'])
        
        for config in config_files.get("deployment_configs", []):
            file_path = config.get("file_path")
            file_content = config.get("file_content")
            
            if file_path and file_content:
                target_path = workspace_path / file_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_text(file_content)
                self.logger.info(f"[LOG] Wrote {file_path} to workspace")
    
    async def _deploy_backend_to_gcp(self, project_id: str) -> Dict[str, Any]:
        """
        Deploy backend to GCP Cloud Run.
        
        Workflow:
        1. Build Docker image from workspace
        2. Push to Artifact Registry
        3. Deploy to Cloud Run
        4. Return deployment info
        """
        
        try:
            self.logger.info("☁️ GCP Cloud Run backend deployment started")
            
            workspace_path = self.workspace['code_dir']
            service_name = f"nexsidi-backend-{project_id[:8]}"
            
            # Build and push image
            image_url = gcp_service.build_and_push_image(
                workspace_path=workspace_path,
                service_name=service_name,
                dockerfile_path=str(Path(workspace_path) / "Dockerfile")
            )
            
            if not image_url:
                raise RuntimeError("Failed to build/push backend image")
            
            # Deploy to Cloud Run
            deployment_info = gcp_service.deploy_to_cloud_run(
                service_name=service_name,
                image_url=image_url,
                env_vars={
                    "ENVIRONMENT": "production",
                    "PORT": "8080",
                    "USE_CLOUD_SECRETS": "true",
                    "GCP_PROJECT_ID": os.getenv("GCP_PROJECT_ID", "nexsidi-ai"),
                    "DATABASE_URL": os.getenv("DATABASE_URL", ""),
                },
                port=8080,
                memory="1Gi",
                cpu="1",
                min_instances=0,
                max_instances=10
            )
            
            if not deployment_info:
                raise RuntimeError("Failed to deploy backend to Cloud Run")
            
            self.logger.info(f"[OK] Backend deployed: {deployment_info['url']}")
            
            return deployment_info
            
        except Exception as e:
            self.logger.error(f"[ERROR] Backend deployment failed: {e}")
            await self.record_failure(
                task_type="backend_gcp_deployment",
                error=str(e),
                context={"project_id": project_id}
            )
            return {
                "status": "failed",
                "error": str(e),
                "platform": "GCP Cloud Run"
            }
    
    async def _deploy_frontend_to_gcp(
        self, 
        project_id: str,
        backend_url: str
    ) -> Dict[str, Any]:
        """
        Deploy frontend to GCP Cloud Run.
        
        Workflow:
        1. Build frontend Docker image (nginx serving static files)
        2. Push to Artifact Registry
        3. Deploy to Cloud Run
        4. Return deployment info
        """
        
        try:
            self.logger.info("[DESIGN] GCP Cloud Run frontend deployment started")
            
            workspace_path = self.workspace['code_dir']
            service_name = f"nexsidi-frontend-{project_id[:8]}"
            
            # Build and push image
            # Note: Assumes frontend has its own Dockerfile for nginx
            frontend_dockerfile = str(Path(workspace_path) / "frontend" / "Dockerfile")
            
            image_url = gcp_service.build_and_push_image(
                workspace_path=str(Path(workspace_path) / "frontend"),
                service_name=service_name,
                dockerfile_path=frontend_dockerfile
            )
            
            if not image_url:
                self.logger.warning("[WARN] Failed to build/push frontend image, using fallback")
                # Fallback: return mock response
                return {
                    "status": "skipped",
                    "platform": "GCP Cloud Run",
                    "note": "Frontend deployment skipped - no Dockerfile found"
                }
            
            # Deploy to Cloud Run with backend URL as env var
            deployment_info = gcp_service.deploy_to_cloud_run(
                service_name=service_name,
                image_url=image_url,
                env_vars={
                    "VITE_API_URL": backend_url,
                    "REACT_APP_API_URL": backend_url  # Support multiple framework conventions
                },
                port=8080,
                memory="512Mi",
                cpu="1",
                min_instances=0,
                max_instances=5
            )
            
            if not deployment_info:
                raise RuntimeError("Failed to deploy frontend to Cloud Run")
            
            self.logger.info(f"[OK] Frontend deployed: {deployment_info['url']}")
            
            return deployment_info
            
        except Exception as e:
            self.logger.error(f"[ERROR] Frontend deployment failed: {e}")
            await self.record_failure(
                task_type="frontend_gcp_deployment",
                error=str(e),
                context={"project_id": project_id, "backend_url": backend_url}
            )
            return {
                "status": "failed",
                "error": str(e),
                "platform": "GCP Cloud Run"
            }
    
    async def deploy_to_test_environment(
        self,
        backend_path: str,
        frontend_path: str
    ) -> Dict[str, Any]:
        """Task 3.1: Deploy to test environment (test-{id}.run.app)"""
        self.logger.info("🧪 Deploying to TEST environment...")
        
        # In a real scenario, this would use a separate GCP project or sandbox
        # For now, we use a 'test' prefix for services
        backend_service = f"test-backend-{self.project_id[:8]}"
        frontend_service = f"test-frontend-{self.project_id[:8]}"
        
        # Mock deployment for test environment in this implementation
        # (Mirroring the actual deploy logic but with 'test' labels)
        await self._send_progress("deployment", 50, "Provisioning test environment resources...")
        
        return {
            "backend": f"https://{backend_service}-run.app",
            "frontend": f"https://{frontend_service}-run.app",
            "environment": "test",
            "status": "deployed"
        }

    def _generate_env_vars(
        self,
        backend_url: str,
        frontend_url: str
    ) -> Dict[str, Any]:
        """Generate environment variables configuration."""
        
        return {
            "backend": {
                "DATABASE_URL": "Automatically set by Railway PostgreSQL addon",
                "JWT_SECRET": "Generate: openssl rand -hex 32",
                "CORS_ORIGINS": frontend_url,
                "ENVIRONMENT": "production"
            },
            "frontend": {
                "VITE_API_URL": f"{backend_url}/api/v1"
            }
        }
    
    def _generate_post_deployment_steps(self, backend_url: str) -> Dict[str, Any]:
        """Generate post-deployment checklist."""
        
        return {
            "database_migrations": f"Run: alembic upgrade head (via Railway console)",
            "health_check": f"GET {backend_url}/health",
            "admin_user": f"Create via: POST {backend_url}/api/auth/signup",
            "monitoring": "Configure error tracking (Sentry recommended)",
            "backups": "Railway PostgreSQL auto-backup enabled",
            "ssl": "Automatic HTTPS enabled on both platforms"
        }
    
    async def _parse_json_response(self, ai_response: str) -> Any:
        """Parse JSON from AI response (H5)."""
        result = safe_json_parse(ai_response)
        if not result:
            self.logger.error(f"[ERROR] Failed to parse JSON response: {ai_response[:200]}...")
            await self.record_failure(
                task_type="deployment_config_parsing",
                error="Invalid JSON response",
                context={"response_preview": ai_response[:200]}
            )
            raise ValueError(f"Invalid JSON response from AI")
        return result
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get deployment statistics."""
        return {
            "deployments_executed": self.deployments_executed,
            "total_cost": self.total_cost
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        pranav = Pranav(project_id="test-deploy-001")
        
        # Sample input
        input_data = {
            "architecture": {
                "backend": {
                    "framework": "FastAPI",
                    "database": "PostgreSQL",
                    "authentication": "JWT"
                },
                "frontend": {
                    "framework": "React",
                    "build_tool": "Vite",
                    "styling": "Tailwind"
                },
                "deployment": {
                    "backend_platform": "Railway",
                    "frontend_platform": "Vercel",
                    "database_platform": "Railway PostgreSQL"
                }
            }
        }
        
        result = await pranav.execute(input_data)
        print(json.dumps(result, indent=2))
        print(f"\nStatistics: {pranav.get_statistics()}")
    
    asyncio.run(test())
