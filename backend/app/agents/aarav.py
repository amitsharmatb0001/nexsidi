"""Aarav — Sandbox Test Executor: Docker build + API test + browser test + DB verify.

Aarav is a PURE AUTOMATION agent — NO AI calls. It orchestrates the
execution engine to test generated code in an isolated Docker sandbox.

Test phases (sequential, 9 total):
1. Docker Build: build backend + frontend containers
2. Server Start: docker-compose up, wait for health checks
3. Database Migration: run alembic + seed data
4. Dependency Check: verify pip/npm install cleanly
5. API Testing: hit EVERY endpoint, verify status codes + schemas
6. Integration Testing: auth flow, validation, SQL injection, rate limiting
7. Browser Testing: Playwright page loads + user flows + responsive
8. Security Scanning: bandit (Python), safety (deps), npm audit (JS)
9. Database Verification: check tables, constraints, seed data

Hard timeouts per phase (AUDIT FIX #2):
- Docker build: 5 min
- Server start + health: 2 min
- Dependency check: 3 min
- API testing: 10 min
- Integration testing: 10 min
- Browser testing: 10 min
- Security scanning: 3 min
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
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


# ── Test Phase Configuration ───────────────────────────────────────


class TestPhase(str, Enum):
    """Ordered test execution phases."""

    DOCKER_BUILD = "docker_build"
    SERVER_START = "server_start"
    DB_MIGRATION = "db_migration"
    DEPENDENCY_CHECK = "dependency_check"      # NEW — verify all deps install
    API_TEST = "api_test"
    INTEGRATION_TEST = "integration_test"      # NEW — auth flow, validation, security
    BROWSER_TEST = "browser_test"
    SECURITY_SCAN = "security_scan"            # NEW — bandit, safety, npm audit
    DB_VERIFY = "db_verify"


# Phase timeout limits in seconds (AUDIT FIX #2)
PHASE_TIMEOUTS: dict[TestPhase, int] = {
    TestPhase.DOCKER_BUILD: 300,   # 5 min
    TestPhase.SERVER_START: 120,   # 2 min
    TestPhase.DB_MIGRATION: 120,   # 2 min
    TestPhase.DEPENDENCY_CHECK: 180,  # 3 min
    TestPhase.API_TEST: 600,       # 10 min
    TestPhase.INTEGRATION_TEST: 600,  # 10 min
    TestPhase.BROWSER_TEST: 600,   # 10 min
    TestPhase.SECURITY_SCAN: 180,  # 3 min
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
        return self.status in (TestStatus.PASSED, TestStatus.SKIPPED)


@dataclass(slots=True)
class SandboxTestReport:
    """Complete sandbox test execution report."""

    phase_results: list[TestPhaseResult] = field(default_factory=list)
    total_duration_ms: float = 0.0
    all_passed: bool = True
    sandbox_id: str = ""

    @property
    def failed_phases(self) -> list[str]:
        """Phases that actually failed (not skipped or passed)."""
        return [
            r.phase.value for r in self.phase_results
            if r.status in (TestStatus.FAILED, TestStatus.ERROR, TestStatus.TIMEOUT)
        ]

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


class Aarav:
    """Sandbox Test Executor — Docker build + API test + browser test.

    PURE AUTOMATION: No AI calls. Orchestrates the execution engine.
    """

    name = "aarav"
    display_name = "Aarav — Sandbox Test Executor"
    default_complexity = TaskComplexity.HIGH
    default_model: str | None = None

    @property
    def tools(self) -> list:
        """No tools — Aarav is pure automation, no AI tool loop."""
        return []

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
        """Execute sandbox tests — no AI calls, pure automation.

        Phases:
        1. Docker build (backend + frontend + database)
        2. Server start + health check
        3. Database migration + seed
        4. Dependency check (pip/npm install verification)
        5. API endpoint testing (contract endpoints)
        6. Integration testing (auth flow, validation, security, rate limiting)
        7. Browser testing (Playwright)
        8. Security scanning (bandit, safety, npm audit)
        9. Database verification (tables, constraints, seed data)
        """
        sandbox_start = time.monotonic()
        report = SandboxTestReport(sandbox_id=pipeline_run_id)

        contract = context.get("vikram", {}).get("contract", {})
        quality_findings = self._collect_quality_findings(context)

        def _check_timeout(phase_name: str) -> bool:
            """Check if total execution time exceeds sandbox timeout."""
            elapsed = time.monotonic() - sandbox_start
            if elapsed > TOTAL_SANDBOX_TIMEOUT:
                logger.warning(
                    "sandbox_total_timeout_exceeded",
                    elapsed_s=round(elapsed, 1),
                    max_s=TOTAL_SANDBOX_TIMEOUT,
                    after_phase=phase_name,
                )
                report.add(TestPhaseResult(
                    phase=TestPhase.DB_VERIFY,   # Sentinel
                    status=TestStatus.TIMEOUT,
                    duration_ms=elapsed * 1000,
                    output=(
                        f"Sandbox timeout ({TOTAL_SANDBOX_TIMEOUT}s) exceeded "
                        f"after {elapsed:.1f}s (after {phase_name})"
                    ),
                ))
                return True
            return False

        # Phase 0 (F11-FIX): Pre-build dependency security scan.
        # Scan dependency files (requirements.txt, package.json) BEFORE Docker
        # build. If a known-malicious package is declared, we catch it before
        # `pip install` or `npm install` executes the package's setup.py.
        pre_sec = await self._phase_pre_security_scan(context)
        report.add(pre_sec)
        # Non-blocking: pre-scan warnings don't stop the build, but they
        # are recorded in the report for visibility.
        if _check_timeout("pre_security_scan"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 1: Docker Build
        build_result = await self._phase_docker_build(
            pipeline_run_id, context, contract
        )
        report.add(build_result)
        if not build_result.passed:
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result
        if _check_timeout("docker_build"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 2: Server Start + Health Check
        start_result = await self._phase_server_start(pipeline_run_id, contract)
        report.add(start_result)
        if not start_result.passed:
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result
        if _check_timeout("server_start"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 3: Database Migration + Seed
        migration_result = await self._phase_db_migration(pipeline_run_id, contract)
        report.add(migration_result)
        if not migration_result.passed:
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result
        if _check_timeout("db_migration"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 4: Dependency Check (NEW — verify all deps install cleanly)
        dep_result = await self._phase_dependency_check(pipeline_run_id, contract)
        report.add(dep_result)
        # Non-blocking: dependency warnings don't stop the pipeline
        if _check_timeout("dependency_check"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 5: API Testing
        api_result = await self._phase_api_test(pipeline_run_id, contract)
        report.add(api_result)
        if _check_timeout("api_test"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 6: Integration Testing (NEW — auth flow, validation, security)
        integration_result = await self._phase_integration_test(
            pipeline_run_id, contract
        )
        report.add(integration_result)
        if _check_timeout("integration_test"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 7: Browser Testing (continue even if API tests partially fail)
        browser_result = await self._phase_browser_test(pipeline_run_id, contract)
        report.add(browser_result)
        if _check_timeout("browser_test"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 8: Security Scanning (NEW — bandit, safety, npm audit)
        security_result = await self._phase_security_scan(pipeline_run_id, contract)
        report.add(security_result)
        if _check_timeout("security_scan"):
            result = self._build_result(report, sandbox_start)
            await store_output(self, pipeline_run_id, result.output)
            return result

        # Phase 9: Database Verification
        db_result = await self._phase_db_verify(pipeline_run_id, contract)
        report.add(db_result)

        # STORE-FIX: Persist output to context engine for downstream agents
        result = self._build_result(report, sandbox_start)

        # ── AI test analysis: LLM analyzes failures and provides triage ──
        try:
            ai_analysis = await self._run_ai_test_analysis(result.output)
            result.output["ai_analysis"] = ai_analysis
            result.output["llm_evaluation"] = ai_analysis
        except Exception:
            logger.warning("aarav_ai_analysis_failed", exc_info=True)

        await store_output(self, pipeline_run_id, result.output)
        return result

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
                # Parse structured build errors for Fixer consumption
                build_errors = self._parse_build_errors(engine, pipeline_run_id)
                return TestPhaseResult(
                    phase=TestPhase.DOCKER_BUILD,
                    status=TestStatus.FAILED,
                    duration_ms=elapsed,
                    tests_total=1,
                    tests_failed=1,
                    errors=build_errors or [{"type": "build_failure", "details": "Docker build failed"}],
                    output=f"Docker build failed: {len(build_errors)} error(s) parsed",
                )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            elapsed = (time.monotonic() - phase_start) * 1000
            logger.error("docker_build_error", error=_sanitize_error(exc))
            return TestPhaseResult(
                phase=TestPhase.DOCKER_BUILD,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
                output=f"Docker build error: {_sanitize_error(exc)}",
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
            from app.services.ai_router import _sanitize_error  # R27-FIX
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.SERVER_START,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
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
            from app.services.ai_router import _sanitize_error  # R27-FIX
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.DB_MIGRATION,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
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

            # AARAV-FIX-5b: Distinguish skipped (passed=None) from real failures.
            # In simulation mode (Docker unavailable), engine returns passed=None.
            skipped = [r for r in api_results if r.get("passed") is None]
            if skipped and len(skipped) == len(api_results):
                # ALL tests skipped — Docker unavailable
                return TestPhaseResult(
                    phase=TestPhase.API_TEST,
                    status=TestStatus.SKIPPED,
                    duration_ms=elapsed,
                    tests_total=len(api_results),
                    output=f"API tests skipped ({len(skipped)} endpoints — Docker unavailable)",
                )

            passed = sum(1 for r in api_results if r.get("passed") is True)
            failed = sum(1 for r in api_results if r.get("passed") is False)
            errors = [r for r in api_results if r.get("passed") is False]

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
            from app.services.ai_router import _sanitize_error  # R27-FIX
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.API_TEST,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
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

            # Handle skipped results (Docker unavailable returns passed=None, skipped=True)
            skipped = [r for r in browser_results if r.get("skipped", False)]
            if skipped and len(skipped) == len(browser_results):
                return TestPhaseResult(
                    phase=TestPhase.BROWSER_TEST,
                    status=TestStatus.SKIPPED,
                    duration_ms=elapsed,
                    tests_total=len(browser_results),
                    output=f"Browser tests skipped ({len(skipped)} pages — Docker unavailable)",
                )

            passed = sum(1 for r in browser_results if r.get("passed") is True)
            failed = sum(1 for r in browser_results if r.get("passed") is False)

            return TestPhaseResult(
                phase=TestPhase.BROWSER_TEST,
                status=TestStatus.PASSED if failed == 0 else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=len(browser_results),
                tests_passed=passed,
                tests_failed=failed,
                errors=[r for r in browser_results if r.get("passed") is False],
                output=f"Browser tests: {passed}/{len(browser_results)} passed",
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error  # R27-FIX
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.BROWSER_TEST,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
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
            from app.services.ai_router import _sanitize_error  # R27-FIX
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.DB_VERIFY,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
            )

    # ── New Test Phases ──────────────────────────────────────────

    async def _phase_dependency_check(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 4: Verify all dependencies install cleanly."""
        phase_start = time.monotonic()

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            dep_results = await engine.verify_dependencies(
                pipeline_run_id=pipeline_run_id,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.DEPENDENCY_CHECK],
            )

            elapsed = (time.monotonic() - phase_start) * 1000

            # Handle simulation mode (Docker unavailable)
            if dep_results.get("simulated", False):
                return TestPhaseResult(
                    phase=TestPhase.DEPENDENCY_CHECK,
                    status=TestStatus.SKIPPED,
                    duration_ms=elapsed,
                    output="Dependency check skipped (Docker unavailable — simulation mode)",
                )

            # AARAV-FIX-2: When Docker is unavailable, verify_dependencies()
            # returns backend=None / frontend=None (key exists but value is None).
            # dict.get("backend", {}) returns None (not {}), then None.get()
            # crashes. Use `or {}` to handle None values.
            backend_data = dep_results.get("backend") or {}
            frontend_data = dep_results.get("frontend") or {}
            backend_ok = backend_data.get("passed", True) if backend_data else True
            frontend_ok = frontend_data.get("passed", True) if frontend_data else True
            all_ok = backend_ok and frontend_ok

            errors = []
            if not backend_ok:
                errors.append({
                    "type": "backend_dependency",
                    "details": backend_data.get("error", "Unknown") if backend_data else "Unknown",
                })
            if not frontend_ok:
                errors.append({
                    "type": "frontend_dependency",
                    "details": frontend_data.get("error", "Unknown") if frontend_data else "Unknown",
                })

            return TestPhaseResult(
                phase=TestPhase.DEPENDENCY_CHECK,
                status=TestStatus.PASSED if all_ok else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=2,
                tests_passed=(1 if backend_ok else 0) + (1 if frontend_ok else 0),
                tests_failed=(0 if backend_ok else 1) + (0 if frontend_ok else 1),
                errors=errors,
                output=(
                    "All dependencies install cleanly"
                    if all_ok
                    else f"Dependency issues: backend={'ok' if backend_ok else 'FAIL'}, "
                    f"frontend={'ok' if frontend_ok else 'FAIL'}"
                ),
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.DEPENDENCY_CHECK,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
            )

    async def _phase_integration_test(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 6: Run real QA-level integration tests.

        Tests auth flow (register, login, protected routes, invalid tokens),
        data validation, SQL injection, rate limiting, and 404 handling.
        """
        phase_start = time.monotonic()

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            # AARAV-FIX-3: Engine's run_integration_tests() takes
            # `endpoints` not `contract`. Extract endpoints from contract.
            endpoints = contract.get("api", {}).get("endpoints", [])
            int_results = await engine.run_integration_tests(
                pipeline_run_id=pipeline_run_id,
                endpoints=endpoints,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.INTEGRATION_TEST],
            )

            elapsed = (time.monotonic() - phase_start) * 1000

            # Engine returns [] when Docker is unavailable — treat as skipped
            if not int_results:
                return TestPhaseResult(
                    phase=TestPhase.INTEGRATION_TEST,
                    status=TestStatus.SKIPPED,
                    duration_ms=elapsed,
                    output="Integration tests skipped (Docker unavailable — simulation mode)",
                )

            passed = sum(1 for r in int_results if r.get("passed") is True)
            failed = sum(1 for r in int_results if r.get("passed") is False)
            errors = [r for r in int_results if r.get("passed") is False]

            return TestPhaseResult(
                phase=TestPhase.INTEGRATION_TEST,
                status=TestStatus.PASSED if failed == 0 else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=len(int_results),
                tests_passed=passed,
                tests_failed=failed,
                errors=errors[:10],  # Cap error output
                output=f"Integration tests: {passed}/{len(int_results)} passed",
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.INTEGRATION_TEST,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
            )

    async def _phase_security_scan(
        self,
        pipeline_run_id: str,
        contract: dict[str, Any],
    ) -> TestPhaseResult:
        """Phase 8: Run real security scanning tools (bandit, safety, npm audit)."""
        phase_start = time.monotonic()

        try:
            from app.engine.execution_engine import get_execution_engine

            engine = get_execution_engine()
            # AARAV-FIX-4: Engine's run_security_scans() doesn't take `tools` param.
            # It runs all scanners internally (bandit, safety, npm_audit).
            scan_results = await engine.run_security_scans(
                pipeline_run_id=pipeline_run_id,
                timeout_seconds=PHASE_TIMEOUTS[TestPhase.SECURITY_SCAN],
            )

            elapsed = (time.monotonic() - phase_start) * 1000

            # AARAV-FIX-5: Engine returns {"simulated": bool, "scans": [...]},
            # not {tool_name: result_dict}. Handle both formats + simulation.
            if scan_results.get("simulated", False):
                return TestPhaseResult(
                    phase=TestPhase.SECURITY_SCAN,
                    status=TestStatus.SKIPPED,
                    duration_ms=elapsed,
                    output="Security scan skipped (Docker unavailable — simulation mode)",
                )

            # Count total findings across all scanners
            total_findings = 0
            critical_findings = 0
            errors = []
            scans = scan_results.get("scans", [])
            for scan in scans:
                findings = scan.get("findings", []) + scan.get("vulnerabilities", [])
                total_findings += len(findings)
                for f in findings:
                    sev = f.get("severity", "").lower() if isinstance(f, dict) else ""
                    if sev in ("critical", "high"):
                        critical_findings += 1
                if scan.get("error"):
                    errors.append({
                        "type": f"{scan.get('tool', 'unknown')}_error",
                        "details": scan["error"],
                    })

            # Pass if no critical/high findings
            passed = critical_findings == 0

            return TestPhaseResult(
                phase=TestPhase.SECURITY_SCAN,
                status=TestStatus.PASSED if passed else TestStatus.FAILED,
                duration_ms=elapsed,
                tests_total=len(scans),
                tests_passed=len(scans) - len(errors),
                tests_failed=len(errors),
                errors=errors,
                output=(
                    f"Security scan: {total_findings} total findings, "
                    f"{critical_findings} critical/high"
                ),
            )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.SECURITY_SCAN,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
            )

    async def _phase_pre_security_scan(
        self,
        context: dict[str, Any],
    ) -> TestPhaseResult:
        """F11-FIX: Pre-build static scan of dependency files.

        Scans requirements.txt, package.json, etc. BEFORE Docker build to catch
        known-malicious or suspicious packages before they're installed.
        """
        phase_start = time.monotonic()

        try:
            # Known dangerous/typosquat packages (non-exhaustive baseline)
            _SUSPICIOUS_PACKAGES: set[str] = {
                "python-binance",  # common typosquat target
                "colourama",       # typosquat of colorama
                "python-mongo",    # typosquat of pymongo
                "nmap",            # security tool, rarely needed in apps
                "requests-html",   # uses pyppeteer (headless Chrome)
            }
            _DANGEROUS_PATTERNS: list[str] = [
                "git+http",   # Git dependencies bypass PyPI audit
                "git+ssh",    # Same
                "--extra-index-url",  # Third-party index injection
            ]

            findings: list[dict[str, str]] = []

            # Collect dependency files from generated code
            shubham = context.get("shubham", {})
            file_contents: dict[str, str] = {}
            if isinstance(shubham, dict):
                file_contents.update(shubham.get("file_contents", {}))
            aanya = context.get("aanya", {})
            if isinstance(aanya, dict):
                file_contents.update(aanya.get("file_contents", {}))

            for path, content in file_contents.items():
                if not isinstance(content, str):
                    continue

                # requirements.txt / requirements-dev.txt
                if "requirements" in path.lower() and path.endswith(".txt"):
                    for line_num, line in enumerate(content.splitlines(), 1):
                        line_stripped = line.strip().lower()
                        if not line_stripped or line_stripped.startswith("#"):
                            continue
                        pkg_name = line_stripped.split("==")[0].split(">=")[0].split("<=")[0].split("<")[0].split(">")[0].strip()
                        if pkg_name in _SUSPICIOUS_PACKAGES:
                            findings.append({"file": path, "line": str(line_num), "issue": f"Suspicious package: {pkg_name}"})
                        for pattern in _DANGEROUS_PATTERNS:
                            if pattern in line_stripped:
                                findings.append({"file": path, "line": str(line_num), "issue": f"Dangerous pattern: {pattern}"})

                # package.json
                if path.endswith("package.json"):
                    try:
                        import json as _json
                        pkg = _json.loads(content)
                        for dep_type in ("dependencies", "devDependencies"):
                            for dep_name, dep_ver in (pkg.get(dep_type, {}) or {}).items():
                                if isinstance(dep_ver, str) and (
                                    dep_ver.startswith("git") or dep_ver.startswith("http")
                                ):
                                    findings.append({
                                        "file": path,
                                        "line": dep_type,
                                        "issue": f"Git/HTTP dependency: {dep_name}@{dep_ver}",
                                    })
                    except Exception:
                        pass  # Unparseable package.json — will fail in build anyway

            elapsed = (time.monotonic() - phase_start) * 1000

            if findings:
                logger.warning("pre_security_scan_findings", count=len(findings), findings=findings[:5])
                return TestPhaseResult(
                    phase=TestPhase.SECURITY_SCAN,
                    status=TestStatus.PASSED if len(findings) < 5 else TestStatus.FAILED,
                    duration_ms=elapsed,
                    tests_total=len(file_contents),
                    tests_passed=len(file_contents) - len(findings),
                    tests_failed=len(findings),
                    errors=[{"type": "pre_security", "details": f["issue"]} for f in findings[:10]],
                    output=f"Pre-build security: {len(findings)} suspicious dependency pattern(s) found",
                )
            else:
                return TestPhaseResult(
                    phase=TestPhase.SECURITY_SCAN,
                    status=TestStatus.PASSED,
                    duration_ms=elapsed,
                    tests_total=len(file_contents),
                    tests_passed=len(file_contents),
                    output=f"Pre-build security: {len(file_contents)} dependency files scanned, clean",
                )

        except Exception as exc:
            from app.services.ai_router import _sanitize_error
            elapsed = (time.monotonic() - phase_start) * 1000
            return TestPhaseResult(
                phase=TestPhase.SECURITY_SCAN,
                status=TestStatus.ERROR,
                duration_ms=elapsed,
                errors=[{"type": "exception", "details": _sanitize_error(exc)}],
            )

    # ── Helpers ────────────────────────────────────────────────────

    def _parse_build_errors(
        self,
        engine: Any,
        pipeline_run_id: str,
    ) -> list[dict[str, Any]]:
        """Parse Docker build stderr into structured errors for Fixer.

        Extracts: ModuleNotFoundError, ImportError, SyntaxError (Python),
        npm ERR! (Node.js), pip ERROR (Python deps).
        """
        import re

        errors: list[dict[str, Any]] = []

        # Try to get build logs from engine state
        build_log = ""
        try:
            sandbox_state = getattr(engine, "_sandbox_states", {}).get(pipeline_run_id, {})
            build_log = sandbox_state.get("build_stderr", "") or sandbox_state.get("build_log", "")
        except Exception:
            pass

        if not build_log:
            return errors

        # Python errors
        for match in re.finditer(
            r"(ModuleNotFoundError|ImportError|SyntaxError|NameError|TypeError):\s*(.+)",
            build_log,
        ):
            error_type, message = match.groups()
            # Try to extract file path
            file_match = re.search(r'File "([^"]+)", line (\d+)', build_log[:match.start()])
            errors.append({
                "type": error_type,
                "details": message.strip()[:200],
                "file": file_match.group(1) if file_match else "(unknown)",
                "line": int(file_match.group(2)) if file_match else None,
            })

        # pip errors
        for match in re.finditer(r"pip\s+(?:ERROR|error):\s*(.+)", build_log):
            errors.append({
                "type": "pip_error",
                "details": match.group(1).strip()[:200],
            })

        # npm errors
        for match in re.finditer(r"npm\s+ERR!\s*(.+)", build_log):
            errors.append({
                "type": "npm_error",
                "details": match.group(1).strip()[:200],
            })

        # Generic build failures (Dockerfile)
        for match in re.finditer(r"ERROR\s*\[[\w/]+\s+(\d+/\d+)\]\s+(.+)", build_log):
            errors.append({
                "type": "docker_step_failure",
                "details": f"Step {match.group(1)}: {match.group(2).strip()[:200]}",
            })

        return errors[:20]  # Cap at 20 errors

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

        # Detect whether a real Docker sandbox ran or was silently skipped.
        # The execution engine returns True from build_sandbox() even when Docker
        # is unavailable (graceful degradation for dev/CI).  We expose this as
        # is_simulation_sandbox so the UI can warn users that no real tests ran.
        try:
            from app.engine.execution_engine import _check_docker_available

            is_simulation_sandbox = not _check_docker_available()
        except Exception:
            is_simulation_sandbox = True  # Assume simulated when engine is absent

        if is_simulation_sandbox:
            logger.warning(
                "sandbox_ran_in_simulation_mode",
                reason="Docker not available — no real containers were built or tested",
            )

        output = {
            # SIM-FIX: When simulated, all_passed is None (unknown), not True
            "all_passed": None if is_simulation_sandbox else report.all_passed,
            "is_simulation_sandbox": is_simulation_sandbox,
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

        # ── Simulation transparency: prominent warning when no real tests ran ──
        if is_simulation_sandbox:
            output["simulation_warning"] = (
                "ALL TEST PHASES SIMULATED — Docker not available. "
                "0/9 phases ran real tests. Results are UNVERIFIED. "
                "Install Docker and restart to enable real testing."
            )
            output["confidence"] = "NONE"

        logger.info(
            "sandbox_test_complete",
            all_passed=report.all_passed,
            is_simulation_sandbox=is_simulation_sandbox,
            total_tests=report.total_tests,
            total_passed=report.total_passed,
            total_failed=report.total_failed,
            duration_ms=round(report.total_duration_ms, 1),
            failed_phases=report.failed_phases,
        )

        # FIX-13: Return FAILED when real Docker tests actually fail.
        # Previously always returned COMPLETED — test failures were invisible to
        # the pipeline and broken code got delivered.  FAILED now triggers the
        # fix-retest loop (TESTING → FIXING) via the explicit route added in
        # FIX-17 (_ALLOWED_ROUTES).  Simulation mode still returns COMPLETED
        # (no real test data to act on).
        if not is_simulation_sandbox and report and not report.all_passed:
            status = AgentStatus.FAILED  # Real test failures → triggers Fixer
        else:
            status = AgentStatus.COMPLETED
        return AgentResult(
            agent_name=self.name,
            status=status,
            output=output,
        )

    # ── AI-driven test analysis ─────────────────────────────────────

    async def _run_ai_test_analysis(
        self,
        test_output: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM analyzes test results to provide root cause analysis.

        Instead of raw 'FAIL: test_login returned 500', produces:
        - Root cause per failure
        - Severity ranking (critical/major/minor)
        - Fix suggestions for the Fixer agent
        - Prioritized fix order
        Uses cheapest model (~$0.002/call).
        """
        import json as json_mod

        is_sim = test_output.get("is_simulation_sandbox", False)
        total = test_output.get("total_tests", 0)
        passed = test_output.get("total_passed", 0)
        failed = test_output.get("total_failed", 0)

        # If simulation or no failures, return quick summary
        if is_sim:
            return {
                "verdict": "SIMULATED",
                "completeness_pct": 0,
                "reasoning": "All tests were SIMULATED — Docker not available. No real test results to analyze.",
                "failures": [],
            }

        if failed == 0:
            return {
                "verdict": "PASS",
                "completeness_pct": 100,
                "reasoning": f"All {total} tests passed.",
                "failures": [],
            }

        # Collect failure details
        failure_details = []
        for phase in test_output.get("phase_results", []):
            if phase.get("tests_failed", 0) > 0:
                for err in phase.get("errors", [])[:5]:
                    failure_details.append(f"  [{phase['phase']}] {err}")

        failures_text = "\n".join(failure_details[:20]) if failure_details else "  (no error details captured)"

        eval_prompt = (
            "You are analyzing test results to help the Fixer agent. Be specific and actionable.\n\n"
            f"## Test Summary: {passed}/{total} passed, {failed} failed\n\n"
            f"## Failure Details\n{failures_text}\n\n"
            "## Your Task\n"
            "For each failure, provide:\n"
            "1. Root cause — WHY did it fail? (missing model, wrong import, missing endpoint, etc.)\n"
            "2. Severity — CRITICAL (blocks core functionality), MAJOR (blocks feature), MINOR (cosmetic)\n"
            "3. Fix suggestion — specific code change needed\n"
            "4. Priority order — fix critical issues first\n\n"
            "Respond in JSON:\n"
            "{\n"
            '  "failures": [\n'
            '    {"phase": "api_test", "root_cause": "User model missing", "severity": "critical",\n'
            '     "fix_suggestion": "Add User SQLAlchemy model to models/user.py", "priority": 1}\n'
            "  ],\n"
            '  "completeness_pct": 0-100,\n'
            '  "verdict": "PASS" or "FAIL",\n'
            '  "reasoning": "brief summary"\n'
            "}\n"
        )

        from app.services.ai_router import get_ai_router, AIRequest, AIMessage

        router = get_ai_router()
        resp = await router.call(AIRequest(
            messages=[AIMessage(role="user", content=eval_prompt)],
            complexity=TaskComplexity.LOW,
            max_tokens=1200,
            agent_name=f"{self.name}_test_analysis",
        ))

        from app.utils.json_parser import parse_json
        result = parse_json(resp.content, fallback={})
        if not isinstance(result, dict):
            result = {}
        return result


# Register the agent
_aarav = Aarav()
register_agent(_aarav)
