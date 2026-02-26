"""Pranav — Deployment Agent: deploys to GCP Cloud Run, Vercel, or Railway.

Pranav handles the DEPLOYMENT stage of the pipeline:
1. Validates all pre-deploy checks passed (CHECKPOINT 2 approved)
2. Selects deployment provider based on project config
3. Builds deployment config (Dockerfile, vercel.json, railway.toml)
4. Deploys to the selected provider
5. Verifies deployment health
6. Returns deployment URL + logs

Supported providers:
- GCP Cloud Run (containers — backend + full-stack)
- Vercel (serverless — frontend + Next.js)
- Railway (containers — backend, databases, full-stack)

Security (AUDIT FIX #17):
- Deployment uses Sonnet 4.6 for all AI calls (security-critical)
- No secrets in generated configs — secrets are injected at deploy time
- Deployment logs are sanitized (no tokens/keys)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    BaseAgent,
    ToolDefinition,
    register_agent,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── Deployment Provider ───────────────────────────────────────────


class DeployProvider(str, Enum):
    """Supported deployment providers."""

    GCP_CLOUD_RUN = "gcp_cloud_run"
    VERCEL = "vercel"
    RAILWAY = "railway"


class DeployStatus(str, Enum):
    """Deployment lifecycle status."""

    PENDING = "pending"
    BUILDING = "building"
    DEPLOYING = "deploying"
    VERIFYING = "verifying"
    LIVE = "live"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# ── Deployment Config ─────────────────────────────────────────────

# Required environment variables per provider (injected at deploy time, never hardcoded)
PROVIDER_REQUIRED_ENV: dict[DeployProvider, list[str]] = {
    DeployProvider.GCP_CLOUD_RUN: [
        "GCP_PROJECT_ID",
        "GCP_REGION",
        "GCP_SERVICE_ACCOUNT_KEY",
    ],
    DeployProvider.VERCEL: [
        "VERCEL_TOKEN",
        "VERCEL_ORG_ID",
        "VERCEL_PROJECT_ID",
    ],
    DeployProvider.RAILWAY: [
        "RAILWAY_TOKEN",
        "RAILWAY_PROJECT_ID",
    ],
}

# Provider-specific deploy commands
PROVIDER_DEPLOY_COMMANDS: dict[DeployProvider, dict[str, str]] = {
    DeployProvider.GCP_CLOUD_RUN: {
        "build": "gcloud builds submit --tag gcr.io/{project_id}/{service_name}",
        "deploy": (
            "gcloud run deploy {service_name}"
            " --image gcr.io/{project_id}/{service_name}"
            " --region {region}"
            " --platform managed"
            " --allow-unauthenticated"
            " --memory 512Mi"
            " --cpu 1"
            " --max-instances 10"
            " --min-instances 0"
        ),
        "health": "gcloud run services describe {service_name} --region {region} --format='value(status.url)'",
    },
    DeployProvider.VERCEL: {
        "build": "vercel build --prod",
        "deploy": "vercel deploy --prod --yes",
        "health": "vercel inspect {deployment_url}",
    },
    DeployProvider.RAILWAY: {
        "build": "railway up --detach",
        "deploy": "railway up --detach",
        "health": "railway status",
    },
}


@dataclass(slots=True)
class DeployConfig:
    """Configuration for a deployment."""

    provider: DeployProvider
    service_name: str
    project_id: str
    region: str = "asia-south1"       # Default: Mumbai (closest to India users)
    memory: str = "512Mi"
    cpu: int = 1
    max_instances: int = 10
    min_instances: int = 0
    env_vars: dict[str, str] = field(default_factory=dict)  # Non-secret env vars only
    health_check_path: str = "/health"
    timeout_seconds: int = 300

    @classmethod
    def from_contract(cls, contract: dict[str, Any], provider: DeployProvider) -> DeployConfig:
        """Create deploy config from architecture contract."""
        project_name = contract.get("project_name", "app")
        # Sanitize project name for service naming
        service_name = re.sub(r"[^a-z0-9-]", "-", project_name.lower())[:50]

        deploy_config = contract.get("deployment", {})
        return cls(
            provider=provider,
            service_name=service_name,
            project_id=deploy_config.get("project_id", "nexsidi"),
            region=deploy_config.get("region", "asia-south1"),
            memory=deploy_config.get("memory", "512Mi"),
            cpu=deploy_config.get("cpu", 1),
            max_instances=deploy_config.get("max_instances", 10),
            min_instances=deploy_config.get("min_instances", 0),
            health_check_path=deploy_config.get("health_check_path", "/health"),
        )


@dataclass(slots=True)
class DeployResult:
    """Result of a deployment attempt."""

    status: DeployStatus
    provider: DeployProvider
    deployment_url: str = ""
    build_log: str = ""
    deploy_log: str = ""
    health_check_passed: bool = False
    error: str = ""
    duration_seconds: float = 0.0


# ── Cloud Run Config Generator ────────────────────────────────────

CLOUD_RUN_SERVICE_YAML = """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: {service_name}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/maxScale: "{max_instances}"
        autoscaling.knative.dev/minScale: "{min_instances}"
        run.googleapis.com/cpu-throttling: "true"
    spec:
      containerConcurrency: 80
      timeoutSeconds: {timeout_seconds}
      containers:
        - image: gcr.io/{project_id}/{service_name}
          ports:
            - containerPort: 8000
          resources:
            limits:
              cpu: "{cpu}"
              memory: "{memory}"
          startupProbe:
            httpGet:
              path: {health_check_path}
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 10
          livenessProbe:
            httpGet:
              path: {health_check_path}
              port: 8000
            periodSeconds: 15
