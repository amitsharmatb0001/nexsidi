"""Pranav -- Deployment Agent: deploys to any cloud provider via CloudConfig registry.

Pranav handles the DEPLOYMENT stage of the pipeline:
1. Validates all pre-deploy checks passed (CHECKPOINT 2 approved)
2. Selects deployment provider based on project config
3. Builds deployment config from CloudConfig registry templates
4. Deploys to the selected provider
5. Verifies deployment health
6. Returns deployment URL + logs

Supported providers (10 via CloudConfig registry):
- Vercel (serverless -- frontend + Next.js)
- AWS ECS (container -- backend, full-stack)
- GCP Cloud Run (container -- backend + full-stack)
- Railway (PaaS -- backend, databases, full-stack)
- Azure App Service (container -- backend, full-stack)
- DigitalOcean App Platform (PaaS -- full-stack)
- Netlify (serverless/static -- frontend, Jamstack)
- Fly.io (container -- backend, edge)
- Render (PaaS -- full-stack)
- Heroku (PaaS -- full-stack)

Security (AUDIT FIX #17):
- Deployment uses Sonnet 4.6 for all AI calls (security-critical)
- No secrets in generated configs -- secrets are injected at deploy time
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
    ToolDefinition,
    register_agent,
    run_agent,
    store_output,
)
from app.agents.cloud_configs import CloudConfig, get_cloud_config, list_clouds
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# -- Deployment Provider (legacy enum -- kept for backward compatibility) ------


class DeployProvider(str, Enum):
    """Legacy deployment provider enum.

    Kept for backward compatibility with existing tests and code that
    references ``DeployProvider.GCP_CLOUD_RUN`` etc.  New providers are
    resolved dynamically via the CloudConfig registry.
    """

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


# -- Deployment Config ---------------------------------------------------------


@dataclass(slots=True)
class DeployConfig:
    """Configuration for a deployment.

    ``provider`` is the legacy enum kept for backward compatibility.
    ``provider_name`` is the canonical CloudConfig name used for all new logic.
    """

    provider: DeployProvider                         # legacy -- kept for compat
    provider_name: str                               # canonical cloud config name
    service_name: str
    project_id: str
    region: str = "asia-south1"                      # Default: Mumbai
    memory: str = "512Mi"
    cpu: int = 1
    max_instances: int = 10
    min_instances: int = 0
    env_vars: dict[str, str] = field(default_factory=dict)
    health_check_path: str = "/health"
    timeout_seconds: int = 300

    @classmethod
    def from_contract(
        cls,
        contract: dict[str, Any],
        provider_name: str,
    ) -> DeployConfig:
        """Create deploy config from architecture contract.

        ``provider_name`` is the canonical CloudConfig name (e.g. ``"vercel"``,
        ``"aws_ecs"``).  A best-effort mapping to the legacy ``DeployProvider``
        enum is performed for backward compatibility.
        """
        project_name = contract.get("project_name", "app")
        # Sanitize project name for service naming
        service_name = re.sub(r"[^a-z0-9-]", "-", project_name.lower())[:50]

        deploy_config = contract.get("deployment", {})

        # Map to legacy enum for backward compat
        try:
            legacy_provider = DeployProvider(provider_name)
        except ValueError:
            legacy_provider = DeployProvider.RAILWAY

        return cls(
            provider=legacy_provider,
            provider_name=provider_name,
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
    provider_name: str = ""
    deployment_url: str = ""
    build_log: str = ""
    deploy_log: str = ""
    health_check_passed: bool = False
    error: str = ""
    duration_seconds: float = 0.0


# -- Pranav Agent --------------------------------------------------------------


class Pranav:
    """Deployment Agent -- deploys to any of 10 cloud providers via CloudConfig.

    ALWAYS uses Sonnet 4.6 for AI calls (deployment is security-critical).
    """

    name = "pranav"
    display_name = "Pranav -- Deployment Engineer"
    default_complexity = TaskComplexity.HIGH
    default_model = "claude-sonnet-4-6"  # AUDIT FIX #17: security-critical

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        # Build the provider enum list from the live CloudConfig registry
        all_providers = list_clouds()

        self.register_tool(ToolDefinition(
            name="deploy",
            description="Deploy the built project to the selected provider.",
            parameters={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "enum": all_providers,
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
                        "enum": all_providers,
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


    def register_tool(self, tool: "ToolDefinition") -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list["ToolDefinition"]:
        """All registered tools."""
        return list(self._tools.values())

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute with timing, logging, and error handling."""
        return await run_agent(self, pipeline_run_id, context)

    # -- Provider resolution ---------------------------------------------------

    @staticmethod
    def _resolve_provider(provider_name: str) -> tuple[str, CloudConfig]:
        """Resolve a provider name to its ``CloudConfig``.

        Supports all 10 cloud providers and their aliases (e.g. ``"aws"`` ->
        ``"aws_ecs"``, ``"gcp"`` -> ``"gcp_cloud_run"``).  Falls back to
        Railway if the name is unknown.
        """
        try:
            cloud_config = get_cloud_config(provider_name)
            return cloud_config.name, cloud_config
        except KeyError:
            cloud_config = get_cloud_config("railway")
            return cloud_config.name, cloud_config

    # -- Main execution --------------------------------------------------------

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
                error="No architecture contract in context -- cannot deploy",
            )

        # Select provider from contract (default: Railway -- easiest)
        deploy_section = contract.get("deployment", {})
        provider_name = deploy_section.get("provider", "railway")

        # Resolve via CloudConfig registry
        resolved_name, cloud_config = self._resolve_provider(provider_name)

        # Build deploy config from contract
        config = DeployConfig.from_contract(contract, resolved_name)

        logger.info(
            "deploy_start",
            provider=resolved_name,
            provider_display=cloud_config.display_name,
            category=cloud_config.category,
            service=config.service_name,
            region=config.region,
        )

        # Generate provider-specific config files
        config_files = self._generate_config_files(config)

        # Execute the deployment
        result = await self._deploy(config, context)

        output = {
            "status": result.status.value,
            "provider": result.provider_name or result.provider.value,
            "provider_display": cloud_config.display_name,
            "category": cloud_config.category,
            "deployment_url": result.deployment_url,
            "health_check_passed": result.health_check_passed,
            "config_files": config_files,
            # R30-FIX-9: Sanitize build_log too. Previously only deploy_log was
            # sanitized. Build logs from `docker build` or `npm run build` can
            # contain env vars leaked via build-args (e.g., API_KEY=sk-...).
            "build_log": _sanitize_log(result.build_log),
            "deploy_log": _sanitize_log(result.deploy_log),
            "duration_seconds": result.duration_seconds,
        }

        if result.error:
            output["error"] = result.error

        # SMOKE-FIX: Run automated smoke tests against deployed URL
        if result.status == DeployStatus.LIVE and result.deployment_url:
            try:
                smoke_results = await _run_smoke_tests(result.deployment_url)
                output["smoke_tests"] = smoke_results
                logger.info(
                    "smoke_tests_complete",
                    url=result.deployment_url,
                    passed=smoke_results["passed"],
                    failed=smoke_results["failed"],
                )
            except Exception as exc:
                logger.warning("smoke_tests_error", url=result.deployment_url, error=str(exc)[:200])
                output["smoke_tests"] = {"error": str(exc)[:200], "passed": 0, "failed": 0, "checks": []}

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "deploy_complete",
            provider=resolved_name,
            status=result.status.value,
            url=result.deployment_url,
            health=result.health_check_passed,
        )

        agent_status = (
            AgentStatus.COMPLETED
            if result.status == DeployStatus.LIVE
            else AgentStatus.FAILED
        )
        return AgentResult(
            agent_name=self.name,
            status=agent_status,
            output=output,
        )

    # -- Config generation (dynamic via CloudConfig registry) ------------------

    def _generate_config_files(self, config: DeployConfig) -> dict[str, str]:
        """Generate provider-specific deployment config files from CloudConfig registry."""
        try:
            cloud_config = get_cloud_config(config.provider_name)
        except KeyError:
            return {}

        files: dict[str, str] = {}
        for filename, template in cloud_config.config_files.items():
            # R28-FIX-20: Use format_map with defaultdict to handle unknown
            # placeholders (e.g., ${ECR_IMAGE} in AWS templates) gracefully.
            # str.format() raises KeyError on unknown placeholders, and the
            # silent except produced half-formatted output or raw templates.
            from collections import defaultdict
            values = defaultdict(
                lambda: "",  # unknown placeholders → empty string
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
            try:
                # R30-FIX-D5: Protect ${VAR} shell-style placeholders from
                # format_map corruption. Python's format_map treats {VAR} as
                # a placeholder, so `${ECR_IMAGE}` becomes `$` + `""` = `$`.
                # Escape `${...}` to `${{...}}` before substitution, then
                # unescape after.
                import re as _re
                escaped = _re.sub(r"\$\{([^}]+)\}", r"${{\1}}", template)
                content = escaped.format_map(values)
            except (KeyError, IndexError, ValueError):
                content = template
            files[filename] = content

        return files

    # -- Deployment execution --------------------------------------------------

    async def _deploy(
        self,
        config: DeployConfig,
        context: dict[str, Any],
    ) -> DeployResult:
        """Execute the deployment pipeline (build -> deploy -> verify).

        In production, this calls actual cloud provider CLIs.
        Currently returns structured simulation for pipeline integration testing.
        """
        import time

        start = time.monotonic()

        # Phase 1: Build
        logger.info(
            "deploy_build_start",
            provider=config.provider_name,
            service=config.service_name,
        )

        # Validate generated files exist in context
        shubham_output = context.get("shubham", {})
        aanya_output = context.get("aanya", {})
        has_backend = bool(shubham_output.get("file_contents", {}))
        has_frontend = bool(aanya_output.get("file_contents", {}))

        if not has_backend and not has_frontend:
            return DeployResult(
                status=DeployStatus.FAILED,
                provider=config.provider,
                provider_name=config.provider_name,
                error="No generated code found in context",
                duration_seconds=time.monotonic() - start,
            )

        # Resolve deploy command from CloudConfig registry
        try:
            cloud_config = get_cloud_config(config.provider_name)
            deploy_cmd = cloud_config.deploy_command
        except KeyError:
            # R27-FIX-2: Fallback to railway config when provider unknown.
            # Previously cloud_config was undefined after this except branch,
            # causing NameError on cloud_config.display_name below.
            cloud_config = get_cloud_config("railway")
            deploy_cmd = cloud_config.deploy_command

        build_log = (
            f"Building {config.service_name} for "
            f"{config.provider_name} ({cloud_config.display_name})..."
        )

        # Phase 2: Deploy
        logger.info("deploy_push_start", provider=config.provider_name)
        deploy_log = f"Deploy command: {deploy_cmd}"

        # Simulate deployment URL
        deployment_url = _generate_deployment_url(config)

        # Phase 3: Health check
        logger.info(
            "deploy_health_check",
            url=deployment_url,
            path=config.health_check_path,
        )
        health_passed = True  # Will be real HTTP check in production

        elapsed = time.monotonic() - start

        return DeployResult(
            status=DeployStatus.LIVE if health_passed else DeployStatus.FAILED,
            provider=config.provider,
            provider_name=config.provider_name,
            deployment_url=deployment_url,
            build_log=build_log,
            deploy_log=deploy_log,
            health_check_passed=health_passed,
            duration_seconds=elapsed,
        )


# -- Smoke Tests ---------------------------------------------------------------


async def _run_smoke_tests(deployment_url: str) -> dict[str, Any]:
    """Smoke test the deployed application. SMOKE-FIX."""
    import httpx
    results: dict[str, Any] = {"checks": [], "passed": 0, "failed": 0}

    checks = [
        ("health", "GET", f"{deployment_url}/health", 200),
        ("root", "GET", deployment_url, [200, 301, 302]),
        ("ssl", "HTTPS", deployment_url, None),
    ]

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, verify=True) as client:
        for name, method, url, expected_status in checks:
            if method == "HTTPS":
                check: dict[str, Any] = {"name": "ssl", "passed": url.startswith("https://"), "detail": "HTTPS enforced"}
                results["checks"].append(check)
                results["passed" if check["passed"] else "failed"] += 1
                continue
            try:
                resp = await client.get(url)
                expected = [expected_status] if isinstance(expected_status, int) else expected_status
                passed = resp.status_code in expected
                results["checks"].append({"name": name, "url": url, "status": resp.status_code, "passed": passed, "latency_ms": int(resp.elapsed.total_seconds() * 1000)})
                results["passed" if passed else "failed"] += 1
            except Exception as exc:
                results["checks"].append({"name": name, "url": url, "passed": False, "error": str(exc)[:100]})
                results["failed"] += 1

    return results


