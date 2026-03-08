"""Browser Testing — Playwright-based page testing + UI preview.

PHASE-6: Full browser testing capabilities:
  - Page load testing (errors, missing elements, JS console)
  - User flow testing (multi-step: login → navigate → click → verify)
  - Responsive testing (mobile/tablet/desktop viewports)
  - UI preview capture (full-page screenshots for checkpoints)

Used by:
  - Aanya for self-testing during frontend generation
  - Aarav for full test cycle
  - Checkpoint system for UI preview screenshots
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class PageTestResult:
    """Result of testing a single page."""

    url: str
    path: str = "/"
    loaded: bool = False
    status_code: int = 0
    title: str = ""
    js_errors: list[str] = field(default_factory=list)
    console_warnings: list[str] = field(default_factory=list)
    missing_elements: list[str] = field(default_factory=list)
    screenshot_b64: str = ""
    time_ms: float = 0.0
    error: str | None = None


@dataclass
class FlowStep:
    """A single step in a user flow test."""

    action: str  # "navigate", "click", "fill", "wait", "assert_text", "assert_visible"
    selector: str = ""
    value: str = ""
    url: str = ""
    timeout_ms: int = 5000


@dataclass
class FlowTestResult:
    """Result of a multi-step user flow test."""

    flow_name: str
    total_steps: int = 0
    steps_passed: int = 0
    steps_failed: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)  # Base64 PNGs
    time_ms: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.steps_passed == self.total_steps and not self.error


@dataclass
class ResponsiveTestResult:
    """Result of responsive testing across viewports."""

    viewport_name: str
    width: int
    height: int
    page_result: PageTestResult | None = None


# ── Phase 2B: Visual Matrix Dataclasses ──────────────────────────────


@dataclass
class StepScreenshot:
    """A screenshot taken at a specific step in a visual flow."""
    step_name: str
    screenshot_b64: str = ""
    viewport: str = ""
    timestamp: float = 0.0


@dataclass
class VisualFlowResult:
    """Result of a visual flow test with step-by-step screenshots."""
    flow_name: str
    total_steps: int = 0
    steps_passed: int = 0
    steps_failed: list[str] = field(default_factory=list)
    step_screenshots: list[StepScreenshot] = field(default_factory=list)
    time_ms: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.steps_passed == self.total_steps and not self.error


@dataclass
class VisualMatrixEntry:
    """Result for a single viewport+flow combination in the visual matrix."""
    viewport_name: str = ""
    width: int = 0
    height: int = 0
    flow_name: str = ""
    steps_passed: int = 0
    total_steps: int = 0
    screenshots: list[StepScreenshot] = field(default_factory=list)
    passed: bool = False
    error: str | None = None


@dataclass
class VisualMatrixReport:
    """Full visual test matrix across all viewports and flows."""
    viewports: list[str] = field(default_factory=list)
    flows: list[str] = field(default_factory=list)
    results: list[VisualMatrixEntry] = field(default_factory=list)
    total_time_ms: float = 0.0
    error: str | None = None

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results) and not self.error


# ── Browser Test Runner ──────────────────────────────────────────────


class BrowserTestRunner:
    """Full browser testing with Playwright."""

    def __init__(self) -> None:
        self._playwright_available: bool | None = None

    async def _check_playwright(self) -> bool:
        """Check if Playwright is available."""
        if self._playwright_available is not None:
            return self._playwright_available
        try:
            from playwright.async_api import async_playwright  # noqa: F401
            self._playwright_available = True
        except ImportError:
            self._playwright_available = False
            logger.warning("playwright_not_available")
        return self._playwright_available

    async def test_page_load(
        self, base_url: str, path: str = "/", timeout_ms: int = 15000
    ) -> PageTestResult:
        """Navigate to page, check for errors, return result + screenshot."""
        url = f"{base_url.rstrip('/')}{path}"
        result = PageTestResult(url=url, path=path)

        if not await self._check_playwright():
            result.error = "Playwright not installed"
            return result

        try:
            from playwright.async_api import async_playwright
            import time

            start = time.monotonic()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                # Capture console messages
                js_errors: list[str] = []
                warnings: list[str] = []
                page.on("pageerror", lambda err: js_errors.append(str(err)[:200]))
                page.on("console", lambda msg: (
                    warnings.append(msg.text[:200])
                    if msg.type == "warning" else None
                ))

                response = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                result.loaded = True
                result.status_code = response.status if response else 0
                result.title = await page.title()
                result.js_errors = js_errors
                result.console_warnings = warnings

                # Take screenshot
                screenshot_bytes = await page.screenshot(full_page=True, type="png")
                import base64
                result.screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")

                result.time_ms = (time.monotonic() - start) * 1000
                await browser.close()

        except Exception as exc:
            result.error = str(exc)[:300]

        logger.info(
            "browser_test_page",
            url=url,
            loaded=result.loaded,
            status=result.status_code,
            js_errors=len(result.js_errors),
            time_ms=round(result.time_ms, 1),
        )
        return result

    async def test_user_flow(
        self, base_url: str, flow_name: str, steps: list[FlowStep]
    ) -> FlowTestResult:
        """Execute multi-step user flow (login → navigate → click → verify)."""
        result = FlowTestResult(flow_name=flow_name, total_steps=len(steps))

        if not await self._check_playwright():
            result.error = "Playwright not installed"
            return result

        try:
            from playwright.async_api import async_playwright
            import time

            start = time.monotonic()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(base_url, wait_until="networkidle", timeout=15000)

                for i, step in enumerate(steps):
                    try:
                        if step.action == "navigate":
                            await page.goto(
                                f"{base_url.rstrip('/')}{step.url}",
                                wait_until="networkidle",
                                timeout=step.timeout_ms,
                            )
                        elif step.action == "click":
                            await page.click(step.selector, timeout=step.timeout_ms)
                        elif step.action == "fill":
                            await page.fill(step.selector, step.value, timeout=step.timeout_ms)
                        elif step.action == "wait":
                            await page.wait_for_selector(step.selector, timeout=step.timeout_ms)
                        elif step.action == "assert_text":
                            el = await page.wait_for_selector(step.selector, timeout=step.timeout_ms)
                            if el:
                                text = await el.text_content()
                                if step.value not in (text or ""):
                                    result.steps_failed.append(
                                        f"Step {i+1}: Expected text '{step.value}' in '{step.selector}'"
                                    )
                                    continue
                        elif step.action == "assert_visible":
                            await page.wait_for_selector(step.selector, state="visible", timeout=step.timeout_ms)

                        result.steps_passed += 1
                    except Exception as step_exc:
                        result.steps_failed.append(f"Step {i+1} ({step.action}): {str(step_exc)[:100]}")

                result.time_ms = (time.monotonic() - start) * 1000
                await browser.close()

        except Exception as exc:
            result.error = str(exc)[:300]

        logger.info(
            "browser_test_flow",
            flow=flow_name,
            passed=result.steps_passed,
            failed=len(result.steps_failed),
            total=result.total_steps,
        )
        return result

    async def test_responsive(
        self, base_url: str, path: str = "/"
    ) -> list[ResponsiveTestResult]:
        """Test page at mobile/tablet/desktop viewports."""
        viewports = [
            ("mobile", 375, 812),
            ("tablet", 768, 1024),
            ("desktop", 1280, 800),
        ]

        results = []
        for name, width, height in viewports:
            vp_result = ResponsiveTestResult(viewport_name=name, width=width, height=height)

            if not await self._check_playwright():
                results.append(vp_result)
                continue

            try:
                from playwright.async_api import async_playwright
                import time

                start = time.monotonic()
                url = f"{base_url.rstrip('/')}{path}"

                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page(viewport={"width": width, "height": height})

                    js_errors: list[str] = []
                    page.on("pageerror", lambda err: js_errors.append(str(err)[:200]))

                    response = await page.goto(url, wait_until="networkidle", timeout=15000)

                    import base64
                    screenshot = await page.screenshot(full_page=True, type="png")

                    vp_result.page_result = PageTestResult(
                        url=url,
                        path=path,
                        loaded=True,
                        status_code=response.status if response else 0,
                        title=await page.title(),
                        js_errors=js_errors,
                        screenshot_b64=base64.b64encode(screenshot).decode("ascii"),
                        time_ms=(time.monotonic() - start) * 1000,
                    )

                    await browser.close()

            except Exception as exc:
                vp_result.page_result = PageTestResult(url=url, path=path, error=str(exc)[:200])

            results.append(vp_result)

        return results

    async def capture_preview(self, url: str) -> str:
        """Capture full-page screenshot for UI preview. Returns base64 PNG."""
        result = await self.test_page_load(url)
        return result.screenshot_b64

    # ── Phase 2B: Visual Flow Testing ─────────────────────────────────

    async def test_visual_flow(
        self,
        base_url: str,
        flow_steps: list[dict[str, str]],
        capture_screenshots: bool = True,
    ) -> VisualFlowResult:
        """Execute a user flow with screenshots at every step.

        Each step captures a screenshot for visual verification of the UI
        state at that point. Unlike test_user_flow, this is designed for
        visual matrix testing — the screenshots are the primary output.

        Args:
            base_url: Application base URL.
            flow_steps: List of dicts with "action", "selector", "value", "name".
            capture_screenshots: Whether to capture screenshots (default True).

        Returns:
            VisualFlowResult with step-by-step screenshots.
        """
        result = VisualFlowResult(flow_name="visual_flow", total_steps=len(flow_steps))

        if not await self._check_playwright():
            result.error = "Playwright not installed"
            return result

        try:
            from playwright.async_api import async_playwright
            import base64
            import time

            start = time.monotonic()

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(base_url, wait_until="networkidle", timeout=15000)

                for i, step in enumerate(flow_steps):
                    step_name = step.get("name", f"step_{i+1}")
                    action = step.get("action", "navigate")
                    selector = step.get("selector", "")
                    value = step.get("value", "")

                    try:
                        if action == "navigate":
                            url = step.get("url", "/")
                            await page.goto(
                                f"{base_url.rstrip('/')}{url}",
                                wait_until="networkidle",
                                timeout=10000,
                            )
                        elif action == "click":
                            await page.click(selector, timeout=5000)
                        elif action == "fill":
                            await page.fill(selector, value, timeout=5000)
                        elif action == "wait":
                            await page.wait_for_selector(selector, timeout=5000)

                        result.steps_passed += 1

                        # Capture screenshot after each step
                        if capture_screenshots:
                            await page.wait_for_timeout(500)  # Brief settle time
                            screenshot = await page.screenshot(full_page=True, type="png")
                            result.step_screenshots.append(StepScreenshot(
                                step_name=step_name,
                                screenshot_b64=base64.b64encode(screenshot).decode("ascii"),
                                viewport=f"{page.viewport_size['width']}x{page.viewport_size['height']}",
                                timestamp=time.monotonic() - start,
                            ))

                    except Exception as step_exc:
                        result.steps_failed.append(f"{step_name}: {str(step_exc)[:100]}")

                result.time_ms = (time.monotonic() - start) * 1000
                await browser.close()

        except Exception as exc:
            result.error = str(exc)[:300]

        logger.info(
            "visual_flow_test",
            steps_passed=result.steps_passed,
            steps_failed=len(result.steps_failed),
            screenshots=len(result.step_screenshots),
        )
        return result

    async def test_visual_matrix(
        self,
        base_url: str,
        flows: list[dict[str, Any]],
        viewports: list[tuple[str, int, int]] | None = None,
    ) -> VisualMatrixReport:
        """Run visual flow tests across multiple viewports.

        The visual matrix tests the application at 4 standard viewports:
        mobile (375x812), tablet (768x1024), desktop (1280x800), large (1920x1080).

        Args:
            base_url: Application base URL.
            flows: List of flow definitions, each with "name" and "steps".
            viewports: Optional custom viewport list. Default: 4 standard viewports.

        Returns:
            VisualMatrixReport with results per viewport per flow.
        """
        if viewports is None:
            viewports = [
                ("mobile", 375, 812),
                ("tablet", 768, 1024),
                ("desktop", 1280, 800),
                ("large", 1920, 1080),
            ]

        report = VisualMatrixReport(
            viewports=[f"{name}({w}x{h})" for name, w, h in viewports],
            flows=[f.get("name", f"flow_{i}") for i, f in enumerate(flows)],
        )

        if not await self._check_playwright():
            report.error = "Playwright not installed"
            return report

        try:
            from playwright.async_api import async_playwright
            import base64
            import time

            start = time.monotonic()

            async with async_playwright() as p:
                for vp_name, width, height in viewports:
                    for flow_def in flows:
                        flow_name = flow_def.get("name", "unnamed")
                        steps = flow_def.get("steps", [])

                        browser = await p.chromium.launch(headless=True)
                        page = await browser.new_page(viewport={"width": width, "height": height})

                        try:
                            await page.goto(base_url, wait_until="networkidle", timeout=15000)

                            step_screenshots: list[StepScreenshot] = []
                            steps_passed = 0

                            for i, step in enumerate(steps):
                                step_name = step.get("name", f"step_{i+1}")
                                action = step.get("action", "navigate")

                                try:
                                    if action == "navigate":
                                        url = step.get("url", "/")
                                        await page.goto(
                                            f"{base_url.rstrip('/')}{url}",
                                            wait_until="networkidle",
                                            timeout=10000,
                                        )
                                    elif action == "click":
                                        await page.click(step.get("selector", ""), timeout=5000)
                                    elif action == "fill":
                                        await page.fill(
                                            step.get("selector", ""),
                                            step.get("value", ""),
                                            timeout=5000,
                                        )

                                    steps_passed += 1

                                    # Capture screenshot
                                    await page.wait_for_timeout(300)
                                    screenshot = await page.screenshot(full_page=True, type="png")
                                    step_screenshots.append(StepScreenshot(
                                        step_name=step_name,
                                        screenshot_b64=base64.b64encode(screenshot).decode("ascii"),
                                        viewport=f"{width}x{height}",
                                        timestamp=time.monotonic() - start,
                                    ))

                                except Exception:
                                    pass  # Continue with remaining steps

                            entry = VisualMatrixEntry(
                                viewport_name=vp_name,
                                width=width,
                                height=height,
                                flow_name=flow_name,
                                steps_passed=steps_passed,
                                total_steps=len(steps),
                                screenshots=step_screenshots,
                                passed=steps_passed == len(steps),
                            )
                            report.results.append(entry)

                        except Exception as exc:
                            report.results.append(VisualMatrixEntry(
                                viewport_name=vp_name,
                                width=width,
                                height=height,
                                flow_name=flow_name,
                                error=str(exc)[:200],
                            ))

                        await browser.close()

            report.total_time_ms = (time.monotonic() - start) * 1000

        except Exception as exc:
            report.error = str(exc)[:300]

        logger.info(
            "visual_matrix_test",
            viewports=len(viewports),
            flows=len(flows),
            results=len(report.results),
            all_passed=report.all_passed,
        )
        return report
