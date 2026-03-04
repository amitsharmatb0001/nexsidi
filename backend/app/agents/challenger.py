"""Challenger — Devil's Advocate Architecture Reviewer.

Runs AFTER Vikram's architecture, BEFORE CHECKPOINT_DESIGN.
Examines the contract for:
- Over-engineering (unnecessary complexity)
- Missing edge cases (error handling, empty states, race conditions)
- Security gaps (auth bypass, injection, IDOR)
- Contradictions between tech choices and requirements
- Scalability concerns

If CRITICAL challenges found -> Vikram re-generates with challenges as constraints.
Max 2 retries before accepting with warnings.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai,
    call_ai_with_tools,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# Challenge severity levels
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# Max retries for Vikram re-generation
MAX_CHALLENGE_RETRIES = 2


class Challenge:
    """A single challenge raised against the architecture."""

    def __init__(
        self,
        category: str,
        severity: str,
        description: str,
        recommendation: str,
    ) -> None:
        self.category = category
        self.severity = severity
        self.description = description
        self.recommendation = recommendation

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "recommendation": self.recommendation,
        }


class ChallengerToolHandler:
    """Handles tool calls for Challenger's agentic architecture review loop."""

    def __init__(self) -> None:
        self._challenges: list[Challenge] = []

    async def __call__(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "raise_challenge":
            return self._raise_challenge(tool_input)
        else:
            return f"Unknown tool: {tool_name}"

    def _raise_challenge(self, tool_input: dict) -> str:
        severity = tool_input.get("severity", SEVERITY_MEDIUM).lower()
        if severity not in (SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW):
            severity = SEVERITY_MEDIUM
        challenge = Challenge(
            category=tool_input.get("category", "general"),
            severity=severity,
            description=tool_input.get("description", ""),
            recommendation=tool_input.get("recommendation", ""),
        )
        self._challenges.append(challenge)
        return f"Challenge raised: [{severity}] {challenge.description[:80]}"


class Challenger:
    """Devil's Advocate — challenges architecture decisions."""

    name = "challenger"
    display_name = "Challenger — Architecture Reviewer"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None  # VULN-1-FIX: Required by call_ai() in base.py

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="raise_challenge",
            description="Raise a challenge against the architecture.",
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["over_engineering", "missing_edge_case", "security_gap",
                                 "contradiction", "scalability", "maintainability"],
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "description": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["category", "severity", "description", "recommendation"],
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

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Review Vikram's architecture contract and raise challenges.

        Performs rule-based checks first (fast, no AI), then uses AI for
        deeper analysis if available.
        """
        vikram_output = context.get("vikram")
        if not vikram_output:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error="No architecture contract from Vikram to review",
            )

        contract = vikram_output.get("contract", {})
        challenges: list[Challenge] = []

        # Phase 1: Rule-based checks (ZERO AI)
        challenges.extend(self._check_over_engineering(contract))
        challenges.extend(self._check_security_gaps(contract))
        challenges.extend(self._check_missing_edge_cases(contract))
        challenges.extend(self._check_contradictions(contract))
        challenges.extend(self._check_scalability(contract))

        # Phase 2: AI-based deep review
        try:
            ai_challenges = await self._ai_review(contract, context)
            challenges.extend(ai_challenges)
        except Exception as exc:
            # R29-FIX-5: Sanitize error — httpx exceptions contain API keys.
            from app.services.ai_router import _sanitize_error
            logger.warning("challenger_ai_review_failed", error=_sanitize_error(exc))

        # R30-FIX-8: Deduplicate by description (case-insensitive). AI-generated
        # challenges may have same description with different capitalization
        # (e.g., "Missing rate limiting" vs "missing rate limiting").
        seen: set[str] = set()
        unique_challenges = []
        for c in challenges:
            desc_key = c.description.lower()
            if desc_key not in seen:
                seen.add(desc_key)
                unique_challenges.append(c)

        # Categorize results
        critical = [c for c in unique_challenges if c.severity == SEVERITY_CRITICAL]
        high = [c for c in unique_challenges if c.severity == SEVERITY_HIGH]
        medium = [c for c in unique_challenges if c.severity == SEVERITY_MEDIUM]
        low = [c for c in unique_challenges if c.severity == SEVERITY_LOW]

        output = {
            "total_challenges": len(unique_challenges),
            "critical_count": len(critical),
            "high_count": len(high),
            "medium_count": len(medium),
            "low_count": len(low),
            "challenges": [c.to_dict() for c in unique_challenges],
            "has_critical": len(critical) > 0,
            "verdict": "reject" if critical else ("warn" if high else "pass"),
        }

        logger.info(
            "architecture_review_complete",
            total=len(unique_challenges),
            critical=len(critical),
            high=len(high),
            verdict=output["verdict"],
        )

        await store_output(self, pipeline_run_id, output)

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    # -- Rule-Based Checks (ZERO AI) ------------------------------------------

    def _check_over_engineering(self, contract: dict[str, Any]) -> list[Challenge]:
        """Detect unnecessary complexity."""
        challenges = []
        tech = contract.get("tech_stack", {})
        endpoints = contract.get("api", {}).get("endpoints", [])
        models = contract.get("database", {}).get("tables", [])

        # Too many microservices for a simple app
        services = contract.get("services", [])
        if len(services) > 5 and len(endpoints) < 20:
            challenges.append(Challenge(
                category="over_engineering",
                severity=SEVERITY_HIGH,
                description=f"Architecture defines {len(services)} services for only {len(endpoints)} endpoints. Consider a monolith.",
                recommendation="Reduce to 1-2 services unless there's a clear scaling boundary.",
            ))

        # GraphQL + REST together
        if "graphql" in str(tech).lower() and len(endpoints) > 0:
            challenges.append(Challenge(
                category="over_engineering",
                severity=SEVERITY_MEDIUM,
                description="Both GraphQL and REST endpoints defined. Pick one API style.",
                recommendation="Use REST for simple CRUD, GraphQL for complex nested queries.",
            ))

        # Kafka/RabbitMQ for a simple app
        infra = str(tech).lower()
        if ("kafka" in infra or "rabbitmq" in infra) and len(endpoints) < 15:
            challenges.append(Challenge(
                category="over_engineering",
                severity=SEVERITY_MEDIUM,
                description="Message queue (Kafka/RabbitMQ) may be overkill for a simple app.",
                recommendation="Start with direct async calls or Redis pub/sub.",
            ))

        return challenges

    def _check_security_gaps(self, contract: dict[str, Any]) -> list[Challenge]:
        """Check for missing security measures."""
        challenges = []
        # AUDIT-FIX: Contract stores auth info under "security", not "auth".
        security = contract.get("security", {})
        auth = security.get("auth_method", security.get("auth", {}))
        endpoints = contract.get("api", {}).get("endpoints", [])

        # No auth defined
        if not auth and endpoints:
            challenges.append(Challenge(
                category="security_gap",
                severity=SEVERITY_CRITICAL,
                description="No authentication defined but API endpoints exist.",
                recommendation="Add JWT/OAuth authentication to all non-public endpoints.",
            ))

        # No rate limiting mentioned
        security = contract.get("security", {})
        if not security.get("rate_limiting") and len(endpoints) > 5:
            challenges.append(Challenge(
                category="security_gap",
                severity=SEVERITY_HIGH,
                description="No rate limiting defined for API endpoints.",
                recommendation="Add rate limiting (e.g., 100 req/min per user) to prevent abuse.",
            ))

        # File upload without validation
        for ep in endpoints:
            if "upload" in str(ep).lower() and not security.get("file_validation"):
                challenges.append(Challenge(
                    category="security_gap",
                    severity=SEVERITY_HIGH,
                    description="File upload endpoint without content type validation.",
                    recommendation="Validate file types, size limits, and scan for malware.",
                ))
                break

        return challenges

    def _check_missing_edge_cases(self, contract: dict[str, Any]) -> list[Challenge]:
        """Check for missing edge case handling."""
        challenges = []
        frontend = contract.get("frontend", {})
        pages = frontend.get("pages", [])

        # No error pages
        page_names = [str(p).lower() for p in pages]
        has_error_page = any("error" in n or "404" in n or "500" in n for n in page_names)
        if pages and not has_error_page:
            challenges.append(Challenge(
                category="missing_edge_case",
                severity=SEVERITY_MEDIUM,
                description="No error pages (404, 500) defined in frontend.",
                recommendation="Add error boundary and custom 404/500 pages.",
            ))

        # No loading states mentioned
        if pages and not any("loading" in str(p).lower() or "skeleton" in str(p).lower() for p in pages):
            challenges.append(Challenge(
                category="missing_edge_case",
                severity=SEVERITY_LOW,
                description="No loading states or skeleton screens defined.",
                recommendation="Add loading skeletons for all data-fetching pages.",
            ))

        return challenges

    def _check_contradictions(self, contract: dict[str, Any]) -> list[Challenge]:
        """Check for contradictions between requirements and tech choices."""
        challenges = []
        tech = contract.get("tech_stack", {})
        # AUDIT-FIX: Contract doesn't have a "requirements" key — requirements
        # live in Saanvi's output. Check description and features instead.
        req_text = str(contract.get("description", "")).lower()
        features_text = str(contract.get("features", [])).lower()
        combined_text = req_text + " " + features_text

        # Real-time requirements with no WebSocket support
        if any(word in combined_text for word in ["real-time", "realtime", "live", "instant"]):
            backend = str(tech.get("backend", "")).lower()
            if "socket" not in str(contract).lower() and "sse" not in str(contract).lower():
                challenges.append(Challenge(
                    category="contradiction",
                    severity=SEVERITY_HIGH,
                    description="Real-time features required but no WebSocket/SSE transport defined.",
                    recommendation="Add WebSocket or Server-Sent Events for real-time data.",
                ))

        return challenges

    def _check_scalability(self, contract: dict[str, Any]) -> list[Challenge]:
        """Check for scalability concerns."""
        challenges = []
        database = contract.get("database", {})
        models = database.get("tables", [])

        # No indexes defined for a complex schema
        if len(models) > 5:
            has_indexes = any("index" in str(m).lower() for m in models)
            if not has_indexes:
                challenges.append(Challenge(
                    category="scalability",
                    severity=SEVERITY_MEDIUM,
                    description=f"Schema has {len(models)} models but no indexes defined.",
                    recommendation="Define indexes on frequently queried columns (foreign keys, status, etc.).",
                ))

        return challenges

    # -- AI-Based Review -------------------------------------------------------

    async def _ai_review(
        self,
        contract: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Challenge]:
        """Use AI agentic loop for deeper architecture analysis.

        The AI uses the raise_challenge tool to report issues it finds,
        enabling structured output without JSON parsing fragility.
        """
        import orjson

        contract_json = orjson.dumps(contract, option=orjson.OPT_INDENT_2).decode("utf-8")

        handler = ChallengerToolHandler()

        system_prompt = (
            "You are a senior software architect acting as devil's advocate. "
            "Review this architecture contract and raise challenges using the raise_challenge tool.\n\n"
            "For each problem you find, call raise_challenge with:\n"
            "- category: over_engineering, missing_edge_case, security_gap, "
            "contradiction, scalability, or maintainability\n"
            "- severity: critical, high, medium, or low\n"
            "- description: clear explanation of the problem\n"
            "- recommendation: actionable fix\n\n"
            "Focus on:\n"
            "1. Over-engineering (unnecessary complexity for the scale)\n"
            "2. Missing edge cases (error handling, empty states, race conditions)\n"
            "3. Security vulnerabilities (auth bypass, injection, IDOR)\n"
            "4. Contradictions between tech choices and requirements\n"
            "5. Scalability bottlenecks\n\n"
            "If the architecture is solid, don't raise any challenges."
        )

        try:
            await call_ai_with_tools(
                agent=self,
                messages=[{"role": "user", "content": f"Review this contract:\n```json\n{contract_json}\n```"}],
                system_prompt=system_prompt,
                task_type="general",
                tool_handler=handler,
                max_tool_rounds=8,
            )
        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            logger.warning("challenger_ai_review_failed", error=_sanitize_error(exc))

        return handler._challenges


# Register the agent
_challenger = Challenger()
register_agent(_challenger)