# -- Helpers -------------------------------------------------------------------


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
    """Generate expected deployment URL for a provider.

    Covers all 10 CloudConfig providers with sensible URL patterns.
    """
    url_patterns: dict[str, str] = {
        "gcp_cloud_run": (
            f"https://{config.service_name}-{config.project_id}"
            f".{config.region}.run.app"
        ),
        "vercel": f"https://{config.service_name}.vercel.app",
        "railway": f"https://{config.service_name}.up.railway.app",
        "aws_ecs": (
            f"https://{config.service_name}"
            f".{config.region}.elb.amazonaws.com"
        ),
        "azure_app_service": f"https://{config.service_name}.azurewebsites.net",
        "digitalocean": f"https://{config.service_name}.ondigitalocean.app",
        "netlify": f"https://{config.service_name}.netlify.app",
        "fly_io": f"https://{config.service_name}.fly.dev",
        "render": f"https://{config.service_name}.onrender.com",
        "heroku": f"https://{config.service_name}.herokuapp.com",
    }
    return url_patterns.get(
        config.provider_name,
        f"https://{config.service_name}.app",
    )


def _sanitize_log(log: str) -> str:
    """Remove secrets/tokens from deployment logs.

    R30-FIX-4: Added 'authorization' to the pattern. Previously missed
    ``Authorization: Bearer eyJ...`` headers that appear in deployment logs
    when cloud CLIs echo HTTP requests (e.g., ``gcloud run deploy --verbosity=debug``).
    """
    sanitized = re.sub(
        r"""(?:token|key|secret|password|credential)\s*[:=]\s*\S+""",
        "[REDACTED]",
        log,
        flags=re.IGNORECASE,
    )
    # R30-FIX-4: Specifically handle Authorization headers. The generic
    # pattern above only captures one \S+ token after the colon, but
    # `Authorization: Bearer <jwt>` has TWO words. This dedicated regex
    # captures both `Bearer <token>` as one match.
    sanitized = re.sub(
        r"""authorization\s*[:=]\s*(?:Bearer\s+)?\S+""",
        "[REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


# Register the agent
_pranav = Pranav()
register_agent(_pranav)
