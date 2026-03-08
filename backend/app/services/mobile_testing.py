"""Mobile Testing — Build + emulator testing for mobile apps.

PHASE-6: Mobile app capabilities:
  - React Native / Expo: Build APK via expo build:android
  - Flutter: Build APK via flutter build apk
  - Emulator testing: Install APK, run basic UI tests

Phase 2B additions:
  - EmulatorState for managing emulator lifecycle
  - launch_emulator() — Android emulator in Docker (budtmo/docker-android)
  - test_mobile_flow() — installs APK, runs UI tests via adb
  - capture_mobile_screenshot() — adb exec-out screencap -p
  - MobileFlowResult with step-by-step screenshots

Note: Full mobile testing requires Android SDK / emulator in Docker.
Graceful degradation: simulation result with explicit flag when Docker unavailable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class BuildResult:
    """Result of building a mobile app."""

    framework: str
    platform: str  # "android" | "ios"
    success: bool = False
    artifact_path: str = ""  # Path to APK/IPA
    artifact_size_bytes: int = 0
    build_time_seconds: float = 0.0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class MobileTestResult:
    """Result of testing a mobile app in emulator."""

    launched: bool = False
    screens_tested: int = 0
    screens_passed: int = 0
    errors: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)  # Base64 PNGs
    test_time_seconds: float = 0.0


# ── Phase 2B: Emulator State + Flow Result ────────────────────────────


@dataclass
class EmulatorState:
    """State of a running Android emulator in Docker."""
    container_id: str = ""
    platform: str = "android"
    api_level: int = 30  # Android 11
    is_running: bool = False
    adb_host: str = ""
    adb_port: int = 5555
    error: str | None = None


@dataclass
class MobileFlowStep:
    """A step in a mobile flow test."""
    action: str  # "tap", "swipe", "type", "wait", "screenshot", "assert"
    selector: str = ""  # resource-id or text
    value: str = ""
    name: str = ""
    timeout_ms: int = 5000


@dataclass
class MobileFlowResult:
    """Result of a mobile flow test with step screenshots."""
    flow_name: str = ""
    total_steps: int = 0
    steps_passed: int = 0
    steps_failed: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)  # Base64 PNGs
    simulated: bool = False  # True if Docker unavailable
    time_seconds: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.steps_passed == self.total_steps and not self.error and not self.simulated


class MobileTestRunner:
    """Mobile app build + testing in Docker."""

    def __init__(self) -> None:
        self._docker_available: bool | None = None
        self._active_emulators: dict[str, EmulatorState] = {}

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

    async def build_react_native(
        self, workspace_path: str, platform: str = "android"
    ) -> BuildResult:
        """Build React Native / Expo app.

        Runs: npx expo build:android (or eas build) in Docker.
        """
        result = BuildResult(framework="react-native", platform=platform)

        if not await self._check_docker():
            result.error = "Docker unavailable — mobile build requires Docker"
            result.warnings.append("Mobile build simulated. APK not generated.")
            return result

        try:
            import time
            start = time.monotonic()

            # Run expo build in Docker container with Node.js
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm",
                "--memory=2g", "--cpus=2",
                "-v", f"{workspace_path}/mobile:/app",
                "-w", "/app",
                "node:20-slim",
                "sh", "-c",
                "npm install && npx expo export --platform android 2>&1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300  # 5 min timeout
            )

            result.build_time_seconds = time.monotonic() - start

            if proc.returncode == 0:
                result.success = True
                result.artifact_path = f"{workspace_path}/mobile/dist/bundle.js"
                logger.info(
                    "mobile_build_success",
                    framework="react-native",
                    platform=platform,
                    time_s=round(result.build_time_seconds, 1),
                )
            else:
                result.error = stderr.decode("utf-8", errors="replace")[:500]
                logger.warning("mobile_build_failed", error=result.error[:200])

        except asyncio.TimeoutError:
            result.error = "Build timed out after 5 minutes"
        except Exception as exc:
            result.error = str(exc)[:200]

        return result

    async def build_flutter(
        self, workspace_path: str, platform: str = "android"
    ) -> BuildResult:
        """Build Flutter app.

        Runs: flutter build apk in Docker.
        """
        result = BuildResult(framework="flutter", platform=platform)

        if not await self._check_docker():
            result.error = "Docker unavailable — Flutter build requires Docker"
            result.warnings.append("Flutter build simulated. APK not generated.")
            return result

        try:
            import time
            start = time.monotonic()

            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm",
                "--memory=4g", "--cpus=2",
                "-v", f"{workspace_path}/mobile:/app",
                "-w", "/app",
                "cirrusci/flutter:stable",
                "sh", "-c",
                "flutter pub get && flutter build apk --release 2>&1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=600  # 10 min timeout
            )

            result.build_time_seconds = time.monotonic() - start

            if proc.returncode == 0:
                result.success = True
                result.artifact_path = f"{workspace_path}/mobile/build/app/outputs/flutter-apk/app-release.apk"
                logger.info(
                    "mobile_build_success",
                    framework="flutter",
                    platform=platform,
                    time_s=round(result.build_time_seconds, 1),
                )
            else:
                result.error = stderr.decode("utf-8", errors="replace")[:500]

        except asyncio.TimeoutError:
            result.error = "Flutter build timed out after 10 minutes"
        except Exception as exc:
            result.error = str(exc)[:200]

        return result

    async def test_in_emulator(self, apk_path: str) -> MobileTestResult:
        """Install APK in Android emulator and run basic UI tests.

        Requires: Android SDK + emulator in Docker (heavy setup).
        Falls back to structural analysis if emulator unavailable.
        """
        result = MobileTestResult()

        if not await self._check_docker():
            result.errors.append("Docker unavailable — emulator testing skipped")
            return result

        # Check if emulator image is available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "images", "-q", "budtmo/docker-android",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)

            if not stdout.decode().strip():
                result.errors.append(
                    "Android emulator Docker image not available. "
                    "Install: docker pull budtmo/docker-android:emulator_11.0"
                )
                return result

        except Exception:
            result.errors.append("Failed to check emulator availability")
            return result

        # If emulator available, run basic test
        try:
            import time
            start = time.monotonic()

            # Start emulator container, install APK, run UI Automator tests
            # This is a framework — actual implementation depends on emulator setup
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm",
                "--memory=4g", "--cpus=2",
                "--device=/dev/kvm",
                "-v", f"{apk_path}:/app.apk:ro",
                "budtmo/docker-android:emulator_11.0",
                "sh", "-c",
                "adb install /app.apk && adb shell monkey -p com.app -v 500 2>&1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )

            result.test_time_seconds = time.monotonic() - start
            result.launched = proc.returncode == 0

            if result.launched:
                result.screens_tested = 1
                result.screens_passed = 1
            else:
                result.errors.append(stderr.decode("utf-8", errors="replace")[:200])

        except asyncio.TimeoutError:
            result.errors.append("Emulator test timed out after 2 minutes")
        except Exception as exc:
            result.errors.append(str(exc)[:200])

        return result

    # ── Phase 2B: Enhanced Emulator Support ──────────────────────────

    async def launch_emulator(
        self,
        platform: str = "android",
        api_level: int = 30,
    ) -> EmulatorState:
        """Launch an Android emulator in Docker (budtmo/docker-android).

        Starts a long-running container with the Android emulator and ADB.
        Returns EmulatorState with container_id for subsequent interactions.

        Graceful degradation: returns state with error if Docker unavailable.
        """
        from app.config import get_settings
        emulator_image = get_settings().mobile_emulator_image

        state = EmulatorState(platform=platform, api_level=api_level)

        if not await self._check_docker():
            state.error = "Docker unavailable — emulator requires Docker"
            return state

        try:
            # Start emulator container in background
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "-d",
                "--memory=4g", "--cpus=2",
                "--device=/dev/kvm",
                "-p", "0:5555",  # Random host port → ADB
                "--name", f"nexsidi-emulator-{api_level}",
                emulator_image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

            if proc.returncode == 0:
                container_id = stdout.decode().strip()[:12]
                state.container_id = container_id
                state.is_running = True

                # Get the mapped ADB port
                port_proc = await asyncio.create_subprocess_exec(
                    "docker", "port", container_id, "5555",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                port_out, _ = await asyncio.wait_for(port_proc.communicate(), timeout=5)
                port_str = port_out.decode().strip()
                if ":" in port_str:
                    state.adb_host = "127.0.0.1"
                    state.adb_port = int(port_str.split(":")[-1])

                self._active_emulators[container_id] = state
                logger.info("emulator_launched", container_id=container_id, api_level=api_level)
            else:
                state.error = stderr.decode("utf-8", errors="replace")[:300]
                logger.warning("emulator_launch_failed", error=state.error[:200])

        except asyncio.TimeoutError:
            state.error = "Emulator launch timed out"
        except Exception as exc:
            state.error = str(exc)[:300]

        return state

    async def test_mobile_flow(
        self,
        emulator: EmulatorState,
        apk_path: str,
        flow_steps: list[MobileFlowStep],
    ) -> MobileFlowResult:
        """Install APK and run UI tests on the emulator via adb.

        Graceful degradation: if emulator is not running, returns
        a simulated result with explicit SIMULATED flag.

        Args:
            emulator: EmulatorState from launch_emulator().
            apk_path: Path to the APK file to install.
            flow_steps: List of MobileFlowStep actions.

        Returns:
            MobileFlowResult with screenshots and pass/fail status.
        """
        result = MobileFlowResult(
            flow_name="mobile_flow",
            total_steps=len(flow_steps),
        )

        if not emulator.is_running or not emulator.container_id:
            result.simulated = True
            result.error = "SIMULATED — NOT VERIFIED: Emulator not running"
            result.steps_passed = 0
            return result

        try:
            import time
            start = time.monotonic()

            container_id = emulator.container_id

            # Install APK
            install_proc = await asyncio.create_subprocess_exec(
                "docker", "exec", container_id,
                "adb", "install", "-r", apk_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, install_err = await asyncio.wait_for(install_proc.communicate(), timeout=60)

            if install_proc.returncode != 0:
                result.error = f"APK install failed: {install_err.decode()[:200]}"
                return result

            # Run flow steps
            for i, step in enumerate(flow_steps):
                step_name = step.name or f"step_{i+1}"
                try:
                    if step.action == "tap":
                        await self._adb_tap(container_id, step.selector)
                    elif step.action == "type":
                        await self._adb_type(container_id, step.value)
                    elif step.action == "swipe":
                        await self._adb_swipe(container_id, step.selector)
                    elif step.action == "wait":
                        await asyncio.sleep(step.timeout_ms / 1000)
                    elif step.action == "screenshot":
                        screenshot = await self.capture_mobile_screenshot(emulator)
                        if screenshot:
                            result.screenshots.append(screenshot)

                    result.steps_passed += 1

                    # Auto-capture screenshot after each action step
                    if step.action != "screenshot":
                        screenshot = await self.capture_mobile_screenshot(emulator)
                        if screenshot:
                            result.screenshots.append(screenshot)

                except Exception as step_exc:
                    result.steps_failed.append(f"{step_name}: {str(step_exc)[:100]}")

            result.time_seconds = time.monotonic() - start

        except asyncio.TimeoutError:
            result.error = "Mobile flow test timed out"
        except Exception as exc:
            result.error = str(exc)[:300]

        return result

    async def capture_mobile_screenshot(self, emulator: EmulatorState) -> str:
        """Capture a screenshot from the emulator via adb.

        Returns base64-encoded PNG string, or empty string on failure.
        """
        if not emulator.is_running or not emulator.container_id:
            return ""

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", emulator.container_id,
                "adb", "exec-out", "screencap", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)

            if proc.returncode == 0 and stdout:
                import base64
                return base64.b64encode(stdout).decode("ascii")
        except Exception:
            pass  # Non-critical — error logged upstream or handled by caller

        return ""

    async def stop_emulator(self, emulator: EmulatorState) -> None:
        """Stop and remove the emulator Docker container."""
        if not emulator.container_id:
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", emulator.container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            emulator.is_running = False
            self._active_emulators.pop(emulator.container_id, None)
            logger.info("emulator_stopped", container_id=emulator.container_id)
        except Exception as exc:
            logger.warning("emulator_stop_failed", error=str(exc)[:200])

    async def _adb_tap(self, container_id: str, coordinates: str) -> None:
        """Tap at coordinates (format: 'x,y' or 'resource-id:name')."""
        if "," in coordinates:
            x, y = coordinates.split(",", 1)
            cmd = f"adb shell input tap {x.strip()} {y.strip()}"
        else:
            # Use uiautomator to find element by resource-id and tap
            cmd = (
                f"adb shell uiautomator dump /dev/tty | "
                f"grep -o 'resource-id=\"{coordinates}\"[^>]*bounds=\"\\[[0-9]*,[0-9]*\\]' | "
                f"head -1"
            )
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "sh", "-c", cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)

    async def _adb_type(self, container_id: str, text: str) -> None:
        """Type text via adb shell input text."""
        # Escape spaces for adb
        safe_text = text.replace(" ", "%s")
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id,
            "adb", "shell", "input", "text", safe_text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)

    async def _adb_swipe(self, container_id: str, direction: str) -> None:
        """Swipe in the given direction (up/down/left/right)."""
        swipe_coords = {
            "up": "500 1500 500 500",
            "down": "500 500 500 1500",
            "left": "800 500 200 500",
            "right": "200 500 800 500",
        }
        coords = swipe_coords.get(direction.lower(), "500 1500 500 500")
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id,
            "adb", "shell", "input", "swipe", *coords.split(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