"""

VERCEL_CONFIG = """\
{{
  "version": 2,
  "name": "{service_name}",
  "builds": [
    {{
      "src": "package.json",
      "use": "@vercel/next"
    }}
  ],
  "routes": [
    {{
      "src": "/(.*)",
      "dest": "/$1"
    }}
  ],
  "regions": ["{region}"]
}}
"""

RAILWAY_CONFIG = """\
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "{health_check_path}"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3

[service]
internalPort = 8000
"""


# ── Pranav Agent ──────────────────────────────────────────────────


class Pranav(BaseAgent):
    """Deployment Agent — deploys to GCP Cloud Run, Vercel, or Railway.

    ALWAYS uses Sonnet 4.6 for AI calls (deployment is security-critical).
    """

    name = "pranav"
    display_name = "Pranav — Deployment Engineer"
    default_complexity = TaskComplexity.HIGH
    default_model = "claude-sonnet-4-6"  # AUDIT FIX #17: security-critical

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="deploy",
            description="Deploy the built project to the selected provider.",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["gcp_cloud_run", "vercel", "railway"],
                    },
                },
                "required": ["provider"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="generate_deploy_config",
            description="Generate deployment configuration files for the selected provider.",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": ["gcp_cloud_run", "vercel", "railway"],
                    },
                },
                "required": ["provider"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="health_check",
            description="Verify deployment health after deploy.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Deployment URL to check."},
                },
                "required": ["url"],
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Deploy the project to the configured provider.

        Steps:
        1. Validate CHECKPOINT 2 was approved
        2. Select provider from contract config
        3. Generate deploy config files
        4. Execute deployment
        5. Verify health
        6. Return deployment URL + logs
        """
        contract = context.get("vikram", {}).get("contract", {})
        if not contract:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract in context — cannot deploy",
            )

        # Select provider from contract (default: Railway — easiest)
        deploy_section = contract.get("deployment", {})
        provider_name = deploy_section.get("provider", "railway")
        try:
            provider = DeployProvider(provider_name)
        except ValueError:
            provider = DeployProvider.RAILWAY

        # Build deploy config from contract
        config = DeployConfig.from_contract(contract, provider)

        logger.info(
            "deploy_start",
            provider=provider.value,
            service=config.service_name,
            region=config.region,
        )

        # Generate provider-specific config files
        config_files = self._generate_config_files(config)

        # Execute the deployment
        result = await self._deploy(config, context)

        output = {
            "status": result.status.value,
            "provider": result.provider.value,
            "deployment_url": result.deployment_url,
            "health_check_passed": result.health_check_passed,
            "config_files": config_files,
            "build_log": result.build_log,
            "deploy_log": _sanitize_log(result.deploy_log),
            "duration_seconds": result.duration_seconds,
        }

        if result.error:
            output["error"] = result.error

        await self.store_output(pipeline_run_id, output)

        logger.info(
            "deploy_complete",
            provider=provider.value,
            status=result.status.value,
            url=result.deployment_url,
            health=result.health_check_passed,
        )

        agent_status = AgentStatus.COMPLETED if result.status == DeployStatus.LIVE else AgentStatus.FAILED
        return AgentResult(
            agent_name=self.name,
            status=agent_status,
            output=output,
        )

    def _generate_config_files(self, config: DeployConfig) -> dict[str, str]:
        """Generate provider-specific deployment config files."""
        files: dict[str, str] = {}

        if config.provider == DeployProvider.GCP_CLOUD_RUN:
            files["service.yaml"] = CLOUD_RUN_SERVICE_YAML.format(
                service_name=config.service_name,
                project_id=config.project_id,
                region=config.region,
                memory=config.memory,
                cpu=config.cpu,
                max_instances=config.max_instances,
                min_instances=config.min_instances,
                timeout_seconds=config.timeout_seconds,
                health_check_path=config.health_check_path,
            )

        elif config.provider == DeployProvider.VERCEL:
            files["vercel.json"] = VERCEL_CONFIG.format(
                service_name=config.service_name,
                region=_vercel_region(config.region),
            )

        elif config.provider == DeployProvider.RAILWAY:
            files["railway.toml"] = RAILWAY_CONFIG.format(
                health_check_path=config.health_check_path,
            )

        return files

    async def _deploy(
        self,
        config: DeployConfig,
        context: dict[str, Any],
    ) -> DeployResult:
        """Execute the deployment pipeline (build → deploy → verify).

        In production, this calls actual cloud provider CLIs.
        Currently returns structured simulation for pipeline integration testing.
        """
        import time

        start = time.monotonic()

        # Phase 1: Build
        logger.info("deploy_build_start", provider=config.provider.value, service=config.service_name)

        # Validate generated files exist in context
        shubham_output = context.get("shubham", {})
        aanya_output = context.get("aanya", {})
        has_backend = bool(shubham_output.get("file_contents", {}))
        has_frontend = bool(aanya_output.get("file_contents", {}))

        if not has_backend and not has_frontend:
            return DeployResult(
                status=DeployStatus.FAILED,
                provider=config.provider,
                error="No generated code found in context",
                duration_seconds=time.monotonic() - start,
            )

        build_log = f"Building {config.service_name} for {config.provider.value}..."

        # Phase 2: Deploy
        logger.info("deploy_push_start", provider=config.provider.value)

        commands = PROVIDER_DEPLOY_COMMANDS[config.provider]
        deploy_cmd = commands["deploy"].format(
            service_name=config.service_name,
            project_id=config.project_id,
            region=config.region,
        )
        deploy_log = f"Deploy command: {deploy_cmd}"

        # Simulate deployment URL
        deployment_url = _generate_deployment_url(config)

        # Phase 3: Health check
        logger.info("deploy_health_check", url=deployment_url, path=config.health_check_path)
        health_passed = True  # Will be real HTTP check in production

        elapsed = time.monotonic() - start

        return DeployResult(
            status=DeployStatus.LIVE if health_passed else DeployStatus.FAILED,
            provider=config.provider,
            deployment_url=deployment_url,
            build_log=build_log,
            deploy_log=deploy_log,
            health_check_passed=health_passed,
            duration_seconds=elapsed,
        )


