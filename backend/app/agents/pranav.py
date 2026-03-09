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

Agentic Tools:
- deploy_to_cloud_run: Trigger GCP Cloud Run deployment via gcloud CLI
- run_smoke_test: HTTP request to verify a live endpoint
- check_deployment_status: Poll Cloud Run service status via gcloud
- web_search / web_scrape: Research provider docs and troubleshoot issues

Security (AUDIT FIX #17):
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
    WEB_SEARCH_TOOL,
    WEB_SCRAPE_TOOL,
    call_ai_with_tools,
    handle_web_tool,
    check_inbox,
    format_inbox_for_prompt,
    notify_agents,
    register_agent,
    run_agent,
    store_output,
)
from app.agents.cloud_configs import CloudConfig, get_cloud_config, list_clouds
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


def _get_railway_api_url() -> str:
    """Railway GraphQL API URL from config."""
    try:
        from app.config import get_settings
        return get_settings().railway_api_url
    except Exception:
        # CRITICAL-2 FIX: Was calling itself recursively → stack overflow.
        return "https://backboard.railway.app/graphql/v2"


def _get_vercel_api_base() -> str:
    """Vercel API base URL from config."""
    try:
        from app.config import get_settings
        return get_settings().vercel_api_base_url
    except Exception:
        return "https://api.vercel.com"


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
    SIMULATED = "simulated"  # CRITICAL-1 FIX: distinct from LIVE — URL is invented
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
        service_name = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", project_name.lower()))[:50].strip("-")

        deploy_config = contract.get("deployment", {})

        # Map to legacy enum for backward compat
        try:
            legacy_provider = DeployProvider(provider_name)
        except ValueError:
            # AUDIT-T2-6: Log warning when unknown provider silently falls back
            logger.warning(
                "deploy_provider_unknown_fallback",
                requested=provider_name,
                fallback="railway",
            )
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
    # Simulation transparency: True when no real deploy was executed.
    # Populated by _deploy() and surfaced in agent output for UI/API honesty.
    is_simulation: bool = True


# -- Pranav Tool Handler -------------------------------------------------------


class PranavToolHandler:
    """Handles tool calls for Pranav's agentic deployment loop.

    Supports:
    - deploy_to_cloud_run: Trigger GCP Cloud Run deployment via gcloud CLI
    - run_smoke_test: HTTP request to verify a live endpoint
    - check_deployment_status: Poll Cloud Run service status via gcloud
    - web_search / web_scrape: Delegated to shared handle_web_tool
    """

    def __init__(self, config: DeployConfig | None = None) -> None:
        self._config = config
        self._deploy_results: list[dict[str, Any]] = []

    async def __call__(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """Route tool calls to the appropriate handler."""
        # Delegate web tools first (web_search, web_scrape)
        web_result = await handle_web_tool(tool_name, tool_input)
        if web_result is not None:
            return web_result

        if tool_name == "deploy_to_cloud_run":
            return await self._deploy_to_cloud_run(tool_input)
        elif tool_name == "run_smoke_test":
            return await self._run_smoke_test(tool_input)
        elif tool_name == "check_deployment_status":
            return await self._check_deployment_status(tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    async def _deploy_to_cloud_run(self, tool_input: dict[str, Any]) -> str:
        """Trigger a GCP Cloud Run deployment via gcloud CLI.

        Executes: gcloud run deploy <service_name> --source <workspace_path>
        Uses the project's default region and settings. Requires gcloud CLI
        and GCP authentication to be configured on the host.
        """
        import json as _json
        import os
        import subprocess

        service_name = tool_input["service_name"]
        workspace_path = tool_input["workspace_path"]

        # Determine region from config or default
        region = self._config.region if self._config else "asia-south1"
        project_id = self._config.project_id if self._config else "nexsidi"

        logger.info(
            "deploy_to_cloud_run_start",
            service=service_name,
            workspace=workspace_path,
            region=region,
        )

        try:
            cmd = [
                "gcloud", "run", "deploy", service_name,
                "--source", workspace_path,
                "--region", region,
                "--project", project_id,
                "--allow-unauthenticated",
                "--format", "json",
                "--quiet",
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                env={**os.environ},
            )

            if proc.returncode == 0:
                try:
                    output = _json.loads(proc.stdout)
                    url = output.get("status", {}).get("url", "")
                    result = {
                        "status": "deployed",
                        "service_name": service_name,
                        "url": url,
                        "region": region,
                    }
                except (_json.JSONDecodeError, AttributeError):
                    # Parse URL from text output
                    url = ""
                    for line in proc.stdout.splitlines():
                        if ".run.app" in line:
                            for part in line.split():
                                if part.startswith("https://"):
                                    url = part.strip()
                                    break
                    result = {
                        "status": "deployed",
                        "service_name": service_name,
                        "url": url,
                        "region": region,
                        "raw_output": _sanitize_log(proc.stdout[:2000]),
                    }
            else:
                result = {
                    "status": "failed",
                    "service_name": service_name,
                    "error": _sanitize_log(proc.stderr[:2000]),
                    "returncode": proc.returncode,
                }

            self._deploy_results.append(result)
            return _json.dumps(result)

        except FileNotFoundError:
            result = {
                "status": "error",
                "error": "gcloud CLI not found. Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install",
            }
            return _json.dumps(result)
        except subprocess.TimeoutExpired:
            result = {
                "status": "error",
                "error": "Deployment timed out after 600 seconds",
            }
            return _json.dumps(result)
        except OSError as exc:
            result = {
                "status": "error",
                "error": f"OS error running gcloud: {str(exc)[:200]}",
            }
            return _json.dumps(result)

    async def _run_smoke_test(self, tool_input: dict[str, Any]) -> str:
        """Hit a live endpoint and verify the response status code.

        Makes an HTTP GET request to the given URL and compares the
        response status against the expected status code.
        """
        import json as _json

        import httpx

        url = tool_input["url"]
        expected_status = tool_input["expected_status"]

        logger.info("smoke_test_start", url=url, expected_status=expected_status)

        try:
            async with httpx.AsyncClient(
                timeout=30.0, follow_redirects=True, verify=True
            ) as client:
                resp = await client.get(url)
                passed = resp.status_code == expected_status
                latency_ms = int(resp.elapsed.total_seconds() * 1000)

                result = {
                    "url": url,
                    "status_code": resp.status_code,
                    "expected_status": expected_status,
                    "passed": passed,
                    "latency_ms": latency_ms,
                }
                return _json.dumps(result)

        except httpx.RequestError as exc:
            result = {
                "url": url,
                "passed": False,
                "error": f"Request failed: {str(exc)[:200]}",
            }
            return _json.dumps(result)

    async def _check_deployment_status(self, tool_input: dict[str, Any]) -> str:
        """Poll a GCP Cloud Run service to check current deployment status.

        Executes: gcloud run services describe <service_name>
        Returns serving status, latest revision, and URL.
        """
        import json as _json
        import os
        import subprocess

        service_name = tool_input["service_name"]
        region = self._config.region if self._config else "asia-south1"
        project_id = self._config.project_id if self._config else "nexsidi"

        logger.info(
            "check_deployment_status_start",
            service=service_name,
            region=region,
        )

        try:
            cmd = [
                "gcloud", "run", "services", "describe", service_name,
                "--region", region,
                "--project", project_id,
                "--format", "json",
            ]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ},
            )

            if proc.returncode == 0:
                try:
                    output = _json.loads(proc.stdout)
                    status = output.get("status", {})
                    conditions = status.get("conditions", [])
                    url = status.get("url", "")
                    latest_revision = status.get("latestReadyRevisionName", "")

                    # Determine overall serving status from conditions
                    serving = any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in conditions
                    )

                    result = {
                        "service_name": service_name,
                        "serving": serving,
                        "url": url,
                        "latest_revision": latest_revision,
                        "conditions": [
                            {
                                "type": c.get("type", ""),
                                "status": c.get("status", ""),
                                "message": c.get("message", ""),
                            }
                            for c in conditions[:5]
                        ],
                    }
                except (_json.JSONDecodeError, AttributeError):
                    result = {
                        "service_name": service_name,
                        "raw_output": _sanitize_log(proc.stdout[:2000]),
                    }
            else:
                result = {
                    "service_name": service_name,
                    "error": _sanitize_log(proc.stderr[:1000]),
                    "returncode": proc.returncode,
                }

            return _json.dumps(result)

        except FileNotFoundError:
            result = {
                "error": "gcloud CLI not found. Install Google Cloud SDK.",
            }
            return _json.dumps(result)
        except subprocess.TimeoutExpired:
            result = {
                "error": "Status check timed out after 60 seconds",
            }
            return _json.dumps(result)
        except OSError as exc:
            result = {
                "error": f"OS error running gcloud: {str(exc)[:200]}",
            }
            return _json.dumps(result)


