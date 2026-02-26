"""Aarav — Sandbox Test Executor: Docker build + API test + browser test + DB verify.

Aarav is a PURE AUTOMATION agent — NO AI calls. It orchestrates the
execution engine to test generated code in an isolated Docker sandbox.

Test phases (sequential):
1. Docker Build: build backend + frontend containers
2. Server Start: docker-compose up, wait for health checks
3. Database Migration: run alembic + seed data
4. API Testing: hit EVERY endpoint, verify status codes + schemas
5. Browser Testing: Playwright page loads + user flows + responsive
6. Database Verification: check tables, constraints, seed data

Hard timeouts per phase (AUDIT FIX #2):
- Docker build: 5 min
- Server start + health: 2 min
- API testing: 10 min
- Browser testing: 10 min
- DB verification: 3 min
- Total sandbox: 30 min

Aarav NEVER sees user input — only structured JSON from pipeline context.
"""

from __future__ import annotations

import time
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


# ── Test Phase Configuration ───────────────────────────────────────


class TestPhase(str, Enum):
    """Ordered test execution phases."""

    DOCKER_BUILD = "docker_build"
    SERVER_START = "server_start"
    DB_MIGRATION = "db_migration"
    API_TEST = "api_test"
    BROWSER_TEST = "browser_test"
    DB_VERIFY = "db_verify"


# Phase timeout limits in seconds (AUDIT FIX #2)
PHASE_TIMEOUTS: dict[TestPhase, int] = {
    TestPhase.DOCKER_BUILD: 300,   # 5 min
    TestPhase.SERVER_START: 120,   # 2 min
    TestPhase.DB_MIGRATION: 120,   # 2 min
    TestPhase.API_TEST: 600,       # 10 min
    TestPhase.BROWSER_TEST: 600,   # 10 min
    TestPhase.DB_VERIFY: 180,      # 3 min
}

TOTAL_SANDBOX_TIMEOUT: int = 1800  # 30 min