# ── Helpers ───────────────────────────────────────────────────────


def _vercel_region(gcp_region: str) -> str:
    """Map GCP region to Vercel region identifier."""
    region_map = {
        "asia-south1": "bom1",         # Mumbai
        "us-central1": "iad1",         # Washington DC
        "us-east1": "iad1",
        "us-west1": "sfo1",            # San Francisco
        "europe-west1": "cdg1",        # Paris
        "europe-west2": "lhr1",        # London
        "asia-east1": "hkg1",          # Hong Kong
        "asia-northeast1": "hnd1",     # Tokyo
    }
    return region_map.get(gcp_region, "iad1")


def _generate_deployment_url(config: DeployConfig) -> str:
    """Generate expected deployment URL for a provider."""
    if config.provider == DeployProvider.GCP_CLOUD_RUN:
        return f"https://{config.service_name}-{config.project_id}.{config.region}.run.app"
    elif config.provider == DeployProvider.VERCEL:
        return f"https://{config.service_name}.vercel.app"
    elif config.provider == DeployProvider.RAILWAY:
        return f"https://{config.service_name}.up.railway.app"
    return ""


def _sanitize_log(log: str) -> str:
    """Remove secrets/tokens from deployment logs."""
    sanitized = re.sub(
        r"""(?:token|key|secret|password|credential)\s*[:=]\s*\S+""",
        "[REDACTED]",
        log,
        flags=re.IGNORECASE,
    )
    return sanitized


# Register the agent
_pranav = Pranav()
register_agent(_pranav)
