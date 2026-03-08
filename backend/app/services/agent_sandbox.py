"""Agent Sandbox — Lightweight Docker for agent self-testing.

PHASE-2: Agents can self-test during code generation:
  - Shubham: start_backend() + test_endpoint() = Postman-like testing
  - Aanya: test_page() = Playwright browser testing
  - Dhruv: test_db_connection() + test_schema() = real DB testing

This is SEPARATE from Aarav's full test cycle. Agent sandboxes are:
  - Lightweight (256MB, 1 CPU)
  - Quick (30s timeout per test)
  - Per-agent (each agent gets own sandbox)
  - Disposable (destroyed after agent completes)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class EndpointTestResult:
    """Result of testing a single API endpoint."""

    method: str
    path: str
    status_code: int = 0
    expected_status: int = 200
    passed: bool = False
    response_body: str = ""
    time_ms: float = 0.0
    error: str | None = None


@dataclass
class PageTestResult:
    """Result of testing a single web page."""

    url: str
    loaded: bool = False
    title: str = ""
    js_errors: list[str] = field(default_factory=list)
    missing_elements: list[str] = field(default_factory=list)
    screenshot_b64: str = ""
    time_ms: float = 0.0
    error: str | None = None


@dataclass
class DBTestResult:
    """Result of testing database connectivity/schema."""

    connected: bool = False
    tables: list[str] = field(default_factory=list)
    missing_tables: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class AgentSandbox:
    """Lightweight Docker sandbox for agent self-testing during code generation."""

    def __init__(self, run_id: str, workspace_path: str | None = None) -> None:
        self.run_id = run_id
        self.workspace_path = workspace_path
        self._container_id: str | None = None
        self._backend_url: str | None = None
        self._frontend_url: str | None = None
        self._docker_available: bool | None = None
        self._backend_port: int = 0
        self._frontend_port: int = 0

    async def _check_docker(self) -> bool:
        """Check if Docker is available."""
        if self._docker_available is not None:
            return self._docker_available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            self._docker_available = proc.returncode == 0
        except Exception:
            self._docker_available = False
        return self._docker_available

    async def start_backend(
        self,
        framework: str = "fastapi",
        port: int = 0,
    ) -> dict[str, Any]:
        """Build and start backend server in Docker container.

        Returns: {url, status, logs, container_id}
        """
        if not await self._check_docker():
            return self._simulate_backend_start(framework)

        container_name = f"sandbox-backend-{self.run_id[:12]}-{uuid.uuid4().hex[:6]}"
        self._backend_port = port or self._find_free_port()

        try:
            # Build and start via docker-compose or direct run
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "--memory=512m",
                "--cpus=1",
                "-p", f"{self._backend_port}:8000",
            ]

            if self.workspace_path:
                cmd.extend(["-v", f"{self.workspace_path}/backend:/app"])

            # Build startup command based on framework
            _startup_commands = {
                "fastapi": "cd /app && pip install -q fastapi uvicorn 2>/dev/null; pip install -q -r requirements.txt 2>/dev/null; uvicorn app.main:app --host 0.0.0.0 --port 8000",
                "django": "cd /app && pip install -q django 2>/dev/null; pip install -q -r requirements.txt 2>/dev/null; python manage.py runserver 0.0.0.0:8000",
                "flask": "cd /app && pip install -q flask 2>/dev/null; pip install -q -r requirements.txt 2>/dev/null; flask run --host 0.0.0.0 --port 8000",
                "express": "cd /app && npm install --silent 2>/dev/null; npm start",
            }
            startup_cmd = _startup_commands.get(
                framework.lower(),
                "cd /app && pip install -q -r requirements.txt 2>/dev/null; python -m uvicorn app.main:app --host 0.0.0.0 --port 8000",
            )

            cmd.extend(["python:3.12-slim", "/bin/bash", "-c", startup_cmd])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if proc.returncode == 0:
                self._container_id = stdout.decode().strip()[:12]
                self._backend_url = f"http://localhost:{self._backend_port}"

                # Wait for server to become healthy (up to 30s)
                _healthy = await self._wait_for_health(self._backend_url, timeout=30)

                logger.info(
                    "agent_sandbox_backend_started",
                    run_id=self.run_id,
                    url=self._backend_url,
                    container=self._container_id,
                    healthy=_healthy,
                )
                return {
                    "url": self._backend_url,
                    "status": "running" if _healthy else "started_unhealthy",
                    "container_id": self._container_id,
                    "healthy": _healthy,
                }
            else:
                error = stderr.decode()[:200]
                logger.warning("agent_sandbox_backend_failed", error=error)
                return {"url": "", "status": "failed", "error": error}

        except asyncio.TimeoutError:
            return {"url": "", "status": "timeout", "error": "Backend start timed out"}
        except Exception as exc:
            return {"url": "", "status": "error", "error": str(exc)[:200]}

    async def test_endpoint(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
        expected_status: int = 200,
    ) -> EndpointTestResult:
        """Test a single API endpoint via real HTTP call.

        Like Postman — sends real request, checks response.
        """
        result = EndpointTestResult(
            method=method.upper(), path=path, expected_status=expected_status
        )

        if not self._backend_url:
            result.error = "Backend not started. Call start_backend() first."
            return result

        try:
            import httpx
            import time

            url = f"{self._backend_url}{path}"
            start = time.monotonic()

            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    json=body,
                    headers=headers or {"Content-Type": "application/json"},
                )

                result.time_ms = (time.monotonic() - start) * 1000
                result.status_code = response.status_code
                result.response_body = response.text[:2000]
                result.passed = response.status_code == expected_status

        except Exception as exc:
            result.error = str(exc)[:200]

        logger.info(
            "agent_sandbox_endpoint_test",
            method=result.method,
            path=path,
            status=result.status_code,
            expected=expected_status,
            passed=result.passed,
            time_ms=round(result.time_ms, 1),
        )

        return result

    async def start_frontend(
        self,
        framework: str = "nextjs",
        port: int = 0,
    ) -> dict[str, Any]:
        """Build and start frontend dev server in Docker."""
        if not await self._check_docker():
            return self._simulate_frontend_start(framework)

        container_name = f"sandbox-frontend-{self.run_id[:12]}-{uuid.uuid4().hex[:6]}"
        self._frontend_port = port or self._find_free_port()

        try:
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "--memory=512m",
                "--cpus=1",
                "-p", f"{self._frontend_port}:3000",
            ]

            if self.workspace_path:
                cmd.extend(["-v", f"{self.workspace_path}/frontend:/app"])

            _startup_commands = {
                "nextjs": "cd /app && npm install --silent 2>/dev/null && npm run dev -- -p 3000",
                "react": "cd /app && npm install --silent 2>/dev/null && npm start",
                "vue": "cd /app && npm install --silent 2>/dev/null && npm run dev -- --port 3000",
                "svelte": "cd /app && npm install --silent 2>/dev/null && npm run dev -- --port 3000",
            }
            startup_cmd = _startup_commands.get(
                framework.lower(),
                "cd /app && npm install --silent 2>/dev/null && npm run dev",
            )

            cmd.extend(["node:20-slim", "/bin/bash", "-c", startup_cmd])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if proc.returncode == 0:
                self._frontend_url = f"http://localhost:{self._frontend_port}"
                _healthy = await self._wait_for_health(self._frontend_url, timeout=30)
                return {
                    "url": self._frontend_url,
                    "status": "running" if _healthy else "started_unhealthy",
                    "healthy": _healthy,
                }
            else:
                error = stderr.decode()[:200]
                return {"url": "", "status": "failed", "error": error}

        except asyncio.TimeoutError:
            return {"url": "", "status": "timeout", "error": "Frontend start timed out"}
        except Exception as exc:
            return {"url": "", "status": "error", "error": str(exc)[:200]}

    async def test_page(
        self,
        path: str = "/",
        checks: list[str] | None = None,
    ) -> PageTestResult:
        """Test a web page via Playwright headless browser.

        Checks: page loads, no JS errors, required elements present.
        """
        result = PageTestResult(url=f"{self._frontend_url or 'http://localhost:3000'}{path}")

        if not self._frontend_url:
            result.error = "Frontend not started. Call start_frontend() first."
            return result

        try:
            # Use Playwright if available
            from playwright.async_api import async_playwright
            import time

            start = time.monotonic()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                # Capture JS errors
                js_errors: list[str] = []
                page.on("pageerror", lambda err: js_errors.append(str(err)[:200]))

                await page.goto(result.url, wait_until="networkidle", timeout=15000)
                result.loaded = True
                result.title = await page.title()
                result.js_errors = js_errors

                # Check for required elements
                if checks:
                    for selector in checks:
                        try:
                            await page.wait_for_selector(selector, timeout=3000)
                        except Exception:
                            result.missing_elements.append(selector)

                result.time_ms = (time.monotonic() - start) * 1000
                await browser.close()

        except ImportError:
            result.error = "Playwright not installed — page testing unavailable"
        except Exception as exc:
            result.error = str(exc)[:200]

        logger.info(
            "agent_sandbox_page_test",
            url=result.url,
            loaded=result.loaded,
            js_errors=len(result.js_errors),
            missing=result.missing_elements,
        )

        return result

    async def test_db_connection(self, db_url: str) -> DBTestResult:
        """Test database connectivity and schema."""
        result = DBTestResult()

        try:
            import asyncpg

            conn = await asyncio.wait_for(
                asyncpg.connect(db_url), timeout=10
            )
            result.connected = True

            # List tables
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            result.tables = [row["table_name"] for row in rows]

            await conn.close()

        except ImportError:
            result.errors.append("asyncpg not installed — DB testing unavailable")
        except Exception as exc:
            result.errors.append(str(exc)[:200])

        return result

    async def cleanup(self) -> None:
        """Stop and remove sandbox containers."""
        if self._container_id:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", self._container_id,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                pass  # Non-critical — error logged upstream or handled by caller
            self._container_id = None

    # ── Health check ──────────────────────────────────────────────────

    async def _wait_for_health(self, url: str, timeout: int = 30) -> bool:
        """Poll URL until it responds (server is ready)."""
        import time
        start = time.monotonic()
        while (time.monotonic() - start) < timeout:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(url)
                    if resp.status_code < 500:
                        return True
            except Exception:
                pass  # Non-critical — error logged upstream or handled by caller
            await asyncio.sleep(2)
        return False

    # ── Simulation fallbacks ─────────────────────────────────────────

    def _simulate_backend_start(self, framework: str) -> dict[str, Any]:
        """Simulate backend start when Docker is unavailable."""
        logger.warning("agent_sandbox_simulated", component="backend", framework=framework)
        self._backend_url = "http://localhost:8000"
        return {
            "url": self._backend_url,
            "status": "simulated",
            "warning": "Docker unavailable — backend start simulated. Real testing skipped.",
        }

    def _simulate_frontend_start(self, framework: str) -> dict[str, Any]:
        """Simulate frontend start when Docker is unavailable."""
        logger.warning("agent_sandbox_simulated", component="frontend", framework=framework)
        self._frontend_url = "http://localhost:3000"
        return {
            "url": self._frontend_url,
            "status": "simulated",
            "warning": "Docker unavailable — frontend start simulated. Real testing skipped.",
        }

    def _find_free_port(self) -> int:
        """Find an available port."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]