class TestStatus(str, Enum):
    """Status of a test phase."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(slots=True)
class TestPhaseResult:
    """Result of a single test phase execution."""

    phase: TestPhase
    status: TestStatus
    duration_ms: float = 0.0
    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    output: str = ""

    @property
    def passed(self) -> bool:
        return self.status == TestStatus.PASSED


@dataclass(slots=True)
class SandboxTestReport:
    """Complete sandbox test execution report."""

    phase_results: list[TestPhaseResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    all_passed: bool = True
    sandbox_id: str = ""

    @property
    def failed_phases(self) -> list[str]:
        return [r.phase.value for r in self.phase_results if not r.passed]

    @property
    def total_tests(self) -> int:
        return sum(r.tests_total for r in self.phase_results)

    @property
    def total_passed(self) -> int:
        return sum(r.tests_passed for r in self.phase_results)

    @property
    def total_failed(self) -> int:
        return sum(r.tests_failed for r in self.phase_results)

    def add(self, result: TestPhaseResult) -> None:
        self.phase_results.append(result)
        if not result.passed:
            self.all_passed = False


# ── Aarav Agent ────────────────────────────────────────────────────


class Aarav(BaseAgent):
    """Sandbox Test Executor — Docker build + API test + browser test.

    PURE AUTOMATION: No AI calls. Orchestrates the execution engine.
    """

    name = "aarav"
    display_name = "Aarav — Sandbox Test Executor"
    default_complexity = TaskComplexity.HIGH

    def __init__(self) -> None:
        super().__init__()

        self.register_tool(ToolDefinition(
            name="run_docker",
            description="Build and start Docker containers for the generated project.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["build", "start", "stop", "cleanup"],
                        "description": "Docker action to perform.",
                    },
                    "services": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Service names to target (default: all).",
                    },
                },
                "required": ["action"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="run_api_test",
            description="Run API endpoint tests against the running server.",
            parameters={
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Server base URL."},
                    "endpoints": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "method": {"type": "string"},
                                "path": {"type": "string"},
                                "expected_status": {"type": "integer"},
                            },
                        },
                        "description": "Endpoints to test.",
                    },
                },
                "required": ["base_url"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="run_playwright",
            description="Run browser tests using Playwright.",
            parameters={
                "type": "object",
                "properties": {
                    "base_url": {"type": "string"},
                    "pages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Page paths to test.",
                    },
                    "responsive": {
                        "type": "boolean",
                        "description": "Test responsive breakpoints.",
                    },
                },
                "required": ["base_url"],
            },
        ))

        self.register_tool(ToolDefinition(
            name="verify_db",
            description="Verify database schema, constraints, and seed data.",
            parameters={
                "type": "object",
                "properties": {
                    "connection_string": {"type": "string"},
                    "expected_tables": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["connection_string"],
            },
        ))

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute sandbox tests — no AI calls, pure automation.

        Phases:
        1. Docker build (backend + frontend + database)
        2. Server start + health check
        3. Database migration + seed
        4. API endpoint testing
        5. Browser testing (Playwright)
        6. Database verification
        """
        sandbox_start = time.monotonic()
        report = SandboxTestReport(sandbox_id=pipeline_run_id)

        contract = context.get("vikram", {}).get("contract", {})
        quality_findings = self._collect_quality_findings(context)

        # Phase 1: Docker Build
        build_result = await self._phase_docker_build(
            pipeline_run_id, context, contract
        )
        report.add(build_result)
        if not build_result.passed:
            return self._build_result(report, sandbox_start)

        # Phase 2: Server Start + Health Check
        start_result = await self._phase_server_start(pipeline_run_id, contract)
        report.add(start_result)
        if not start_result.passed:
            return self._build_result(report, sandbox_start)

        # Phase 3: Database Migration + Seed
        migration_result = await self._phase_db_migration(pipeline_run_id, contract)
        report.add(migration_result)
        if not migration_result.passed:
            return self._build_result(report, sandbox_start)

        # Phase 4: API Testing
        api_result = await self._phase_api_test(pipeline_run_id, contract)
        report.add(api_result)

        # Phase 5: Browser Testing (continue even if API tests partially fail)
        browser_result = await self._phase_browser_test(pipeline_run_id, contract)
        report.add(browser_result)

        # Phase 6: Database Verification
        db_result = await self._phase_db_verify(pipeline_run_id, contract)
        report.add(db_result)

        # Check total sandbox timeout
        elapsed = (time.monotonic() - sandbox_start) * 1000
        if elapsed > TOTAL_SANDBOX_TIMEOUT * 1000:
            logger.warning(
                "sandbox_timeout",
                elapsed_ms=elapsed,
                max_ms=TOTAL_SANDBOX_TIMEOUT * 1000,
            )

        return self._build_result(report, sandbox_start)

    # ── Test Phase Implementations ─────────────────────────────────

    async def _phase_docker_build(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 1: Build Docker containers from generated files."""
        phase_start = time.monotonic()

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()

            # Validate Dockerfile base images (AUDIT FIX #1)
            generated_files = context.get("shubham", {}).get("generated_files", [])
            dockerfile_content = context.get("shubham", {}).get("file_contents", {}).get(
                "backend/Dockerfile", ""
            )

            if dockerfile_content:
                validation = engine.validate_dockerfile(dockerfile_content)
                if not validation["valid"]:
                    return TestPhaseResult(
                        phase=TestPhase.DOCKER_BUILD,
                        status=TestStatus.FAILED,
                        duration_ms=(time.monotonic() - phase_start) * 1000,
                        errors=[{"type": "dockerfile_validation", "details": validation["errors"]}],
                        output=f"Dockerfile validation failed: {validation['errors']}",
                    )

            # Build sandbox containers
            build_success = await engine.build_sandbox(
                pipeline_run_id=pipeline_run_id,
                project_files=context.get("shubham", {}).get("file_contents", {}),
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.DOCKER_BUILD],
            )

            elapsed = (time.monotonic() - phase_start) * 1000
            if build_success:
                return TestPhaseResult(
                    phase=TestPhase.DOCKER_BUILD,
                    status=TestStatus.PASSED,
                    duration_ms=elapsed,
                    tests_total=1,
                    tests_passed=1,
                    output="Docker build succeeded",
                )
            else:
                return TestPhaseResult(
                    phase=TestPhase.DOCKER_BUILD,
                    status=TestStatus.FAILED,
                    duration_ms=elapsed,
                    tests_total=1,
                    tests_failed=1,
                    errors=[{"type": "build_failure", "details": "Docker build failed"}],
                    output="Docker build failed",
                )

        except Exception as exc:
            elapsed = (time.monotonic() - phase_start) * 1000
            logger.error("docker_build_error", error=str(exc))
            return TestPhaseResult(
                phase=TestPhase.DOCKER_BUILD,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": str(exc)}],
                output=f"Docker build error: {exc}",
            )

    async def _phase_server_start(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 2: Start containers and wait for health checks."""
        phase_start = time.monotonic()

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            healthy = await engine.start_sandbox(
                pipeline_run_id=pipeline_run_id,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.SERVER_START],
            )

            elapsed = (time.monotonic() - phase_start) * 1000
            if healthy:
                return TestPhaseResult(
                    phase=TestPhase.SERVER_START,
                    status=TestStatus.PASSED,
                    duration_ms=elapsed,
                    tests_total=1,
                    tests_passed=1,
                    output="All services healthy",
                )
            else:
                return TestPhaseResult(
                    phase=TestPhase.SERVER_START,
                    status=TestStatus.FAILED,
                    duration_ms=elapsed,
                    tests_total=1,
                    tests_failed=1,
                    errors=[{"type": "health_check_failure", "details": "Services failed to become healthy"}],
                    output="Health check timeout",
                )

        except Exception as exc:
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.SERVER_START,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": str(exc)}],
            )

    async def _phase_db_migration(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 3: Run Alembic migrations + seed data."""
        phase_start = time.monotonic()

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            migration_ok = await engine.run_migrations(
                pipeline_run_id=pipeline_run_id,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.DB_MIGRATION],
            )

            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.DB_MIGRATION,
                status=TestStatus.PASSED if migration_ok else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=1,
                tests_passed=1 if migration_ok else 0,
                tests_failed=0 if migration_ok else 1,
                output="Migrations applied" if migration_ok else "Migration failed",
            )

        except Exception as exc:
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.DB_MIGRATION,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": str(exc)}],
            )

    async def _phase_api_test(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 4: Hit every endpoint defined in the contract."""
        phase_start = time.monotonic()

        endpoints = contract.get("api", {}).get("endpoints", [])
        if not endpoints:
            return TestPhaseResult(
                phase=TestPhase.API_TEST,
                status=TestStatus.SKIPPED,
                duration_ms=0.0,
                output="No endpoints defined in contract",
            )

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            api_results = await engine.run_api_tests(
                pipeline_run_id=pipeline_run_id,
                endpoints=endpoints,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.API_TEST],
            )

            elapsed = (time.monotonic() - phase_start) * 1000
            passed = sum(1 for r in api_results if r.get("passed", False))
            failed = len(api_results) - passed
            errors = [r for r in api_results if not r.get("passed", False)]

            return TestPhaseResult(
                phase=TestPhase.API_TEST,
                status=TestStatus.PASSED if failed == 0 else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=len(api_results),
                tests_passed=passed,
                tests_failed=failed,
                errors=errors,
                output=f"API tests: {passed}/{len(api_results)} passed",
            )

        except Exception as exc:
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.API_TEST,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": str(exc)}],
            )

    async def _phase_browser_test(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 5: Playwright browser tests on every page."""
        phase_start = time.monotonic()

        pages = contract.get("frontend", {}).get("pages", [])
        if not pages:
            return TestPhaseResult(
                phase=TestPhase.BROWSER_TEST,
                status=TestStatus.SKIPPED,
                duration_ms=0.0,
                output="No frontend pages defined in contract",
            )

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            browser_results = await engine.run_browser_tests(
                pipeline_run_id=pipeline_run_id,
                pages=pages,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.BROWSER_TEST],
            )

            elapsed = (time.monotonic() - phase_start) * 1000
            passed = sum(1 for r in browser_results if r.get("passed", False))
            failed = len(browser_results) - passed

            return TestPhaseResult(
                phase=TestPhase.BROWSER_TEST,
                status=TestStatus.PASSED if failed == 0 else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=len(browser_results),
                tests_passed=passed,
                tests_failed=failed,
                errors=[r for r in browser_results if not r.get("passed", False)],
                output=f"Browser tests: {passed}/{len(browser_results)} passed",
            )

        except Exception as exc:
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.BROWSER_TEST,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": str(exc)}],
            )

    async def _phase_db_verify(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 6: Verify database tables, constraints, and seed data."""
        phase_start = time.monotonic()

        tables = contract.get("database", {}).get("tables", [])
        if not tables:
            return TestPhaseResult(
                phase=TestPhase.DB_VERIFY,
                status=TestStatus.SKIPPED,
                duration_ms=0.0,
                output="No tables defined in contract",
            )

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            db_results = await engine.verify_database(
                pipeline_run_id=pipeline_run_id,
                expected_tables=[t["name"] for t in tables if "name" in t],
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.DB_VERIFY],
            )

            elapsed = (time.monotonic() - phase_start) * 1000
            checks_passed = db_results.get("checks_passed", 0)
            checks_total = db_results.get("checks_total", 0)
            checks_failed = checks_total - checks_passed

            return TestPhaseResult(
                phase=TestPhase.DB_VERIFY,
                status=TestStatus.PASSED if checks_failed == 0 else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=checks_total,
                tests_passed=checks_passed,
                tests_failed=checks_failed,
                errors=db_results.get("errors", []),
                output=f"DB verification: {checks_passed}/{checks_total} checks passed",
            )

        except Exception as exc:
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.DB_VERIFY,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": str(exc)}],
            )

    # ── Helpers ────────────────────────────────────────────────────

    def _collect_quality_findings(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect findings from quality review agents."""
        findings: list[dict[str, Any]] = []
        for agent_name in ("karan", "navya", "deepika"):
            agent_output = context.get(agent_name, {})
            if isinstance(agent_output, dict):
                findings.extend(agent_output.get("findings", []))
        return findings

    def _build_result(
        self,
        report: SandboxTestReport,
        sandbox_start: float,
    ) -> AgentResult:
        """Build AgentResult from test report."""
        report.total_duration_ms = (time.monotonic() - sandbox_start) * 1000

        output = {
            "all_passed": report.all_passed,
            "total_duration_ms": round(report.total_duration_ms, 1),
            "total_tests": report.total_tests,
            "total_passed": report.total_passed,
            "total_failed": report.total_failed,
            "failed_phases": report.failed_phases,
            "phase_results": [
                {
                    "phase": r.phase.value,
                    "status": r.status.value,
                    "duration_ms": round(r.duration_ms, 1),
                    "tests_total": r.tests_total,
                    "tests_passed": r.tests_passed,
                    "tests_failed": r.tests_failed,
                    "errors": r.errors[:10],  # Cap error details
                    "output": r.output,
                }
                for r in report.phase_results
            ],
        }

        logger.info(
            "sandbox_test_complete",
            all_passed=report.all_passed,
            total_tests=report.total_tests,
            total_passed=report.total_passed,
            total_failed=report.total_failed,
            duration_ms=round(report.total_duration_ms, 1),
            failed_phases=report.failed_phases,
        )

        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.COMPLETED if report.all_passed else AgentStatus.FAILED,
            output=output,
        )


# Register the agent
_aarav = Aarav()
register_agent(_aarav)
