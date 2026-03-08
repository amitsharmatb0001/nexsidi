"""Support Agent: triages bug reports and user issues.

Receives bug reports / support requests and:
1. Classifies the issue (bug, feature request, question, config issue)
2. Determines severity (critical, high, medium, low)
3. Identifies the likely affected component (backend, frontend, database, deploy)
4. Suggests resolution steps
5. Routes to appropriate team/agent if escalation needed

Uses AI for classification and resolution suggestion.
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
    check_inbox,
    format_inbox_for_prompt,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class IssueType(str, Enum):
    BUG = "bug"
    FEATURE_REQUEST = "feature_request"
    QUESTION = "question"
    CONFIG_ISSUE = "config_issue"
    SECURITY = "security"
    PERFORMANCE = "performance"


class IssueSeverity(str, Enum):
    CRITICAL = "critical"     # System down, data loss
    HIGH = "high"             # Major feature broken
    MEDIUM = "medium"         # Minor feature broken, workaround exists
    LOW = "low"               # Cosmetic, enhancement


class IssueComponent(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    DATABASE = "database"
    DEPLOYMENT = "deployment"
    AUTH = "auth"
    API = "api"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class TriagedIssue:
    """Result of triaging a bug report / support request."""

    original_report: str
    issue_type: IssueType
    severity: IssueSeverity
    component: IssueComponent
    title: str
    summary: str
    suggested_resolution: str
    escalation_agent: str = ""      # Which agent should handle this
    related_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_type": self.issue_type.value,
            "severity": self.severity.value,
            "component": self.component.value,
            "title": self.title,
            "summary": self.summary,
            "suggested_resolution": self.suggested_resolution,
            "escalation_agent": self.escalation_agent,
            "related_files": self.related_files,
        }


# Keyword-based classification (fast path, no AI needed)
_BUG_KEYWORDS = re.compile(r"\b(?:error|crash|fail|broken|500|404|exception|traceback|bug)\b", re.IGNORECASE)
_SECURITY_KEYWORDS = re.compile(r"\b(?:vulnerability|exploit|injection|xss|csrf|leak|breach|unauthorized)\b", re.IGNORECASE)
_PERF_KEYWORDS = re.compile(r"\b(?:slow|timeout|latency|memory|cpu|performance|hang|freeze)\b", re.IGNORECASE)
_FEATURE_KEYWORDS = re.compile(r"\b(?:add|feature|request|enhance|improve|wish|could you)\b", re.IGNORECASE)

_COMPONENT_KEYWORDS: dict[IssueComponent, re.Pattern] = {
    IssueComponent.AUTH: re.compile(r"\b(?:login|logout|password|token|jwt|auth|register|session)\b", re.IGNORECASE),
    IssueComponent.DATABASE: re.compile(r"\b(?:database|db|query|sql|migration|table|column|postgres)\b", re.IGNORECASE),
    IssueComponent.FRONTEND: re.compile(r"\b(?:ui|button|page|render|css|style|component|react|next)\b", re.IGNORECASE),
    IssueComponent.DEPLOYMENT: re.compile(r"\b(?:deploy|docker|railway|vercel|cloud\s*run|build|ci|cd)\b", re.IGNORECASE),
    IssueComponent.API: re.compile(r"\b(?:api|endpoint|request|response|rest|graphql|cors|header)\b", re.IGNORECASE),
    IssueComponent.BACKEND: re.compile(r"\b(?:server|backend|fastapi|uvicorn|celery|redis|worker)\b", re.IGNORECASE),
}

# Severity escalation mapping by component
_ESCALATION_MAP: dict[IssueComponent, str] = {
    IssueComponent.AUTH: "karan",         # Security auditor
    IssueComponent.DATABASE: "dhruv",     # Database architect
    IssueComponent.FRONTEND: "aanya",     # Frontend developer
    IssueComponent.DEPLOYMENT: "pranav",  # Deployment engineer
    IssueComponent.API: "shubham",        # Backend engineer
    IssueComponent.BACKEND: "shubham",
    IssueComponent.UNKNOWN: "tilotma",    # Project manager for routing
}


class SupportAgent:
    """Support Agent — triages bug reports and user issues."""

    name = "support_agent"
    display_name = "Support Agent"
    default_complexity = TaskComplexity.LOW
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

        self.register_tool(ToolDefinition(
            name="classify_issue",
            description="Classify a bug report or support request.",
            parameters={
                "type": "object",
                "properties": {
                    "report": {"type": "string", "description": "The bug report or support request text."},
                },
                "required": ["report"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="suggest_resolution",
            description="Suggest resolution steps for a classified issue.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_type": {"type": "string"},
                    "component": {"type": "string"},
                },
                "required": ["issue_type", "component"],
            },
        ))

        self.register_tool(WEB_SEARCH_TOOL)
        self.register_tool(WEB_SCRAPE_TOOL)

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
        """Triage issues from pipeline context.

        Looks for issues in context['support_requests'] list.
        """
        # Check inbox for messages from other agents (esp. AUTHORITY directives)
        inbox_messages = await check_inbox(self.name, pipeline_run_id)
        inbox_context = format_inbox_for_prompt(inbox_messages)
        if inbox_context and "AUTHORITY" in inbox_context:
            logger.info("authority_directive_received", agent=self.name)

        requests = context.get("support_requests", [])

        if not requests:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                output={"triaged": [], "total": 0},
            )

        triaged: list[dict[str, Any]] = []
        for req in requests:
            report_text = req if isinstance(req, str) else req.get("report", "")
            issue = self.triage(report_text)
            triaged.append(issue.to_dict())

        output = {
            "triaged": triaged,
            "total": len(triaged),
            "by_severity": self._count_by_key(triaged, "severity"),
            "by_type": self._count_by_key(triaged, "issue_type"),
            "by_component": self._count_by_key(triaged, "component"),
        }

        await store_output(self, pipeline_run_id, output)

        logger.info(
            "support_triage_complete",
            total=len(triaged),
            critical=sum(1 for t in triaged if t["severity"] == "critical"),
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED,
            output=output,
        )

    def triage(self, report: str) -> TriagedIssue:
        """Triage a single bug report / support request (zero-AI fast path)."""
        issue_type = self._classify_type(report)
        component = self._classify_component(report)
        severity = self._classify_severity(report, issue_type)
        title = self._extract_title(report)
        # Security issues always escalate to Karan regardless of component
        escalation = "karan" if issue_type == IssueType.SECURITY else _ESCALATION_MAP.get(component, "tilotma")
        resolution = self._suggest_resolution(issue_type, component, severity)

        return TriagedIssue(
            original_report=report,
            issue_type=issue_type,
            severity=severity,
            component=component,
            title=title,
            summary=report[:200],
            suggested_resolution=resolution,
            escalation_agent=escalation,
        )

    def _classify_type(self, report: str) -> IssueType:
        """Classify issue type from keywords."""
        if _SECURITY_KEYWORDS.search(report):
            return IssueType.SECURITY
        if _PERF_KEYWORDS.search(report):
            return IssueType.PERFORMANCE
        if _BUG_KEYWORDS.search(report):
            return IssueType.BUG
        if _FEATURE_KEYWORDS.search(report):
            return IssueType.FEATURE_REQUEST
        return IssueType.QUESTION

    def _classify_component(self, report: str) -> IssueComponent:
        """Classify affected component from keywords."""
        scores: dict[IssueComponent, int] = {}
        for comp, pattern in _COMPONENT_KEYWORDS.items():
            matches = len(pattern.findall(report))
            if matches > 0:
                scores[comp] = matches

        if not scores:
            return IssueComponent.UNKNOWN
        return max(scores, key=scores.get)  # type: ignore[arg-type]

    def _classify_severity(self, report: str, issue_type: IssueType) -> IssueSeverity:
        """Classify severity based on keywords + issue type."""
        report_lower = report.lower()

        if issue_type == IssueType.SECURITY:
            return IssueSeverity.CRITICAL

        if any(w in report_lower for w in ("crash", "data loss", "500", "down", "broken")):
            return IssueSeverity.CRITICAL
        if any(w in report_lower for w in ("error", "fail", "cannot", "blocked")):
            return IssueSeverity.HIGH
        if any(w in report_lower for w in ("slow", "wrong", "incorrect", "unexpected")):
            return IssueSeverity.MEDIUM
        return IssueSeverity.LOW

    def _extract_title(self, report: str) -> str:
        """Extract a concise title from the report."""
        first_line = report.strip().split("\n")[0]
        if len(first_line) <= 80:
            return first_line
        return first_line[:77] + "..."

    def _suggest_resolution(
        self,
        issue_type: IssueType,
        component: IssueComponent,
        severity: IssueSeverity,
    ) -> str:
        """Suggest resolution steps based on classification."""
        if issue_type == IssueType.SECURITY:
            return "Escalate immediately to security auditor (Karan). Review access controls and input validation."
        if issue_type == IssueType.PERFORMANCE:
            return "Profile the affected component. Check database queries for N+1 patterns. Review caching strategy."
        if issue_type == IssueType.FEATURE_REQUEST:
            return "Add to product backlog. Prioritize based on user impact and alignment with roadmap."
        if issue_type == IssueType.CONFIG_ISSUE:
            return "Verify environment variables and deployment configuration. Check logs for startup errors."

        # Bug resolution by component
        resolutions = {
            IssueComponent.AUTH: "Check JWT token validity, password hashing, and session management.",
            IssueComponent.DATABASE: "Review migration status, check query logs, verify connection pool settings.",
            IssueComponent.FRONTEND: "Check browser console for errors, verify API responses, test responsive layout.",
            IssueComponent.DEPLOYMENT: "Check deployment logs, verify environment variables, test health endpoint.",
            IssueComponent.API: "Test endpoint with curl/Postman, check request/response schema, verify CORS.",
            IssueComponent.BACKEND: "Check application logs, verify dependencies, test in isolation.",
        }
        return resolutions.get(component, "Gather more information and reproduce the issue.")

    def _count_by_key(self, items: list[dict[str, Any]], key: str) -> dict[str, int]:
        """Count items grouped by a key."""
        counts: dict[str, int] = {}
        for item in items:
            val = item.get(key, "unknown")
            counts[val] = counts.get(val, 0) + 1
        return counts


# Register
_support_agent = SupportAgent()
register_agent(_support_agent)