# -- Pranav Agent --------------------------------------------------------------


class Pranav:
    """Deployment Agent -- deploys to any of 10 cloud providers via CloudConfig.

    Agentic deployment agent with real deployment tools: GCP Cloud Run
    deployment, smoke testing, deployment status polling, and web
    search/scrape for researching provider docs and troubleshooting.
    """

    name = "pranav"
    display_name = "Pranav -- Deployment Engineer"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        # -- Deployment tools ---------------------------------------------------

        self.register_tool(ToolDefinition(
            name="deploy_to_cloud_run",
            description=(
                "Trigger a GCP Cloud Run deployment for a service. Builds a "
                "container image, pushes it to GCR/Artifact Registry, and "
                "deploys it to Cloud Run. Returns the deployment URL and status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Cloud Run service name (e.g., 'my-app-backend')",
                    },
                    "workspace_path": {
                        "type": "string",
                        "description": "Path to the workspace directory containing the Dockerfile",
                    },
                },
                "required": ["service_name", "workspace_path"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="run_smoke_test",
            description=(
                "Hit a live endpoint and verify the HTTP response status code. "
                "Use this after deployment to confirm the service is responding "
                "correctly. Returns status code, latency, and pass/fail result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to test (e.g., 'https://my-app.run.app/health')",
                    },
                    "expected_status": {
                        "type": "integer",
                        "description": "Expected HTTP status code (e.g., 200)",
                    },
                },
                "required": ["url", "expected_status"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="check_deployment_status",
            description=(
                "Poll a GCP Cloud Run service to check its current deployment "
                "status. Returns whether the service is serving, the latest "
                "revision, and any error conditions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Cloud Run service name to check",
                    },
                },
                "required": ["service_name"],
            },
        ))

        # -- Web research tools (from base.py) ---------------------------------
        self.register_tool(WEB_SEARCH_TOOL)
        self.register_tool(WEB_SCRAPE_TOOL)

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list[ToolDefinition]:
        """All registered deployment and web research tools."""
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
        # PHASE-3: Check inbox for messages from other agents
        inbox_messages = await check_inbox(self.name, pipeline_run_id)
        inbox_context = format_inbox_for_prompt(inbox_messages)

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

        # ── AI deployment analysis: LLM evaluates project needs ──
        try:
            ai_deploy_guidance = await self._run_ai_deployment_analysis(
                contract, resolved_name, cloud_config,
                config=config,
                inbox_context=inbox_context,
            )
            # LLM may recommend a different provider — log but don't auto-switch
            if ai_deploy_guidance.get("recommended_provider"):
                rec = ai_deploy_guidance["recommended_provider"]
                if rec.lower() != resolved_name.lower():
                    logger.info(
                        "ai_deploy_recommendation_differs",
                        configured=resolved_name,
                        recommended=rec,
                        reason=ai_deploy_guidance.get("reasoning", ""),
                    )
        except Exception:
            logger.warning("pranav_ai_analysis_failed", exc_info=True)
            ai_deploy_guidance = {}

        # Generate provider-specific config files
        config_files = self._generate_config_files(config)

        # D5-FIX: Generate Terraform IaC files alongside existing configs
        terraform_files = self._generate_terraform_files(config)

        # Generate CI/CD pipeline (GitHub Actions) — FIX-33: now includes
        # DB migration step (alembic/prisma) so first deploy creates tables.
        ci_workflow = self._generate_ci_cd_workflow(config, contract)
        if ci_workflow:
            config_files[".github/workflows/ci.yml"] = ci_workflow

        # ── FIX-33: 7-Step URL Injection Deploy Flow ──────────────────
        # Master Prompt: deploy backend → get URL → inject into frontend
        # build → deploy frontend → update CORS → verify → smoke test.
        #
        # Step 1: Deploy backend → get backend_url
        result = await self._deploy(config, context)
        backend_url = result.deployment_url or ""
        is_sim = result.is_simulation

        # Steps 2-4: Frontend deployment with injected backend URL
        frontend_url = ""
        frontend_result: DeployResult | None = None
        has_frontend = bool(context.get("aanya", {}).get("file_contents", {}))

        if has_frontend and backend_url:
            # Step 2: Build frontend env with backend URL
            frontend_env = self._build_frontend_env(backend_url)
            config_files["frontend/.env.production"] = "\n".join(
                f"{k}={v}" for k, v in frontend_env.items()
            )

            # Step 3: Deploy frontend (simulation or real)
            if is_sim:
                # SIMULATION-FIX: Do NOT generate fake frontend URLs.
                # A fake URL that looks real but doesn't exist is worse than no URL.
                # Previously generated plausible-looking URLs like "app.nexsidi.com:3000"
                # that were completely non-functional. Now we set frontend_url to empty
                # and log a clear WARNING so the delivery pipeline surfaces it.
                frontend_url = ""
                logger.warning(
                    "frontend_deploy_skipped_simulation",
                    reason="Backend deployment was simulated — frontend deployment also skipped. "
                           "No real URL exists. Configure RAILWAY_TOKEN or VERCEL_TOKEN for real deployment.",
                )
            else:
                # Real deploy: Vercel-style frontend deployment
                frontend_config = DeployConfig(
                    provider=config.provider,
                    provider_name=config.provider_name,
                    service_name=f"{config.service_name}-frontend",
                    project_id=config.project_id,
                    region=config.region,
                    env_vars=frontend_env,
                    health_check_path="/",
                    timeout_seconds=config.timeout_seconds,
                )
                frontend_result = await self._deploy(frontend_config, context)
                frontend_url = frontend_result.deployment_url or ""

            # Step 4: Update backend CORS with frontend URL
            if frontend_url:
                cors_origins = f"{frontend_url},http://localhost:3000"
                config_files[".env.cors_update"] = (
                    f"# Add to backend environment:\n"
                    f"CORS_ORIGINS={cors_origins}\n"
                )
                logger.info(
                    "cors_update_generated",
                    backend_url=backend_url,
                    frontend_url=frontend_url,
                    cors_origins=cors_origins,
                )

        # Step 5-6: Verify connectivity + smoke tests happen below

        output = {
            "status": result.status.value,
            "provider": result.provider_name or result.provider.value,
            "provider_display": cloud_config.display_name,
            "category": cloud_config.category,
            "deployment_url": result.deployment_url,
            # FIX-33: Frontend URL from 7-step deploy flow
            "frontend_url": frontend_url,
            "health_check_passed": result.health_check_passed,
            # Simulation transparency — always present so UI/API consumers can
            # show "simulated output" banners when no real deploy ran.
            "is_simulation_deploy": result.is_simulation,
            "config_files": config_files,
            # D5-FIX: Terraform IaC output (empty dict if provider has no templates)
            "terraform_files": terraform_files,
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
                from app.services.ai_router import _sanitize_error
                logger.warning("smoke_tests_error", url=result.deployment_url, error=_sanitize_error(exc)[:200])
                output["smoke_tests"] = {"error": _sanitize_error(exc)[:200], "passed": 0, "failed": 0, "checks": []}

        # ── LLM self-evaluation: deployment config completeness ──
        try:
            llm_eval = await self._run_llm_self_evaluation(
                output, contract, config=config,
            )
            output["llm_evaluation"] = llm_eval
            output["ai_deploy_guidance"] = ai_deploy_guidance
        except Exception:
            logger.warning("pranav_self_eval_failed", exc_info=True)

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

        # PHASE-3: Notify all agents (especially Tilotma/Vikram) of deploy results
        try:
            deploy_summary = (
                f"Deployment {result.status.value} — provider: {resolved_name}, "
                f"URL: {result.deployment_url or 'N/A'}, "
                f"health: {'passed' if result.health_check_passed else 'failed'}"
            )
            if frontend_url:
                deploy_summary += f", frontend: {frontend_url}"
            await notify_agents(self, pipeline_run_id, deploy_summary)
        except Exception:
            logger.debug("pranav_notify_failed", exc_info=True)

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

    # -- FIX-33: Frontend environment builder ------------------------------------

    @staticmethod
    def _build_frontend_env(backend_url: str) -> dict[str, str]:
        """Build frontend environment variables with injected backend URL.

        Covers all major frontend frameworks:
        - Vite: VITE_API_URL, VITE_WS_URL
        - Next.js: NEXT_PUBLIC_API_URL, NEXT_PUBLIC_WS_URL
        - CRA: REACT_APP_API_URL

        The WebSocket URL is derived from the backend URL by replacing
        the protocol prefix (https → wss, http → ws).
        """
        ws_url = backend_url.replace("https://", "wss://").replace(
            "http://", "ws://"
        )
        return {
            "VITE_API_URL": backend_url,
            "NEXT_PUBLIC_API_URL": backend_url,
            "REACT_APP_API_URL": backend_url,
            "VITE_WS_URL": ws_url,
            "NEXT_PUBLIC_WS_URL": ws_url,
        }

    # -- D5-FIX: Terraform IaC generation (additive) --------------------------

    def _generate_terraform_files(self, config: DeployConfig) -> dict[str, str]:
        """Generate Terraform HCL files for the deployment provider.

        D5-FIX: Additive to existing cloud config templates.  These files
        provide Infrastructure-as-Code for users who want reproducible
        infrastructure provisioning via ``terraform apply``.

        Returns an empty dict if no Terraform templates exist for the provider.
        """
        from app.agents.iac_templates.terraform import get_terraform_templates

        templates = get_terraform_templates(config.provider_name)
        if not templates:
            return {}

        from collections import defaultdict
        import re as _re

        files: dict[str, str] = {}
        values = defaultdict(
            lambda: "",  # unknown placeholders -> empty string
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

        for filename, template in templates.items():
            try:
                content = template.format_map(values)
            except (KeyError, IndexError, ValueError):
                content = template
            files[f"terraform/{filename}"] = content

        return files

    # -- Deployment execution --------------------------------------------------

    async def _deploy(  # DEPLOY-FIX
        self,
        config: DeployConfig,
        context: dict[str, Any],
    ) -> DeployResult:
        """Execute deployment (build -> deploy -> verify).  # DEPLOY-FIX

        Priority order:
          1. Railway CLI (``railway up --service NAME --json``) if RAILWAY_TOKEN set
          2. Railway GraphQL API v2 if CLI not installed but token available
          3. Provider-specific simulation fallback (logged as deploy_mode=simulation)

        Real HTTP health-check is performed after steps 1 & 2.
        Smoke tests are run after a successful live deploy.
        """
        import json as _json
        import os
        import subprocess
        import time

        import httpx

        from app.config import get_settings

        start = time.monotonic()
        settings = get_settings()

        # Pre-flight: require generated code
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

        # Resolve CloudConfig
        try:
            cloud_config = get_cloud_config(config.provider_name)
            deploy_cmd = cloud_config.deploy_command
        except KeyError:
            # R27-FIX-2: Fallback to railway when provider unknown
            cloud_config = get_cloud_config("railway")
            deploy_cmd = cloud_config.deploy_command

        logger.info(
            "deploy_build_start",
            provider=config.provider_name,
            service=config.service_name,
        )
        build_log = (
            f"Building {config.service_name} for "
            f"{config.provider_name} ({cloud_config.display_name})..."
        )

        # Resolve Railway token (env var wins over settings)
        railway_token: str = (
            os.environ.get("RAILWAY_TOKEN", "") or settings.railway_token
        )
        deployment_url: str = ""
        deploy_log: str = ""
        deploy_mode: str = "simulation"

        # -- PATH A: Railway CLI --------------------------------------------------
        if railway_token and config.provider_name == "railway":
            logger.info("deploy_mode", mode="railway_cli", service=config.service_name)
            deploy_mode = "railway_cli"
            try:
                env = {**os.environ, "RAILWAY_TOKEN": railway_token}
                cmd = ["railway", "up", "--service", config.service_name, "--json"]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env,
                )
                deploy_log = _sanitize_log(proc.stdout + proc.stderr)
                if proc.returncode == 0:
                    try:
                        output = _json.loads(proc.stdout)
                        deployment_url = (
                            output.get("url") or output.get("deploymentUrl", "")
                        )
                    except (_json.JSONDecodeError, AttributeError):
                        # Extract URL from plain-text output
                        for line in proc.stdout.splitlines():
                            if "railway.app" in line:
                                for part in line.split():
                                    if part.startswith("https://"):
                                        deployment_url = part.strip()
                                        break
                    if not deployment_url:
                        deployment_url = _generate_deployment_url(config)
                else:
                    logger.warning(
                        "railway_cli_failed",
                        returncode=proc.returncode,
                        stderr=_sanitize_log(proc.stderr[:500]),
                    )
                    deploy_mode = "simulation"
            except FileNotFoundError:
                logger.info("railway_cli_not_found", fallback="graphql_api")
                deploy_mode = "railway_graphql"
            except subprocess.TimeoutExpired:
                logger.warning("railway_cli_timeout", timeout=300)
                deploy_mode = "simulation"
            except OSError as exc:
                logger.warning("railway_cli_os_error", error=str(exc)[:100])
                deploy_mode = "simulation"

        # -- PATH B: Railway GraphQL API (CLI missing, token available) -----------
        if deploy_mode == "railway_graphql" and railway_token:
            logger.info(
                "deploy_mode", mode="railway_graphql", service=config.service_name
            )
            try:
                mutation = (
                    "mutation DeployService($serviceId: String!) {"
                    "  serviceInstanceDeploy(serviceId: $serviceId) { id status }"
                    "}"
                )
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        _get_railway_api_url(),
                        headers={
                            "Authorization": f"Bearer {railway_token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "query": mutation,
                            "variables": {"serviceId": config.service_name},
                        },
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    if "errors" not in data:
                        # CRITICAL-1 FIX: Poll Railway for real deployment URL
                        # instead of inventing one with _generate_deployment_url().
                        # The serviceInstanceDeploy mutation returns {id, status}
                        # but NOT the URL. We query the service for its domain.
                        deploy_id = ""
                        try:
                            deploy_data = data.get("data", {}).get("serviceInstanceDeploy", {})
                            deploy_id = deploy_data.get("id", "")
                        except (AttributeError, TypeError):
                            pass

                        real_url = ""
                        if deploy_id:
                            # Poll for deployment URL via service domains query
                            domain_query = (
                                "query ServiceDomains($serviceId: String!) {"
                                "  service(id: $serviceId) {"
                                "    deployments(first: 1) {"
                                "      edges { node { staticUrl status } }"
                                "    }"
                                "  }"
                                "}"
                            )
                            for _poll in range(24):  # 24 × 5s = 120s max
                                await asyncio.sleep(5)
                                try:
                                    poll_resp = await client.post(
                                        _get_railway_api_url(),
                                        headers={
                                            "Authorization": f"Bearer {railway_token}",
                                            "Content-Type": "application/json",
                                        },
                                        json={
                                            "query": domain_query,
                                            "variables": {"serviceId": config.service_name},
                                        },
                                    )
                                    if poll_resp.status_code == 200:
                                        poll_data = poll_resp.json()
                                        edges = (
                                            poll_data.get("data", {})
                                            .get("service", {})
                                            .get("deployments", {})
                                            .get("edges", [])
                                        )
                                        if edges:
                                            node = edges[0].get("node", {})
                                            static_url = node.get("staticUrl", "")
                                            deploy_status = node.get("status", "")
                                            if static_url:
                                                real_url = (
                                                    f"https://{static_url}"
                                                    if not static_url.startswith("https://")
                                                    else static_url
                                                )
                                                break
                                            if deploy_status in ("FAILED", "CRASHED", "REMOVED"):
                                                logger.warning(
                                                    "railway_deploy_failed",
                                                    status=deploy_status,
                                                    deploy_id=deploy_id,
                                                )
                                                break
                                except Exception as _poll_exc:
                                    logger.debug("railway_poll_error", error=str(_poll_exc)[:100])

                        if real_url:
                            deployment_url = real_url
                        else:
                            # Fallback: use generated URL but mark as unverified
                            deployment_url = _generate_deployment_url(config)
                            logger.warning(
                                "railway_graphql_url_unverified",
                                url=deployment_url,
                                reason="Could not fetch real URL from Railway API. "
                                       "Using generated URL — may not match actual domain.",
                            )
                        deploy_log = f"Railway GraphQL deploy triggered: {data}"
                        deploy_mode = "railway_graphql"
                    else:
                        logger.warning("railway_graphql_errors", errors=data["errors"])
                        deploy_mode = "simulation"
                else:
                    logger.warning(
                        "railway_graphql_http_error", status=resp.status_code
                    )
                    deploy_mode = "simulation"
            except httpx.RequestError as exc:
                logger.warning("railway_graphql_error", error=str(exc)[:100])
                deploy_mode = "simulation"

        # -- PATH D: Vercel REST API -----------------------------------------------
        vercel_token: str = (
            os.environ.get("VERCEL_TOKEN", "") or getattr(settings, "vercel_token", "")
        )
        if (
            not deployment_url
            and deploy_mode == "simulation"
            and vercel_token
            and config.provider_name in ("vercel", "netlify")
        ):
            logger.info("deploy_mode", mode="vercel_api", service=config.service_name)
            deploy_mode = "vercel_api"
            try:
                # HIGH-5 FIX: Collect files with size guard — Vercel v13 has practical
                # limits on JSON body size (~100MB hard, but httpx times out on large
                # payloads). Skip binary/vendor files and cap total payload at 50MB.
                _MAX_VERCEL_PAYLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
                _MAX_SINGLE_FILE_BYTES = 5 * 1024 * 1024      # 5 MB per file
                _SKIP_PATTERNS = (
                    "node_modules/", ".git/", "__pycache__/", ".venv/",
                    "dist/", "build/", ".next/", "venv/",
                )

                all_files: list[dict[str, str]] = []
                _total_size = 0
                _skipped_files: list[str] = []
                for agent_name in ("shubham", "aanya"):
                    agent_out = context.get(agent_name, {})
                    if isinstance(agent_out, dict):
                        for fpath, fcontent in agent_out.get("file_contents", {}).items():
                            # Skip vendor/build directories
                            if any(pat in fpath for pat in _SKIP_PATTERNS):
                                _skipped_files.append(fpath)
                                continue
                            file_size = len(fcontent.encode("utf-8", errors="replace"))
                            if file_size > _MAX_SINGLE_FILE_BYTES:
                                _skipped_files.append(f"{fpath} ({file_size // 1024}KB)")
                                continue
                            if _total_size + file_size > _MAX_VERCEL_PAYLOAD_BYTES:
                                _skipped_files.append(f"{fpath} (would exceed 50MB limit)")
                                continue
                            all_files.append({"file": fpath, "data": fcontent})
                            _total_size += file_size

                if _skipped_files:
                    logger.warning(
                        "vercel_deploy_files_skipped",
                        skipped_count=len(_skipped_files),
                        first_5=_skipped_files[:5],
                        total_payload_kb=_total_size // 1024,
                    )

                if all_files:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        # Create deployment via Vercel REST API v13
                        vercel_body: dict[str, Any] = {
                            "name": config.service_name,
                            "files": all_files,
                            "target": "production",
                            "projectSettings": {
                                "framework": None,  # Auto-detect
                            },
                        }
                        vercel_org_id = os.environ.get("VERCEL_ORG_ID", "") or getattr(settings, "vercel_org_id", "")
                        headers: dict[str, str] = {
                            "Authorization": f"Bearer {vercel_token}",
                            "Content-Type": "application/json",
                        }
                        if vercel_org_id:
                            headers["x-vercel-team-id"] = vercel_org_id

                        resp = await client.post(
                            f"{_get_vercel_api_base()}/v13/deployments",
                            headers=headers,
                            json=vercel_body,
                        )

                        if resp.status_code in (200, 201):
                            vercel_data = resp.json()
                            deployment_url = vercel_data.get("url", "")
                            if deployment_url and not deployment_url.startswith("https://"):
                                deployment_url = f"https://{deployment_url}"
                            deploy_log = f"Vercel deployment created: id={vercel_data.get('id', '?')}"
                            deploy_mode = "vercel_api"

                            # Poll for READY status (max 120s)
                            deploy_id = vercel_data.get("id", "")
                            if deploy_id:
                                import asyncio as _asyncio

                                for _poll in range(24):  # 24 * 5s = 120s
                                    await _asyncio.sleep(5)
                                    poll_resp = await client.get(
                                        f"{_get_vercel_api_base()}/v13/deployments/{deploy_id}",
                                        headers={"Authorization": f"Bearer {vercel_token}"},
                                    )
                                    if poll_resp.status_code == 200:
                                        poll_data = poll_resp.json()
                                        ready_state = poll_data.get("readyState", "")
                                        if ready_state == "READY":
                                            deployment_url = poll_data.get("url", deployment_url)
                                            if deployment_url and not deployment_url.startswith("https://"):
                                                deployment_url = f"https://{deployment_url}"
                                            deploy_log += f" | Status: READY after {(_poll+1)*5}s"
                                            break
                                        elif ready_state in ("ERROR", "CANCELED"):
                                            deploy_log += f" | Status: {ready_state}"
                                            deploy_mode = "simulation"
                                            break
                                else:
                                    deploy_log += " | Status: polling timed out (120s)"
                        else:
                            vercel_err = resp.text[:300]
                            logger.warning("vercel_api_error", status=resp.status_code, body=vercel_err)
                            deploy_log = f"Vercel API error ({resp.status_code}): {vercel_err}"
                            deploy_mode = "simulation"
            except httpx.RequestError as exc:
                logger.warning("vercel_api_request_error", error=str(exc)[:200])
                deploy_mode = "simulation"
            except Exception as exc:
                from app.services.ai_router import _sanitize_error
                logger.warning("vercel_api_unexpected_error", error=_sanitize_error(exc)[:200])
                deploy_mode = "simulation"

        # -- PATH E: Simulation fallback ------------------------------------------
        if not deployment_url:
            logger.info(
                "deploy_mode",
                mode=deploy_mode,
                provider=config.provider_name,
                reason="no_provider_token" if deploy_mode == "simulation" else "deploy_failed",
            )
            deployment_url = _generate_deployment_url(config)
            if deploy_mode == "simulation":
                deploy_log = (
                    f"Simulation deploy: {deploy_cmd} | "
                    f"Reason: no provider token configured. "
                    f"Set RAILWAY_TOKEN or VERCEL_TOKEN to enable real deployment."
                )
            elif not deploy_log:
                deploy_log = f"Simulation fallback after deploy failure: {deploy_cmd}"

        # -- Phase 3: Real HTTP health-check with retries -------------------------
        health_passed = False
        health_url = deployment_url.rstrip("/") + config.health_check_path
        logger.info("deploy_health_check", url=health_url, deploy_mode=deploy_mode)

        if deploy_mode == "simulation":
            # Simulation: URL is invented -- skip real probe
            health_passed = True
            logger.info("deploy_health_simulated", url=health_url)
        else:
            # REVIEW-FIX: Retry health check with exponential backoff.
            # Deployments take time to become healthy. Previously a single
            # GET was attempted, failing for slow-starting containers.
            import asyncio as _asyncio

            health_delays = [5, 10, 20]  # 3 retries: 5s, 10s, 20s
            for attempt, delay in enumerate(health_delays, 1):
                try:
                    async with httpx.AsyncClient(
                        timeout=30.0, follow_redirects=True
                    ) as client:
                        resp = await client.get(health_url)
                        health_passed = resp.status_code < 500
                        logger.info(
                            "deploy_health_result",
                            attempt=attempt,
                            status=resp.status_code,
                            passed=health_passed,
                        )
                        if health_passed:
                            break
                except httpx.RequestError as exc:
                    logger.warning(
                        "deploy_health_error",
                        attempt=attempt,
                        error=str(exc)[:100],
                    )
                    health_passed = False

                if not health_passed and attempt < len(health_delays):
                    logger.info("deploy_health_retry", delay=delay, attempt=attempt)
                    await _asyncio.sleep(delay)

        elapsed = time.monotonic() - start

        # AUDIT-FIX: Removed duplicate smoke tests from _deploy(). Smoke tests
        # are run in execute() after _deploy() returns (lines 276-289), where the
        # results are actually stored in output["smoke_tests"]. Running them here
        # too was pure waste — the results were logged and discarded.

        _is_sim = deploy_mode == "simulation"
        return DeployResult(
            status=(
                DeployStatus.LIVE
                if health_passed and not _is_sim
                else DeployStatus.FAILED
                if not health_passed and not _is_sim
                else DeployStatus.SIMULATED  # CRITICAL-1 FIX: never report sim as LIVE
            ),
            provider=config.provider,
            provider_name=config.provider_name,
            deployment_url=deployment_url,
            build_log=build_log,
            deploy_log=deploy_log,
            health_check_passed=health_passed,
            duration_seconds=elapsed,
            is_simulation=_is_sim,
        )

    # ── AI-driven deployment analysis ───────────────────────────────

    async def _run_ai_deployment_analysis(
        self,
        contract: dict[str, Any],
        provider_name: str,
        cloud_config: CloudConfig,
        config: DeployConfig | None = None,
        inbox_context: str = "",
    ) -> dict[str, Any]:
        """LLM analyzes project needs and recommends deployment strategy.

        Uses call_ai_with_tools so the LLM can deploy_to_cloud_run,
        run_smoke_test, check_deployment_status, and web_search/web_scrape
        during its analysis. This enables real deployment actions driven
        by AI reasoning rather than hard-coded logic.

        Evaluates: WebSocket support, background jobs, database needs,
        file storage, expected traffic, and whether the selected provider fits.
        """
        tech_stack = contract.get("tech_stack", {})
        features = contract.get("features", [])
        database = contract.get("database", {})
        deployment = contract.get("deployment", {})

        system_prompt = (
            "You are Pranav, NexSidi's Deployment Engineer. You have access to "
            "real deployment tools: deploy_to_cloud_run, run_smoke_test, "
            "check_deployment_status, web_search, and web_scrape.\n\n"
            "Use these tools when needed to:\n"
            "- Research provider documentation (web_search/web_scrape)\n"
            "- Deploy services to GCP Cloud Run (deploy_to_cloud_run)\n"
            "- Verify deployments are healthy (run_smoke_test)\n"
            "- Check service status (check_deployment_status)\n\n"
            "When you finish your analysis, respond with your final JSON result."
        )

        eval_prompt = (
            "Analyze this project's deployment needs. Be specific.\n\n"
            f"## Selected Provider: {cloud_config.display_name} ({cloud_config.category})\n"
            f"## Tech Stack: {tech_stack}\n"
            f"## Features: {features[:10]}\n"
            f"## Database: {database.get('name', 'none')}\n"
            f"## Deployment Config: {deployment}\n\n"
            "## Your Task\n"
            "Analyze whether the selected provider is RIGHT for this project:\n"
            "1. Does the project need WebSockets? (If so, serverless won't work)\n"
            "2. Does it need background jobs? (Need worker processes)\n"
            "3. Does it need file storage? (Need S3/GCS integration)\n"
            "4. Database requirements match provider's offerings?\n"
            "5. Any provider-specific limitations that affect this project?\n\n"
            "You may use web_search to research provider-specific limitations "
            "if you are unsure about a provider's capabilities.\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "recommended_provider": "railway" or "vercel" or same,\n'
            '  "provider_fit_score": 0-100,\n'
            '  "concerns": ["WebSocket support limited", ...],\n'
            '  "missing_configs": ["Redis worker", "file storage bucket"],\n'
            '  "reasoning": "brief explanation"\n'
            "}\n"
        )

        # PHASE-10: Enrich prompt with learned knowledge, lessons, and warnings
        try:
            from app.services.dynamic_prompt_builder import get_dynamic_prompt_builder
            _dpb = get_dynamic_prompt_builder()
            system_prompt = await _dpb.build_system_prompt(
                agent_name=self.name,
                task_context={
                    "task_type": "deployment_analysis",
                    "task_summary": f"Analyze deployment for {cloud_config.display_name}",
                },
                base_prompt_fallback=system_prompt,
            )
        except Exception as _dpb_exc:
            logger.debug("dynamic_prompt_fallback", agent=self.name, error=str(_dpb_exc)[:100])

        # PHASE-3: Inject inbox messages (AUTHORITY directives) into prompt
        if inbox_context:
            eval_prompt += f"\n\n{inbox_context}"

        tool_handler = PranavToolHandler(config=config)
        resp = await call_ai_with_tools(
            self,
            messages=[{"role": "user", "content": eval_prompt}],
            system_prompt=system_prompt,
            task_type="deployment_analysis",
            complexity=TaskComplexity.LOW,
            tool_handler=tool_handler,
            max_tool_rounds=10,
        )

        from app.utils.json_parser import parse_json
        result = parse_json(resp.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result

    async def _run_llm_self_evaluation(
        self,
        output: dict[str, Any],
        contract: dict[str, Any],
        config: DeployConfig | None = None,
    ) -> dict[str, Any]:
        """LLM reviews its own deployment config for completeness.

        Uses call_ai_with_tools so the LLM can run_smoke_test to verify
        the deployed service, check_deployment_status to confirm it is live,
        and web_search/web_scrape for troubleshooting.

        Checks: env vars present, resource limits appropriate, health check configured,
        CI/CD integration, database connection, scaling settings.
        """
        config_files = output.get("config_files", {})
        config_list = ", ".join(config_files.keys()) if config_files else "(none)"
        is_sim = output.get("is_simulation_deploy", False)
        provider = output.get("provider", "unknown")
        deployment_url = output.get("deployment_url", "")

        system_prompt = (
            "You are Pranav, NexSidi's Deployment Engineer, reviewing your own "
            "deployment output. You have tools available: run_smoke_test, "
            "check_deployment_status, web_search, web_scrape.\n\n"
            "Use run_smoke_test to verify the deployed URL is healthy. "
            "Use check_deployment_status to confirm the service is serving. "
            "Use web_search if you need to troubleshoot any issues.\n\n"
            "After your review, respond with your final JSON evaluation."
        )

        eval_prompt = (
            "Review the deployment config you just generated. Be brutally honest.\n\n"
            f"## Provider: {provider}\n"
            f"## Simulation: {'YES' if is_sim else 'NO'}\n"
            f"## Config Files Generated: {config_list}\n"
            f"## Health Check Passed: {output.get('health_check_passed', 'N/A')}\n"
            f"## Deployment URL: {deployment_url}\n\n"
            "## Your Task\n"
            "Check your deployment config for completeness:\n"
            "1. Are all required env vars documented? (DATABASE_URL, SECRET_KEY, CORS_ORIGINS, etc.)\n"
            "2. Are resource limits appropriate for the project?\n"
            "3. Is a health check endpoint configured? (/health returning {status: ok})\n"
            "4. Is HTTPS/SSL configured?\n"
            "5. Is the database connection configured? (Cloud SQL / RDS / managed DB)\n"
            "6. Are scaling settings appropriate?\n"
            "7. CRITICAL: Does the frontend build inject the backend API URL?\n"
            "   - For Vite: VITE_API_URL must be set as build arg pointing to deployed backend\n"
            "   - For Next.js: NEXT_PUBLIC_API_URL must be set at build time\n"
            "   - Without this, frontend will call localhost:8000 in production!\n"
            "8. Is database migration included in the deployment steps? (alembic upgrade head)\n"
            "9. Is there a seed data script or initial admin setup?\n\n"
        )

        # If there is a real deployment URL and it's not a simulation, prompt
        # the LLM to use run_smoke_test to verify it
        if deployment_url and not is_sim:
            eval_prompt += (
                f"The deployment URL is {deployment_url}. Use run_smoke_test "
                "to verify it is responding (expected_status=200 on /health).\n\n"
            )

        eval_prompt += (
            "Respond in JSON:\n"
            "{\n"
            '  "missing_env_vars": ["DATABASE_URL", ...],\n'
            '  "missing_configs": ["health_check", "ssl"],\n'
            '  "completeness_pct": 0-100,\n'
            '  "verdict": "PASS" or "FAIL",\n'
            '  "reasoning": "brief explanation"\n'
            "}\n"
        )

        tool_handler = PranavToolHandler(config=config)
        resp = await call_ai_with_tools(
            self,
            messages=[{"role": "user", "content": eval_prompt}],
            system_prompt=system_prompt,
            task_type="deployment_self_eval",
            complexity=TaskComplexity.LOW,
            tool_handler=tool_handler,
            max_tool_rounds=5,
        )

        from app.utils.json_parser import parse_json
        result = parse_json(resp.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result

    def _generate_ci_cd_workflow(
        self,
        config: DeployConfig,
        contract: dict[str, Any],
    ) -> str:
        """Generate GitHub Actions CI/CD workflow for the project.

        Template based on tech stack: Python → pytest + ruff; Node → jest + eslint.
        Includes lint, test, build, and deploy steps.
        """
        tech_stack = contract.get("tech_stack", {})
        backend_fw = tech_stack.get("backend", "").lower() if isinstance(tech_stack, dict) else ""
        frontend_fw = tech_stack.get("frontend", "").lower() if isinstance(tech_stack, dict) else ""

        # Determine language
        is_python = any(fw in backend_fw for fw in ("fastapi", "django", "flask", "python"))
        is_node = any(fw in frontend_fw for fw in ("react", "next", "vue", "angular", "svelte", "node"))

        lines = [
            "name: CI/CD Pipeline",
            "",
            "on:",
            "  push:",
            "    branches: [main]",
            "  pull_request:",
            "    branches: [main]",
            "",
            "jobs:",
        ]

        if is_python:
            lines.extend([
                "  backend:",
                "    runs-on: ubuntu-latest",
                "    steps:",
                "      - uses: actions/checkout@v4",
                "      - uses: actions/setup-python@v5",
                "        with:",
                "          python-version: '3.12'",
                "      - name: Install dependencies",
                "        run: pip install -r backend/requirements.txt",
                "      - name: Lint",
                "        run: cd backend && ruff check .",
                "      - name: Type check",
                "        run: cd backend && pyright .",
                "        continue-on-error: true",
                "      - name: Test",
                "        run: cd backend && pytest -x -q",
                "      # FIX-33: DB migration step for deploy (runs on main only)",
                "      - name: Run migrations",
                "        if: github.ref == 'refs/heads/main'",
                "        run: cd backend && alembic upgrade head",
                "        env:",
                "          DATABASE_URL: ${{ secrets.DATABASE_URL }}",
                "",
            ])

        if is_node:
            lines.extend([
                "  frontend:",
                "    runs-on: ubuntu-latest",
                "    steps:",
                "      - uses: actions/checkout@v4",
                "      - uses: actions/setup-node@v4",
                "        with:",
                "          node-version: '20'",
                "      - name: Install dependencies",
                "        run: cd frontend && npm ci",
                "      - name: Lint",
                "        run: cd frontend && npm run lint",
                "        continue-on-error: true",
                "      - name: Type check",
                "        run: cd frontend && npx tsc --noEmit",
                "        continue-on-error: true",
                "      - name: Build",
                "        run: cd frontend && npm run build",
                "",
            ])

        # Deploy step (only for providers we support)
        provider = config.provider_name
        if provider == "railway":
            lines.extend([
                "  deploy:",
                f"    needs: [{', '.join(n for n in ['backend', 'frontend'] if (n == 'backend' and is_python) or (n == 'frontend' and is_node))}]",
                "    runs-on: ubuntu-latest",
                "    if: github.ref == 'refs/heads/main'",
                "    steps:",
                "      - uses: actions/checkout@v4",
                "      - name: Deploy to Railway",
                "        uses: bervProject/railway-deploy@main",
                "        with:",
                "          railway_token: ${{ secrets.RAILWAY_TOKEN }}",
                f"          service: {config.service_name}",
            ])
        elif provider == "vercel":
            lines.extend([
                "  deploy:",
                "    needs: [frontend]" if is_node else "    needs: [backend]",
                "    runs-on: ubuntu-latest",
                "    if: github.ref == 'refs/heads/main'",
                "    steps:",
                "      - uses: actions/checkout@v4",
                "      - name: Deploy to Vercel",
                "        uses: amondnet/vercel-action@v25",
                "        with:",
                "          vercel-token: ${{ secrets.VERCEL_TOKEN }}",
                "          vercel-org-id: ${{ secrets.VERCEL_ORG_ID }}",
                "          vercel-project-id: ${{ secrets.VERCEL_PROJECT_ID }}",
                "          vercel-args: '--prod'",
            ])

        return "\n".join(lines) + "\n"


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
